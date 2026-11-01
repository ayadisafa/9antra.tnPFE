"""How the centre's posts do: each post's numbers (reactions, comments, shares, saves, clicks, views or reach, followers),
its engagement rate, and what works best by kind (post, story, video), subject, network, day and hour, with advice
counted from them (never guessed: below a few posts it says so).

The numbers are typed on the Social media page, or read from Facebook once the page's access token is given (Graph API:
reactions, comments and shares of each post, the page's followers). LinkedIn and Instagram numbers are typed.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.social import SocialPost

logger = logging.getLogger(__name__)
PLATFORMS = {"facebook": ("Facebook", "bi-facebook", "#1877f2"), "instagram": ("Instagram", "bi-instagram", "#e1306c"),
             "linkedin": ("LinkedIn", "bi-linkedin", "#0a66c2"), "tiktok": ("TikTok", "bi-tiktok", "#111827"), "other": ("Other", "bi-globe2", "#6b7280")}
KINDS = {"post": ("Post", "bi-image"), "story": ("Story", "bi-phone"), "video": ("Video or reel", "bi-camera-reels")}
NUMBERS = ("reactions", "comments", "shares", "saves", "clicks", "views", "followers")
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ENOUGH = 2           # posts in a group before it is compared with another


def interactions(post: SocialPost) -> int:
    return sum(getattr(post, name) or 0 for name in ("reactions", "comments", "shares", "saves", "clicks"))


def rate(post: SocialPost) -> float | None:
    """Interactions per 100 people reached (or per 100 followers when the reach is not known)."""
    base = post.views or post.followers
    if not base:
        return None
    return round(100 * interactions(post) / base, 2)


def _number(value) -> int | None:
    text = str(value if value is not None else "").strip().replace(" ", "").replace(",", "").replace(" ", "")
    if not text:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", text)
    if not match:
        raise ValueError(f"“{value}” is not a number.")
    amount = float(match.group(1)) * {"": 1, "k": 1000, "m": 1_000_000}[match.group(2).lower()]
    return int(round(amount))


def save(db: Session, values: dict, post_id: str | None = None) -> SocialPost:
    """A post and its numbers, typed (or corrected) on the page. Raises ValueError with a message for the admin."""
    post = db.get(SocialPost, post_id) if post_id else None
    if post is None:
        post = SocialPost(source=values.get("source") or "typed")
        db.add(post)
    platform = values.get("platform") or "facebook"
    post.platform = platform if platform in PLATFORMS else "other"
    kind = values.get("kind") or "post"
    post.kind = kind if kind in KINDS else "post"
    topic = values.get("topic") or ""
    from app.services import social_content

    post.topic = topic if topic in social_content.TOPICS else (topic or None) if topic == "other" else None
    post.text = (values.get("text") or "").strip()[:4000] or post.text
    link = (values.get("link") or "").strip()
    if link and not re.match(r"^https?://", link, re.I):
        raise ValueError("The link must start with http:// or https://.")
    post.link = link[:600] or post.link
    when = (values.get("published_at") or "").strip()
    if when:
        for pattern in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                post.published_at = datetime.strptime(when, pattern)
                break
            except ValueError:
                continue
        else:
            raise ValueError("Give the day it was published, like 2026-11-01 18:30.")
    elif post.published_at is None:
        post.published_at = datetime.now().replace(second=0, microsecond=0)
    typed = False
    for name in NUMBERS:
        if name in values:
            amount = _number(values.get(name))
            setattr(post, name, amount)
            typed = typed or amount is not None
    if typed:
        post.numbers_at = datetime.utcnow()
    for name in ("asset_id", "external_id"):
        if values.get(name):
            setattr(post, name, values[name])
    db.flush()
    return post


def remove(db: Session, post_id: str) -> bool:
    post = db.get(SocialPost, post_id)
    if post is None:
        return False
    db.delete(post)
    db.flush()
    return True


def _group(posts: list[SocialPost], key) -> dict[str, list[float]]:
    found: dict[str, list[float]] = {}
    for post in posts:
        value = rate(post)
        name = key(post)
        if value is not None and name is not None:
            found.setdefault(name, []).append(value)
    return found


def _rows(groups: dict[str, list[float]], labels: dict) -> list[dict]:
    best = max((sum(values) / len(values) for values in groups.values()), default=0) or 1
    rows = [{"key": key, "label": labels.get(key, key), "count": len(values), "average": round(sum(values) / len(values), 2),
             "pct": round(100 * (sum(values) / len(values)) / best)} for key, values in groups.items()]
    return sorted(rows, key=lambda row: -row["average"])


def _hour_band(post: SocialPost) -> str | None:
    if post.published_at is None or (post.published_at.hour == 0 and post.published_at.minute == 0):
        return None
    hour = post.published_at.hour
    return "morning" if 5 <= hour < 12 else "afternoon" if hour < 17 else "evening" if hour < 22 else "night"


def overview(db: Session) -> dict:
    """Every post with its rate, the comparisons, and the advice."""
    from app.services import social_content

    def _said(template: str, *values) -> str:
        # Written in English with its values; the page shows it in its language (dynamic_words, patterns in
        # dynamic_phrases.json, the values through the page's dictionary).
        return template.format(*values)

    posts = db.query(SocialPost).order_by(SocialPost.published_at.desc().nullslast(), SocialPost.created_at.desc()).all()
    rated = [post for post in posts if rate(post) is not None]
    month = [post for post in posts if post.published_at and post.published_at >= datetime.now() - timedelta(days=30)]
    by_kind = _rows(_group(rated, lambda post: post.kind), {key: value[0] for key, value in KINDS.items()})
    by_topic = _rows(_group(rated, lambda post: post.topic), {key: value[0] for key, value in social_content.TOPICS.items()})
    by_platform = _rows(_group(rated, lambda post: post.platform), {key: value[0] for key, value in PLATFORMS.items()})
    by_day = _rows(_group(rated, lambda post: DAYS[post.published_at.weekday()] if post.published_at else None), {day: day for day in DAYS})
    by_hour = _rows(_group(rated, _hour_band), {"morning": "Morning (5–12 h)", "afternoon": "Afternoon (12–17 h)",
                                                "evening": "Evening (17–22 h)", "night": "Night (22–5 h)"})
    advice = []
    compared = [row for row in by_kind if row["count"] >= ENOUGH]
    if len(compared) >= 2 and compared[-1]["average"] > 0 and compared[0]["average"] / compared[-1]["average"] >= 1.3:
        advice.append({"icon": "bi-camera-reels", "text": _said("“{0}” get {1}× the engagement of “{2}”: make more of them.", compared[0]["label"],
                                                              round(compared[0]["average"] / compared[-1]["average"], 1), compared[-1]["label"])})
    compared = [row for row in by_topic if row["count"] >= ENOUGH]
    if len(compared) >= 2:
        advice.append({"icon": "bi-bullseye", "text": _said("Posts about “{0}” get the most engagement ({1}%): post them more often.", compared[0]["label"],
                                                          compared[0]["average"])})
    compared = [row for row in by_day if row["count"] >= ENOUGH]
    if len(compared) >= 3:
        advice.append({"icon": "bi-calendar-heart", "text": _said("{0} is your best day ({1}% engagement).", compared[0]["label"], compared[0]["average"])})
    compared = [row for row in by_hour if row["count"] >= ENOUGH]
    if len(compared) >= 2:
        band = compared[0]["key"]
        advice.append({"icon": "bi-clock", "text": {"morning": "Post in the morning: it is when your posts do best.",
                                                     "afternoon": "Post in the afternoon: it is when your posts do best.",
                                                     "evening": "Post in the evening: it is when your posts do best.",
                                                     "night": "Post at night: it is when your posts do best."}[band]})
    talked = sum(post.comments or 0 for post in rated)
    total = sum(interactions(post) for post in rated)
    if len(rated) >= 3 and total and talked / total < 0.05:
        advice.append({"icon": "bi-chat-dots", "text": _said("Few comments ({0}% of interactions): end your posts with a question to people.", round(100 * talked / total))})
    if posts and len(month) < 4:
        advice.append({"icon": "bi-calendar-plus", "text": _said("{0} post(s) in 30 days: aim for two a week; the ideas below are ready.", len(month))})
    if len(rated) < 3:
        advice.append({"icon": "bi-pencil-square", "text": _said("Add the numbers of your last posts (reactions, comments, shares, views): the comparisons start from three posts with numbers.")})
    followers: dict[str, int] = {}
    for post in sorted(posts, key=lambda item: item.numbers_at or datetime.min):
        if post.followers:
            followers[post.platform] = post.followers
    rates = [rate(post) for post in rated]
    best = max(rated, key=rate) if rated else None
    return {
        "posts": [{"post": post, "rate": rate(post), "interactions": interactions(post), "platform": PLATFORMS.get(post.platform, PLATFORMS["other"]),
                   "kind": KINDS.get(post.kind, KINDS["post"]), "topic": social_content.TOPICS.get(post.topic or "", (post.topic or "", "bi-tag"))}
                  for post in posts],
        "count": len(posts), "month": len(month), "rated": len(rated),
        "average": round(sum(rates) / len(rates), 2) if rates else None, "best": best, "best_rate": rate(best) if best else None,
        "by_kind": by_kind, "by_topic": by_topic, "by_platform": by_platform, "by_day": by_day, "by_hour": by_hour,
        "advice": advice, "followers": [{"platform": PLATFORMS[key], "count": count} for key, count in followers.items() if key in PLATFORMS],
        "facebook": facebook_connected(),
    }


def facebook_connected() -> bool:
    from app.services import website_sync

    return website_sync.facebook_connected()


def facebook_import(db: Session) -> dict:
    """The page's last posts with their reactions, comments and shares, and its followers (needs the page token)."""
    import requests

    if not facebook_connected():
        raise ValueError("Connect the Facebook page first (Settings › Identity: the page ID and its access token).")
    page, token = os.getenv("FACEBOOK_PAGE_ID").strip(), os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN").strip()
    base = "https://graph.facebook.com/v20.0"
    try:
        followers = requests.get(f"{base}/{page}", params={"fields": "followers_count", "access_token": token}, timeout=30).json().get("followers_count")
        response = requests.get(f"{base}/{page}/posts", timeout=30, params={
            "fields": "id,message,created_time,permalink_url,shares,reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0)",
            "limit": 40, "access_token": token})
        rows = response.json().get("data") if response.status_code == 200 else None
    except (requests.RequestException, ValueError) as error:
        raise ValueError(f"Facebook did not answer: {error}")
    if not isinstance(rows, list):
        raise ValueError("Facebook did not give the posts: check the page's access token in Settings.")
    added = updated = 0
    for row in rows:
        identifier = "fb:" + str(row.get("id"))
        post = db.query(SocialPost).filter(SocialPost.external_id == identifier).first()
        values = {"platform": "facebook", "kind": "post", "text": row.get("message") or "", "link": row.get("permalink_url") or "",
                  "published_at": (row.get("created_time") or "")[:16].replace("T", " "), "external_id": identifier, "source": "facebook",
                  "reactions": ((row.get("reactions") or {}).get("summary") or {}).get("total_count"),
                  "comments": ((row.get("comments") or {}).get("summary") or {}).get("total_count"),
                  "shares": (row.get("shares") or {}).get("count"), "followers": followers}
        save(db, values, post.id if post else None)
        added, updated = (added, updated + 1) if post else (added + 1, updated)
    db.commit()
    return {"added": added, "updated": updated}

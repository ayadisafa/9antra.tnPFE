"""What the public website says, kept in the database and kept up to date.

The website (9antra.tn) publishes its formations, events, internship offers
and news as data. This module reads them, keeps a copy (WebsiteItem), and
makes the programs of the application follow their formation:

- a detail the program does not have yet is filled from the website;
- a detail that came from the website is updated when the website changes;
- a detail the admin wrote themselves is never replaced. The difference is
  shown, and one click takes the website's version.

Nothing here is guessed by a model: the website gives structured data, and
every value is copied or converted by a rule that can be read below.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy.orm import Session, selectinload

from app.models.notification import Notification
from app.models.program import CurriculumResource, Program, ProgramLevel, WeeklyCurriculum
from app.models.website import WebsiteCheck, WebsiteItem

logger = logging.getLogger(__name__)

SITE = os.getenv("WEBSITE_URL", "https://9antra.tn").rstrip("/")
CHECK_EVERY = timedelta(hours=6)
NO_PROGRAM = "-"                    # a formation the admin does not want as a program
KEEP_CHECKS = 400                   # the record of what changed on the website, reading after reading
_HEADERS = {"User-Agent": "9antra training app (catalogue sync)", "Accept": "application/json"}

KINDS = {
    "formation": {"label": "Formations", "one": "formation", "icon": "bi-mortarboard-fill"},
    "event": {"label": "Events", "one": "event", "icon": "bi-calendar-event-fill"},
    "offer": {"label": "Internship offers", "one": "internship offer", "icon": "bi-briefcase-fill"},
    "news": {"label": "News", "one": "news article", "icon": "bi-newspaper"},
    "video": {"label": "Videos", "one": "video", "icon": "bi-play-btn-fill"},
    "feedback": {"label": "Feedback", "one": "feedback", "icon": "bi-chat-heart-fill"},
    "post": {"label": "Facebook posts", "one": "Facebook post", "icon": "bi-facebook"},
}
FACEBOOK_PAGE = os.getenv("FACEBOOK_PAGE_NAME", "9antra.tn")

# The details of a program that can follow the website, and how they read.
FIELDS = {
    "description": "Description",
    "total_duration_days": "Duration",
    "price_online": "Price online",
    "price_onsite": "Price on site",
    "hours_per_week": "Hours per week",
    "training_hours": "Course hours per week",
    "supervision_hours": "Supervised hours per week",
    "class_session_time": "Session time",
}


class SiteUnreachable(Exception):
    """The website did not answer, or not with what was expected."""


# ------------------------------------------------------------------
# Reading the website
# ------------------------------------------------------------------

def _get(path: str):
    try:
        response = requests.get(f"{SITE}/api/{path}", headers=_HEADERS, timeout=30)
    except requests.RequestException as exc:
        raise SiteUnreachable(f"{SITE} did not answer ({type(exc).__name__}).") from exc
    if response.status_code != 200:
        raise SiteUnreachable(f"{SITE} answered {response.status_code} for its {path}.")
    response.encoding = "utf-8"
    try:
        return response.json()
    except ValueError as exc:
        raise SiteUnreachable(f"{SITE} did not send data for its {path}.") from exc


def _list(value, key: str) -> list[dict]:
    if isinstance(value, dict):
        value = value.get(key) if key else None
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _optional(path: str, key: str) -> list[dict]:
    """A part of the website that may be missing without stopping the rest."""
    try:
        return _list(_get(path), key)
    except SiteUnreachable as exc:
        logger.warning("Website: %s", exc)
        return []


def _site_videos() -> list[dict]:
    """The testimonial videos and the presentation video of the website."""
    try:
        settings = _get("settings")
    except SiteUnreachable as exc:
        logger.warning("Website: %s", exc)
        return []
    if not isinstance(settings, dict):
        return []
    videos = []
    labels = {"formation": "formation", "pfe": "stage PFE", "summer-camp": "Summer Camp"}
    counts: dict[str, int] = {}
    for raw in _list(settings.get("testimonialVideos"), ""):
        category = _text(raw.get("category"), 60) or "formation"
        counts[category] = counts.get(category, 0) + 1
        videos.append({**raw, "category": category, "title": f"Témoignage {labels.get(category, category)} n°{counts[category]}"})
    action = settings.get("actionVideo") if isinstance(settings.get("actionVideo"), dict) else None
    if action and (action.get("driveUrl") or action.get("url")):
        videos.append({**action, "_id": "action-video", "category": "présentation", "title": "Vidéo de présentation de 9antra"})
    return videos


def facebook_connected() -> bool:
    """Whether the page's access token is there (a real one is long)."""
    token = (os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN") or "").strip()
    return len(token) >= 60 and bool((os.getenv("FACEBOOK_PAGE_ID") or "").strip())


def _facebook() -> dict[str, list[dict]]:
    """The page's posts, videos and reviews, read with the page's own access
    token (Facebook gives them to nobody else). Nothing without a token."""
    if not facebook_connected():
        return {}
    page, token = os.getenv("FACEBOOK_PAGE_ID").strip(), os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN").strip()
    edges = {
        "post": ("posts", "id,message,created_time,permalink_url,full_picture"),
        "video": ("videos", "id,title,description,created_time,permalink_url,picture,length"),
        "feedback": ("ratings", "created_time,recommendation_type,review_text,open_graph_story,reviewer{name}"),
    }
    found: dict[str, list[dict]] = {}
    for kind, (edge, fields) in edges.items():
        try:
            response = requests.get(
                f"https://graph.facebook.com/v20.0/{page}/{edge}", timeout=30,
                params={"fields": fields, "limit": 50, "access_token": token},
            )
            rows = response.json().get("data") if response.status_code == 200 else None
        except (requests.RequestException, ValueError):
            rows = None
        if not isinstance(rows, list):
            logger.warning("Facebook did not give the page's %s.", edge)
            continue
        found[kind] = [{**row, "_facebook": True} for row in rows if isinstance(row, dict)]
    return found


def fetch() -> dict[str, list[dict]]:
    """Everything published, as it is sent. The formations are the heart of
    it: without them the website counts as unreachable."""
    published = {
        "formation": _list(_get("formations"), "formations"),
        "event": _optional("events", "events"),
        "offer": _optional("offers", "offers"),
        "news": _optional("news", "news"),
        "video": _site_videos(),
        "feedback": [
            {**raw, "title": _text(raw.get("name"), 120) or f"Avis publié n°{raw.get('order') or index + 1}"}
            for index, raw in enumerate(_optional("testimonial-screenshots", ""))
        ],
        "post": [],
    }
    for kind, rows in _facebook().items():
        published[kind] = published.get(kind, []) + rows
    return published


def _text(value, limit: int = 4000) -> str:
    return " ".join(str(value or "").split())[:limit]


def _when(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def _week_rows(rows) -> list[dict]:
    weeks = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not _text(row.get("content")):
            continue
        weeks.append({
            "week": int(row.get("week") or len(weeks) + 1),
            "phase": _text(row.get("phase"), 120),
            "content": _text(row.get("content"), 1500),
            "video_title": _text(row.get("videoTitle"), 200),
            "video_url": _text(row.get("videoUrl"), 500),
            "video_length": _text(row.get("duree"), 20),
            "free": bool(row.get("gratuit")),
        })
    return sorted(weeks, key=lambda item: item["week"])


def normalise(kind: str, raw: dict) -> dict | None:
    """One thing of the website in the shape it is kept in. None when it has no name."""
    if raw.get("_facebook"):
        return _normalise_facebook(kind, raw)
    external_id = _text(raw.get("_id") or raw.get("slug"), 80)
    title = _text(raw.get("title"), 300)
    if not external_id or not title:
        return None
    item = {"external_id": external_id, "slug": None, "title": title, "category": None, "starts_at": None, "volatile": {}}
    if kind == "formation":
        slug = _text(raw.get("slug"), 120)
        price = raw.get("price") if isinstance(raw.get("price"), dict) else {}
        item.update(
            slug=slug, summary=_text(raw.get("description")), category=_text(raw.get("level"), 120) or None,
            url=f"{SITE}/formations/{slug}" if slug else f"{SITE}/formations",
            data={
                "description": _text(raw.get("description")), "duration": _text(raw.get("duration"), 200),
                "level": _text(raw.get("level"), 120), "mode": _text(raw.get("mode"), 80),
                "schedule": _text(raw.get("schedule"), 300), "certificate": bool(raw.get("certificate")),
                "price": {name: _text(price.get(name), 60) for name in ("onsite", "online", "recordings") if price.get(name)},
                "technologies": [_text(tech, 40) for tech in raw.get("technologies") or [] if _text(tech, 40)],
                "weeks": _week_rows(raw.get("weeks")), "supervision": _week_rows(raw.get("supervision")),
                "trailer": _text(raw.get("trailerVideoUrl") or raw.get("trailerDriveUrl"), 500),
            },
            volatile={"views": raw.get("views") or 0},
        )
    elif kind == "event":
        item.update(
            summary=_text(raw.get("description")), category=_text(raw.get("category"), 120) or None,
            url=f"{SITE}/events/{external_id}", starts_at=_when(raw.get("startsAt")),
            data={
                "description": _text(raw.get("description")), "starts_at": _text(raw.get("startsAt"), 40),
                "ends_at": _text(raw.get("endsAt"), 40), "mode": _text(raw.get("mode"), 40),
                "location": _text(raw.get("location"), 200), "capacity": raw.get("capacity"),
                "status": _text(raw.get("status"), 40),
            },
            volatile={"registered": raw.get("registrationCount") or 0},
        )
    elif kind == "offer":
        item.update(
            summary=_text(raw.get("description")), category=_text(raw.get("domain"), 120) or None,
            url=f"{SITE}/offers/{external_id}",
            data={
                "description": _text(raw.get("description")), "type": _text(raw.get("type"), 40),
                "company": _text(raw.get("companyName") or raw.get("company"), 160),
                "duration": _text(raw.get("duration"), 80), "location": _text(raw.get("location"), 160),
                "interns": _text(raw.get("nbrInterns"), 40), "active": raw.get("isActive") is not False,
                "skills": [_text(skill, 60) for skill in raw.get("skillsRequired") or raw.get("skills") or [] if _text(skill, 60)],
            },
            volatile={"views": raw.get("views") or 0},
        )
    elif kind == "news":
        item.update(
            summary=_text(raw.get("excerpt") or raw.get("content"), 600), category=_text(raw.get("category"), 120) or None,
            url=f"{SITE}/blog/{external_id}", starts_at=_when(raw.get("publishedAt")),
            data={
                "excerpt": _text(raw.get("excerpt"), 600), "content": _text(raw.get("content"), 6000),
                "author": _text(raw.get("author"), 120), "published_at": _text(raw.get("publishedAt"), 40),
            },
        )
    elif kind == "video":
        watch = _text(raw.get("driveUrl") or raw.get("url"), 500)
        item.update(
            summary=None, category=_text(raw.get("category"), 60) or None, url=watch,
            data={"embed": _text(raw.get("url"), 500), "thumbnail": drive_picture(raw.get("thumbnail")), "provider": _text(raw.get("provider"), 40)},
        )
    elif kind == "feedback":
        image = _text(raw.get("imageUrl"), 500)
        image = image if image.startswith("http") else SITE + "/" + image.lstrip("/") if image else ""
        item.update(summary=None, category="website", url=image or SITE, data={"image": image, "order": raw.get("order") or 0})
    else:
        return None
    return _finish(item)


def drive_picture(link) -> str:
    """A Google Drive file link as a picture a page can show."""
    link = _text(link, 500)
    found = re.search(r"/file/d/([A-Za-z0-9_-]+)", link) or re.search(r"[?&]id=([A-Za-z0-9_-]+)", link)
    return f"https://drive.google.com/thumbnail?id={found.group(1)}&sz=w640" if found else link


def _normalise_facebook(kind: str, raw: dict) -> dict | None:
    """A post, a video or a review of the Facebook page."""
    created = _when(raw.get("created_time"))
    item = {"slug": None, "category": "facebook", "starts_at": created, "volatile": {}}
    if kind == "post":
        message = str(raw.get("message") or "").strip()
        if not raw.get("id") or not message:
            return None
        item.update(
            external_id="fb:" + _text(raw["id"], 70), title=_text(message.splitlines()[0], 140), summary=_text(message),
            url=_text(raw.get("permalink_url"), 500) or f"https://www.facebook.com/{FACEBOOK_PAGE}",
            data={"message": message[:6000], "picture": _text(raw.get("full_picture"), 800)},
        )
    elif kind == "video":
        if not raw.get("id"):
            return None
        words = _text(raw.get("title") or str(raw.get("description") or "").strip().split("\n")[0], 160)
        link = _text(raw.get("permalink_url"), 500)
        item.update(
            external_id="fb:" + _text(raw["id"], 70), title=words or "Vidéo de la page Facebook", summary=_text(raw.get("description")),
            url=link if link.startswith("http") else "https://www.facebook.com" + link,
            data={"embed": "", "thumbnail": _text(raw.get("picture"), 800), "provider": "facebook", "length": raw.get("length")},
        )
    elif kind == "feedback":
        text = _text(raw.get("review_text"))
        story = raw.get("open_graph_story") if isinstance(raw.get("open_graph_story"), dict) else {}
        reviewer = raw.get("reviewer") if isinstance(raw.get("reviewer"), dict) else {}
        identifier = _text(story.get("id") or f"{raw.get('created_time')}-{reviewer.get('name')}", 70)
        if not text or not identifier:
            return None
        item.update(
            external_id="fb:" + identifier, title=_text(reviewer.get("name"), 120) or "Avis Facebook", summary=text,
            url=f"https://www.facebook.com/{FACEBOOK_PAGE}/reviews",
            data={"text": text, "recommends": raw.get("recommendation_type") != "negative", "image": ""},
        )
    else:
        return None
    return _finish(item)


def _finish(item: dict) -> dict:
    # The fingerprint leaves out what moves all the time (views, seats taken).
    item["fingerprint"] = hashlib.sha1(
        json.dumps([item["title"], item["category"], item["data"]], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    item["data"] = {**item["data"], **item["volatile"]}
    del item["volatile"]
    return item


# ------------------------------------------------------------------
# From a formation's words to a program's details
# ------------------------------------------------------------------

_DAYS = {
    "lundi": "Monday", "mardi": "Tuesday", "mercredi": "Wednesday", "jeudi": "Thursday",
    "vendredi": "Friday", "samedi": "Saturday", "dimanche": "Sunday",
}
_SLOT = re.compile(
    r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+(\d{1,2})\s*h\s*(\d{2})?\s*[-–à]\s*(\d{1,2})\s*h\s*(\d{2})?\s*(\([^)]*\))?",
    re.IGNORECASE,
)


def amount(text) -> float | None:
    """"490 DT/Mois" is 490."""
    found = re.search(r"(\d+(?:[.,]\d+)?)", str(text or ""))
    return float(found.group(1).replace(",", ".")) if found else None


def days_of(duration) -> int | None:
    """"2 mois" is 60 days, "6 semaines" 42."""
    text = str(duration or "").lower()
    found = re.search(r"(\d+)\s*(mois|month|semaine|week|jour|day)", text)
    if not found:
        return None
    count, unit = int(found.group(1)), found.group(2)
    return count * (30 if unit in ("mois", "month") else 7 if unit in ("semaine", "week") else 1)


def slots_of(schedule) -> list[dict]:
    """"Samedi 9h-13h (en présentiel et en ligne) + Mercredi 20h-22h (en ligne)" as sessions."""
    slots = []
    for day, start_h, start_m, end_h, end_m, where in _SLOT.findall(str(schedule or "")):
        start = int(start_h) + int(start_m or 0) / 60
        end = int(end_h) + int(end_m or 0) / 60
        if end <= start:
            continue
        where = where.lower()
        place = (
            "Online/Onsite" if "ligne" in where and ("présentiel" in where or "presentiel" in where)
            else "Onsite" if "présentiel" in where or "presentiel" in where
            else "Online" if "ligne" in where else ""
        )
        slots.append({
            "day": _DAYS[day.lower()], "hours": end - start, "place": place,
            "label": f"{_DAYS[day.lower()]} from {int(start_h)}:{start_m or '00'} until {int(end_h)}:{end_m or '00'}"
                     + (f" ({place})" if place else ""),
        })
    return slots


def program_values(details: dict) -> dict:
    """What a formation says about the details a program has. Missing ones are left out."""
    values: dict = {}
    if details.get("description"):
        values["description"] = details["description"]
    days = days_of(details.get("duration"))
    if days:
        values["total_duration_days"] = days
    price = details.get("price") or {}
    for field, name in (("price_online", "online"), ("price_onsite", "onsite")):
        found = amount(price.get(name))
        if found:
            values[field] = found
    slots = slots_of(details.get("schedule"))
    if slots:
        # The first session of the week is the course, the others are supervised work.
        values["hours_per_week"] = int(round(sum(slot["hours"] for slot in slots)))
        values["training_hours"] = int(round(slots[0]["hours"]))
        values["supervision_hours"] = int(round(sum(slot["hours"] for slot in slots[1:])))
        values["class_session_time"] = slots[0]["label"]
    return values


def _same(field: str, left, right) -> bool:
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        try:
            return abs(float(left or 0) - float(right or 0)) < 0.005
        except (TypeError, ValueError):
            return False
    return " ".join(str(left or "").split()) == " ".join(str(right or "").split())


def _empty(field: str, value) -> bool:
    return value in (None, "", 0, 0.0) or (isinstance(value, str) and not value.strip())


def shown(field: str, value) -> str:
    """A detail in words, for the pages."""
    if _empty(field, value):
        return "Not set"
    if field == "total_duration_days":
        days = int(value)
        return f"{days // 30} month{'s' if days // 30 != 1 else ''}" if days % 30 == 0 else f"{days} days"
    if field in ("price_online", "price_onsite"):
        return f"{float(value):.0f} DT a month"
    if field in ("hours_per_week", "training_hours", "supervision_hours"):
        return f"{int(value)} h"
    return str(value)


def _stored(program: Program) -> dict:
    try:
        loaded = json.loads(program.website_values or "{}")
    except (TypeError, ValueError):
        loaded = {}
    loaded = loaded if isinstance(loaded, dict) else {}
    changed = [entry for entry in (loaded.get("changed") or []) if isinstance(entry, dict)]
    return {"applied": dict(loaded.get("applied") or {}), "curriculum": loaded.get("curriculum"), "changed": changed}


def _curriculum_print(program: Program) -> str:
    """Changes when the program's weekly plan does."""
    rows = [
        [level.level_order, level.title, [[week.week_number, week.topic_title, week.learning_objectives] for week in level.weeks]]
        for level in program.levels
    ]
    return hashlib.sha1(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def _plan_from(details: dict) -> list[dict]:
    """A formation's weeks as levels of four weeks, the way programs are organised here."""
    weeks = details.get("weeks") or []
    supervised = {row["week"]: row for row in details.get("supervision") or []}
    levels = []
    for start in range(0, len(weeks), 4):
        chunk = weeks[start:start + 4]
        phases = list(dict.fromkeys(row["phase"] for row in chunk if row["phase"]))
        number = start // 4 + 1
        levels.append({
            "order": number,
            "title": f"Level {number}" + (f": {' & '.join(phases[:2])}" if phases else ""),
            "description": "; ".join(row["content"].split(" – ")[0] for row in chunk)[:500],
            "weeks": [
                {
                    "number": row["week"],
                    "topic": (row["content"].split(" – ")[0] or f"Semaine {row['week']}")[:200],
                    "objectives": "; ".join(part.strip() for part in row["content"].split(" – ") if part.strip())
                                  + (f"\nEncadrement : {supervised[row['week']]['content']}" if row["week"] in supervised else ""),
                    "video": (row["video_title"] or f"Vidéo de la semaine {row['week']}", row["video_url"]) if row["video_url"] else None,
                }
                for row in chunk
            ],
        })
    return levels


def _write_plan(db: Session, program: Program, details: dict, values: dict) -> bool:
    plan = _plan_from(details)
    if not plan:
        return False
    for level in list(program.levels):
        db.delete(level)
    db.flush()
    for entry in plan:
        level = ProgramLevel(
            program_id=program.id, level_order=entry["order"], title=entry["title"], description=entry["description"],
            hours_per_week=values.get("hours_per_week") or program.hours_per_week or None,
            class_session_time=values.get("class_session_time") or program.class_session_time,
        )
        db.add(level)
        db.flush()
        for row in entry["weeks"]:
            week = WeeklyCurriculum(
                level_id=level.id, week_number=row["number"], topic_title=row["topic"], learning_objectives=row["objectives"],
            )
            db.add(week)
            if row["video"]:
                db.flush()
                db.add(CurriculumResource(week_id=week.id, resource_type="video", title=row["video"][0][:200], url=row["video"][1]))
    db.flush()
    db.refresh(program)
    return True


def apply_to_program(db: Session, program: Program, item: WebsiteItem, *, take: set[str] | None = None) -> dict:
    """Make a program follow its formation.

    `take` names the details for which the website's version replaces the
    admin's own ("plan" for the weekly plan). Returns {"filled", "updated"}:
    the labels of what changed.
    """
    details = item.details
    site = program_values(details)
    stored = _stored(program)
    applied = stored["applied"]
    take = take or set()
    outcome = {"filled": [], "updated": []}
    changed = stored["changed"]
    moment = datetime.utcnow().isoformat(timespec="seconds")

    def remember(field: str, label: str, was: str, now: str) -> None:
        """What was changed, kept so the page can say it is done (and by whom)."""
        changed[:] = [entry for entry in changed if entry.get("field") != field]
        changed.append({"field": field, "label": label, "was": was, "now": now, "at": moment,
                        "by": "you" if field in take else "website"})

    for field, value in site.items():
        current = getattr(program, field)
        if _same(field, current, value):
            applied[field] = value      # the same as the website: it follows it from now on
            continue
        if _empty(field, current):
            setattr(program, field, value)
            applied[field] = value
            outcome["filled"].append(FIELDS[field])
            remember(field, FIELDS[field], shown(field, current), shown(field, value))
        elif field in take or (field in applied and _same(field, current, applied[field])):
            setattr(program, field, value)
            applied[field] = value
            outcome["updated"].append(FIELDS[field])
            remember(field, FIELDS[field], shown(field, current), shown(field, value))

    # The weekly plan: written when the program has none, and rewritten when
    # the website changes a plan that still is the one it gave.
    plan_print = hashlib.sha1(json.dumps([details.get("weeks"), details.get("supervision")], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    curriculum = stored["curriculum"] if isinstance(stored["curriculum"], dict) else None
    untouched = curriculum is not None and curriculum.get("written") == _curriculum_print(program)
    if details.get("weeks") and (
        not program.levels or "plan" in take or (untouched and curriculum.get("site") != plan_print)
    ):
        had_plan = bool(program.levels)
        weeks_before = sum(len(level.weeks) for level in program.levels)
        if _write_plan(db, program, details, site):
            remember("plan", "Weekly plan", f"{weeks_before} weeks of your own" if had_plan else "Not set",
                     f"{len(details.get('weeks') or [])} weeks, as on the website")
            curriculum = {"site": plan_print, "written": _curriculum_print(program)}
            outcome["updated" if had_plan else "filled"].append("Weekly plan")

    program.website_slug = item.slug or item.external_id
    program.website_values = json.dumps(
        {"applied": applied, "curriculum": curriculum, "changed": changed[-KEEP_CHANGES:]}, ensure_ascii=False)
    program.website_synced_at = datetime.utcnow()
    item.program_id = program.id
    db.flush()
    return outcome


KEEP_CHANGES = 12


def recent_changes(program: Program, item: WebsiteItem) -> list[dict]:
    """What was changed in the program from the website and still is as the website says:
    done, so there is nothing to click again. Newest first."""
    site = program_values(item.details)
    plan = plan_state(program, item)
    found = []
    for entry in reversed(_stored(program)["changed"]):
        field = entry.get("field")
        still = plan["from_site"] if field == "plan" else (field in site and _same(field, getattr(program, field, None), site[field]))
        if not still:
            continue
        try:
            when = datetime.fromisoformat(entry.get("at") or "")
        except ValueError:
            when = None
        found.append({**entry, "when": when, "long": field == "description"})
    return found


def same_as_website(program: Program, item: WebsiteItem) -> list[str]:
    """The details in which the program says exactly what the website says."""
    return [FIELDS[field] for field, value in program_values(item.details).items() if _same(field, getattr(program, field), value)]


def differences(program: Program, item: WebsiteItem) -> list[dict]:
    """Where the program says something else than the website."""
    found = []
    for field, value in program_values(item.details).items():
        current = getattr(program, field)
        if not _same(field, current, value):
            found.append({
                "field": field, "label": FIELDS[field],
                "yours": shown(field, current), "website": shown(field, value),
                "long": field == "description",
            })
    return found


def plan_state(program: Program, item: WebsiteItem) -> dict:
    """Whether the program's weekly plan is the website's."""
    curriculum = _stored(program)["curriculum"]
    site_weeks = len(item.details.get("weeks") or [])
    yours = sum(len(level.weeks) for level in program.levels)
    from_site = isinstance(curriculum, dict) and curriculum.get("written") == _curriculum_print(program)
    return {"site_weeks": site_weeks, "your_weeks": yours, "from_site": bool(from_site and yours)}


# ------------------------------------------------------------------
# Which program is which formation
# ------------------------------------------------------------------

_GENERIC = {
    "development", "developpement", "stack", "full", "web", "formation", "course", "js", "and", "et",
    "de", "du", "the", "with", "avec", "en", "of",
}
_SAME_WORD = {"ia": "ai", "nodejs": "node", "reactjs": "react", "springboot": "spring", "angularjs": "angular"}


def _words(text: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return {_SAME_WORD.get(word, word) for word in re.findall(r"[a-z0-9]+", plain)}


def likeness(program_name: str, formation_title: str, technologies: list[str] | None = None) -> float:
    """1.0 when a program and a formation are clearly the same thing, else less."""
    left, right = _words(program_name), _words(formation_title)
    if left == right and left:
        return 1.0
    mine = left - _GENERIC
    theirs = (right | _words(" ".join(technologies or []))) - _GENERIC
    if not mine or not theirs:
        return 0.0
    shared = mine & theirs
    if len(shared) < min(2, len(mine)):
        return 0.0
    return round(0.9 * len(shared) / len(mine), 3) if len(mine - theirs) else 0.9


def link_programs(db: Session, items: list[WebsiteItem]) -> dict[str, Program]:
    """The program of each formation: the one already following it, else the
    one with the same name, else the one that clearly teaches the same thing."""
    programs = db.query(Program).options(selectinload(Program.levels).selectinload(ProgramLevel.weeks)).all()
    by_slug = {program.website_slug: program for program in programs if program.website_slug}
    linked: dict[str, Program] = {}
    items = [item for item in items if item.program_id != NO_PROGRAM]
    for item in items:
        program = by_slug.get(item.slug or item.external_id)
        if program is not None:
            linked[item.id] = program
    free_programs = [program for program in programs if program.id not in {found.id for found in linked.values()}]
    pairs = sorted(
        (
            (likeness(program.name, item.title, item.details.get("technologies")), item, program)
            for item in items if item.id not in linked for program in free_programs
        ),
        key=lambda entry: -entry[0],
    )
    used: set[str] = set()
    for score, item, program in pairs:
        if score < 0.9 or item.id in linked or program.id in used:
            continue
        linked[item.id] = program
        used.add(program.id)
    return linked


def _retire_proposal(db: Session, program: Program) -> None:
    """A proposal written for details the website has now filled is no longer needed."""
    from app.services import program_assistant

    if program_assistant.missing(program):
        return
    proposal = program_assistant.open_proposal(db, program.id)
    if proposal is not None:
        proposal.status = "dismissed"
        proposal.decided_at = datetime.now(timezone.utc)


def _new_program(db: Session, item: WebsiteItem) -> Program:
    program = Program(name=item.title, is_approved=True)
    db.add(program)
    db.flush()
    return program


# ------------------------------------------------------------------
# One check of the website
# ------------------------------------------------------------------

def _line(icon: str, text: str, url: str | None = None) -> dict:
    return {"icon": icon, "text": text, "url": url}


def last_check(db: Session) -> WebsiteCheck | None:
    return db.query(WebsiteCheck).order_by(WebsiteCheck.checked_at.desc(), WebsiteCheck.id.desc()).first()


def is_due(db: Session, now: datetime | None = None) -> bool:
    check = last_check(db)
    if check is None:
        return True
    moment = check.checked_at.replace(tzinfo=None) if check.checked_at.tzinfo else check.checked_at
    wait = CHECK_EVERY if check.ok else timedelta(minutes=30)
    return (now or datetime.utcnow()) - moment >= wait


def check(db: Session, by: str = "system") -> WebsiteCheck:
    """Read the website and bring the database up to date. Always returns the
    record of the check; a website that cannot be read changes nothing."""
    now = datetime.utcnow()
    try:
        published = fetch()
    except SiteUnreachable as exc:
        record = WebsiteCheck(checked_at=now, ok=False, note=str(exc), changes="[]", by=by)
        db.add(record)
        db.commit()
        return record

    first_time = db.query(WebsiteItem.id).first() is None
    lines: list[dict] = []
    known = {(item.kind, item.external_id): item for item in db.query(WebsiteItem).all()}
    seen: set[tuple[str, str]] = set()
    formations: list[WebsiteItem] = []

    for kind, raws in published.items():
        for raw in raws:
            fresh = normalise(kind, raw)
            if fresh is None:
                continue
            key = (kind, fresh["external_id"])
            seen.add(key)
            item = known.get(key)
            one = KINDS[kind]["one"]
            if item is None:
                item = WebsiteItem(kind=kind, external_id=fresh["external_id"], first_seen_at=now, title=fresh["title"])
                db.add(item)
                known[key] = item
                if not first_time:
                    lines.append(_line("bi-plus-circle", f"New {one} on the website: {fresh['title']}", "/website#" + kind))
            elif item.fingerprint != fresh["fingerprint"]:
                item.changed_at = now
                lines.append(_line("bi-pencil", f"The website changed the {one} “{fresh['title']}”", "/website#" + kind))
            elif item.gone_at is not None:
                lines.append(_line("bi-arrow-counterclockwise", f"Back on the website: {fresh['title']}", "/website#" + kind))
            item.slug, item.title, item.summary = fresh["slug"], fresh["title"], fresh.get("summary")
            item.category, item.url, item.starts_at = fresh["category"], fresh.get("url"), fresh["starts_at"]
            item.data = json.dumps(fresh["data"], ensure_ascii=False)
            item.fingerprint = fresh["fingerprint"]
            item.last_seen_at, item.gone_at = now, None
            if kind == "formation":
                formations.append(item)

    # A kind the website sent nothing for is left alone: an empty answer is
    # more likely a problem over there than everything being removed at once.
    for (kind, external_id), item in known.items():
        if (kind, external_id) not in seen and item.gone_at is None and published.get(kind):
            item.gone_at = now
            lines.append(_line("bi-dash-circle", f"No longer on the website: {item.title} ({KINDS[kind]['one']})", "/website#" + kind))
    db.flush()

    linked = link_programs(db, formations)
    for item in formations:
        if item.program_id == NO_PROGRAM:
            continue
        program = linked.get(item.id)
        created = program is None
        if created:
            program = _new_program(db, item)
            db.add(Notification(
                category="WEBSITE", recipient_role="admin", program_id=program.id,
                message=f"New program from the website: {program.name}. Its details and weekly plan were taken from 9antra.tn.",
            ))
        newly_linked = not program.website_slug
        outcome = apply_to_program(db, program, item)
        if outcome["filled"]:
            _retire_proposal(db, program)
        url = f"/programs/{program.id}"
        if created:
            lines.append(_line("bi-stars", f"Program created from the website: {program.name}", url))
        elif newly_linked:
            what = ", ".join(outcome["filled"]).lower()
            lines.append(_line(
                "bi-link-45deg",
                f"{program.name} now follows the formation “{item.title}”" + (f": {what} filled in" if what else ""), url,
            ))
        else:
            for label in outcome["filled"]:
                lines.append(_line("bi-magic", f"{program.name}: {label.lower()} filled from the website", url))
            for label in outcome["updated"]:
                lines.append(_line("bi-arrow-repeat", f"{program.name}: {label.lower()} updated from the website", url))

    if first_time:
        counted = {kind: sum(1 for key in seen if key[0] == kind) for kind in KINDS}
        lines.insert(0, _line("bi-cloud-download", "First reading of the website: " + ", ".join(
            f"{count} {KINDS[kind]['label'].lower()}" for kind, count in counted.items() if count
        ), "/website"))
    record = WebsiteCheck(checked_at=now, ok=True, changes=json.dumps(lines, ensure_ascii=False), by=by)
    db.add(record)
    old = db.query(WebsiteCheck).order_by(WebsiteCheck.checked_at.desc(), WebsiteCheck.id.desc()).offset(KEEP_CHECKS).all()
    for stale in old:
        db.delete(stale)
    db.commit()
    return record


def kept(db: Session) -> list[WebsiteItem]:
    """What was taken from the website and is no longer there. It stays recorded here until the admin removes it."""
    return (db.query(WebsiteItem).filter(WebsiteItem.gone_at.isnot(None), WebsiteItem.removed_at.is_(None))
            .order_by(WebsiteItem.gone_at.desc()).all())


def remove(db: Session, item: WebsiteItem) -> None:
    """The admin removes a record the website no longer has. It is the only way one leaves."""
    item.removed_at = datetime.utcnow()
    db.commit()


def check_if_due(db: Session) -> WebsiteCheck | None:
    """The reading that happens by itself, every few hours."""
    return check(db) if is_due(db) else None


# ------------------------------------------------------------------
# For the pages
# ------------------------------------------------------------------

def formation_of(db: Session, program: Program) -> WebsiteItem | None:
    if not program.website_slug:
        return None
    return db.query(WebsiteItem).filter(
        WebsiteItem.kind == "formation", WebsiteItem.slug == program.website_slug, WebsiteItem.gone_at.is_(None),
    ).first()


def coming_events(db: Session, limit: int = 4, now: datetime | None = None) -> list[WebsiteItem]:
    now = now or datetime.utcnow()
    return (
        db.query(WebsiteItem)
        .filter(WebsiteItem.kind == "event", WebsiteItem.gone_at.is_(None), WebsiteItem.starts_at >= now - timedelta(hours=3))
        .order_by(WebsiteItem.starts_at.asc())
        .limit(limit)
        .all()
    )


def overview(db: Session) -> dict:
    """Everything the Website page shows."""
    items = db.query(WebsiteItem).filter(WebsiteItem.gone_at.is_(None)).order_by(WebsiteItem.title.asc()).all()
    programs = {
        program.id: program
        for program in db.query(Program).options(selectinload(Program.levels).selectinload(ProgramLevel.weeks)).all()
    }
    now = datetime.utcnow()
    formations = []
    for item in (entry for entry in items if entry.kind == "formation"):
        program = programs.get(item.program_id)
        details = item.details
        formations.append({
            "ignored": item.program_id == NO_PROGRAM,
            "item": item, "details": details, "program": program,
            "differences": differences(program, item) if program else [],
            "changed": recent_changes(program, item) if program else [],
            "same": same_as_website(program, item) if program else [],
            "plan": plan_state(program, item) if program else None,
            "slots": slots_of(details.get("schedule")),
        })
    events = sorted((entry for entry in items if entry.kind == "event"), key=lambda entry: entry.starts_at or now)
    news = sorted((entry for entry in items if entry.kind == "news"), key=lambda entry: entry.starts_at or datetime.min, reverse=True)
    followed = {entry["program"].id for entry in formations if entry["program"]}
    checks = db.query(WebsiteCheck).order_by(WebsiteCheck.checked_at.desc(), WebsiteCheck.id.desc()).limit(8).all()
    return {
        "site": SITE,
        "formations": formations,
        "events_coming": [entry for entry in events if entry.starts_at and entry.starts_at >= now - timedelta(hours=3)],
        "events_past": [entry for entry in events if not entry.starts_at or entry.starts_at < now - timedelta(hours=3)][::-1],
        "offers": [entry for entry in items if entry.kind == "offer"],
        "news": news,
        "videos": [entry for entry in items if entry.kind == "video"],
        "feedback": sorted((entry for entry in items if entry.kind == "feedback"), key=lambda entry: (entry.details.get("order") or 0, entry.title)),
        "posts": sorted((entry for entry in items if entry.kind == "post"), key=lambda entry: entry.starts_at or datetime.min, reverse=True),
        "facebook": facebook_connected(),
        "all_programs": sorted(programs.values(), key=lambda program: program.name or ""),
        "own_programs": sorted((program for program in programs.values() if program.id not in followed), key=lambda program: program.name or ""),
        "checks": checks,
        "last": checks[0] if checks else None,
        "to_decide": sum(len(entry["differences"]) for entry in formations),
        "counts": {kind: sum(1 for entry in items if entry.kind == kind) for kind in KINDS},
    }


def week_news(db: Session, now: datetime | None = None, days: int = 7) -> dict:
    """What appeared on the website (and the Facebook page) these last days,
    for the dashboard: per kind, how many and the first names."""
    now = now or datetime.utcnow()
    since = now - timedelta(days=days)
    items = (
        db.query(WebsiteItem)
        .filter(WebsiteItem.gone_at.is_(None), WebsiteItem.first_seen_at >= since)
        .order_by(WebsiteItem.first_seen_at.desc())
        .all()
    )
    changed = (
        db.query(WebsiteItem)
        .filter(WebsiteItem.gone_at.is_(None), WebsiteItem.changed_at >= since, WebsiteItem.first_seen_at < since)
        .count()
    )
    lines = []
    for kind, info in KINDS.items():
        mine = [item for item in items if item.kind == kind]
        if not mine:
            continue
        names = [item.read.get("name") or item.title for item in mine] if kind == "feedback" else [item.title for item in mine]
        lines.append({
            "kind": kind, "icon": info["icon"], "label": info["label"], "one": info["one"], "count": len(mine),
            "names": [name for name in names if name][:3], "url": f"/website#{kind}",
        })
    last = db.query(WebsiteCheck).order_by(WebsiteCheck.checked_at.desc(), WebsiteCheck.id.desc()).first()
    return {"lines": lines, "total": len(items), "changed": changed, "last": last, "days": days}


def knowledge_chunks(db: Session, now: datetime | None = None) -> list[dict]:
    """What the website (and the Facebook page) add to what the email assistant
    knows, in French, one piece per subject."""
    items = db.query(WebsiteItem).filter(WebsiteItem.gone_at.is_(None)).all()
    chunks = []
    for item in (entry for entry in items if entry.kind == "formation"):
        details = item.details
        price = details.get("price") or {}
        lines = [f"Formation « {item.title} » telle qu'elle est publiée sur le site 9antra.tn ({item.url})."]
        for label, value in (
            ("Niveau (level)", details.get("level")), ("Mode", details.get("mode")),
            ("Durée (duration)", details.get("duration")), ("Horaire (schedule)", details.get("schedule")),
            ("Technologies", ", ".join(details.get("technologies") or [])),
        ):
            if value:
                lines.append(f"{label} : {value}.")
        prices = [f"{price[name]} {label}" for name, label in (("onsite", "en présentiel"), ("online", "en ligne"), ("recordings", "pour les enregistrements seuls")) if price.get(name)]
        if prices:
            lines.append("Prix (price) : " + ", ".join(prices) + ".")
        if details.get("certificate"):
            lines.append("Un certificat est délivré à la fin de la formation (certificate).")
        chunks.append({
            "source_key": f"website:formation:{item.external_id}", "kind": "program",
            "program_id": item.program_id if item.program_id and item.program_id != NO_PROGRAM else None,
            "title": f"{item.title} — fiche publiée sur 9antra.tn", "content": "\n".join(lines),
        })
        # The course itself, week by week, with its videos: what a candidate asks about most.
        weeks = [week for week in details.get("weeks") or [] if isinstance(week, dict)]
        support = [week for week in details.get("supervision") or [] if isinstance(week, dict)]
        if weeks or support or details.get("description"):
            plan = [f"Contenu de la formation « {item.title} » : les cours semaine par semaine (programme, course content, weeks, videos) publiés sur 9antra.tn."]
            if details.get("description"):
                plan.append(f"Présentation : {details['description']}")
            for label, rows in (("Cours", weeks), ("Encadrement", support)):
                for week in rows:
                    line = f"{label}, semaine {week.get('week')}" + (f" ({week['phase']})" if week.get("phase") else "") + f" : {_text(week.get('content'), 500)}"
                    if week.get("video_title"):
                        line += (f" Vidéo : « {_text(week['video_title'], 160)} »" + (f" ({week['video_length']})" if week.get("video_length") else "")
                                 + (", en accès libre" if week.get("free") else "") + ".")
                    plan.append(line)
            chunks.append({
                "source_key": f"website:formation-content:{item.external_id}", "kind": "program",
                "program_id": item.program_id if item.program_id and item.program_id != NO_PROGRAM else None,
                "title": f"{item.title} — contenu des cours semaine par semaine (9antra.tn)", "content": "\n".join(plan)[:7000],
            })

    def one(key: str, title: str, lines: list[str]) -> None:
        if lines:
            chunks.append({"source_key": f"website:{key}", "kind": "process", "program_id": None, "title": title, "content": "\n".join(lines)})

    events = []
    for item in coming_events(db, limit=10, now=now):
        local = item.starts_at + timedelta(hours=1)     # Tunis is UTC+1 all year
        mode = {"online": "en ligne", "onsite": "en présentiel"}.get(item.details.get("mode"), item.details.get("mode") or "")
        events.append(f"« {item.title} » : le {local:%d/%m/%Y} à {local:%H:%M}" + (f", {mode}" if mode else "") + f". Inscription : {item.url}")
    one("events", "Événements et workshops à venir (9antra.tn)", ["Événements à venir (upcoming events, workshops) annoncés sur 9antra.tn :"] + events if events else [])
    offers = [
        f"« {item.title} » ({item.details.get('type') or 'stage'}, {item.category or 'domaine non précisé'}) : durée {item.details.get('duration') or 'non précisée'}, "
        f"{item.details.get('location') or 'lieu non précisé'}. Détails : {item.url}"
        for item in items if item.kind == "offer" and item.details.get("active", True)
    ]
    one("offers", "Offres de stage et de PFE (9antra.tn)", ["Offres de stage et de PFE (internship offers) publiées sur 9antra.tn :"] + offers if offers else [])
    news = sorted((item for item in items if item.kind == "news"), key=lambda item: item.starts_at or datetime.min, reverse=True)[:6]
    one("news", "Actualités de 9antra", ["Actualités récentes (news) publiées sur 9antra.tn :"] + [f"« {item.title} » : {item.summary or ''}" for item in news] if news else [])
    posts = sorted((item for item in items if item.kind == "post"), key=lambda item: item.starts_at or datetime.min, reverse=True)[:10]
    one("facebook", "Dernières publications de la page Facebook", ["Dernières publications de la page Facebook de 9antra :"] + [f"Le {item.starts_at:%d/%m/%Y} : {(item.summary or item.title)[:600]}" for item in posts if item.starts_at] if posts else [])
    # What former participants say, once their published feedback has been read (see feedback).
    from app.services import feedback

    one("feedback", "Avis et témoignages d'anciens participants", feedback.knowledge_lines(db))
    return chunks

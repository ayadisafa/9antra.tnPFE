"""The first page's stories: what happened lately, told like an advert you want to open, and the days in a row.

A row of round stories opens the dashboard (new people, what they said, the money in, the sessions coming, the news of
the fields, the successes). Each one opens full screen and plays its slides by itself: a big number that counts up, a
photo that moves, one sentence, one button to the place. A story whose content changed since it was last opened has
its colour back (the browser remembers what it saw), so there is always something new to open. Everything is read
from the records, never invented: a story with nothing in it is not shown.

The days in a row: each day the team opens the dashboard is kept (system_settings.active_days); the streak is how many
days in a row, and the ring is what was done today (answers, messages, registrations, payments, reviews) against a
small goal.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.services.ui_words import t as _t

logger = logging.getLogger(__name__)

KEEP_DAYS = 90
DAY_GOAL = 10                   # things done in a day for a full ring


def _sig(slides: list[dict]) -> str:
    return hashlib.sha1(json.dumps(slides, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]


def _story(key: str, title: str, icon: str, colour: str, slides: list[dict]) -> dict | None:
    if not slides:
        return None
    return {"key": key, "title": title, "icon": icon, "colour": colour, "slides": slides, "sig": _sig(slides),
            "image": next((slide["image"] for slide in slides if slide.get("image")), None)}


def _first(name: str | None) -> str:
    return ((name or "").split() or [""])[0].capitalize()


def _today(db: Session) -> list[dict]:
    from app.services import advisor, overview

    figures = overview.figures(db, datetime.utcnow())
    advice = advisor.for_dashboard(db)
    slides = []
    if figures["emails_waiting"]:
        slides.append({"kicker": _t("Waiting for you"), "big": figures["emails_waiting"], "icon": "bi-envelope-exclamation",
                       "text": _t("people wait for your answer. My draft is ready for each."), "url": "/inbox?filter=todo", "cta": _t("Answer them")})
    if advice:
        slides.append({"kicker": _t("To do today"), "big": len(advice), "icon": "bi-list-check",
                       "text": _t("The first one: {0}", advice[0].title), "url": "/#live-dashboard-advice", "cta": _t("See them")})
    return slides


def _people(db: Session) -> list[dict]:
    from app.models.registration import Registration

    since = datetime.utcnow() - timedelta(days=7)
    rows = (db.query(Registration).filter(Registration.registration_date > since)
            .order_by(Registration.registration_date.desc()).limit(30).all())
    if not rows:
        return []
    slides = [{"kicker": _t("This week"), "big": len(rows), "icon": "bi-person-plus-fill",
               "text": _t("new registrations in seven days."), "url": "/candidate/all", "cta": _t("Meet them")}]
    for row in rows[:4]:
        name = _first(row.candidate.full_name if row.candidate else "")
        if name:
            slides.append({"kicker": _t("Welcome"), "big": name, "icon": "bi-stars",
                           "text": _t("joined {0}.", row.program_name or _t("a program")), "url": f"/candidate/{row.candidate_id}", "cta": _t("Open")})
    return slides


def _reviews(db: Session) -> list[dict]:
    from app.models.feedback import CandidateFeedback
    from app.services import program_feedback

    since = datetime.utcnow() - timedelta(days=30)
    rows = (db.query(CandidateFeedback).filter(CandidateFeedback.source == program_feedback.SOURCE, CandidateFeedback.status == "received",
                                                CandidateFeedback.submitted_at > since)
            .order_by(CandidateFeedback.submitted_at.desc()).limit(12).all())
    if not rows:
        return []
    found = program_feedback.rating(db)
    slides = []
    if found["stars"] is not None:
        slides.append({"kicker": _t("What they think"), "big": found["stars"], "suffix": "/5", "icon": "bi-star-fill",
                       "text": _t("from 1 candidate.") if found["count"] == 1 else _t("from {0} candidates.", found["count"]), "url": "/website#feedback", "cta": _t("Read them")})
    for row in rows:
        if row.comment and (row.rating or 0) >= 4 and len(slides) < 5:
            slides.append({"kicker": "★" * (row.rating or 0), "quote": row.comment[:180], "icon": "bi-chat-quote-fill",
                           "text": "— " + (_first(row.author_name) or _t("A candidate")), "url": f"/candidate/{row.candidate_id}" if row.candidate_id else "/website#feedback",
                           "cta": _t("Open")})
    low = sum(1 for row in rows if program_feedback.is_low(row) and not program_feedback.was_contacted(db, row))
    if low:
        slides.append({"kicker": _t("To call"), "big": low, "icon": "bi-telephone-fill",
                       "text": _t("not fully happy, waiting for a call from the team."), "url": "/website#feedback", "cta": _t("See who")})
    return slides


def _money(db: Session) -> list[dict]:
    from sqlalchemy import func

    from app.models.payment import Payment

    since = datetime.utcnow() - timedelta(days=7)
    total, count = db.query(func.coalesce(func.sum(Payment.amount), 0), func.count(Payment.id)).filter(Payment.created_at > since).one()
    if not count:
        return []
    return [{"kicker": _t("Money in"), "big": round(float(total)), "suffix": " DT", "icon": "bi-cash-coin",
             "text": _t("received in seven days, in {0} payments.", count), "url": "/payments", "cta": _t("See the payments")}]


def _sessions(db: Session) -> list[dict]:
    from app.services import overview
    from app.services.schedule_utils import local_now

    now = local_now()
    slides = []
    for item in overview.coming_sessions(db, now, days=3, limit=3):
        when = item["date"].strftime("%d/%m") + (f" · {item['start_time'].strftime('%H:%M')}" if item.get("start_time") else "")
        slides.append({"kicker": _t("Today") if item["date"] == now.date() else when, "big": item["class"].name, "icon": "bi-calendar-event",
                       "text": item.get("topic") or (item["class"].program.name if item["class"].program else ""),
                       "url": f"/classes/{item['class'].id}", "cta": _t("Open the class")})
    return slides


def _news(db: Session) -> list[dict]:
    from app.models.news_item import NewsItem
    from app.services import journal

    rows = db.query(NewsItem).filter(NewsItem.image_url.isnot(None)).order_by(NewsItem.published_at.desc()).limit(4).all()
    return [{"kicker": journal.DOMAINS.get(row.domain, ("News", ""))[0], "title": row.title, "image": row.image_url, "icon": "bi-newspaper",
             "text": row.source, "url": row.url, "cta": _t("Read it"), "outside": True} for row in rows]


def _wins(db: Session) -> list[dict]:
    from app.services import program_feedback

    return [{"kicker": _t("A success"), "big": item["first_name"], "icon": "bi-trophy-fill",
             "text": f"{item['label']}{' (' + item['words'] + ')' if item['words'] else ''} · {item['program']}",
             "url": "/social#create", "cta": _t("Tell it in a post")} for item in program_feedback.successes(db, 4)]


def for_dashboard(db: Session) -> list[dict]:
    """The stories that have something in them, the most pressing first."""
    found = []
    for key, title, icon, colour, read in (
        ("today", "Today", "bi-sun", "#a42c67", _today), ("people", "New people", "bi-person-plus", "#7c3aed", _people),
        ("reviews", "Reviews", "bi-star", "#f59e0b", _reviews), ("money", "Money in", "bi-cash-coin", "#15803d", _money),
        ("sessions", "Sessions", "bi-calendar-event", "#0ea5e9", _sessions), ("news", "News", "bi-newspaper", "#e11d48", _news),
        ("wins", "Successes", "bi-trophy", "#d97706", _wins),
    ):
        try:
            story = _story(key, _t(title), icon, colour, read(db))
        except Exception:
            logger.exception("The %s story could not be read.", key)
            story = None
        if story:
            found.append(story)
    return found


# ------------------------------------------------------------------
# Days in a row
# ------------------------------------------------------------------

def _days(settings) -> list[str]:
    try:
        days = json.loads(getattr(settings, "active_days", None) or "[]")
    except ValueError:
        return []
    return [day for day in days if isinstance(day, str)]


def run_of(days: list[str], today: date) -> int:
    """How many days in a row end today (or yesterday, when today is not counted yet)."""
    have = set(days)
    day = today if today.isoformat() in have else today - timedelta(days=1)
    count = 0
    while day.isoformat() in have:
        count += 1
        day -= timedelta(days=1)
    return count


def mark_today(db: Session) -> dict:
    """The team opened the dashboard today: kept. {"days", "new_today", "done", "goal", "pct", "best"}."""
    from app.services import digest
    from app.services.schedule_utils import _tz, local_now
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    now = local_now()
    today = now.date().isoformat()
    days = _days(settings)
    new_today = today not in days
    if new_today:
        days = sorted(set(days + [today]))[-KEEP_DAYS:]
        settings.active_days = json.dumps(days)
        db.commit()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=_tz())
    counted = digest.counts(db, start.astimezone(timezone.utc).replace(tzinfo=None))
    done = (counted["went"]["answered"] + counted["went"]["sent"] + counted["went"]["by_team"] + counted["came"]["registrations"]
            + counted["came"]["payments"] + counted["came"]["reviews"])
    best, run, previous = 0, 0, None
    for day in sorted(days):
        current = date.fromisoformat(day)
        run = run + 1 if previous is not None and current - previous == timedelta(days=1) else 1
        best, previous = max(best, run), current
    return {"days": run_of(days, now.date()), "new_today": new_today, "done": done, "goal": DAY_GOAL,
            "pct": min(100, round(100 * done / DAY_GOAL)), "best": best, "today": today}

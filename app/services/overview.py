"""The figures and short lists of the dashboard, read from the records."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, joinedload

from app.models.activity import Activity
from app.models.candidate import Candidate
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.email_thread import EmailThread
from app.models.program import Program
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, Registration, RegistrationStatus
from app.services import program_icons, weekly_questions
from app.services.class_views import class_summary
from app.services.schedule_utils import class_start_dt, utc_to_local

_RECORD_ID = re.compile(r"_[0-9a-f]{8}-[0-9a-f-]{27}$")

# How a recorded event reads on the dashboard: (label, icon, tone).
_EVENTS = {
    "REGISTRATION": ("Registration", "bi-person-plus", "brand"),
    "PAYMENT_CONFIRMED": ("Payment", "bi-cash-coin", "ok"),
    "PAYMENT_RECORDED": ("Payment", "bi-cash-coin", "ok"),
    "PAYMENT_REMINDER_SENT": ("Payment reminder", "bi-bell", "warn"),
    "PAYMENT_RELANCE_SENT": ("Payment reminder", "bi-bell", "warn"),
    "REMINDER_SENT": ("Reminder", "bi-bell", "warn"),
    "CLASS_ASSIGNED": ("Class", "bi-mortarboard", "info"),
    "CLASS_CHANGED": ("Class", "bi-mortarboard", "info"),
    "WEEKLY_QUESTIONS_ANSWERED": ("Weekly questions", "bi-patch-check", "ok"),
    "CUSTOM_EMAIL_SENT": ("Email", "bi-envelope", "info"),
    "Course Approved": ("Program approved", "bi-check2-circle", "ok"),
}


def _active(registrations) -> list[Registration]:
    return [item for item in registrations if item.status != RegistrationStatus.CANCELLED]


def figures(db: Session, now_utc: datetime) -> dict:
    """The numbers at the top of the dashboard."""
    registrations = _active(db.query(Registration).all())
    total = sum((item.total_amount or item.total_price or 0) for item in registrations)
    paid = sum(item.paid_amount or 0 for item in registrations)
    owing = {item.candidate_id for item in registrations if item.remaining_amount > 0}
    week_ago = now_utc - timedelta(days=7)
    new_candidates = {
        item.candidate_id for item in registrations
        if item.registration_date and item.registration_date.replace(tzinfo=None) >= week_ago
    }
    classes = db.query(ClassGroup).all()
    scores = [
        quiz.score for quiz in db.query(Quiz).filter(Quiz.kind == "weekly", Quiz.completed_at.isnot(None)).all()
    ]
    understanding = round(sum(scores) / len(scores), 1) if scores else None
    return {
        "candidates": db.query(Candidate).count(),
        "new_candidates": len(new_candidates),
        "registrations": len(registrations),
        "paid_registrations": sum(1 for item in registrations if item.payment_status == PaymentStatus.PAID),
        "collected": paid,
        "due": max(0.0, total - paid),
        "collection_rate": round(paid / total * 100) if total else 0,
        "owing_candidates": len(owing),
        "classes_running": sum(1 for item in classes if item.status == ClassStatus.ONGOING),
        "classes_upcoming": sum(1 for item in classes if item.status == ClassStatus.NOT_STARTED),
        "emails_waiting": db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).count(),
        "understanding": understanding,
        "understanding_level": weekly_questions.level_of(understanding),
        "answers": len(scores),
    }


def coming_sessions(db: Session, now_local: datetime, days: int = 7, limit: int = 7) -> list[dict]:
    """Sessions of the next days, soonest first."""
    today, last = now_local.date(), now_local.date() + timedelta(days=days)
    classes = (
        db.query(ClassGroup)
        .options(joinedload(ClassGroup.sessions), joinedload(ClassGroup.registrations), joinedload(ClassGroup.program))
        .filter(ClassGroup.status != ClassStatus.COMPLETED)
        .all()
    )
    found = []
    for class_group in classes:
        members = len(_active(class_group.registrations))
        planned = [session for session in class_group.sessions if today <= session.session_date <= last]
        if planned:
            for session in planned:
                found.append({
                    "date": session.session_date,
                    "start_time": session.start_time or class_group.start_time,
                    "end_time": session.end_time or class_group.end_time,
                    "topic": session.topic, "week": session.week_number,
                    "class": class_group, "members": members,
                })
        elif not class_group.sessions and class_start_dt(class_group):
            # No weekly plan: the class still meets every week from its first day.
            first = class_start_dt(class_group).date()
            for index in range(class_group.total_weeks or 12):
                day = first + timedelta(weeks=index)
                if today <= day <= last:
                    found.append({
                        "date": day, "start_time": class_group.start_time, "end_time": class_group.end_time,
                        "topic": None, "week": index + 1, "class": class_group, "members": members,
                    })
    found.sort(key=lambda item: (item["date"], str(item["start_time"] or "")))
    for item in found:
        item["is_today"] = item["date"] == today
        item["program_icon"] = program_icons.view(item["class"].program) if item["class"].program else None
    return found[:limit]


def classes_in_progress(db: Session, now_local: datetime, limit: int = 6) -> list[dict]:
    """Running classes first, then the next to start, each with where it stands."""
    classes = (
        db.query(ClassGroup)
        .options(joinedload(ClassGroup.registrations), joinedload(ClassGroup.sessions), joinedload(ClassGroup.program))
        .filter(ClassGroup.status != ClassStatus.COMPLETED)
        .all()
    )
    order = {ClassStatus.ONGOING: 0, ClassStatus.NOT_STARTED: 1}
    classes.sort(key=lambda item: (order.get(item.status, 2), class_start_dt(item) or datetime.max, item.name))
    rows = []
    for class_group in classes[:limit]:
        summary = class_summary(class_group, now_local)
        rows.append({
            "class": class_group,
            "summary": summary,
            "progress": round(summary["current_week"] / summary["total_weeks"] * 100) if summary["total_weeks"] else 0,
            "course": weekly_questions.class_overview(db, class_group),
            "program_icon": program_icons.view(class_group.program) if class_group.program else None,
        })
    return rows


def newest_registrations(db: Session, limit: int = 6) -> list[dict]:
    rows = (
        db.query(Registration)
        .options(joinedload(Registration.candidate), joinedload(Registration.program))
        .filter(Registration.status != RegistrationStatus.CANCELLED)
        .order_by(Registration.registration_date.desc())
        .limit(limit * 4)
        .all()
    )
    found, seen = [], set()
    for registration in rows:
        candidate = registration.candidate
        if candidate is None or candidate.id in seen:
            continue
        seen.add(candidate.id)
        mine = [item for item in rows if item.candidate_id == candidate.id]
        found.append({
            "candidate": candidate,
            "name": candidate.full_name or candidate.email,
            "programs": list(dict.fromkeys(
                item.program.name if item.program else (item.program_name or "Program") for item in mine
            )),
            "when": utc_to_local(registration.registration_date),
            "owes": sum(item.remaining_amount for item in _active(candidate.registrations)),
        })
        if len(found) >= limit:
            break
    return found


def popular_programs(db: Session, limit: int = 6) -> list[dict]:
    counts: dict[str, int] = {}
    for (program_id,) in db.query(Registration.program_id).filter(
        Registration.program_id.isnot(None), Registration.status != RegistrationStatus.CANCELLED,
    ).all():
        counts[program_id] = counts.get(program_id, 0) + 1
    programs = db.query(Program).all()
    programs.sort(key=lambda program: (-counts.get(program.id, 0), program.name or ""))
    return [
        {"program": program, "icon": program_icons.view(program), "registrations": counts.get(program.id, 0)}
        for program in programs[:limit]
    ]


# What happened, by part of the work: (label, icon, tone, where to see it, the recorded events it counts).
_PARTS = [
    ("New registrations", "bi-person-plus-fill", "brand", "/candidate/all", ("REGISTRATION",)),
    ("Payments received", "bi-cash-coin", "ok", "/payments", ("PAYMENT_CONFIRMED", "PAYMENT_RECORDED")),
    ("Reminders sent", "bi-bell-fill", "warn", "/payments",
     ("PAYMENT_REMINDER_SENT", "PAYMENT_RELANCE_SENT", "REMINDER_SENT", "WHATSAPP_SENT")),
    ("Placed in a class", "bi-mortarboard-fill", "info", "/classes/", ("CLASS_ASSIGNED", "CLASS_CHANGED")),
    ("Weekly answers", "bi-patch-check-fill", "ok", "/classes/", ("WEEKLY_QUESTIONS_ANSWERED",)),
    ("Emails sent", "bi-envelope-check-fill", "info", "/inbox?filter=sent", ("_EMAIL_SENT",)),
]


def activity_parts(db: Session, now_utc: datetime, days: int = 7) -> list[dict]:
    """The last days in a few figures, one per part of the work, instead of
    one line per recorded event. The same event recorded twice counts once."""
    rows = (
        db.query(Activity.candidate_id, Activity.event_type, Activity.details, Activity.created_at)
        .filter(Activity.created_at >= now_utc - timedelta(days=days))
        .all()
    )
    seen: dict[tuple, datetime] = {}
    for candidate_id, event_type, details, created_at in rows:
        key = (candidate_id, _RECORD_ID.sub("", event_type or ""), details)
        if key not in seen or (created_at and created_at > seen[key]):
            seen[key] = created_at
    parts = []
    for label, icon, tone, url, kinds in _PARTS:
        mine = [when for (_, kind, _), when in seen.items() if any(kind == name or kind.endswith(name) for name in kinds)]
        parts.append({
            "label": label, "icon": icon, "tone": tone, "url": url, "count": len(mine),
            "last": utc_to_local(max(mine)) if mine and all(mine) else None,
        })
    return parts


def activity_feed(db: Session, limit: int = 8) -> list[dict]:
    """What happened lately. The same event recorded several times shows once."""
    rows = (
        db.query(Activity)
        .options(joinedload(Activity.candidate))
        .order_by(Activity.created_at.desc())
        .limit(limit * 25)
        .all()
    )
    feed, seen = [], {}
    for row in rows:
        kind = _RECORD_ID.sub("", row.event_type or "")
        key = (row.candidate_id, kind, row.details)
        if key in seen:
            seen[key]["times"] += 1
            continue
        if len(feed) >= limit:
            continue
        label, icon, tone = _EVENTS.get(kind, (kind.replace("_", " ").capitalize() or "Event", "bi-dot", "muted"))
        if kind.endswith("_EMAIL_SENT"):
            icon, tone = "bi-envelope-check", "info"
        elif kind.endswith("_EMAIL_FAILED"):
            icon, tone = "bi-envelope-x", "warn"
        entry = {
            "label": label, "icon": icon, "tone": tone,
            "details": row.details or "",
            "candidate": row.candidate,
            "when": utc_to_local(row.created_at),
            "times": 1,
        }
        seen[key] = entry
        feed.append(entry)
    return feed

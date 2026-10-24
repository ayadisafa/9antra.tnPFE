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
        active = _active(candidate.registrations)
        total = sum(float(item.total_amount or item.total_price or 0) for item in active)
        owes = sum(item.remaining_amount for item in active)
        found.append({
            "total": total, "paid_pct": round(100 * (total - owes) / total) if total else 0,
            "feedback": candidate_feedback(db, candidate),
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
    from app.models.classgroup import ClassGroup, ClassStatus
    from app.services import program_feedback

    top = max([counts.get(program.id, 0) for program in programs[:limit]] or [0]) or 1
    found = []
    for program in programs[:limit]:
        paid = (db.query(Registration).filter(Registration.program_id == program.id, Registration.status != RegistrationStatus.CANCELLED,
                                              Registration.payment_status == PaymentStatus.PAID).count())
        running = db.query(ClassGroup).filter(ClassGroup.program_id == program.id, ClassGroup.status == ClassStatus.ONGOING).count()
        reviews = program_feedback.of_program(db, program.id)
        found.append({"program": program, "icon": program_icons.view(program), "registrations": counts.get(program.id, 0),
                      "share": round(100 * counts.get(program.id, 0) / top), "paid": paid, "running": running,
                      "feedback": {"stars": reviews["stars"], "answered": reviews["answered"], "happy_pct": reviews["happy_pct"],
                                   "recommend_pct": reviews["recommend_pct"], "asked": reviews["asked"]}})
    return found


def candidate_feedback(db: Session, candidate) -> dict | None:
    """What this candidate said: their review at the end of a program, else the feeling of their last messages; None when nothing."""
    from app.models.feedback import CandidateFeedback
    from app.services import program_feedback

    row = (db.query(CandidateFeedback).filter(CandidateFeedback.candidate_id == candidate.id, CandidateFeedback.source == program_feedback.SOURCE,
                                              CandidateFeedback.status == "received").order_by(CandidateFeedback.submitted_at.desc()).first())
    if row is not None:
        return {"kind": "review", "stars": row.rating, "words": (row.comment or "")[:120]}
    try:
        from app.services import candidate_insights

        feelings = candidate_insights.feelings_of(db, candidate)
    except Exception:
        feelings = None
    top = (feelings or {}).get("emotions") or []
    if top:
        return {"kind": "feeling", "key": top[0]["key"], "label": top[0]["label"], "count": top[0]["count"]}
    return None


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
    today = utc_to_local(now_utc).date()
    for label, icon, tone, url, kinds in _PARTS:
        mine = [when for (_, kind, _), when in seen.items() if any(kind == name or kind.endswith(name) for name in kinds)]
        per_day = [0] * days                     # oldest first, today last
        for when in mine:
            if when:
                back = (today - utc_to_local(when).date()).days
                if 0 <= back < days:
                    per_day[days - 1 - back] += 1
        parts.append({
            "label": label, "icon": icon, "tone": tone, "url": url, "count": len(mine), "days": per_day,
            "last": utc_to_local(max(mine)) if mine and all(mine) else None,
        })
    return parts


_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_DAY_KEYS = {key: index for index, keys in enumerate((
    ("mon", "monday", "lun", "lundi"), ("tue", "tuesday", "mar", "mardi"), ("wed", "wednesday", "mer", "mercredi"),
    ("thu", "thursday", "jeu", "jeudi"), ("fri", "friday", "ven", "vendredi"), ("sat", "saturday", "sam", "samedi"),
    ("sun", "sunday", "dim", "dimanche"))) for key in keys}


def _hue(name: str) -> int:
    """The colour of a program on the week, the same one every time (as the class pages draw it)."""
    value = 0
    for char in name or "":
        value = (value * 31 + ord(char)) % 360
    return value


def _initials(name: str) -> str:
    parts = (name or "?").split()
    return ("".join(part[0] for part in parts)[:2] or "?").upper()


def _lanes(spans: list[dict]) -> int:
    """Spans of time that overlap go on separate lanes, so none hides another. Sets "lane" on each; returns how many."""
    ends: list[int] = []
    for span in spans:
        lane = next((index for index, end in enumerate(ends) if end <= span["start"]), None)
        if lane is None:
            lane = len(ends)
            ends.append(0)
        ends[lane] = span["end"]
        span["lane"] = lane
    return max(1, len(ends))


def week_board(db: Session, now_local: datetime) -> dict:
    """Every class that is not finished, once, at its place in this week: the days it meets, its hours (said once for
    the classes that share them), who teaches it, how full and how far along it is, what its candidates understood,
    and the professors booked twice. The dashboard's one view of the classes and their timetable."""
    classes = (
        db.query(ClassGroup)
        .options(joinedload(ClassGroup.registrations), joinedload(ClassGroup.sessions), joinedload(ClassGroup.program))
        .filter(ClassGroup.status != ClassStatus.COMPLETED)
        .all()
    )
    order = {ClassStatus.ONGOING: 0, ClassStatus.NOT_STARTED: 1}
    classes.sort(key=lambda item: (order.get(item.status, 2), item.start_time or datetime.max.time(), item.name or ""))
    minutes = lambda value: value.hour * 60 + value.minute        # noqa: E731
    cards = []
    for class_group in classes:
        summary = class_summary(class_group, now_local)
        days = sorted({_DAY_KEYS[key] for key in (part.strip().lower() for part in (class_group.days_of_week or "").split(","))
                       if key in _DAY_KEYS})
        start = minutes(class_group.start_time) if class_group.start_time else None
        end = minutes(class_group.end_time) if class_group.end_time else None
        if start is not None and (end is None or end <= start):
            end = start + 60
        running = class_group.status == ClassStatus.ONGOING
        week, weeks = summary["current_week"], summary["total_weeks"]
        program = class_group.program.name if class_group.program else ""
        cards.append({
            "class": class_group, "id": class_group.id, "name": class_group.name, "program": program,
            "icon": program_icons.view(class_group.program) if class_group.program else None,
            "professor": (class_group.professor_name or "").strip(), "hue": _hue(program or class_group.name),
            "running": running, "week": week, "weeks": weeks,
            "progress": min(100, round(week / weeks * 100)) if running and weeks else 0,
            "blocks": ["done" if number < week else "now" if number == week and running else "next" for number in range(1, weeks + 1)],
            "enrolled": summary["enrolled"], "capacity": summary["capacity"], "fill_pct": summary["fill_pct"],
            "countdown": summary["countdown"], "starts_label": summary["starts_label"],
            "course": weekly_questions.class_overview(db, class_group),
            "days": days, "start": start, "end": end,
            "hours": f"{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}" if start is not None else "",
            "clash_with": [],
        })

    # The same professor at the same hours of the same day, in two classes.
    placed = [card for card in cards if card["days"] and card["start"] is not None]
    clashes: dict[str, dict] = {}
    for index, one in enumerate(placed):
        for other in placed[index + 1:]:
            shared = set(one["days"]) & set(other["days"])
            if not one["professor"] or one["professor"].lower() != other["professor"].lower() or not shared:
                continue
            if not (one["start"] < other["end"] and other["start"] < one["end"]):
                continue
            one["clash_with"].append(other["name"])
            other["clash_with"].append(one["name"])
            group = clashes.setdefault(one["professor"].lower(), {
                "professor": one["professor"], "classes": [],
                "when": f"{_WEEKDAYS[min(shared)]} {max(one['start'], other['start']) // 60:02d}:{max(one['start'], other['start']) % 60:02d}"
                        f"–{min(one['end'], other['end']) // 60:02d}:{min(one['end'], other['end']) % 60:02d}",
            })
            for card in (one, other):
                if card["id"] not in {item["id"] for item in group["classes"]}:
                    group["classes"].append({"id": card["id"], "name": card["name"]})

    # The hour ruler: an hour before the first class to an hour after the last, at least 08:00 to 14:00.
    first = min([card["start"] for card in placed] + [9 * 60])
    last = max([card["end"] for card in placed] + [13 * 60])
    ruler_from, ruler_to = max(0, (first // 60) * 60 - 60), min(24 * 60, -(-last // 60) * 60 + 60)
    span = ruler_to - ruler_from
    ruler = [{"label": f"{mark // 60:02d}:00", "left": round((mark - ruler_from) / span * 100, 3)}
             for mark in range(ruler_from, ruler_to + 1, 60)]

    today = now_local.date()
    monday = today - timedelta(days=today.weekday())
    days, strip = [], []
    for index, name in enumerate(_WEEKDAYS):
        date = monday + timedelta(days=index)
        mine = sorted((card for card in cards if index in card["days"]), key=lambda card: (card["start"] is None, card["start"] or 0, card["name"]))
        slots: dict[tuple, dict] = {}
        for card in mine:
            key = (card["start"], card["end"])
            slots.setdefault(key, {"start": card["start"] if card["start"] is not None else 0, "end": card["end"] or 0,
                                   "hours": card["hours"], "timed": card["start"] is not None, "classes": []})["classes"].append(card)
        for number, slot in enumerate(slots.values()):
            slot["key"] = f"{index}-{number}"
        timed = [slot for slot in slots.values() if slot["timed"]]
        lanes = _lanes(timed)
        for slot in timed:
            slot["left"] = round((slot["start"] - ruler_from) / span * 100, 3)
            slot["width"] = round((slot["end"] - slot["start"]) / span * 100, 3)
            slot["clash"] = any(card["clash_with"] for card in slot["classes"])
        days.append({"index": index, "name": name, "date": date, "is_today": date == today, "classes": mine,
                     "slots": list(slots.values()), "bands": timed, "lanes": lanes})
        strip.append({"name": name, "date": date, "is_today": date == today, "count": len(mine), "hues": [card["hue"] for card in mine]})

    # Days without a class, one after the other, read as one line.
    rows = []
    for day in days:
        if day["classes"]:
            rows.append({"kind": "day", **day})
        elif rows and rows[-1]["kind"] == "free":
            rows[-1]["last"] = day
        else:
            rows.append({"kind": "free", "first": day, "last": day})
    for row in rows:
        if row["kind"] == "free":
            row["is_today"] = row["first"]["date"] <= today <= row["last"]["date"]

    professors: dict[str, dict] = {}
    for card in cards:
        if card["professor"]:
            entry = professors.setdefault(card["professor"].lower(), {"name": card["professor"], "initials": _initials(card["professor"]),
                                                                       "hue": _hue(card["professor"]), "count": 0, "clash": False})
            entry["count"] += 1
            entry["clash"] = entry["clash"] or bool(card["clash_with"])
    return {
        "cards": cards, "rows": rows, "strip": strip, "ruler": ruler, "from": ruler_from, "to": ruler_to,
        "unplaced": [card for card in cards if not card["days"]],
        "clashes": list(clashes.values()),
        "professors": sorted(professors.values(), key=lambda entry: (-entry["count"], entry["name"].lower())),
        "running": sum(1 for card in cards if card["running"]),
        "upcoming": sum(1 for card in cards if not card["running"]),
        "sessions": sum(len(card["days"]) for card in cards),
        "no_professor": sum(1 for card in cards if not card["professor"]),
    }


def programs_without_teacher(db: Session) -> list:
    """The programs taught here that no professor teaches yet (a place still to name does not count)."""
    from app.models import Professor
    from app.services import enrollment_service

    real = [professor for professor in db.query(Professor).all() if not (professor.name or "").startswith("À nommer")]
    return [program for program in db.query(Program).filter(Program.is_approved.is_(True)).order_by(Program.name).all()
            if not any(enrollment_service.teaches(professor, program) for professor in real)]


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

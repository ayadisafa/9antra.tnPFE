"""One letter a week, and a limit on what the application sends by itself.

A candidate receives at most WEEKLY_LIMIT automatic messages in seven days. Past it, what can wait (the week's
questions, the questions after a session, the review request) is not sent on its own: it is kept for the candidate's
weekly letter (status "in_letter", it counts as on its way). What must arrive on time is never kept: the payment
reminder, a class that moved, the reminders before the start, the session link, the level check, the welcome.
What the admin sends by hand never counts and is never kept.

The weekly letter ("weekly_letter" in Email templates, where its words are changed or it is switched off) leaves on
LETTER_WEEKDAY from LETTER_HOUR to each candidate of a running class who has something in it: their sessions of the
week, the questions still waiting for their answer (the one nudge they get), and what was kept for it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.candidate_email import CandidateEmail

WEEKLY_LIMIT = 4                    # automatic messages in seven days, the letter included
CAN_WAIT = ("weekly_questions", "session_check", "feedback_request")
KEY = "weekly_letter"
LETTER_WEEKDAY = 0                  # Monday
LETTER_HOUR = 9
WAITING_DAYS = 14                   # questions older than this are no longer asked for
_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _on(db: Session) -> bool:
    from app.services import email_templates

    return email_templates.get(db, KEY).sends_itself


def sent_this_week(db: Session, candidate_id: str, now: datetime | None = None) -> int:
    """Automatic messages that went to this candidate in the last seven days."""
    now = now or datetime.utcnow()
    return (db.query(CandidateEmail.id)
            .filter(CandidateEmail.candidate_id == candidate_id, CandidateEmail.direction == "outbound",
                    CandidateEmail.author == "system", CandidateEmail.status.in_(("sent", "to_send")),
                    CandidateEmail.received_at >= now - timedelta(days=7))
            .count())


def should_hold(db: Session, key: str, candidate, manual: bool) -> bool:
    """Whether this automatic message waits for the weekly letter instead of going now."""
    if manual or key not in CAN_WAIT or candidate is None or not _on(db):
        return False
    return sent_this_week(db, candidate.id) >= WEEKLY_LIMIT


def hold(db: Session, candidate, registration, key: str, rendered: dict, values: dict | None) -> CandidateEmail:
    """Keeps the message for the weekly letter; it shows in the candidate's messages as waiting for it."""
    import os

    from app.services.email_threads import PLATFORM

    link = next((value for name, value in (values or {}).items() if name.endswith("_link") and value), None)
    message = CandidateEmail(
        candidate_id=candidate.id, registration_id=getattr(registration, "id", None), direction="outbound",
        sender=os.getenv("SMTP_EMAIL") or PLATFORM, recipient=candidate.email, subject=rendered.get("subject"), body=rendered.get("text") or "", html_body=rendered.get("html"),
        intent=key, status="in_letter", author="system", channel="email", received_at=datetime.utcnow(),
        analysis="Kept for the weekly letter: the candidate already received enough messages this week.",
        sources=json.dumps({"title": rendered.get("title") or rendered.get("subject"), "link": link}, ensure_ascii=False),
    )
    db.add(message)
    db.flush()
    return message


def _when(day) -> str:
    return f"{_DAYS[day.weekday()]} {day.day} {_MONTHS[day.month - 1]}"


def contents(db: Session, candidate, base_url: str, now: datetime | None = None) -> dict:
    """What the candidate's letter says: {"registration", "sessions": [lines], "waiting": [lines], "held": [messages]}."""
    from app.models.classgroup import ClassGroup, ClassStatus
    from app.models.classsession import ClassSession
    from app.models.quiz import Quiz
    from app.models.registration import Registration, RegistrationStatus
    from app.services import meetings, weekly_questions
    from app.services.schedule_utils import local_now

    now = now or local_now()
    registrations = [row for row in db.query(Registration).filter(Registration.candidate_id == candidate.id,
                                                                  Registration.status == RegistrationStatus.ASSIGNED).all()
                     if row.class_group_id]
    found = {"registration": registrations[0] if registrations else None, "sessions": [], "waiting": [], "held": []}
    monday = now.date() - timedelta(days=now.weekday())
    for registration in registrations:
        group = db.get(ClassGroup, registration.class_group_id)
        if group is None or group.status == ClassStatus.COMPLETED:
            continue
        for session in (db.query(ClassSession).filter(ClassSession.class_group_id == group.id, ClassSession.session_date >= max(monday, now.date()),
                                                      ClassSession.session_date <= monday + timedelta(days=6))
                        .order_by(ClassSession.session_date.asc()).all()):
            start, end = meetings.hours_of(session, group)
            hours = f", {start:%H:%M} – {end:%H:%M}" if (session.start_time or group.start_time) else ""
            found["sessions"].append(f"- {_when(session.session_date)}{hours} : {session.topic or group.name}")
    local = weekly_questions.is_local_address(base_url)
    ids = [row.id for row in registrations]
    if ids:
        for quiz in (db.query(Quiz).filter(Quiz.registration_id.in_(ids), Quiz.kind.in_(("weekly", "session")), Quiz.sent_at.isnot(None),
                                           Quiz.completed_at.is_(None), Quiz.sent_at >= datetime.utcnow() - timedelta(days=WAITING_DAYS))
                     .order_by(Quiz.sent_at.asc()).all()):
            what = f"Les questions de la semaine {quiz.week_number}" if quiz.kind == "weekly" and quiz.week_number else "Les questions de votre séance"
            way = (f"répondez à l'email du {quiz.sent_at:%d/%m}" if local or not quiz.access_token
                   else (quiz.google_form_url or weekly_questions.public_link(base_url, quiz)))
            found["waiting"].append(f"- {what} : {way}")
    for message in (db.query(CandidateEmail).filter(CandidateEmail.candidate_id == candidate.id, CandidateEmail.status == "in_letter")
                    .order_by(CandidateEmail.received_at.asc()).all()):
        found["held"].append(message)
    return found


def _held_lines(held: list[CandidateEmail], waiting: list[str]) -> list[str]:
    """What was kept for the letter, unless it is already among the questions waiting (same link)."""
    lines = []
    for message in held:
        try:
            said = json.loads(message.sources or "{}")
        except (TypeError, ValueError):
            said = {}
        link = said.get("link")
        if link and any(link in line for line in waiting):
            continue
        title = said.get("title") or message.subject or "Un message"
        lines.append(f"- {title}" + (f" : {link}" if link else ""))
    return lines


def send_one(db: Session, candidate, base_url: str, *, manual: bool = False, now: datetime | None = None) -> CandidateEmail | None:
    """The candidate's letter, when there is something in it. Returns the message (or None)."""
    from app.services import email_templates

    found = contents(db, candidate, base_url, now)
    waiting = list(dict.fromkeys(found["waiting"] + _held_lines(found["held"], found["waiting"])))
    if not found["sessions"] and not waiting:
        return None
    message = email_templates.send(db, KEY, candidate=candidate, registration=found["registration"], manual=manual, values={
        "week_sessions": ("**Vos séances cette semaine**\n" + "\n".join(found["sessions"])) if found["sessions"] else "",
        "waiting": ("**Ce qui attend votre réponse**\n" + "\n".join(waiting)) if waiting else "",
    })
    arrived = email_templates.delivered(message)
    for held in found["held"]:
        held.status = "sent" if arrived else "failed"
        held.analysis = ("Sent in the weekly letter." if arrived else "Not delivered: the weekly letter that carried it did not arrive.")
    db.flush()
    return message


def send_due(db: Session, base_url: str, now: datetime | None = None, limit: int = 60) -> int:
    """On the letter's day (or when something kept for it waited a week), each candidate of a running class gets their
    letter, once a week. Returns how many went out."""
    from app.models.candidate import Candidate
    from app.models.registration import Registration, RegistrationStatus
    from app.services.schedule_utils import local_now

    if not _on(db):
        return 0
    now = now or local_now()
    utc = datetime.utcnow()
    oldest_held = {row.candidate_id: row.received_at for row in
                   db.query(CandidateEmail).filter(CandidateEmail.status == "in_letter").order_by(CandidateEmail.received_at.desc()).all()}
    letter_day = now.weekday() == LETTER_WEEKDAY and now.hour >= LETTER_HOUR
    ids = set(oldest_held) | ({row.candidate_id for row in db.query(Registration.candidate_id).filter(
        Registration.status == RegistrationStatus.ASSIGNED, Registration.class_group_id.isnot(None)).all()} if letter_day else set())
    sent = 0
    for candidate_id in ids:
        overdue = candidate_id in oldest_held and oldest_held[candidate_id] <= utc - timedelta(days=7)
        if not (letter_day or overdue):
            continue
        last = (db.query(CandidateEmail.received_at).filter(CandidateEmail.candidate_id == candidate_id, CandidateEmail.intent == KEY,
                                                             CandidateEmail.direction == "outbound")
                .order_by(CandidateEmail.received_at.desc()).first())
        if last is not None and last[0] >= utc - timedelta(days=6):
            continue
        candidate = db.get(Candidate, candidate_id)
        if candidate is None or not candidate.email:
            continue
        message = send_one(db, candidate, base_url, now=now)
        db.commit()
        sent += message is not None
        if sent >= limit:
            break
    return sent

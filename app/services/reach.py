"""What did not reach the candidates: emails that failed, WhatsApp messages and SMS that failed or still wait for the
admin's phone. One list (/inbox/not-reached, and one line in "What needs you today"), each with why in plain words and
what to do: send again, open WhatsApp, call, or "not needed any more".

An email that failed is tried again once by itself (retry_due, with the reminders job), between RETRY_AFTER and
RETRY_UNTIL after it failed, unless it is no longer useful. What happened is kept on the message (`reach`, JSON).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, joinedload

from app.models.candidate_email import CandidateEmail
from app.services.ui_words import t as _t

SHOWN_DAYS = 30                     # older than this, it is history
RETRY_AFTER = timedelta(minutes=10)
RETRY_UNTIL = timedelta(hours=48)
# What each message was, as the admin calls it.
KINDS = {
    "weekly_questions": "The week's questions", "payment_reminder": "Payment reminder", "session_check": "Questions after the session",
    "reply": "An answer", "personal": "Your message", "training_reminder": "Reminder before the class starts",
    "quiz_invitation": "Level check", "class_rescheduled": "Class rescheduled", "feedback_request": "Review request", "success_review": "Congratulations",
    "enrollment": "Registration confirmed", "session_reminder": "Session reminder", "class_scheduled": "Class scheduled",
    "class_moved": "Moved to another class", "session_link": "Link to the session", "welcome": "Welcome message",
}
# Messages about a moment: once it has passed (a day later), sending them makes no sense.
_DATED = ("training_reminder", "session_reminder", "session_link", "class_rescheduled", "class_scheduled")
STALE_DAYS = 7                      # the others are probably no longer useful after this


def state_of(message: CandidateEmail) -> dict:
    try:
        found = json.loads(message.reach or "{}")
    except (TypeError, ValueError):
        found = {}
    return found if isinstance(found, dict) else {}


def _note(message: CandidateEmail, **values) -> None:
    message.reach = json.dumps({**state_of(message), **values}, ensure_ascii=False)


def stale(message: CandidateEmail, now: datetime | None = None) -> bool:
    """Whether sending it now would no longer help (its moment passed, or it is old)."""
    now = now or datetime.utcnow()
    age = now - (message.received_at or now)
    return age > timedelta(days=1 if (message.intent or "") in _DATED else STALE_DAYS)


def reason(message: CandidateEmail) -> str:
    """Why it did not arrive, in plain words."""
    if message.status == "to_send":
        return _t("It waits for you to send it from your phone (WhatsApp is not connected).")
    said = (message.analysis or "").replace("Not delivered:", "").strip()
    if "SMTP_EMAIL" in said or "not configured" in said:
        return _t("The email account was not set up when it left.")
    if "attached file" in said:
        return said
    return said or _t("The mail server refused it.")


def not_reached(db: Session, *, candidate_id: str | None = None, now: datetime | None = None) -> list[dict]:
    """Each message that did not reach its candidate (newest first), unless the admin said it is not needed."""
    from app.services import whatsapp_inbox

    now = now or datetime.utcnow()
    query = (db.query(CandidateEmail).options(joinedload(CandidateEmail.candidate))
             .filter(CandidateEmail.direction == "outbound",
                     ((CandidateEmail.status == "failed") | ((CandidateEmail.status == "to_send") & CandidateEmail.channel.in_(("whatsapp", "sms")))),
                     CandidateEmail.received_at >= now - timedelta(days=SHOWN_DAYS)))
    if candidate_id:
        query = query.filter(CandidateEmail.candidate_id == candidate_id)
    found = []
    for message in query.order_by(CandidateEmail.received_at.desc()).all():
        state = state_of(message)
        if state.get("dismissed"):
            continue
        candidate = message.candidate
        phone = getattr(candidate, "phone", None)
        found.append({
            "message": message, "id": message.id, "candidate_id": message.candidate_id, "candidate": candidate,
            "name": (getattr(candidate, "full_name", None) or getattr(candidate, "email", None) or message.recipient or "").strip(),
            "channel": message.channel or "email", "kind": _t(KINDS.get(message.intent or "", "A message")),
            "subject": message.subject or "", "when": message.received_at, "why": reason(message), "stale": stale(message, now),
            "retried": state.get("retried"),
            "whatsapp": whatsapp_inbox.link_of(message) if message.channel in ("whatsapp", "sms") else None,
            "call": f"tel:{phone.replace(' ', '')}" if phone else None,
            "thread_id": message.thread_id,
        })
    return found


def summary(items: list[dict]) -> dict:
    people = {item["candidate_id"] for item in items}
    return {"messages": len(items), "people": len(people), "useful": sum(1 for item in items if not item["stale"])}


def retry(db: Session, message: CandidateEmail, *, by: str = "you") -> bool:
    """Send again (an email); True when it arrived."""
    from app.services import email_threads

    if message.channel not in (None, "email") or message.status != "failed":
        return False
    delivered = email_threads.resend(db, message)
    _note(message, retried=datetime.utcnow().isoformat(timespec="seconds"), retried_by=by, retried_ok=delivered)
    db.flush()
    return delivered


def mark_sent(db: Session, message: CandidateEmail) -> None:
    """A WhatsApp message or SMS the admin sent from the phone."""
    from app.services import whatsapp_inbox

    if message.status == "to_send":
        whatsapp_inbox.mark_sent(db, message)
    else:
        message.status = "sent"
        message.analysis = _t("Sent by you from your phone.")
    _note(message, sent_by_hand=datetime.utcnow().isoformat(timespec="seconds"))
    db.flush()


def dismiss(db: Session, message: CandidateEmail, *, by: str = "you") -> None:
    """Not needed any more: it leaves the list (the message stays in the discussion)."""
    _note(message, dismissed=datetime.utcnow().isoformat(timespec="seconds"), dismissed_by=by)
    db.flush()


def retry_due(db: Session, now: datetime | None = None, limit: int = 20) -> int:
    """Each email that failed is tried again once by itself, while it is still useful. Returns how many arrived."""
    now = now or datetime.utcnow()
    waiting = (db.query(CandidateEmail)
               .filter(CandidateEmail.direction == "outbound", CandidateEmail.status == "failed",
                       (CandidateEmail.channel == "email") | CandidateEmail.channel.is_(None),
                       CandidateEmail.received_at <= now - RETRY_AFTER, CandidateEmail.received_at >= now - RETRY_UNTIL)
               .order_by(CandidateEmail.received_at.asc()).limit(limit * 3).all())
    arrived = tried = 0
    for message in waiting:
        state = state_of(message)
        if state.get("retried") or state.get("dismissed") or stale(message, now):
            continue
        tried += 1
        arrived += retry(db, message, by="the system")
        db.commit()
        if tried >= limit:
            break
    return arrived

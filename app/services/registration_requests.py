"""Somebody who is not a candidate yet writes to ask for a place in a program.

Emails from strangers are never answered by themselves (newsletters, spam).
But when one says it wants to register and names a program taught here, the
admin is told, with one button that adds the person and registers them. The
wish and the program are recognised by their words; no language model decides.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session

from app.models.candidate import Candidate
from app.models.notification import Notification
from app.models.program import Program
from app.models.registration import PaymentStatus, Registration, RegistrationStatus

CATEGORY = "REGISTRATION_REQUEST"
# French, English and Tunisian written in Latin letters.
_WISH = re.compile(
    r"inscri\w*|m inscrire|register\w*|registration|enrol+\w*|sign up|rejoindre|participer|integrer|"
    r"nsajel|nsajjel|n7eb n9ra|nheb na9ra|n7eb nsajel|postuler|candidature|je veux suivre|je souhaite suivre|"
    r"i want to (join|follow|take)|i would like to (join|follow|take)|place disponible|comment s inscrire"
)
_WISH_ARABIC = re.compile(r"تسجيل|نسجل|التسجيل|نحب نقرا|أريد التسجيل")


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", folded).split())


def wants_to_register(subject: str, body: str) -> bool:
    text = f"{subject or ''} {body or ''}"
    return _WISH.search(_plain(text)) is not None or _WISH_ARABIC.search(text) is not None


def program_named(db: Session, subject: str, body: str) -> Program | None:
    from app.services.chatbot import _program_in       # the same reading of a program's name as the assistant

    return _program_in(db, _plain(f"{subject or ''} {body or ''}"))


def name_from(address: str) -> str:
    """A first guess of the person's name from their address; the admin corrects it on their page."""
    local = re.sub(r"\d+", " ", (address or "").split("@")[0])
    words = [word for word in re.split(r"[._\-+\s]+", local) if len(word) > 1]
    return " ".join(word.capitalize() for word in words)[:80] or (address or "").split("@")[0]


def notice(db: Session, *, sender: str, subject: str, body: str, message_id: str | None = None,
           automatic: bool = False) -> Notification | None:
    """Tell the admin about a stranger's wish to register. None when the email is not one."""
    address = (sender or "").strip().lower()
    if automatic or "@" not in address or re.search(r"no-?reply|newsletter|mailer-daemon|notification", address):
        return None
    if not wants_to_register(subject, body):
        return None
    program = program_named(db, subject, body)
    if program is None:
        return None
    waiting = db.query(Notification).filter(Notification.category == CATEGORY, Notification.is_read.is_(False)).all()
    if any((row.payload or {}).get("email") == address and (row.payload or {}).get("program_id") == program.id for row in waiting):
        return None
    words = " ".join((body or "").split())
    row = Notification(
        recipient_role="admin", category=CATEGORY, title=f"Asks to register: {program.name}",
        message=f"{address} is not a candidate yet and writes: “{words[:220]}{'…' if len(words) > 220 else ''}”",
        action_url="/candidate/all", program_id=program.id,
        payload={"email": address, "name": name_from(address), "program_id": program.id, "program_name": program.name,
                 "subject": (subject or "")[:200], "body": words[:2000], "message_id": message_id},
    )
    db.add(row)
    db.commit()
    return row


def accept(db: Session, notification: Notification) -> tuple[Candidate | None, Registration | None, str | None]:
    """Add the person and register them to the program they asked for. (candidate, registration, problem)."""
    payload = notification.payload if isinstance(notification.payload, dict) else {}
    address, program = (payload.get("email") or "").strip().lower(), db.get(Program, payload.get("program_id") or "")
    if notification.category != CATEGORY or "@" not in address:
        return None, None, "This notification is not a request to register."
    if program is None:
        return None, None, "The program they asked for no longer exists."
    candidate = db.query(Candidate).filter(Candidate.email.ilike(address)).first()
    if candidate is None:
        candidate = Candidate(full_name=payload.get("name") or name_from(address), email=address)
        db.add(candidate)
        db.flush()
    registration = db.query(Registration).filter(
        Registration.candidate_id == candidate.id, Registration.program_id == program.id,
        Registration.status != RegistrationStatus.CANCELLED).first()
    if registration is None:
        price = program.price_online or 0
        registration = Registration(
            candidate_id=candidate.id, program_id=program.id, program_name=program.name, delivery_mode="online",
            total_price=price, total_amount=price, status=RegistrationStatus.PENDING, payment_status=PaymentStatus.UNPAID,
        )
        db.add(registration)
    notification.is_read, notification.status = True, "read"
    db.commit()
    return candidate, registration, None


def fix(db: Session, notification: Notification) -> dict | None:
    """The button of the notification, while the person is not registered to that program yet."""
    payload = notification.payload if isinstance(notification.payload, dict) else {}
    address, program = (payload.get("email") or "").lower(), db.get(Program, payload.get("program_id") or "")
    if not address or program is None:
        return None
    candidate = db.query(Candidate).filter(Candidate.email.ilike(address)).first()
    if candidate is not None and db.query(Registration.id).filter(
            Registration.candidate_id == candidate.id, Registration.program_id == program.id,
            Registration.status != RegistrationStatus.CANCELLED).first() is not None:
        return None
    return {
        "label": f"Register them in {program.name}", "url": f"/candidate/requests/{notification.id}/accept", "method": "post", "fields": {},
        "confirm": f"Add {address} as a candidate and register them in {program.name} (online, not paid)? "
                   "They then get the usual payment reminder a day later. You can correct their name on their page.",
    }

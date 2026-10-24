"""How a message to a candidate goes: email, WhatsApp or SMS, chosen above the inbox.

"Let the system choose" sends by email when it goes by itself (the reminders also on WhatsApp once a Business number is
connected, as before), then by the other ways: WhatsApp, then SMS. With "if it does not work, try the next one" (on by default), a way
that fails or cannot reach this candidate (no address, no number, email not connected) hands over to the next one.
WhatsApp without a Business number and SMS (no SMS account) are kept for the admin to send from the phone.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

WAYS = ("email", "whatsapp", "sms")
NAMES = {"auto": "Let the system choose", "email": "Email", "whatsapp": "WhatsApp", "sms": "SMS"}


def choice(db: Session) -> dict:
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    way = getattr(settings, "send_way", None) or "auto"
    fallback = getattr(settings, "send_fallback", None)
    return {"way": way if way in NAMES else "auto", "fallback": True if fallback is None else bool(fallback)}


def set_choice(db: Session, way: str, fallback: bool) -> dict:
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    settings.send_way = way if way in NAMES else "auto"
    settings.send_fallback = bool(fallback)
    db.commit()
    return choice(db)


def status() -> dict:
    """Whether each way goes by itself today."""
    from app.services import email_threads, whatsapp

    return {
        "email": {"alone": email_threads.smtp_configured(),
                  "note": "Goes by itself" if email_threads.smtp_configured() else "Not connected (Settings, Email)"},
        "whatsapp": {"alone": whatsapp.configured(),
                     "note": "Goes by itself" if whatsapp.configured() else "Waits for your phone (no Business number yet)"},
        "sms": {"alone": __import__("app.services.sms", fromlist=["configured"]).configured(),
                "note": "Goes by itself (from the Android phone)" if __import__("app.services.sms", fromlist=["configured"]).configured()
                else "Waits for your phone (no Android phone connected)"},
    }


def order_for(db: Session, candidate, first: str | None = None) -> list[str]:
    """The ways to try for this candidate, in order (only the ones that can reach them)."""
    from app.services import whatsapp

    wanted = choice(db)
    first = first or wanted["way"]
    alone = status()
    if first == "auto":
        order = sorted(WAYS, key=lambda way: (not alone[way]["alone"], WAYS.index(way)))
    else:
        rest = sorted((way for way in WAYS if way != first), key=lambda way: (not alone[way]["alone"], WAYS.index(way)))
        order = [first] + rest
    has_email = bool((getattr(candidate, "email", None) or "").strip())
    has_phone = whatsapp.normalise(getattr(candidate, "phone", None)) is not None
    possible = [way for way in order if (has_email if way == "email" else has_phone)]
    if not wanted["fallback"]:
        return possible[:1] if possible and possible[0] == order[0] else []
    return possible


def deliver(db: Session, candidate, rendered: dict, *, kind: str, registration=None, first: str | None = None):
    """Send a written message (rendered: subject, text, html, files, inline, and the WhatsApp text) by the first way that
    works. Returns the stored message: sent, waiting for the admin's phone, or the last failure (None when no way
    can reach this candidate)."""
    from app.services import email_threads, whatsapp_inbox

    last = None
    for way in order_for(db, candidate, first):
        if way == "email":
            message = email_threads.deliver(
                db, candidate, rendered["subject"], rendered["text"], rendered["html"], kind=kind, author="system",
                registration=registration, files=rendered.get("files"), inline=rendered.get("inline"),
            )
        elif way == "whatsapp":
            message = whatsapp_inbox.send(db, candidate, rendered["short"], kind=kind, registration=registration)
        else:
            message = whatsapp_inbox.send_sms(db, candidate, rendered["short"], kind=kind, registration=registration)
        if message is None:
            continue
        last = message
        if message.status in ("sent", "to_send", "test"):
            return message
    return last

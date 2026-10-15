"""WhatsApp, next to email, for the reminders.

Two ways, and the first one needs nothing:

1. A link that opens WhatsApp with the message already written; the admin
   presses "send" in WhatsApp. Every reminder page offers it.
2. When a WhatsApp Business number is connected (WHATSAPP_TOKEN and
   WHATSAPP_PHONE_ID in .env), each reminder the application emails is also
   sent on WhatsApp by itself.

Phone numbers get +216 in front unless the number already says its country
("+33 6…", "0033 6…").
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import quote

import requests
from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.candidate import Candidate

logger = logging.getLogger(__name__)

COUNTRY = "216"                     # Tunisia
LOCAL_DIGITS = 8                    # a Tunisian number without its country code
# The emails that are reminders: these also go out on WhatsApp.
REMINDERS = {"payment_reminder", "training_reminder", "weekly_questions", "quiz_invitation", "level_reminder", "session_link", "feedback_request"}
_API = "https://graph.facebook.com/v20.0"


# ------------------------------------------------------------------
# Phone numbers
# ------------------------------------------------------------------

def normalise(phone, country: str = COUNTRY) -> str | None:
    """A number written the international way ("+21698123456"), or None when
    it cannot be one. The country code is added unless the number has its own."""
    raw = str(phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if raw.startswith("+"):
        return "+" + digits if 8 <= len(digits) <= 15 else None
    if raw.startswith("00"):
        digits = digits[2:]
        return "+" + digits if 8 <= len(digits) <= 15 else None
    if digits.startswith(country) and len(digits) == len(country) + LOCAL_DIGITS:
        return "+" + digits         # "216 98 123 456": the code was there, without its "+"
    if not 6 <= len(digits) <= 12:
        return None
    return "+" + country + digits


def pretty(phone, country: str = COUNTRY) -> str:
    """"+216 98 123 456" for reading; what was typed when it is not a number."""
    number = normalise(phone, country)
    if number is None:
        return str(phone or "").strip()
    if number.startswith("+" + country) and len(number) == 1 + len(country) + LOCAL_DIGITS:
        local = number[1 + len(country):]
        return f"+{country} {local[:2]} {local[2:5]} {local[5:]}"
    return number


def is_usual(phone, country: str = COUNTRY) -> bool:
    """Whether a number of this country has the length it should have."""
    number = normalise(phone, country)
    if number is None:
        return False
    return not number.startswith("+" + country) or len(number) == 1 + len(country) + LOCAL_DIGITS


def as_message(text: str) -> str:
    """An email's plain text the way WhatsApp shows it (bold is one star, not two)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", (text or "").replace("\r\n", "\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def link(phone, text: str = "") -> str | None:
    """The address that opens WhatsApp on this number, with the message written."""
    number = normalise(phone)
    if number is None:
        return None
    return f"https://wa.me/{number[1:]}" + (f"?text={quote(as_message(text))}" if text else "")


def tidy_stored(db: Session) -> int:
    """Write the candidates' numbers the international way. Returns how many changed."""
    changed = 0
    for candidate in db.query(Candidate).filter(Candidate.phone.isnot(None)).all():
        number = normalise(candidate.phone)
        if number and number != candidate.phone:
            candidate.phone = number
            changed += 1
    if changed:
        db.commit()
    return changed


# ------------------------------------------------------------------
# Sending by itself (WhatsApp Business)
# ------------------------------------------------------------------

def configured() -> bool:
    return bool((os.getenv("WHATSAPP_TOKEN") or "").strip() and (os.getenv("WHATSAPP_PHONE_ID") or "").strip())


def send(phone, text: str) -> str | None:
    """Send a message from the business number. Returns the problem, or None when it went.

    WhatsApp only lets a business write freely to someone who wrote to it in
    the last 24 hours. For everyone else the message must be a template that
    WhatsApp approved: name it in WHATSAPP_TEMPLATE (one variable, the text).
    """
    number = normalise(phone)
    if number is None:
        return "This phone number cannot be used on WhatsApp."
    if not configured():
        return "No WhatsApp Business number is connected."
    body = as_message(text)
    template = (os.getenv("WHATSAPP_TEMPLATE") or "").strip()
    if template:
        payload = {
            "messaging_product": "whatsapp", "to": number[1:], "type": "template",
            "template": {
                "name": template, "language": {"code": os.getenv("WHATSAPP_TEMPLATE_LANG", "fr")},
                # A template variable is one line, without long runs of spaces.
                "components": [{"type": "body", "parameters": [{"type": "text", "text": " ".join(body.split())[:1000]}]}],
            },
        }
    else:
        payload = {"messaging_product": "whatsapp", "to": number[1:], "type": "text", "text": {"body": body[:4000]}}
    try:
        response = requests.post(
            f"{_API}/{os.getenv('WHATSAPP_PHONE_ID').strip()}/messages", json=payload, timeout=30,
            headers={"Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN').strip()}"},
        )
    except requests.RequestException as exc:
        return f"WhatsApp did not answer ({type(exc).__name__})."
    if response.status_code >= 300:
        try:
            reason = response.json().get("error", {}).get("message") or response.text[:200]
        except ValueError:
            reason = response.text[:200]
        return f"WhatsApp refused the message: {reason}"
    return None


def after_email(db: Session, candidate: Candidate | None, kind: str, text: str) -> bool:
    """A reminder was just emailed: send it on WhatsApp too, when a business
    number is connected. Never raises; True when the message went."""
    if kind not in REMINDERS or candidate is None or not configured() or not (candidate.phone or "").strip():
        return False
    try:
        problem = send(candidate.phone, text)
        db.add(Activity(
            candidate_id=candidate.id, event_type="WHATSAPP_FAILED" if problem else "WHATSAPP_SENT",
            details=(problem or f"Reminder also sent on WhatsApp to {pretty(candidate.phone)}")[:500],
        ))
        db.flush()
        return problem is None
    except Exception:
        logger.exception("WhatsApp reminder for candidate %s failed.", candidate.id)
        return False

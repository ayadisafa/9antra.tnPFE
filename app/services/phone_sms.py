"""SMS from the admin's iPhone, all at once: an iPhone never lets an application send SMS by itself, but its Shortcuts
app can. A shortcut asks this application for the SMS waiting to be sent (to candidates, professors, the team), sends
each from the iPhone's own number with "Send Message", and says which went. One tap (or a home-screen icon, or Siri, or
a time of day) instead of one tap per SMS.

The shortcut reaches the application by a private link holding a secret (Settings, SMS), over the private Tailscale
address (or the Wi-Fi at the centre). The link only lists what waits and marks it sent: nothing else can be read or done.
"""
from __future__ import annotations

import hmac
import json
import secrets
import subprocess
import time

from sqlalchemy.orm import Session

_tailscale: dict = {"at": 0.0, "address": None}


def token(db: Session, *, renew: bool = False) -> str:
    """The secret of the iPhone's link (made the first time, or again to cut the old link off)."""
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    if renew or not settings.phone_sms_token:
        settings.phone_sms_token = secrets.token_urlsafe(24)
        db.commit()
    return settings.phone_sms_token


def allowed(db: Session, given: str) -> bool:
    from app.services.settings_service import get_settings

    kept = get_settings(db).phone_sms_token or ""
    return bool(kept) and hmac.compare_digest(kept, given or "")


def waiting(db: Session) -> list[dict]:
    """[{"id", "number", "text", "to"}]: every SMS waiting for the admin's phone, the oldest first."""
    from app.models.candidate_email import CandidateEmail
    from app.models.team_message import TeamMessage
    from app.services import team_messages, whatsapp

    found = []
    for row in (db.query(CandidateEmail).filter(CandidateEmail.channel == "sms", CandidateEmail.status == "to_send")
                .order_by(CandidateEmail.received_at.asc()).all()):
        number = whatsapp.normalise(row.recipient)
        if number:
            found.append({"id": f"c-{row.id}", "number": number, "text": row.body or "",
                          "to": row.candidate.full_name if row.candidate else number})
    for row in (db.query(TeamMessage).filter(TeamMessage.channel == "sms", TeamMessage.status == "to_send")
                .order_by(TeamMessage.created_at.asc()).all()):
        person = team_messages.person(db, row.person) or {}
        number = whatsapp.normalise(person.get("number"))
        if number:
            found.append({"id": f"t-{row.id}", "number": number, "text": row.body or "", "to": person.get("name") or number})
    return found


def mark_sent(db: Session, key: str) -> bool:
    """The shortcut sent it: marked as sent, like the "Sent" button."""
    from app.models.candidate_email import CandidateEmail
    from app.services import team_messages, whatsapp_inbox

    kind, _, row_id = (key or "").partition("-")
    if kind == "c":
        row = db.get(CandidateEmail, row_id)
        if row is None or row.channel != "sms" or row.status != "to_send":
            return False
        whatsapp_inbox.mark_sent(db, row)
        row.analysis = "Sent from your iPhone (the shortcut)."
        db.commit()
        return True
    if kind == "t":
        return team_messages.mark_sent(db, row_id) is not None
    return False


def secure_address() -> str | None:
    """The private https address of this computer on Tailscale (None without Tailscale), read once a minute."""
    if time.time() - _tailscale["at"] < 60:
        return _tailscale["address"]
    address = None
    for program in ("tailscale", r"C:\Program Files\Tailscale\tailscale.exe"):
        try:
            done = subprocess.run([program, "status", "--json"], capture_output=True, text=True, timeout=4)
        except (OSError, subprocess.SubprocessError):
            continue
        try:
            name = (json.loads(done.stdout or "{}").get("Self") or {}).get("DNSName") or ""
        except ValueError:
            name = ""
        if name:
            address = "https://" + name.rstrip(".")
        break
    _tailscale.update(at=time.time(), address=address)
    return address

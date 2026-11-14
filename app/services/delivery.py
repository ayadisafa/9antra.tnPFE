"""What happened to a WhatsApp message or an SMS after it left: sent, delivered, read, or failed.

- WhatsApp Business (Meta's Cloud API) says it by itself: each message sent gets Meta's id (wamid), and Meta posts its
  statuses (sent, delivered, read, failed) to the same webhook as the messages (/whatsapp/webhook).
- SMS from the Android phone (SMS Gateway for Android): the phone gives each SMS an id, and posts sms:sent,
  sms:delivered and sms:failed to /sms/webhook when those webhooks are added in the app.
- SMS from the iPhone: Apple tells nothing after Messages took it; the shortcut says it was sent (phone_sms.mark_sent).
  The SMS that come IN to the iPhone reach the application through a personal automation of Shortcuts ("When I get a
  message"), posting to /phone-sms/{secret}/received (see Settings › SMS).

The state only goes forward (sent → delivered → read); "failed" is kept with its reason, and the message then shows in
Inbox › Didn't reach them like a failed email.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.candidate_email import CandidateEmail
from app.models.notification import Notification

ORDER = {"sent": 1, "delivered": 2, "read": 3}
LABELS = {"sent": "Sent", "delivered": "Delivered", "read": "Read", "failed": "Not delivered"}


def _when(value) -> datetime:
    try:
        if value is None:
            raise ValueError
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return datetime.utcnow()


def update(db: Session, external_id: str, state: str, *, when=None, reason: str | None = None) -> CandidateEmail | None:
    """A status for the message sent with this id (Meta's or the phone's). Returns the message, or None when unknown."""
    if not external_id or state not in (*ORDER, "failed"):
        return None
    message = db.query(CandidateEmail).filter(CandidateEmail.external_id == str(external_id)).first()
    if message is None:
        return None
    current = message.delivery or "sent"
    if state == "failed":
        if current == "read":
            return message                      # read already: a late failure says nothing more
        message.delivery, message.delivery_at = "failed", _when(when)
        message.status = "failed"
        way = "WhatsApp" if message.channel == "whatsapp" else "SMS"
        message.analysis = f"Not delivered by {way}" + (f": {reason}" if reason else ".")
        db.add(Notification(recipient_role="admin", category="EMAIL_FAILED", title=f"{way} not delivered",
                            message=f"{message.recipient}: {reason or 'the phone network refused it'}"[:500],
                            action_url=f"/inbox?thread={message.thread_id}", candidate_id=message.candidate_id))
    elif ORDER[state] > ORDER.get(current, 0) or current == "failed" and state != "sent":
        message.delivery, message.delivery_at = state, _when(when)
        if message.status == "failed":
            message.status = "sent"
    db.flush()
    return message


def from_whatsapp(payload: dict) -> list[dict]:
    """The statuses of one webhook call from Meta: [{"id", "state", "when", "reason"}]."""
    found = []
    for entry in (payload or {}).get("entry") or []:
        for change in entry.get("changes") or []:
            for item in (change.get("value") or {}).get("statuses") or []:
                errors = item.get("errors") or []
                reason = (errors[0].get("title") or errors[0].get("message")) if errors else None
                found.append({"id": item.get("id"), "state": item.get("status"), "when": item.get("timestamp"), "reason": reason})
    return found


def from_sms(payload: dict) -> dict | None:
    """{"id", "state", "when", "reason"} of an sms:sent / sms:delivered / sms:failed event of the Android phone."""
    event = (payload or {}).get("event") or ""
    state = {"sms:sent": "sent", "sms:delivered": "delivered", "sms:failed": "failed"}.get(event)
    if state is None:
        return None
    part = payload.get("payload") or {}
    return {"id": part.get("messageId") or part.get("id"), "state": state,
            "when": part.get("deliveredAt") or part.get("sentAt") or part.get("failedAt"), "reason": part.get("reason")}


def summary(db: Session, days: int = 30) -> dict:
    """How the WhatsApp messages and SMS of the last days went, for the inbox."""
    from datetime import timedelta

    since = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(CandidateEmail.channel, CandidateEmail.delivery, CandidateEmail.status)
            .filter(CandidateEmail.direction == "outbound", CandidateEmail.channel.in_(("whatsapp", "sms")),
                    CandidateEmail.received_at >= since).all())
    found = {"whatsapp": {"sent": 0, "delivered": 0, "read": 0, "failed": 0, "to_send": 0},
             "sms": {"sent": 0, "delivered": 0, "read": 0, "failed": 0, "to_send": 0}}
    for channel, state, status in rows:
        bucket = found[channel]
        if status == "to_send":
            bucket["to_send"] += 1
        elif status == "failed" or state == "failed":
            bucket["failed"] += 1
        elif status in ("sent", "answered", "replied"):
            bucket[state if state in ORDER else "sent"] += 1
    return found

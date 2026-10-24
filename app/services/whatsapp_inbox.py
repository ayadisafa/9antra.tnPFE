"""WhatsApp conversations with candidates, kept in the inbox next to the emails.

A WhatsApp message is one more message of a discussion whose channel is
"whatsapp": the assistant reads it exactly as it reads an email (same rules,
same confidence, same "ask the admin" when it is not sure), and its answer,
or the admin's, goes back on WhatsApp.

How a message leaves:
* a WhatsApp Business number is connected: it is sent by itself (a free reply
  within 24 hours of the candidate's last message, the approved template
  otherwise, as WhatsApp requires);
* no Business number yet: it waits in "To send on WhatsApp", with a button
  that opens WhatsApp on the candidate's number with the message written.
  The admin presses send there, then "Sent".
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.candidate import Candidate
from app.models.candidate_email import CandidateEmail
from app.models.email_thread import EmailThread
from app.models.notification import Notification
from app.services import email_threads, whatsapp

logger = logging.getLogger(__name__)

CHANNELS = {
    "email": "Email",
    "both": "Email and WhatsApp",
    "whatsapp": "WhatsApp only",
}
WINDOW = timedelta(hours=24)        # WhatsApp lets a business write freely this long after the person wrote
SUBJECT = "WhatsApp"


def clean_channel(value: str | None, default: str = "email") -> str:
    return value if value in CHANNELS else default


def thread_for(db: Session, candidate: Candidate) -> EmailThread:
    """The candidate's WhatsApp discussion (one per candidate), opened the first time."""
    thread = (db.query(EmailThread).filter(EmailThread.candidate_id == candidate.id, EmailThread.channel == "whatsapp")
              .order_by(EmailThread.last_message_at.desc()).first())
    if thread is None:
        thread = email_threads.open_thread(db, candidate.id, SUBJECT)
        thread.channel = "whatsapp"
        db.flush()
    return thread


def last_inbound_at(db: Session, thread: EmailThread) -> datetime | None:
    row = (db.query(CandidateEmail.received_at).filter(CandidateEmail.thread_id == thread.id, CandidateEmail.direction == "inbound")
           .order_by(CandidateEmail.received_at.desc()).first())
    return row[0].replace(tzinfo=None) if row and row[0] else None


def within_window(db: Session, thread: EmailThread, now: datetime | None = None) -> bool:
    last = last_inbound_at(db, thread)
    return last is not None and (now or datetime.utcnow()) - last <= WINDOW


def send(db: Session, candidate: Candidate, text: str, *, kind: str, author: str = "system", thread: EmailThread | None = None,
         registration=None, confidence: float | None = None, sources: str | None = None, note: str | None = None,
         language: str = "fr", dry_run: bool = False) -> CandidateEmail | None:
    """Send (or keep for the admin to send) one WhatsApp message. None when the candidate has no usable number."""
    phone = whatsapp.normalise(getattr(candidate, "phone", None))
    if phone is None:
        return None
    thread = thread or thread_for(db, candidate)
    body = whatsapp.as_message(text)
    problem = None
    if dry_run:
        status = "test"
    elif whatsapp.configured():
        problem = whatsapp.send(phone, body, free=within_window(db, thread))
        status = "failed" if problem else "sent"
    else:
        status = "to_send"
    message = email_threads.record_message(
        db, thread, channel="whatsapp", direction="outbound", author=author, message_id=f"wa-out:{uuid.uuid4()}",
        sender="WhatsApp", recipient=phone, subject=SUBJECT, body=body, language=language, intent=kind, status=status,
        confidence=confidence, sources=sources,
        registration_id=getattr(registration, "id", None) or thread.registration_id,
        analysis=(f"Not delivered on WhatsApp: {problem}" if problem
                  else "Waiting for you: press “Open WhatsApp”, send it there, then “Sent”." if status == "to_send"
                  else "Test: not sent." if dry_run else note),
    )
    if status == "to_send":
        thread.status = "sent" if not thread.inbound_count else thread.status
    if not dry_run:
        db.add(Activity(candidate_id=candidate.id,
                        event_type=f"{(kind or 'message').upper()}_WHATSAPP_{ {'sent': 'SENT', 'failed': 'FAILED'}.get(status, 'WAITING') }",
                        details=(problem or body)[:300]))
    if problem:
        db.add(Notification(recipient_role="admin", category="EMAIL_FAILED", title="WhatsApp message not delivered",
                            message=f"{candidate.full_name or phone}: {problem}"[:500], action_url=f"/inbox?thread={thread.id}",
                            candidate_id=candidate.id))
    db.flush()
    return message


def deliver_files_by_email(db: Session, candidate: Candidate, files, language: str = "fr") -> bool:
    """WhatsApp carries the words; a document the answer carries (a program's PDF) goes by email."""
    if not files or not candidate.email:
        return False
    from app.services.email_assistant import compose_reply

    text = ("Here is the document you asked for on WhatsApp." if language == "en"
            else "Voici le document que vous avez demandé sur WhatsApp.")
    plain, html_body = compose_reply(db, candidate, text, language=language)
    message = email_threads.deliver(db, candidate, "Your document" if language == "en" else "Votre document", plain, html_body,
                                    kind="reply", author="system", files=files, language=language)
    return bool(message and message.status == "sent")


def sms_thread_for(db: Session, candidate: Candidate) -> EmailThread:
    """The candidate's SMS discussion (one per candidate), opened the first time."""
    thread = (db.query(EmailThread).filter(EmailThread.candidate_id == candidate.id, EmailThread.channel == "sms")
              .order_by(EmailThread.last_message_at.desc()).first())
    if thread is None:
        thread = email_threads.open_thread(db, candidate.id, "SMS")
        thread.channel = "sms"
        db.flush()
    return thread


def send_sms(db: Session, candidate: Candidate, text: str, *, kind: str, author: str = "system", registration=None,
             thread: EmailThread | None = None, dry_run: bool = False, note: str | None = None) -> CandidateEmail | None:
    """An SMS for the candidate: from the Android phone connected in Settings (it goes by itself), otherwise it waits
    for the admin with a button that opens the messages of their phone, written. None without a usable number."""
    from app.services import sms

    phone = whatsapp.normalise(getattr(candidate, "phone", None))
    if phone is None:
        return None
    thread = thread or sms_thread_for(db, candidate)
    body = whatsapp.as_message(text)
    problem = None
    if dry_run:
        status = "test"
    elif sms.configured():
        problem = sms.send(phone, body)
        status = "failed" if problem else "sent"
    else:
        status = "to_send"
    message = email_threads.record_message(
        db, thread, channel="sms", direction="outbound", author=author, message_id=f"sms-out:{uuid.uuid4()}",
        sender="SMS", recipient=phone, subject="SMS", body=body, intent=kind, status=status,
        registration_id=getattr(registration, "id", None) or thread.registration_id,
        analysis=(f"Not delivered by SMS: {problem}" if problem
                  else "Waiting for you: press “Open messages”, send it from your phone, then “Sent”." if status == "to_send"
                  else "Test: not sent." if dry_run else note),
    )
    if not dry_run:
        db.add(Activity(candidate_id=candidate.id, event_type=f"{(kind or 'message').upper()}_SMS_{ {'sent': 'SENT', 'failed': 'FAILED'}.get(status, 'WAITING') }",
                        details=(problem or body)[:300]))
    if problem:
        db.add(Notification(recipient_role="admin", category="EMAIL_FAILED", title="SMS not delivered",
                            message=f"{candidate.full_name or phone}: {problem}"[:500], action_url=f"/inbox?thread={thread.id}",
                            candidate_id=candidate.id))
    db.flush()
    return message


def receive_sms(db: Session, *, phone: str, text: str, sms_id: str = "", when=None) -> CandidateEmail | None:
    """An SMS someone sent to the Android phone: kept in the candidate's SMS discussion for the assistant to read. A
    professor's or a team member's goes into the team chat; a stranger's is told to the admin. None when not a candidate."""
    key = f"sms:{sms_id}" if sms_id else None
    if key and db.query(CandidateEmail.id).filter(CandidateEmail.message_id == key).first() is not None:
        return None
    body = (text or "").strip() or "(an empty SMS)"
    digits = "".join(char for char in str(phone) if char.isdigit())
    # The phone writes local numbers as they came ("98765432"): those are Tunisian; others carry their country code.
    phone = (whatsapp.normalise(digits) if len(digits) == 8 else None) or ("+" + digits if digits else str(phone))
    candidate = find_candidate(db, phone)
    if candidate is None:
        from app.services import team_messages

        found = team_messages.find_by_phone(db, phone)
        if found is not None:
            team_messages.receive(db, found["key"], body, "sms", message_id=key)
            return None
        number = whatsapp.normalise("+" + str(phone).lstrip("+")) if not str(phone).startswith("+") else whatsapp.normalise(phone)
        from app.services import registration_form           # asking how to register: the form's link goes back

        answered = (__import__("app.services.prospects", fromlist=["answer_stranger"]).answer_stranger(db, channel="sms", to=number or str(phone), text=body)
                    or registration_form.answer_stranger(db, channel="sms", to=number or str(phone), text=body))
        if answered is not None:
            registration_form.tell_admin(db, channel="sms", who=number or str(phone), number=number, text=body, answered=answered)
            db.flush()
            return None
        db.add(Notification(recipient_role="admin", category="CANDIDATE_EMAIL", title="SMS from a number that is not a candidate",
                            message=f"{number or phone}: {body}"[:500], action_url="/inbox"))
        db.flush()
        return None
    thread = sms_thread_for(db, candidate)
    message = email_threads.record_message(
        db, thread, channel="sms", direction="inbound", author="candidate", message_id=key or f"sms:{uuid.uuid4()}",
        sender=whatsapp.normalise(phone if str(phone).startswith("+") else "+" + str(phone).lstrip("+")) or str(phone),
        recipient="SMS", subject="SMS", body=body[:4000], status="processing", received_at=email_threads.utcnow(),
    )
    db.add(Activity(candidate_id=candidate.id, event_type="SMS_RECEIVED", details=body[:300]))
    db.flush()
    return message


def outbox(db: Session) -> list[CandidateEmail]:
    """The WhatsApp messages and SMS waiting for the admin to send them from the phone, oldest first."""
    return (db.query(CandidateEmail).filter(CandidateEmail.channel.in_(("whatsapp", "sms")), CandidateEmail.status == "to_send")
            .order_by(CandidateEmail.received_at.asc()).all())


def link_of(message: CandidateEmail) -> str | None:
    """Opens WhatsApp (or the phone's messages, for an SMS) on the candidate's number with the message written."""
    if message.channel == "sms":
        from urllib.parse import quote

        number = whatsapp.normalise(message.recipient)
        return f"sms:{number}?body={quote(message.body or '')}" if number else None
    return whatsapp.link(message.recipient, message.body)


def mark_sent(db: Session, message: CandidateEmail, *, cancelled: bool = False) -> None:
    message.status = "cancelled" if cancelled else "sent"
    message.analysis = "Not sent: you removed it." if cancelled else "Sent by you from WhatsApp."
    thread = db.get(EmailThread, message.thread_id)
    if thread is not None and not cancelled:
        thread.last_status = "sent"
        thread.status = "answered" if thread.inbound_count else "sent"
        thread.needs_admin = False
    db.flush()


def find_candidate(db: Session, phone: str) -> Candidate | None:
    """The candidate whose number this is, written whichever way."""
    number = whatsapp.normalise(phone if str(phone).startswith("+") else "+" + str(phone).lstrip("+"))
    if number is None:
        return None
    for candidate in db.query(Candidate).filter(Candidate.phone.isnot(None)).all():
        if whatsapp.normalise(candidate.phone) == number:
            return candidate
    return None


def receive(db: Session, *, phone: str, text: str, wa_id: str, name: str = "", when: datetime | None = None,
            media: dict | None = None) -> CandidateEmail | None:
    """A message a candidate sent on WhatsApp: kept in their discussion. None when it was already kept, or the
    number is nobody's (then the admin is told, with a button to answer on WhatsApp)."""
    key = f"wa:{wa_id}"
    if db.query(CandidateEmail.id).filter(CandidateEmail.message_id == key).first() is not None:
        return None
    candidate = find_candidate(db, phone)
    body = (text or "").strip() or "(a message without text: a photo, a voice note or a file)"
    if candidate is None:
        from app.services import team_messages               # a professor or a team member: into their conversation

        if team_messages.find_by_phone(db, phone) is not None:
            team_messages.from_whatsapp(db, phone=phone, text=text, wa_id=wa_id, name=name)
            return None
        number = whatsapp.normalise("+" + str(phone).lstrip("+"))
        from app.services import registration_form           # asking how to register: the form's link goes back

        answered = (__import__("app.services.prospects", fromlist=["answer_stranger"]).answer_stranger(db, channel="whatsapp", to=number or str(phone), text=body)
                    or registration_form.answer_stranger(db, channel="whatsapp", to=number or str(phone), text=body))
        if answered is not None:
            registration_form.tell_admin(db, channel="whatsapp", who=name or number or str(phone), number=number, text=body,
                                         answered=answered)
            db.flush()
            return None
        db.add(Notification(
            recipient_role="admin", category="CANDIDATE_EMAIL", title="WhatsApp from a number that is not a candidate",
            message=f"{name or number or phone}: {body}"[:500], action_url=whatsapp.link(number) or "/inbox",
        ))
        db.flush()
        return None
    thread = thread_for(db, candidate)
    message = email_threads.record_message(
        db, thread, channel="whatsapp", direction="inbound", author="candidate", message_id=key,
        sender=whatsapp.normalise("+" + str(phone).lstrip("+")) or str(phone), recipient="WhatsApp", subject=SUBJECT,
        # Kept at the moment it arrives (Meta's own time can differ by a few seconds): the order of the discussion stays true.
        body=body[:4000], status="processing", received_at=email_threads.utcnow(),     # the assistant reads it next
    )
    if media:
        fetched = whatsapp.download_media(media.get("id"))
        if fetched is not None:
            from app.services import email_files

            kept = email_files.store_inbound(db, [(media.get("filename") or "whatsapp-file", fetched[1] or media.get("mime"), fetched[0])])
            if kept:
                message.attachments = json.dumps([email_files.describe(file) for file in kept], ensure_ascii=False)
                if body.startswith("(a message without text"):
                    message.body = "(Pièce jointe envoyée sur WhatsApp)"
    db.add(Activity(candidate_id=candidate.id, event_type="WHATSAPP_RECEIVED", details=body[:300]))
    db.flush()
    return message


def from_webhook(payload: dict) -> list[dict]:
    """The text messages of one webhook call from Meta: [{"phone", "name", "text", "wa_id", "when"}]."""
    found = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {contact.get("wa_id"): (contact.get("profile") or {}).get("name", "") for contact in value.get("contacts") or []}
            for item in value.get("messages") or []:
                kind = item.get("type")
                if kind == "text":
                    text = (item.get("text") or {}).get("body", "")
                elif kind == "button":
                    text = (item.get("button") or {}).get("text", "")
                elif kind == "interactive":
                    reply = (item.get("interactive") or {})
                    text = (reply.get("button_reply") or reply.get("list_reply") or {}).get("title", "")
                elif kind in ("image", "document"):
                    # A photo of a receipt, a PDF: its caption is the text, the file is fetched from Meta.
                    part = item.get(kind) or {}
                    text = part.get("caption", "")
                else:
                    text = ""
                media = None
                if kind in ("image", "document") and (item.get(kind) or {}).get("id"):
                    part = item[kind]
                    media = {"id": part["id"], "mime": part.get("mime_type") or "", "filename": part.get("filename") or f"whatsapp-{kind}"}
                try:
                    when = datetime.utcfromtimestamp(int(item.get("timestamp")))
                except (TypeError, ValueError):
                    when = None
                found.append({"phone": item.get("from", ""), "name": names.get(item.get("from"), ""), "text": text,
                              "wa_id": item.get("id") or str(uuid.uuid4()), "when": when, "media": media})
    return found

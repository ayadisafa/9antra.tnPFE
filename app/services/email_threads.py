"""Discussions with candidates: threads, their messages, and delivery.

Every email the system sends or receives is one `CandidateEmail` row inside
an `EmailThread`. Outgoing emails carry a Message-ID, so a candidate's reply
is matched back to the discussion it belongs to.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import SessionLocal

from app.models.activity import Activity
from app.models.candidate import Candidate
from app.models.candidate_email import CandidateEmail
from app.models.email_thread import EmailThread
from app.models.notification import Notification
from app.services import email_files
from app.services.background import run_in_background
from app.services.email_layout import html_to_text
from app.services.email_service import (
    EmailConfigurationError,
    EmailDeliveryError,
    new_message_id,
    send_email,
    set_journal,
)

logger = logging.getLogger(__name__)

PLATFORM = "9antra.tn"
SUBJECT_MATCH_DAYS = 30

_SUBJECT_PREFIX = re.compile(r"^\s*(?:re|fw|fwd|tr|rép|rep|réf|ref|aw|sv)\s*(?:\[\d+\])?\s*:\s*", re.IGNORECASE)


def utcnow() -> datetime:
    """Naive UTC, the form SQLite gives back, so stored and new values compare."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def smtp_configured() -> bool:
    return bool((os.getenv("SMTP_EMAIL") or "").strip() and os.getenv("SMTP_PASSWORD"))


def flag_smtp_not_configured(db: Session) -> None:
    """Keep one standing admin alert while email cannot be sent, instead of one per message."""
    db.flush()
    already_flagged = (
        db.query(Notification.id)
        .filter(Notification.category == "EMAIL_CONFIG", Notification.is_read.is_(False))
        .first()
    )
    if not already_flagged:
        db.add(Notification(
            recipient_role="admin", category="EMAIL_CONFIG",
            title="Emails are not being sent",
            message="SMTP is not configured, so candidates are not receiving their class emails or reminders. Open Settings to fix it.",
            action_url="/settings#email",
        ))
        db.flush()


def normalize_subject(subject: str | None) -> str:
    """'RE: Fwd:  Question prix' -> 'Question prix'."""
    value = " ".join((subject or "").split())
    while True:
        stripped = _SUBJECT_PREFIX.sub("", value, count=1)
        if stripped == value:
            return value
        value = stripped


def snippet(text: str | None, limit: int = 140) -> str:
    value = " ".join((text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def message_files(message: CandidateEmail) -> list[dict]:
    """The photos and documents that came with a stored email."""
    try:
        items = json.loads(message.attachments) if message.attachments else []
    except ValueError:
        return []
    return [item for item in items if isinstance(item, dict) and item.get("id")] if isinstance(items, list) else []


# ------------------------------------------------------------------
# Threads
# ------------------------------------------------------------------

def open_thread(db: Session, candidate_id: str, subject: str | None,
                registration_id: str | None = None) -> EmailThread:
    thread = EmailThread(
        candidate_id=candidate_id,
        registration_id=registration_id,
        subject=normalize_subject(subject) or "Sans objet",
        status="sent",
        last_message_at=utcnow(),
        created_at=utcnow(),
    )
    db.add(thread)
    db.flush()
    return thread


def find_thread(db: Session, candidate_id: str, *, in_reply_to: str | None = None,
                references: list[str] | None = None, subject: str | None = None) -> EmailThread | None:
    """The discussion an incoming email belongs to, if it continues one."""
    message_ids = [value for value in [in_reply_to, *(references or [])] if value]
    if message_ids:
        parent = (
            db.query(CandidateEmail)
            .filter(
                CandidateEmail.candidate_id == candidate_id,
                CandidateEmail.message_id.in_(message_ids),
                CandidateEmail.thread_id.isnot(None),
            )
            .order_by(CandidateEmail.received_at.desc())
            .first()
        )
        if parent:
            thread = db.get(EmailThread, parent.thread_id)
            if thread:
                return thread

    normalized = normalize_subject(subject)
    if not normalized:
        return None
    recent = utcnow() - timedelta(days=SUBJECT_MATCH_DAYS)
    candidates = (
        db.query(EmailThread)
        .filter(EmailThread.candidate_id == candidate_id, EmailThread.last_message_at >= recent)
        .order_by(EmailThread.last_message_at.desc())
        .all()
    )
    for thread in candidates:
        if (thread.subject or "").casefold() == normalized.casefold():
            return thread
    return None


def _apply_message(thread: EmailThread, message: CandidateEmail) -> None:
    thread.message_count = (thread.message_count or 0) + 1
    thread.last_message_at = message.received_at or utcnow()
    thread.last_direction = message.direction
    thread.last_author = message.author
    thread.last_status = message.status
    thread.last_snippet = snippet(message.body)
    if message.direction == "inbound":
        thread.inbound_count = (thread.inbound_count or 0) + 1
        thread.unread = True
        thread.status = "processing"
        thread.closed_at = None
    elif message.status in ("sent", "test"):
        thread.status = "answered" if thread.inbound_count else "sent"
        thread.needs_admin = False


def record_message(db: Session, thread: EmailThread, **fields) -> CandidateEmail:
    """Add one message to a discussion and update the discussion's summary."""
    fields.setdefault("received_at", utcnow())
    fields.setdefault("registration_id", thread.registration_id)
    message = CandidateEmail(candidate_id=thread.candidate_id, thread_id=thread.id, **fields)
    db.add(message)
    _apply_message(thread, message)
    db.flush()
    return message


def rebuild_thread(db: Session, thread: EmailThread) -> None:
    """Recompute a discussion's counters from its messages."""
    messages = (
        db.query(CandidateEmail)
        .filter(CandidateEmail.thread_id == thread.id)
        .order_by(CandidateEmail.received_at.asc())
        .all()
    )
    thread.message_count = len(messages)
    thread.inbound_count = sum(1 for message in messages if message.direction == "inbound")
    if not messages:
        return
    last = messages[-1]
    thread.last_message_at = last.received_at or thread.last_message_at
    thread.last_direction = last.direction
    thread.last_author = last.author
    thread.last_status = last.status
    thread.last_snippet = snippet(last.body)
    pending = [message for message in messages if message.direction == "inbound" and message.requires_admin]
    thread.needs_admin = bool(pending)
    if thread.status != "closed":
        if pending:
            thread.status = "needs_admin"
        elif last.direction == "inbound":
            thread.status = "received"
        else:
            thread.status = "answered" if thread.inbound_count else "sent"


def backfill_threads(db: Session) -> int:
    """Give a discussion to every stored email that predates discussions."""
    known = {row[0] for row in db.query(EmailThread.id).all()}
    orphans = (
        db.query(CandidateEmail)
        .filter(CandidateEmail.thread_id.is_(None) | CandidateEmail.thread_id.notin_(known or [""]))
        .order_by(CandidateEmail.received_at.asc())
        .all()
    )
    if not orphans:
        return 0

    groups: dict[tuple, EmailThread] = {}
    for message in orphans:
        key = (message.candidate_id, message.thread_id or normalize_subject(message.subject).casefold())
        thread = groups.get(key)
        if thread is None:
            thread = EmailThread(
                candidate_id=message.candidate_id,
                registration_id=message.registration_id,
                subject=normalize_subject(message.subject) or "Sans objet",
                created_at=message.received_at or utcnow(),
                last_message_at=message.received_at or utcnow(),
            )
            db.add(thread)
            db.flush()
            groups[key] = thread
        message.thread_id = thread.id
        if not message.author:
            message.author = (
                "candidate" if message.direction == "inbound"
                else "admin" if (message.sender or "").lower() == "admin"
                else "system"
            )
    db.flush()
    for thread in groups.values():
        rebuild_thread(db, thread)
    db.commit()
    return len(orphans)


# ------------------------------------------------------------------
# Delivery
# ------------------------------------------------------------------

def _references(db: Session, thread: EmailThread) -> tuple[str | None, str | None]:
    """(In-Reply-To, References) headers continuing this discussion."""
    ids = [
        row[0] for row in (
            db.query(CandidateEmail.message_id)
            .filter(CandidateEmail.thread_id == thread.id, CandidateEmail.message_id.isnot(None))
            .order_by(CandidateEmail.received_at.desc())
            .limit(8)
            .all()
        )
    ]
    if not ids:
        return None, None
    return ids[0], " ".join(reversed(ids))


def _attempt(recipient: str, subject: str, text: str, html_body: str,
             message_id: str, in_reply_to: str | None, references: str | None,
             files=None, inline=None) -> tuple[str | None, bool]:
    """Try to send. Returns (error or None, error is a configuration problem)."""
    parts, unreadable = email_files.as_mime(list(files or []), list(inline or []))
    if unreadable:
        return f"the attached file “{unreadable}” could not be read", False
    try:
        send_email(
            to_email=recipient, subject=subject, body=html_body, is_html=True,
            text_body=text, message_id=message_id, in_reply_to=in_reply_to, references=references,
            journal=False, attachments=parts or None,
        )
    except EmailConfigurationError as exc:
        return str(exc), True
    except (EmailDeliveryError, ValueError) as exc:
        return str(exc), False
    return None, False


def deliver(
    db: Session,
    candidate: Candidate,
    subject: str,
    text: str,
    html_body: str,
    *,
    kind: str,
    author: str = "system",
    thread: EmailThread | None = None,
    registration=None,
    confidence: float | None = None,
    sources: str | None = None,
    note: str | None = None,
    language: str = "fr",
    dry_run: bool = False,
    files=None,
    inline=None,
) -> CandidateEmail | None:
    """Send one email to a candidate and record it in its discussion. Never raises.

    The message is stored whether or not delivery worked, so the admin can
    always see what was sent, what failed, and why. `files` are attached;
    `inline` are the photos the HTML shows (see `email_files.cid_src`).
    """
    if not candidate or not candidate.email:
        logger.warning("No email address for candidate %s.", getattr(candidate, "id", None))
        return None

    if thread is None:
        thread = open_thread(db, candidate.id, subject, getattr(registration, "id", None))
    in_reply_to, references = _references(db, thread)
    message_id = new_message_id()
    files, inline = list(files or []), list(inline or [])

    error, config_error = (None, False)
    if not dry_run:
        error, config_error = _attempt(
            candidate.email, subject, text, html_body, message_id, in_reply_to, references,
            files, inline,
        )
    status = "test" if dry_run else "sent" if error is None else "failed"
    carried = (
        [email_files.describe(file, inline=True) for file in inline]
        + [email_files.describe(file) for file in files]
    )

    message = record_message(
        db, thread,
        registration_id=getattr(registration, "id", None) or thread.registration_id,
        direction="outbound",
        author=author,
        message_id=message_id,
        in_reply_to=in_reply_to,
        sender=os.getenv("SMTP_EMAIL") or PLATFORM,
        recipient=candidate.email,
        subject=subject,
        body=text,
        html_body=html_body,
        language=language,
        intent=kind,
        status=status,
        confidence=confidence,
        sources=sources,
        attachments=json.dumps(carried, ensure_ascii=False) if carried else None,
        analysis=(
            f"Not delivered: {error}" if error
            else "Test: this email was not sent to the candidate." if dry_run
            else note
        ),
    )
    if dry_run:
        return message

    sent = error is None
    db.add(Activity(
        candidate_id=candidate.id,
        event_type=f"{kind.upper()}_EMAIL_{'SENT' if sent else 'FAILED'}",
        details=subject if sent else f"{subject} — not delivered: {error}",
    ))
    if not sent:
        logger.warning("Email %r to %s was not delivered: %s", kind, candidate.email, error)
        if config_error:
            flag_smtp_not_configured(db)
        else:
            db.add(Notification(
                recipient_role="admin", category="EMAIL_FAILED",
                title="Email not delivered",
                message=f"'{subject}' could not be delivered to {candidate.full_name or candidate.email}: {error}",
                action_url=f"/inbox?thread={thread.id}", candidate_id=candidate.id,
                registration_id=message.registration_id,
            ))
    return message


def resend(db: Session, message: CandidateEmail) -> bool:
    """Try again to deliver an email that failed."""
    if message.direction != "outbound" or message.status != "failed":
        return False
    thread = db.get(EmailThread, message.thread_id) if message.thread_id else None
    carried = message_files(message)
    stored = {file.id: file for file in email_files.load(db, carried)}
    missing = next((item["name"] for item in carried if item["id"] not in stored), None)
    if missing:
        message.analysis = f"Not delivered: the attached file “{missing}” no longer exists."
        return False
    error, config_error = _attempt(
        message.recipient, message.subject or "9antra", message.body,
        message.html_body or message.body, message.message_id or new_message_id(),
        message.in_reply_to, message.in_reply_to,
        [stored[item["id"]] for item in carried if not item.get("inline")],
        [stored[item["id"]] for item in carried if item.get("inline")],
    )
    if error:
        message.analysis = f"Not delivered: {error}"
        if config_error:
            flag_smtp_not_configured(db)
        return False
    message.status = "sent"
    message.analysis = "Delivered on a second attempt."
    message.replied_at = utcnow()
    if thread:
        rebuild_thread(db, thread)
        # The reply this email carried has now reached the candidate.
        pending = (
            db.query(CandidateEmail)
            .filter(
                CandidateEmail.thread_id == thread.id,
                CandidateEmail.direction == "inbound",
                CandidateEmail.requires_admin.is_(True),
                CandidateEmail.received_at <= message.received_at,
            )
            .all()
        )
        if message.intent == "reply":
            for inbound in pending:
                inbound.requires_admin = False
                inbound.status = "replied"
                inbound.replied_at = utcnow()
            rebuild_thread(db, thread)
    db.add(Activity(
        candidate_id=message.candidate_id,
        event_type=f"{(message.intent or 'email').upper()}_EMAIL_SENT",
        details=message.subject,
    ))
    return True


# ------------------------------------------------------------------
# Emails sent by older code paths
# ------------------------------------------------------------------

def _journal_task(fields: dict) -> None:
    """Record an email that was sent outside `deliver` in the candidate's discussions."""
    for _ in range(3):
        db = SessionLocal()
        try:
            candidate = db.query(Candidate).filter(Candidate.email.ilike(fields["recipient"])).first()
            if candidate is None:
                return
            subject = fields["subject"]
            lowered = subject.lower()
            kind = (
                "payment" if re.search(r"paiement|payment|relance", lowered)
                else "quiz" if "quiz" in lowered
                else "notice"
            )
            text = fields.get("text_body") or (
                html_to_text(fields["body"]) if fields["is_html"] else fields["body"]
            )
            record_message(
                db, open_thread(db, candidate.id, subject),
                direction="outbound", author="system", message_id=fields.get("message_id"),
                sender=os.getenv("SMTP_EMAIL") or PLATFORM, recipient=candidate.email,
                subject=subject, body=text, html_body=fields["body"] if fields["is_html"] else None,
                language="fr", intent=kind, status="sent",
            )
            db.commit()
            return
        except OperationalError:
            # The sender may still hold its own transaction; try again shortly.
            db.rollback()
            time.sleep(2)
        except Exception:
            db.rollback()
            logger.exception("Could not record the email to %s.", fields.get("recipient"))
            return
        finally:
            db.close()


def journal_external(**fields) -> None:
    run_in_background(_journal_task, fields)


set_journal(journal_external)

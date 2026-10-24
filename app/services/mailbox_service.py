"""Reads the 9antra mailbox (IMAP) and hands candidates' emails to the assistant.

The mailbox is opened read-only: nothing is marked as read, moved or deleted,
so the admin's own mail client is not affected. Progress is tracked by message
UID, and tracking starts from the moment the mailbox is first connected:
older emails are never imported or answered.

Configuration (.env). By default the mailbox is the SMTP account:
    IMAP_HOST      default: SMTP_HOST with "smtp." replaced by "imap."
    IMAP_PORT      default: 993
    IMAP_EMAIL     default: SMTP_EMAIL
    IMAP_PASSWORD  default: SMTP_PASSWORD
    IMAP_FOLDER    default: INBOX
"""
from __future__ import annotations

import email
import imaplib
import logging
import os
import re
import threading
from email import policy
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from sqlalchemy.orm import Session

from app.models.email_thread import MailboxState
from app.services import email_assistant, email_files
from app.services.email_layout import html_to_text
from app.services.email_threads import utcnow

logger = logging.getLogger(__name__)

MAX_PER_RUN = 25
_poll_lock = threading.Lock()

_QUOTE_MARKERS = [
    re.compile(r"^\s*(le|on)\b.{0,200}\b(a écrit|wrote)\s*:?\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*(original message|message d'origine|message original|forwarded message)\s*-{2,}", re.IGNORECASE),
    re.compile(r"^\s*(de|from)\s*:.+", re.IGNORECASE),
    re.compile(r"^\s*_{10,}\s*$"),
    re.compile(r"^\s*(envoyé depuis|envoyé de mon|sent from my)\b", re.IGNORECASE),
]
_WRAPPED_QUOTE = re.compile(r"\n[ \t]*(?:le|on)\b[^\n]{0,200}\n[^\n]{0,120}(?:a écrit|wrote)[ \t]*:", re.IGNORECASE)
_MESSAGE_ID = re.compile(r"<[^<>\s]+>")


def config() -> dict:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    return {
        "host": os.getenv("IMAP_HOST") or re.sub(r"^smtp\.", "imap.", smtp_host),
        "port": int(os.getenv("IMAP_PORT", "993")),
        "email": (os.getenv("IMAP_EMAIL") or os.getenv("SMTP_EMAIL") or "").strip(),
        "password": os.getenv("IMAP_PASSWORD") or os.getenv("SMTP_PASSWORD") or "",
        "folder": os.getenv("IMAP_FOLDER", "INBOX"),
    }


def configured() -> bool:
    settings = config()
    return bool(settings["email"] and settings["password"])


def get_state(db: Session) -> MailboxState:
    state = db.get(MailboxState, "default")
    if state is None:
        state = MailboxState(id="default", last_uid=0, imported_count=0)
        db.add(state)
        db.flush()
    return state


# ------------------------------------------------------------------
# Parsing
# ------------------------------------------------------------------

def strip_quoted(text: str) -> str:
    """Keep what the candidate wrote; drop the quoted earlier emails below it."""
    text = text.replace("\r\n", "\n")
    # Mail clients sometimes wrap the "On ... wrote:" line over two lines.
    wrapped = _WRAPPED_QUOTE.search(text)
    if wrapped and wrapped.start() > 0:
        text = text[:wrapped.start()]
    kept = []
    for line in text.split("\n"):
        if any(marker.match(line) for marker in _QUOTE_MARKERS):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line.rstrip())
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return cleaned or text.strip()


def _body_of(message) -> str:
    part = message.get_body(preferencelist=("plain",))
    if part is not None:
        try:
            return part.get_content()
        except (LookupError, ValueError):
            payload = part.get_payload(decode=True) or b""
            return payload.decode("utf-8", errors="replace")
    part = message.get_body(preferencelist=("html",))
    if part is not None:
        try:
            return html_to_text(part.get_content())
        except (LookupError, ValueError):
            payload = part.get_payload(decode=True) or b""
            return html_to_text(payload.decode("utf-8", errors="replace"))
    return ""


def _attachments_of(message) -> list[tuple[str, str, bytes]]:
    """(filename, content type, content) of the files sent with an email.

    Pictures that are part of the message's own layout, like a logo in a
    signature, are not attachments and are left out.
    """
    found = []
    try:
        parts = list(message.iter_attachments())
    except Exception:
        return found
    for part in parts:
        if part.is_multipart() or len(found) >= email_files.MAX_ATTACHMENTS:
            continue
        try:
            data = part.get_payload(decode=True)
        except Exception:
            data = None
        if not data or len(data) > email_files.MAX_FILE_BYTES:
            continue
        found.append((part.get_filename() or "piece-jointe", part.get_content_type(), data))
    return found


def parse_message(raw: bytes) -> dict:
    """The fields the assistant needs from one raw email."""
    message = email.message_from_bytes(raw, policy=policy.default)
    sender = parseaddr(str(message.get("From", "")))[1].lower()
    received_at = None
    try:
        received_at = parsedate_to_datetime(str(message.get("Date", "")))
    except (TypeError, ValueError):
        pass
    auto_submitted = str(message.get("Auto-Submitted", "no")).strip().lower()
    precedence = str(message.get("Precedence", "")).strip().lower()
    automatic = (
        auto_submitted not in ("", "no")
        or precedence in ("bulk", "junk", "list", "auto_reply")
        or message.get("X-Autoreply") is not None
        or message.get("X-Autorespond") is not None
        or message.get("List-Unsubscribe") is not None
        or sender.startswith(("mailer-daemon@", "postmaster@", "no-reply@", "noreply@"))
    )
    in_reply_to = _MESSAGE_ID.search(str(message.get("In-Reply-To", "")))
    return {
        "sender": sender,
        "recipients": [address.lower() for _, address in getaddresses([str(message.get("To", ""))])],
        "subject": " ".join(str(message.get("Subject", "")).split()),
        "body": strip_quoted(_body_of(message)),
        "message_id": (_MESSAGE_ID.search(str(message.get("Message-ID", ""))) or [None])[0],
        "in_reply_to": in_reply_to.group(0) if in_reply_to else None,
        "references": _MESSAGE_ID.findall(str(message.get("References", ""))),
        "received_at": received_at,
        "automatic": automatic,
        "attachments": _attachments_of(message),
    }


# ------------------------------------------------------------------
# Polling
# ------------------------------------------------------------------

def _connect(settings: dict):
    connection = imaplib.IMAP4_SSL(settings["host"], settings["port"], timeout=30)
    connection.login(settings["email"], settings["password"])
    return connection


def _uidvalidity(connection, folder: str) -> str | None:
    status, data = connection.status(folder, "(UIDVALIDITY UIDNEXT)")
    if status != "OK" or not data or not data[0]:
        return None
    match = re.search(rb"UIDVALIDITY (\d+)", data[0])
    return match.group(1).decode() if match else None


def poll(db: Session) -> dict:
    """Import the emails that arrived since the last check. Never raises."""
    if not configured():
        return {"status": "not_configured", "imported": 0}
    if not _poll_lock.acquire(blocking=False):
        return {"status": "busy", "imported": 0}

    settings = config()
    state = get_state(db)
    imported = ignored = 0
    connection = None
    try:
        from app.database import let_others_write

        let_others_write()                  # the mailbox is far: what is changed so far is saved before reaching it
        connection = _connect(settings)
        validity = _uidvalidity(connection, settings["folder"])
        status, _ = connection.select(settings["folder"], readonly=True)
        if status != "OK":
            raise RuntimeError(f"Folder {settings['folder']!r} could not be opened.")

        status, data = connection.uid("search", None, "ALL")
        uids = sorted(int(value) for value in (data[0].split() if status == "OK" and data and data[0] else []))
        first_connection = state.uidvalidity is None
        if first_connection or (validity and state.uidvalidity != validity):
            # Start from now: emails already in the mailbox are left alone.
            state.uidvalidity = validity or "unknown"
            state.last_uid = uids[-1] if uids else 0
            uids = []

        new_uids = [uid for uid in uids if uid > (state.last_uid or 0)][:MAX_PER_RUN]
        own_address = settings["email"].lower()
        for uid in new_uids:
            let_others_write()              # each email imported is saved before the next is fetched
            status, parts = connection.uid("fetch", str(uid), "(BODY.PEEK[])")
            raw = next((part[1] for part in parts or [] if isinstance(part, tuple) and len(part) > 1), None)
            state.last_uid = uid
            if status != "OK" or not raw:
                continue
            parsed = parse_message(raw)
            if parsed["sender"] == own_address:
                ignored += 1
                continue
            result = email_assistant.ingest_inbound(
                db, sender=parsed["sender"], subject=parsed["subject"], body=parsed["body"],
                message_id=parsed["message_id"], in_reply_to=parsed["in_reply_to"],
                references=parsed["references"], received_at=parsed["received_at"],
                automatic=parsed["automatic"], attachments=parsed["attachments"],
            )
            if result["status"] in ("received", "stored"):
                imported += 1
            else:
                ignored += 1
            state = get_state(db)

        state.last_checked_at = utcnow()
        state.last_success_at = utcnow()
        state.last_error = None
        state.imported_count = (state.imported_count or 0) + imported
        db.commit()
        return {"status": "ok", "imported": imported, "ignored": ignored, "first_connection": first_connection}
    except Exception as exc:
        db.rollback()
        logger.warning("Mailbox check failed: %s", exc)
        try:
            state = get_state(db)
            state.last_checked_at = utcnow()
            state.last_error = _explain(exc)
            db.commit()
        except Exception:
            db.rollback()
        return {"status": "error", "imported": imported, "error": _explain(exc)}
    finally:
        if connection is not None:
            try:
                connection.logout()
            except Exception:
                pass
        _poll_lock.release()


def _explain(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, imaplib.IMAP4.error) and re.search(r"auth|credential|login|password", text, re.IGNORECASE):
        return (
            "The mailbox refused the password. For Gmail, use an app password and make sure "
            "IMAP is enabled in the Gmail settings."
        )
    if isinstance(exc, OSError):
        return f"The mail server could not be reached ({text})."
    return text[:300] or exc.__class__.__name__

"""Notifications that close themselves.

Two kinds of notification reach the admin:

- what NEEDS them (an email to answer, a registration to follow, a proof to check, a professor who wrote…): it rings
  (the bell's count) until the thing is handled, wherever it was handled, and then closes by itself;
- what only tells them something happened (a class started, reminders went out, a candidate was placed): it never
  rings, shows quietly under the others, and closes by itself after INFO_DAYS.

Opening the page a notification leads to closes it too, and the same notification repeated (one registration told
five times) is kept once. tidy() does all of it; it runs with the reminders job and when a page shows the bell.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from sqlalchemy.orm import Session

from app.models.notification import Notification

INFO_DAYS = 3                    # a notification that only tells something closes by itself after this
EVERY = 60                       # seconds between two tidy-ups from the pages
# What needs the admin until it is handled.
NEEDS_YOU = {"EMAIL_QUESTION", "CANDIDATE_EMAIL", "NEW_REGISTRATION", "REGISTRATION_REQUEST", "ASSIGNMENT_PENDING",
             "PROGRAM_PROPOSAL", "PROGRAM_APPROVAL", "TEAM_MESSAGE", "EMAIL_CONFIG", "EMAIL_FAILED", "PAYMENT_PROOF", "BIAS_ALERT",
             "REVIEW_LOW"}
# What opening its page does not close: it waits for the thing to be done (someone not happy waits for a call).
STAYS_OPEN = {"REVIEW_LOW"}
# Words of a CANDIDATE_EMAIL notification that only reports what the system did by itself.
_REPORTS = ("corrected itself",)

_last = 0.0
_lock = threading.Lock()


def _query(url: str | None) -> tuple[str, dict]:
    parsed = urlparse(url or "")
    return parsed.path.rstrip("/") or "/", {key: values[0] for key, values in parse_qs(parsed.query).items()}


def needs_you(db: Session, notification: Notification) -> bool:
    """Whether this notification asks the admin to do something (it rings), or only tells them something."""
    category = notification.category or ""
    if category == "CANDIDATE_EMAIL" and any(word in (notification.title or "") for word in _REPORTS):
        return False
    if category == "CLASS":
        from app.services import notification_fixes

        return notification_fixes.fix_for(db, notification) is not None     # a class that lacks something
    return category in NEEDS_YOU


def settled(db: Session, notification: Notification) -> bool:
    """Whether what this notification asks for is done, wherever it was done."""
    from app.models.email_thread import EmailThread
    from app.models.registration import PaymentStatus, Registration, RegistrationStatus

    category = notification.category or ""
    _path, query = _query(notification.action_url)
    if category in ("EMAIL_QUESTION", "CANDIDATE_EMAIL"):
        if query.get("thread"):
            thread = db.get(EmailThread, query["thread"])
            return thread is None or not thread.needs_admin
        return db.query(EmailThread.id).filter(EmailThread.needs_admin.is_(True)).first() is None
    if category == "NEW_REGISTRATION":
        # Handled once it is paid, placed or cancelled, or once its payment reminder went out (the dashboard follows it).
        registration = db.get(Registration, notification.registration_id) if notification.registration_id else None
        return (registration is None or registration.status != RegistrationStatus.PENDING
                or registration.payment_status != PaymentStatus.UNPAID or registration.class_group_id is not None
                or bool(registration.payment_reminder_sent) or (registration.reminder_count or 0) > 0)
    if category in ("REGISTRATION_REQUEST", "ASSIGNMENT_PENDING", "CLASS"):
        from app.services import notification_fixes

        return notification_fixes.fix_for(db, notification) is None
    if category == "PROGRAM_PROPOSAL":
        from app.services import program_assistant

        return query.get("review") not in program_assistant.open_proposals(db)
    if category == "PROGRAM_APPROVAL":
        from app.models.program import Program

        return db.query(Program.id).filter(Program.is_approved.is_(False)).first() is None
    if category == "TEAM_MESSAGE":
        from app.models.team_message import TeamMessage

        unread = db.query(TeamMessage.id).filter(TeamMessage.direction == "in", TeamMessage.read_at.is_(None))
        if query.get("person"):
            unread = unread.filter(TeamMessage.person == query["person"])
        return unread.first() is None
    if category == "PAYMENT_PROOF":
        from app.models.payment_proof import PaymentProof

        proof = db.get(PaymentProof, query.get("proof") or "")
        return proof is None or proof.status in ("confirmed", "refused")
    if category == "EMAIL_CONFIG":
        from app.models.candidate_email import CandidateEmail

        since = _naive(notification.created_at)
        return db.query(CandidateEmail.id).filter(CandidateEmail.direction == "outbound", CandidateEmail.channel == "email",
                                                  CandidateEmail.status == "sent", CandidateEmail.received_at > since).first() is not None
    if category == "REVIEW_LOW":
        from app.services import program_feedback

        row = program_feedback.of_registration(db, notification.registration_id) if notification.registration_id else None
        return row is None or row.status != "received" or not program_feedback.is_low(row) or program_feedback.was_contacted(db, row)
    if category == "EMAIL_FAILED":
        from app.services import reach

        return not any(item["candidate_id"] == notification.candidate_id for item in reach.not_reached(db))
    return False


def _naive(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.utcnow()
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def _close(notification: Notification, how: str, now: datetime) -> None:
    notification.is_read = True
    notification.status = how                    # "settled" (done elsewhere), "seen" (its page was opened), "kept once", "told"
    notification.read_at = now


def tidy(db: Session, *, now: datetime | None = None, force: bool = False) -> int:
    """Closes what was handled, what only told something INFO_DAYS ago, and the same notification repeated.
    Returns how many closed. From the pages, at most once a minute."""
    global _last
    with _lock:
        if not force and time.monotonic() - _last < EVERY:
            return 0
        _last = time.monotonic()
    now = now or datetime.utcnow()
    closed = 0
    open_ones = (db.query(Notification).filter(Notification.recipient_role == "admin", Notification.is_read.is_(False))
                 .order_by(Notification.created_at.desc()).all())
    seen: set[tuple] = set()
    try:
        for notification in open_ones:
            same = (notification.category, notification.title, notification.action_url, notification.registration_id)
            if same in seen:
                _close(notification, "kept once", now)
                closed += 1
                continue
            seen.add(same)
            if needs_you(db, notification):
                if settled(db, notification):
                    _close(notification, "settled", now)
                    closed += 1
            elif notification.category != "WHATS_NEW" and _naive(notification.created_at) < now - timedelta(days=INFO_DAYS):
                _close(notification, "told", now)
                closed += 1
        if closed:
            db.commit()
    except Exception:
        db.rollback()
        return 0
    return closed


def seen_at(db: Session, path: str, query: dict) -> int:
    """The admin opened a page: the notifications leading exactly there are read."""
    closed = 0
    path = path.rstrip("/") or "/"
    if path == "/":
        return 0
    for notification in db.query(Notification).filter(Notification.recipient_role == "admin", Notification.is_read.is_(False),
                                                       Notification.action_url.like(f"{path}%")).all():
        if notification.category in STAYS_OPEN:
            continue
        target, wanted = _query(notification.action_url)
        if target == path and all(query.get(key) == value for key, value in wanted.items()):
            _close(notification, "seen", datetime.utcnow())
            closed += 1
    if closed:
        db.commit()
    return closed


def ringing(db: Session) -> int:
    """How many notifications need the admin now: the bell's count."""
    return sum(1 for notification in db.query(Notification).filter(Notification.recipient_role == "admin",
                                                                    Notification.is_read.is_(False)).all()
               if needs_you(db, notification))

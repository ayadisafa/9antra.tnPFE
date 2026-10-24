"""Payment reminders: one by itself, the next ones only when the admin decides.

The system writes once to a candidate who has not paid: a single email for
everything they owe, 24 hours after they registered. It never writes a
second time by itself. When three days pass with no payment and no answer,
the dashboard suggests sending the reminder again (see advisor), and the
admin reads the text, changes it if they want, and sends it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, joinedload

from app.models.activity import Activity
from app.models.candidate import Candidate
from app.models.candidate_email import CandidateEmail
from app.models.candidate_reminder import CandidateReminder
from app.models.registration import Registration, RegistrationStatus
from app.services import email_templates, email_threads
from app.services.email_templates import money

logger = logging.getLogger(__name__)

FIRST_AFTER = timedelta(hours=24)           # after the registration, unless the admin changed it (welcome.reminder_hours)


def welcome_hours(db: Session) -> int:
    from app.services import welcome

    return welcome.reminder_hours(db)


def first_after(db: Session) -> timedelta:
    from app.services import welcome

    return timedelta(hours=welcome.reminder_hours(db))
SUGGEST_AGAIN_AFTER = timedelta(days=3)     # of silence, before proposing another one
MAX_ATTEMPTS = 3                            # of an email that does not go out
_MARK = "PAYMENT_REMINDER_SENT_"


def _utc(moment: datetime | None) -> datetime | None:
    """Stored times are UTC, with or without saying so."""
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=timezone.utc)


def owing(candidate: Candidate) -> list[Registration]:
    """What a candidate still has to pay, oldest registration first."""
    found = [
        item for item in candidate.registrations
        if item.status != RegistrationStatus.CANCELLED and item.remaining_amount > 0
    ]
    return sorted(found, key=lambda item: _utc(item.registration_date) or datetime.min.replace(tzinfo=timezone.utc))


def covered_ids(db: Session, candidate_ids: list[str]) -> dict[str, set[str]]:
    """Per candidate, the registrations a delivered payment reminder was about."""
    covered: dict[str, set[str]] = {candidate_id: set() for candidate_id in candidate_ids}
    if not candidate_ids:
        return covered
    marks = db.query(Activity.candidate_id, Activity.event_type).filter(
        Activity.candidate_id.in_(candidate_ids), Activity.event_type.like(_MARK + "%"),
    ).all()
    for candidate_id, event_type in marks:
        covered[candidate_id].add(event_type[len(_MARK):])
    emails = db.query(CandidateEmail.candidate_id, CandidateEmail.registration_id).filter(
        CandidateEmail.candidate_id.in_(candidate_ids), CandidateEmail.direction == "outbound",
        CandidateEmail.intent == "payment_reminder", CandidateEmail.status == "sent",
        CandidateEmail.registration_id.isnot(None),
    ).all()
    for candidate_id, registration_id in emails:
        covered[candidate_id].add(registration_id)
    flagged = db.query(Registration.candidate_id, Registration.id).filter(
        Registration.candidate_id.in_(candidate_ids), Registration.payment_reminder_sent.is_(True),
    ).all()
    for candidate_id, registration_id in flagged:
        covered[candidate_id].add(registration_id)
    return covered


def program_names(registrations: list[Registration], limit: int = 3) -> str:
    """"A", "A et B", "A, B et C", then "A, B, C et 2 autres formations"."""
    names = list(dict.fromkeys(email_templates.program_name_of(item) for item in registrations))
    if len(names) <= 1:
        return names[0] if names else "votre formation"
    if len(names) <= limit:
        return ", ".join(names[:-1]) + " et " + names[-1]
    rest = len(names) - limit
    return ", ".join(names[:limit]) + f" et {rest} autre{'s' if rest > 1 else ''} formation{'s' if rest > 1 else ''}"


def _values(registrations: list[Registration]) -> dict:
    return {
        "program_name": program_names(registrations),
        "remaining_amount": money(sum(item.remaining_amount for item in registrations)),
        "paid_amount": money(sum(item.paid_amount or 0 for item in registrations)),
    }


def draft(db: Session, candidate: Candidate) -> dict | None:
    """The reminder as it would go out now, in words the admin can change.

    Returns None when the candidate owes nothing.
    """
    debts = owing(candidate)
    if not debts:
        return None
    template = email_templates.get(db, "payment_reminder")
    context = {**email_templates.context_for(candidate, debts[0]), **_values(debts)}
    return {
        "subject": " ".join(email_templates.personalize(template.subject, context).split()),
        "body": email_templates.personalize(template.body, context).strip(),
        "closing": email_templates.personalize(template.closing, context).strip(),
        "registrations": debts,
        "total": sum(item.remaining_amount for item in debts),
    }


def send(db: Session, candidate: Candidate, *, subject: str | None = None, body: str | None = None,
         manual: bool = False) -> CandidateEmail | None:
    """One reminder for everything the candidate owes, recorded in their discussion.

    `subject` and `body` are the admin's own words for this one email. Returns
    the stored message (its status says whether it was delivered), or None
    when there was nothing to remind about.
    """
    debts = owing(candidate)
    if not debts or not candidate.email:
        return None
    overrides = {
        name: text.strip() for name, text in (("subject", subject), ("body", body)) if text and text.strip()
    }
    message = email_templates.send(
        db, "payment_reminder", registration=debts[0], candidate=candidate,
        values=_values(debts), overrides=overrides or None, manual=manual,
    )
    if email_templates.delivered(message):
        now = datetime.now(timezone.utc)
        for item in debts:
            item.payment_reminder_sent = True
            item.reminder_sent_at = now.isoformat()
            item.reminder_count = (item.reminder_count or 0) + 1
        total = money(sum(item.remaining_amount for item in debts))
        db.add(Activity(
            candidate_id=candidate.id,
            event_type="PAYMENT_RELANCE_SENT" if manual else "PAYMENT_REMINDER_SENT",
            details=f"{'Sent by the admin' if manual else 'First reminder, sent by itself'}: {total} due for {program_names(debts)}",
        ))
    db.flush()
    return message


def _attempt(db: Session, candidate: Candidate, registration: Registration) -> CandidateReminder:
    """Where the automatic reminder of a candidate stands (kept on their oldest debt)."""
    reminder = db.query(CandidateReminder).filter(
        CandidateReminder.registration_id == registration.id, CandidateReminder.reminder_key == "payment_24h",
    ).first()
    if reminder is None:
        registered = _utc(registration.registration_date) or datetime.now(timezone.utc)
        reminder = CandidateReminder(
            candidate_id=candidate.id, registration_id=registration.id,
            reminder_key="payment_24h", reminder_type="payment", planned_for=registered + first_after(db),
            strategy=f"One reminder for everything the candidate owes, {welcome_hours(db)} hours after registering. Never repeated by itself.",
        )
        db.add(reminder)
        db.commit()
    return reminder


def send_first(db: Session, now: datetime | None = None) -> int:
    """The automatic reminder: once per candidate, never again while they still owe.

    Returns how many emails went out.
    """
    now = now or datetime.now(timezone.utc)
    candidates = (
        db.query(Candidate)
        .options(joinedload(Candidate.registrations).joinedload(Registration.program))
        .filter(Candidate.email.isnot(None))
        .all()
    )
    covered = covered_ids(db, [candidate.id for candidate in candidates])
    wait = first_after(db)
    sent = 0
    for candidate in candidates:
        debts = owing(candidate)
        if not debts or not (candidate.email or "").strip():
            continue
        if any(item.id in covered[candidate.id] for item in debts):
            continue    # reminded once and still owing: what comes next is the admin's decision
        if not any((_utc(item.registration_date) or now) <= now - wait for item in debts):
            continue    # registered too recently (24 hours unless the admin changed it)
        reminder = _attempt(db, candidate, debts[0])
        if reminder.status == "failed":
            continue

        # A reminder that failed earlier is sent again as it was, not written twice.
        earlier = (
            db.query(CandidateEmail)
            .filter(
                CandidateEmail.candidate_id == candidate.id, CandidateEmail.direction == "outbound",
                CandidateEmail.intent == "payment_reminder", CandidateEmail.status == "failed",
                CandidateEmail.registration_id.in_([item.id for item in debts]),
            )
            .order_by(CandidateEmail.received_at.desc())
            .first()
        )
        if earlier is not None:
            delivered = email_threads.resend(db, earlier)
            if delivered:
                for item in debts:
                    item.payment_reminder_sent = True
                    item.reminder_count = (item.reminder_count or 0) + 1
        else:
            delivered = email_templates.delivered(send(db, candidate))
        if not delivered:
            reminder.attempts = (reminder.attempts or 0) + 1
            reminder.last_error = "Email delivery failed"
            if reminder.attempts >= MAX_ATTEMPTS:
                reminder.status = "failed"
            db.commit()
            continue
        reminder.sent_at = datetime.utcnow()
        reminder.status = "sent"
        reminder.last_error = None
        db.commit()
        sent += 1
        logger.info("First payment reminder sent to candidate %s.", candidate.id)
    return sent


def story(db: Session, candidate: Candidate) -> dict:
    """What already happened about this candidate's payment, for the page where the admin writes."""
    emails = (
        db.query(CandidateEmail)
        .filter(CandidateEmail.candidate_id == candidate.id)
        .order_by(CandidateEmail.received_at.asc())
        .all()
    )
    reminders = [
        item for item in emails
        if item.direction == "outbound" and item.intent in ("payment_reminder", "payment_partial")
    ]
    delivered = [item for item in reminders if item.status == "sent"]
    last = delivered[-1] if delivered else None
    answers = [
        item for item in emails
        if item.direction == "inbound" and last is not None and item.received_at and item.received_at > last.received_at
    ]
    return {
        "reminders": delivered,
        "failed": [item for item in reminders if item.status == "failed"],
        "last": last,
        "answers": answers,
    }

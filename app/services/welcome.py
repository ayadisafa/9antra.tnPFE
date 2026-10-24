"""The welcome message, as soon as someone registers (the "welcome" automatic email; its words and its switch are in
Inbox, Email templates), and when the payment reminder goes after registering (24 hours unless the admin changed it).

The welcome goes once per candidate. Its clock starts the first time this runs: people who registered before it
existed are never written to by it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

KEY = "welcome"
REMINDER_HOURS = 24
MOST_HOURS = 24 * 14


def reminder_hours(db: Session) -> int:
    from app.services.settings_service import get_settings

    hours = getattr(get_settings(db), "payment_reminder_hours", None) or REMINDER_HOURS
    return max(1, min(MOST_HOURS, int(hours)))


def set_reminder_hours(db: Session, hours) -> int:
    from app.services.settings_service import get_settings

    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = REMINDER_HOURS
    get_settings(db).payment_reminder_hours = max(1, min(MOST_HOURS, hours))
    db.commit()
    return reminder_hours(db)


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def send_due(db: Session, now: datetime | None = None, limit: int = 20) -> int:
    """Welcome those who registered since the clock started and were not welcomed yet. Returns how many went."""
    from app.models import Candidate, Registration
    from app.models.candidate_email import CandidateEmail
    from app.models.registration import RegistrationStatus
    from app.services import email_templates
    from app.services.settings_service import get_settings

    now = now or datetime.utcnow()
    settings = get_settings(db)
    since = getattr(settings, "welcome_since", None)
    if since is None:
        settings.welcome_since = now          # the clock starts now: nobody who registered before is written to
        db.commit()
        return 0
    if not email_templates.get(db, KEY).sends_itself:
        return 0
    fresh = {}
    for registration in db.query(Registration).filter(Registration.status != RegistrationStatus.CANCELLED).all():
        when = _utc(registration.registration_date)
        if when is None or when < since:
            continue
        fresh.setdefault(registration.candidate_id, registration)
    if not fresh:
        return 0
    welcomed = {row[0] for row in db.query(CandidateEmail.candidate_id).filter(
        CandidateEmail.candidate_id.in_(list(fresh)), CandidateEmail.intent == KEY).all()}
    sent = 0
    for candidate_id, registration in fresh.items():
        if candidate_id in welcomed or sent >= limit:
            continue
        candidate = db.get(Candidate, candidate_id)
        if candidate is None:
            continue
        message = email_templates.send(db, KEY, registration=registration, candidate=candidate)
        db.commit()
        if message is not None:
            sent += 1
    return sent

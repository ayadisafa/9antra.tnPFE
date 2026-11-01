"""The reminder before each session: some hours before every session of their class (24 by default, the day before),
each candidate is reminded of the day, the hour, the place or the online class, and what it is about.

Once per session and candidate (session_reminders_sent), whatever happens to the email. Its words are an automatic
email (“session_reminder”): the admin rewrites it, switches it off, or puts the original back in Messages › Automatic
emails; the hours before are changed on the Reminders page. A session without an hour, a class on hold or finished, and
a session already begun get no reminder.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Session

from app.database import Base

logger = logging.getLogger(__name__)
KEY = "session_reminder"
HOURS = 24
LEAST, MOST = 1, 96


class SessionReminderSent(Base):
    __tablename__ = "session_reminders_sent"
    __table_args__ = (UniqueConstraint("session_id", "registration_id", name="uq_session_reminder"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, nullable=False, index=True)
    registration_id = Column(String, nullable=False, index=True)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String, nullable=True)            # sent | failed


def hours_before(db: Session) -> int:
    from app.services.settings_service import get_settings

    value = getattr(get_settings(db), "session_reminder_hours", None)
    try:
        value = int(value) if value is not None else HOURS
    except (TypeError, ValueError):
        value = HOURS
    return max(LEAST, min(MOST, value))


def set_hours(db: Session, value) -> int:
    from app.services.settings_service import get_settings

    try:
        hours = int(value)
    except (TypeError, ValueError):
        raise ValueError("The hours before must be a number.")
    if not LEAST <= hours <= MOST:
        raise ValueError(f"Between {LEAST} and {MOST} hours before the session.")
    get_settings(db).session_reminder_hours = hours
    db.commit()
    return hours


def _place(db: Session, class_group, session, registration) -> str:
    from app.services.settings_service import get_settings

    if (registration.delivery_mode or "online") == "onsite":
        address = (get_settings(db).centre_address or "").strip()
        room = (class_group.room or "").strip()
        return "au centre" + (f", {room}" if room else "") + (f" ({address})" if address else "")
    return "en ligne : votre lien personnel arrive une heure avant la séance"


def due(db: Session, now: datetime | None = None) -> list[tuple]:
    """(class, session, start) of the sessions whose reminder time has come and that have not begun."""
    from app.models.classgroup import ClassGroup, ClassStatus
    from app.models.classsession import ClassSession
    from app.services import meetings
    from app.services.schedule_utils import local_now

    now = now or local_now()
    ahead = timedelta(hours=hours_before(db))
    found = []
    days = [now.date() + timedelta(days=offset) for offset in range(0, ahead.days + 2)]
    for session in db.query(ClassSession).filter(ClassSession.session_date.in_(days)).all():
        class_group = db.get(ClassGroup, session.class_group_id)
        if class_group is None or class_group.status == ClassStatus.COMPLETED:
            continue
        if not (session.start_time or class_group.start_time):
            continue
        start, _ = meetings.hours_of(session, class_group)
        if start - ahead <= now < start:
            found.append((class_group, session, start))
    return found


def send_due(db: Session, now: datetime | None = None) -> int:
    """Sends the reminders whose time has come. Returns how many went."""
    from app.models.classgroup import ClassStatus
    from app.services import attendance, email_templates, meetings
    from app.services.enrollment_service import hold_reasons

    if not email_templates.is_on(db, KEY):
        return 0
    sent = 0
    for class_group, session, start in due(db, now):
        members = attendance.members(db, class_group.id)
        if class_group.status == ClassStatus.NOT_STARTED and hold_reasons(class_group, len(members)):
            continue                    # a class on hold: its sessions may move
        done = {row.registration_id for row in db.query(SessionReminderSent).filter(SessionReminderSent.session_id == session.id).all()}
        _, end = meetings.hours_of(session, class_group)
        hours = f"{start:%H:%M}" + (f" – {end:%H:%M}" if (session.end_time or class_group.end_time) else "")
        for registration in members:
            if registration.id in done or registration.candidate is None:
                continue
            message = email_templates.send(db, KEY, registration=registration, values={
                "session_date": meetings._french_date(session.session_date),
                "session_hours": hours,
                "session_topic": session.topic or "Séance",
                "session_place": _place(db, class_group, session, registration),
                "class_name": class_group.name,
            })
            if message is None:
                return sent             # switched off meanwhile, or nothing could be written
            db.add(SessionReminderSent(session_id=session.id, registration_id=registration.id,
                                       status="sent" if email_templates.delivered(message) else "failed"))
            db.commit()
            sent += 1
    if sent:
        logger.info("Reminded %d candidate(s) of their next session.", sent)
    return sent


def sent_count(db: Session, days: int = 30) -> int:
    since = datetime.utcnow() - timedelta(days=days)
    return db.query(SessionReminderSent).filter(SessionReminderSent.sent_at >= since).count()

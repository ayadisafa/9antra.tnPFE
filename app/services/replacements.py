"""A professor is away: their sessions that day find a replacement, for that day only.

When an absence is noted, each session of their classes on that day (at those hours) asks the professors who could
take it, one at a time: one who teaches its program, is available that date, is not away that day and has no session of
their own at those hours. The professor asked gets a message (in the application, by email, WhatsApp and SMS) with a
private link to confirm or say no. Yes: they replace the usual professor for that session only, and the admin is told.
No, or no answer in time: the next one is asked. Nobody left: the admin is told so. Until then the admin has one
notification that waits for it to be settled.
"""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.orm import Session

from app.database import Base
from app.services.ui_words import t as _t

logger = logging.getLogger(__name__)

WAIT = timedelta(hours=6)              # no answer by then: the next professor is asked
WAIT_SOON = timedelta(hours=1)         # ... or within the hour when the session is near
SOON = timedelta(hours=12)


class SessionReplacement(Base):
    """One session that needs another professor, and who was asked."""
    __tablename__ = "session_replacements"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, nullable=False, index=True)
    class_group_id = Column(String, nullable=False, index=True)
    absent_professor_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="searching")     # asked | accepted | none | past
    asked_professor_id = Column(String, nullable=True)
    token = Column(String, nullable=True, index=True)
    asked_at = Column(DateTime, nullable=True)
    answered_at = Column(DateTime, nullable=True)
    declined = Column(Text, nullable=True)                           # JSON: the professors who said no or did not answer
    created_at = Column(DateTime, default=datetime.utcnow)


def _declined(row: SessionReplacement) -> list[str]:
    try:
        return list(json.loads(row.declined or "[]"))
    except ValueError:
        return []


def _hours(session, group) -> tuple[time | None, time | None]:
    return session.start_time or group.start_time, session.end_time or group.end_time


def _crosses(start_a, end_a, start_b, end_b) -> bool:
    if not (start_a and end_a and start_b and end_b):
        return True                      # a whole day, or hours not known: counted as crossing
    return max(start_a, start_b) < min(end_a, end_b)


def busy_then(db: Session, professor_id: str, day: date, start: time | None, end: time | None, *, skip_session: str | None = None) -> bool:
    """Whether a professor already teaches at those hours that day: a session of their own classes, a class of theirs
    that runs that weekday in its weeks, or a session they already replace."""
    from app.models import ClassGroup, ClassStatus
    from app.models.classsession import ClassSession
    from app.services.enrollment_service import _weeks_of
    from app.services.schedule_utils import parse_days

    for group in db.query(ClassGroup).filter(ClassGroup.professor_id == professor_id, ClassGroup.status != ClassStatus.COMPLETED).all():
        sessions = db.query(ClassSession).filter(ClassSession.class_group_id == group.id, ClassSession.session_date == day).all()
        if sessions:
            for session in sessions:
                if session.substitute_professor_id and session.substitute_professor_id != professor_id:
                    continue             # someone else teaches it that day
                if _crosses(*_hours(session, group), start, end):
                    return True
            continue
        first, last = _weeks_of(group)
        if first and last and first <= day <= last and day.weekday() in parse_days(group.days_of_week) and _crosses(group.start_time, group.end_time, start, end):
            return True
    for session in db.query(ClassSession).filter(ClassSession.substitute_professor_id == professor_id, ClassSession.session_date == day).all():
        if session.id == skip_session:
            continue
        group = db.get(ClassGroup, session.class_group_id)
        if group is not None and _crosses(*_hours(session, group), start, end):
            return True
    return False


def candidates_for(db: Session, row: SessionReplacement) -> list:
    """The professors who could take that session, best first: its program among their specialities (the closest first),
    available that date, not away, free at those hours, and not already asked."""
    from app.models import ClassGroup, Professor
    from app.models.classsession import ClassSession
    from app.services.enrollment_service import _specialty_rank, absent_on

    session = db.get(ClassSession, row.session_id)
    group = db.get(ClassGroup, row.class_group_id)
    if session is None or group is None or group.program is None:
        return []
    start, end = _hours(session, group)
    skip = set(_declined(row)) | {row.absent_professor_id}
    ranked = []
    for professor in db.query(Professor).filter(Professor.is_available.is_(True)).all():
        if professor.id in skip or (professor.name or "").startswith("À nommer"):
            continue
        rank = _specialty_rank(professor, group.program.name)
        if rank is None:
            continue
        if (professor.available_from and professor.available_from > session.session_date) or \
                (professor.available_until and professor.available_until < session.session_date):
            continue
        if absent_on(db, professor.id, session.session_date, start, end) or busy_then(db, professor.id, session.session_date, start, end):
            continue
        ranked.append((rank, (professor.name or "").lower(), professor))
    ranked.sort(key=lambda item: item[:2])
    return [item[2] for item in ranked]


def _when(session, group) -> str:
    from app.services.schedule_utils import format_date_fr

    start, end = _hours(session, group)
    return format_date_fr(session.session_date) + (f" de {start:%H:%M} à {end:%H:%M}" if start and end else "")


def _notify_admin(db: Session, row: SessionReplacement, message: str, *, done: bool = False) -> None:
    """The admin's one notification about this session: it waits until a replacement is found."""
    from app.models.notification import Notification

    note = (db.query(Notification).filter(Notification.category == "REPLACEMENT", Notification.action_url == f"/classes/{row.class_group_id}?replacement={row.id}")
            .first())
    if note is None:
        from app.models import Professor

        absent = db.get(Professor, row.absent_professor_id)
        note = Notification(recipient_role="admin", category="REPLACEMENT", title=f"Replacement for {absent.name if absent else 'a professor'}",
                            message=message, action_url=f"/classes/{row.class_group_id}?replacement={row.id}")
        db.add(note)
    note.message = message
    if done:
        note.is_read, note.status, note.read_at = True, "settled", datetime.utcnow()
    db.flush()


def ask_next(db: Session, row: SessionReplacement, base_url: str) -> SessionReplacement:
    """The next professor who could take it is asked; nobody left: the admin is told."""
    from app.models import ClassGroup
    from app.models.classsession import ClassSession
    from app.services import team_messages

    session = db.get(ClassSession, row.session_id)
    group = db.get(ClassGroup, row.class_group_id)
    if session is None or group is None:
        row.status = "past"
        db.commit()
        return row
    choices = candidates_for(db, row)
    when = _when(session, group)
    if not choices:
        row.status, row.asked_professor_id, row.token = "none", None, None
        from app.services.settings_service import get_settings

        if (get_settings(db).replacement_fallback or "ask") == "postpone":
            # Nobody can take it: the session goes a week later by itself (Settings, Classes), and everyone is told.
            db.commit()
            postpone_week(db, row, by="the application")
            return row
        _notify_admin(db, row, f"{group.name}, {when}: no professor who teaches it is free that day. "
                               "Choose another professor, another day, or a week later.")
        db.commit()
        return row
    professor = choices[0]
    row.status, row.asked_professor_id, row.token, row.asked_at = "asked", professor.id, secrets.token_urlsafe(18), datetime.utcnow()
    first = ((professor.name or "").replace("Pr ", "").replace("Dr ", "").split() or [""])[0]
    text = (f"Bonjour {first}, pourriez-vous remplacer un collègue absent le {when} pour la classe {group.name} "
            f"({group.program.name if group.program else ''}) ? Pour ce jour seulement. Confirmez ou dites non ici : "
            f"{base_url.rstrip('/')}/replace/{row.token}")
    _notify_admin(db, row, f"{group.name}, {when}: {professor.name} was asked to replace them that day; waiting for the answer.")
    db.commit()
    try:
        team_messages.send(db, f"professor:{professor.id}", text, ["app", "email", "whatsapp", "sms"], author="The assistant")
    except Exception:
        db.rollback()
        logger.exception("Replacement %s: %s could not be asked.", row.id, professor.id)
    return row


def open_for_absence(db: Session, absence, base_url: str) -> list[SessionReplacement]:
    """An absence was noted: each session of the professor's classes that day (at those hours) looks for a replacement."""
    from app.models import ClassGroup, ClassStatus
    from app.models.classsession import ClassSession

    opened = []
    for group in db.query(ClassGroup).filter(ClassGroup.professor_id == absence.professor_id, ClassGroup.status != ClassStatus.COMPLETED).all():
        for session in db.query(ClassSession).filter(ClassSession.class_group_id == group.id, ClassSession.session_date == absence.absence_date).all():
            if session.substitute_professor_id or not _crosses(*_hours(session, group), absence.start_time, absence.end_time):
                continue
            if db.query(SessionReplacement.id).filter(SessionReplacement.session_id == session.id,
                                                      SessionReplacement.status.in_(("asked", "accepted"))).first():
                continue
            row = SessionReplacement(session_id=session.id, class_group_id=group.id, absent_professor_id=absence.professor_id)
            db.add(row)
            db.flush()
            opened.append(ask_next(db, row, base_url))
    return opened


def by_token(db: Session, token: str) -> SessionReplacement | None:
    if not token or len(token) < 16:
        return None
    return db.query(SessionReplacement).filter(SessionReplacement.token == token).first()


def answer(db: Session, row: SessionReplacement, yes: bool, base_url: str) -> SessionReplacement:
    """The professor asked said yes (they take that session) or no (the next one is asked)."""
    from app.models import ClassGroup, Professor
    from app.models.classsession import ClassSession
    from app.services import team_messages

    if row.status != "asked":
        return row
    session = db.get(ClassSession, row.session_id)
    group = db.get(ClassGroup, row.class_group_id)
    professor = db.get(Professor, row.asked_professor_id) if row.asked_professor_id else None
    row.answered_at = datetime.utcnow()
    if not yes or session is None or group is None or professor is None:
        row.declined = json.dumps(_declined(row) + ([row.asked_professor_id] if row.asked_professor_id else []))
        row.token = None
        db.commit()
        return ask_next(db, row, base_url)
    if busy_then(db, professor.id, session.session_date, *_hours(session, group), skip_session=session.id):
        row.declined = json.dumps(_declined(row) + [professor.id])         # taken meanwhile
        db.commit()
        return ask_next(db, row, base_url)
    session.substitute_professor_id = professor.id
    row.status, row.token = "accepted", None
    when = _when(session, group)
    _notify_admin(db, row, f"{group.name}, {when}: {professor.name} replaces them that day (confirmed).", done=True)
    db.commit()
    try:
        team_messages.send(db, f"professor:{professor.id}", f"Merci ! Vous remplacez le {when} pour {group.name}. La feuille de présence "
                           f"de cette séance : {base_url.rstrip('/')}/classes/{group.id}/attendance?session={session.id}",
                           ["app", "email"], author="The assistant")
    except Exception:
        db.rollback()
    return row


def tick(db: Session, base_url: str, now: datetime | None = None) -> int:
    """No answer in time: the next professor is asked; a session already past is closed. Returns how many moved on."""
    from app.models import ClassGroup
    from app.models.classsession import ClassSession
    from app.services.schedule_utils import local_now

    now_utc, local = datetime.utcnow(), now or local_now()
    moved = 0
    for row in db.query(SessionReplacement).filter(SessionReplacement.status.in_(("asked", "none"))).all():
        session = db.get(ClassSession, row.session_id)
        group = db.get(ClassGroup, row.class_group_id)
        if session is None or group is None:
            row.status = "past"
            continue
        start = datetime.combine(session.session_date, _hours(session, group)[0] or time(23, 59))
        if start <= local:
            row.status = "past"
            _notify_admin(db, row, f"{group.name}, {_when(session, group)}: the session has passed.", done=True)
            continue
        if row.status != "asked" or row.asked_at is None:
            continue
        wait = WAIT_SOON if start - local <= SOON else WAIT
        if now_utc - row.asked_at >= wait:
            row.declined = json.dumps(_declined(row) + [row.asked_professor_id])
            db.commit()
            ask_next(db, row, base_url)
            moved += 1
    db.commit()
    return moved


# ----------------------------------------------------------------------
# Nobody replaces them: another professor, another day, or a week later
# ----------------------------------------------------------------------

def _tell_class(db: Session, group, subject: str, body: str, by: str) -> None:
    """The class's candidates are told, within a minute, the way the admin's reminders go (email, WhatsApp when chosen)."""
    from app.services import reminders

    try:
        reminders.create(db, audience="class", audience_id=group.id, subject=subject, body=body, by=by, send_at=None)
    except ValueError:
        pass                                    # nobody to tell (an empty class)


def _day_fr(day: date) -> str:
    from app.services.schedule_utils import format_date_fr

    return format_date_fr(day)


def give_to(db: Session, row: SessionReplacement, professor_id: str, base_url: str, *, by: str = "Admin") -> str | None:
    """The admin chose who replaces them that day. Returns what is wrong, or None."""
    from app.models import ClassGroup, Professor
    from app.models.classsession import ClassSession
    from app.services import team_messages

    session, group = db.get(ClassSession, row.session_id), db.get(ClassGroup, row.class_group_id)
    professor = db.get(Professor, professor_id) if professor_id else None
    if session is None or group is None or professor is None:
        return _t("Choose a professor.")
    if professor.id == row.absent_professor_id:
        return _t("That is the professor who is away.")
    if busy_then(db, professor.id, session.session_date, *_hours(session, group), skip_session=session.id):
        return _t("{0} teaches another class at those hours that day.", professor.name)
    session.substitute_professor_id = professor.id
    row.status, row.asked_professor_id, row.token, row.answered_at = "accepted", professor.id, None, datetime.utcnow()
    when = _when(session, group)
    _notify_admin(db, row, f"{group.name}, {when}: {professor.name} replaces them that day (chosen by {by}).", done=True)
    db.commit()
    try:
        team_messages.send(db, f"professor:{professor.id}", f"Bonjour, vous remplacez un collègue le {when} pour la classe {group.name}. "
                           f"La feuille de présence : {base_url.rstrip('/')}/classes/{group.id}/attendance?session={session.id}",
                           ["app", "email", "whatsapp"], author="The assistant")
    except Exception:
        db.rollback()
    return None


def move_session(db: Session, row: SessionReplacement, day: date, start: time | None, end: time | None, *, by: str = "Admin") -> str | None:
    """The session goes to another day (and hours), with its usual professor. Returns what is wrong, or None."""
    from app.models import ClassGroup
    from app.models.classsession import ClassSession
    from app.services import enrollment_service, team_messages
    from app.services.schedule_utils import local_now

    session, group = db.get(ClassSession, row.session_id), db.get(ClassGroup, row.class_group_id)
    if session is None or group is None:
        return _t("This session no longer exists.")
    start, end = start or _hours(session, group)[0], end or _hours(session, group)[1]
    if day < local_now().date():
        return _t("Choose a day to come.")
    if start and end and end <= start:
        return _t("The end must be after the start.")
    if group.professor_id and (busy_then(db, group.professor_id, day, start, end, skip_session=session.id)
                               or enrollment_service.absent_on(db, group.professor_id, day, start, end)):
        return _t("Its professor is not free then: choose another day or other hours.")
    old = _when(session, group)
    session.session_date, session.start_time, session.end_time = day, start, end
    row.status, row.token = "moved", None
    new = _when(session, group)
    _notify_admin(db, row, f"{group.name}: the session of {old} moved to {new} (by {by}).", done=True)
    db.commit()
    _tell_class(db, group, f"Votre séance de {group.name} change de jour",
                f"Bonjour {{first_name}},\n\nVotre formateur est absent le {old}. La séance de **{group.name}** a donc lieu le **{new}**.\n\n"
                "Le reste de votre planning ne change pas. À bientôt !", by)
    if group.professor_id:
        try:
            team_messages.send(db, f"professor:{group.professor_id}", f"Votre séance de {group.name} du {old} est déplacée au {new}.",
                               ["app", "email"], author="The assistant")
        except Exception:
            db.rollback()
    return None


def postpone_week(db: Session, row: SessionReplacement, *, by: str = "Admin") -> str | None:
    """The session does not take place: it and every session after it go one week later, so the class ends a week later.
    Candidates whose next class would then start too soon are moved to a later one (one class at a time)."""
    from app.models import ClassGroup
    from app.models.classsession import ClassSession
    from app.services import enrollment_service, team_messages

    session, group = db.get(ClassSession, row.session_id), db.get(ClassGroup, row.class_group_id)
    if session is None or group is None:
        return _t("This session no longer exists.")
    old = _when(session, group)
    later = (db.query(ClassSession).filter(ClassSession.class_group_id == group.id, ClassSession.session_date >= session.session_date)
             .order_by(ClassSession.session_date.desc()).all())
    for item in later:                           # the last first, so two sessions never share a day meanwhile
        item.session_date = item.session_date + timedelta(days=7)
    group.total_weeks = (group.total_weeks or 12) + 1          # the class lasts a week more (its end follows)
    row.status, row.token = "postponed", None
    new = _when(session, group)
    last = max(item.session_date for item in later)
    _notify_admin(db, row, f"{group.name}: the session of {old} goes a week later, to {new}; the sessions after it too "
                           f"(the class now ends {last:%d/%m/%Y}). By {by}.", done=True)
    db.commit()
    _tell_class(db, group, f"Votre séance de {group.name} est reportée d'une semaine",
                f"Bonjour {{first_name}},\n\nVotre formateur est absent le {old} : cette séance de **{group.name}** n'a pas lieu et "
                f"est reportée d'une semaine, au **{new}**.\n\n[box]\nLes séances suivantes sont décalées d'une semaine elles aussi : "
                f"la formation se termine le {_day_fr(last)}.\n\nÀ bientôt !", by)
    if group.professor_id:
        try:
            team_messages.send(db, f"professor:{group.professor_id}", f"La séance de {group.name} du {old} est reportée au {new} ; "
                               "les séances suivantes sont décalées d'une semaine.", ["app", "email"], author="The assistant")
        except Exception:
            db.rollback()
    from app.services.enrollment_service import class_members

    enrollment_service.keep_one_at_a_time(db, {member.candidate_id for member in class_members(db, group.id)})
    return None


def of_class(db: Session, class_id: str) -> list[dict]:
    """The replacements of a class's sessions, for its page (with whom the admin may choose while one is open)."""
    from app.models import ClassGroup, Professor
    from app.models.classsession import ClassSession

    found = []
    for row in db.query(SessionReplacement).filter(SessionReplacement.class_group_id == class_id).order_by(SessionReplacement.created_at.desc()).limit(12):
        session = db.get(ClassSession, row.session_id)
        group = db.get(ClassGroup, row.class_group_id)
        choices = []
        if row.status in ("none", "asked") and session is not None and group is not None:
            # Who could take it, for the admin to choose: those who teach the program and are free then.
            choices = [person for person in candidates_for(db, row)]
        found.append({"row": row, "session": session, "absent": db.get(Professor, row.absent_professor_id),
                      "asked": db.get(Professor, row.asked_professor_id) if row.asked_professor_id else None,
                      "choices": choices, "open": row.status in ("none", "asked")})
    return found

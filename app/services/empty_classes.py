"""A class never runs empty. Whenever a candidate leaves a class (moved to another one, taken out of it, their
registration cancelled, or removed from the candidates), it is written down with why. A class left with nobody:
- not started yet: removed by itself half an hour after its last candidate left (time for the admin to move people
  between classes); one nobody ever joined is removed when its first session comes, as it cannot start (the admin
  is told three days before);
- already running: it ends that day (its history stays, "Reopen" brings it back), also when every candidate stopped
  after their months.
Its professor is told, every way they can be reached, in a designed message saying why it became empty; the admin is
told in a notification.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from contextvars import ContextVar
from datetime import date, datetime, timedelta

from sqlalchemy import Column, DateTime, String, event, inspect
from sqlalchemy.orm import Session

from app.database import Base
from app.models.activity import Activity
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.registration import Registration, RegistrationStatus

logger = logging.getLogger(__name__)

GRACE = timedelta(minutes=30)            # emptied before its start: removed this long after its last candidate left
WARN_BEFORE = timedelta(days=3)          # never filled: the admin is told this long before its first session
SHOWN = 5                                # departures named in a message (then "and N more")


class ClassDeparture(Base):
    """A candidate who left a class, and why (kept after the class is gone)."""
    __tablename__ = "class_departures"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    class_group_id = Column(String, nullable=False, index=True)
    class_name = Column(String, nullable=True)
    registration_id = Column(String, nullable=True)
    candidate_id = Column(String, nullable=True)
    candidate_name = Column(String, nullable=True)
    kind = Column(String, nullable=False)          # moved | out | cancelled | deleted
    cause = Column(String, nullable=True)          # one_class (detail: the day their class before ends)
    detail = Column(String, nullable=True)
    to_class_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ----------------------------------------------------------------------------------------------------------------------
# Who leaves, and why
# ----------------------------------------------------------------------------------------------------------------------

_cause: ContextVar[tuple[str, str] | None] = ContextVar("class_departure_cause", default=None)


@contextlib.contextmanager
def because(cause: str, detail: str = ""):
    """What a move made inside this block is due to (written with the departure)."""
    token = _cause.set((cause, detail or ""))
    try:
        yield
    finally:
        _cause.reset(token)


def _old(history):
    return history.deleted[0] if history.deleted else None


def _add(session, registration: Registration, class_id: str, kind: str, to: str | None = None) -> None:
    group = session.get(ClassGroup, class_id)
    person = registration.candidate
    cause, detail = _cause.get() or (None, "")
    session.add(ClassDeparture(class_group_id=class_id, class_name=group.name if group else None, registration_id=registration.id,
                               candidate_id=registration.candidate_id,
                               candidate_name=(person.full_name or person.email) if person else None,
                               kind=kind, cause=cause, detail=detail or None, to_class_id=to))


def _record(session, flush_context, instances) -> None:
    """Before each write: the registrations that leave a class (never stops the write)."""
    try:
        for registration in [obj for obj in session.dirty if isinstance(obj, Registration)]:
            attrs = inspect(registration).attrs
            status = attrs.status.history
            if (_old(status) if status.deleted else registration.status) == RegistrationStatus.CANCELLED:
                continue                                    # already out of every class
            column, link = attrs.class_group_id.history, attrs.class_group.history
            old = _old(column) if column.deleted else getattr(_old(link), "id", None)
            new = registration.class_group_id
            if link.has_changes() and not column.has_changes():          # only the class itself was given
                new = registration.class_group.id if registration.class_group is not None else None
            if old and old != new:
                _add(session, registration, old, "moved" if new else "out", to=new)
            elif new and status.deleted and registration.status == RegistrationStatus.CANCELLED:
                _add(session, registration, new, "cancelled")
        for registration in [obj for obj in session.deleted if isinstance(obj, Registration)]:
            if registration.class_group_id and registration.status != RegistrationStatus.CANCELLED:
                _add(session, registration, registration.class_group_id, "deleted")
    except Exception:
        logger.exception("A departure from a class could not be written down.")


# The old value of a class or a status is needed to know which class was left, even on an object not read yet.
event.listen(Registration.class_group_id, "set", lambda *args: None, active_history=True)
event.listen(Registration.status, "set", lambda *args: None, active_history=True)
event.listen(Session, "before_flush", _record)


# ----------------------------------------------------------------------------------------------------------------------
# Why it is empty, in words
# ----------------------------------------------------------------------------------------------------------------------

def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _sentence(db: Session, row, lang: str) -> str:
    from app.services.schedule_utils import format_date_en, format_date_fr

    name = row.candidate_name or ("Un candidat" if lang == "fr" else "A candidate")
    target = db.get(ClassGroup, row.to_class_id) if row.to_class_id else None
    if target is None and row.registration_id:
        now_in = db.get(Registration, row.registration_id)
        target = now_in.class_group if now_in is not None and now_in.class_group_id != row.class_group_id else None
    ends = _day(row.detail)
    if lang == "fr":
        if row.cause == "one_class":
            said = f"{name} suit encore une autre classe qui se termine le {format_date_fr(ends)}" if ends else f"{name} suit encore une autre classe"
            return said + (f" : une classe à la fois, il/elle rejoint donc {target.name}" if target else " : une classe à la fois, il/elle attend une classe plus tard")
        if row.kind == "moved":
            return f"{name} a été déplacé(e) vers la classe {target.name}" if target else f"{name} a été déplacé(e) vers une autre classe"
        if row.kind == "out":
            return f"{name} a été retiré(e) de la classe"
        if row.kind == "cancelled":
            return f"{name} : inscription annulée"
        if row.kind == "stopped":
            return f"{name} a choisi de s'arrêter à la fin de ses mois"
        return f"{name} ne fait plus partie des candidats"
    if row.cause == "one_class":
        said = f"{name} still follows another class, which ends {format_date_en(ends)}" if ends else f"{name} still follows another class"
        return said + (f": one class at a time, so they now join {target.name}" if target else ": one class at a time, so they wait for a later one")
    if row.kind == "moved":
        return f"{name} was moved to {target.name}" if target else f"{name} was moved to another class"
    if row.kind == "out":
        return f"{name} was taken out of the class"
    if row.kind == "cancelled":
        return f"{name}: registration cancelled"
    if row.kind == "stopped":
        return f"{name} chose to stop after their months"
    return f"{name} was removed from the candidates"


def why(db: Session, rows: list, lang: str = "en", created: datetime | None = None) -> str:
    """Why a class is empty, in one sentence (the last departures first)."""
    from app.services.schedule_utils import format_date_en, format_date_fr, utc_to_local

    if not rows:
        made = utc_to_local(created) if created else None
        if lang == "fr":
            return f"aucun candidat ne l'a rejointe depuis sa création le {format_date_fr(made)}" if made else \
                "aucun candidat ne l'a rejointe depuis sa création"
        return f"nobody joined it since it was created on {format_date_en(made)}" if made else "nobody joined it since it was created"
    ordered = sorted(rows, key=lambda row: row.created_at or datetime.min, reverse=True)
    said = [_sentence(db, row, lang) for row in ordered[:SHOWN]]
    more = len(ordered) - SHOWN
    if more > 0:
        said.append(f"et {more} autre(s)" if lang == "fr" else f"and {more} more")
    return "; ".join(said)


class _Past:
    """A departure read from the history written before departures were kept."""

    def __init__(self, **fields):
        self.registration_id = self.to_class_id = self.cause = self.detail = None
        self.__dict__.update(fields)


def departures(db: Session, group: ClassGroup) -> list:
    """Who left this class, and why: what was written down, else what the history says (before this was kept)."""
    rows = db.query(ClassDeparture).filter(ClassDeparture.class_group_id == group.id).all()
    if rows:
        return rows
    from app.models import Candidate

    found = []
    for item in (db.query(Activity).filter(Activity.event_type.in_(("CLASS_MOVED", "CLASS_CHANGED")),
                                           Activity.details.like(f"%{group.name}%")).all()):
        details = item.details or ""
        person = db.get(Candidate, item.candidate_id) if item.candidate_id else None
        name = (person.full_name or person.email) if person else None
        registration = None
        if person is not None:
            registration = next((row for row in person.registrations if row.class_group_id and row.class_group_id != group.id
                                 and row.class_group is not None and row.class_group.program_id == group.program_id), None)
        if details.startswith(f"Moved out of {group.name}:"):
            ends = None
            if "now ends " in details:
                try:
                    ends = datetime.strptime(details.split("now ends ", 1)[1].rstrip(". "), "%a %d %b %Y").date().isoformat()
                except ValueError:
                    ends = None
            found.append(_Past(candidate_name=name, kind="moved", cause="one_class", detail=ends, created_at=item.created_at,
                               to_class_id=registration.class_group_id if registration else None))
        elif details.startswith(f"Moved from {group.name} to "):
            found.append(_Past(candidate_name=name, kind="moved", created_at=item.created_at,
                               to_class_id=registration.class_group_id if registration else None))
    for registration in (db.query(Registration).filter(Registration.class_group_id == group.id,
                                                       Registration.status == RegistrationStatus.CANCELLED).all()):
        person = registration.candidate
        found.append(_Past(candidate_name=(person.full_name or person.email) if person else None, kind="cancelled",
                           created_at=registration.updated_at if hasattr(registration, "updated_at") and registration.updated_at
                           else group.created_at))
    return found


# ----------------------------------------------------------------------------------------------------------------------
# The sweep (every minute, with the class lifecycle)
# ----------------------------------------------------------------------------------------------------------------------

def _to_utc(local: datetime) -> datetime:
    from app.services.reminders import to_utc

    return to_utc(local)


def _ran(db: Session, group: ClassGroup, start: datetime | None, now: datetime, left: list) -> bool:
    """It really began: running, a presence taken, or somebody left after its first session."""
    from app.models.user import Attendance

    if start is None or start > now:
        return False
    if group.status == ClassStatus.ONGOING or db.query(Attendance.id).filter(Attendance.class_group_id == group.id).first():
        return True
    began = _to_utc(start)
    return any(row.created_at and row.created_at >= began for row in left)


def state(db: Session, group: ClassGroup, now: datetime | None = None) -> dict | None:
    """For the class page, when nobody is in it: {"emptied", "why", "removes_at" (local), "start"}."""
    from app.services.enrollment_service import class_members
    from app.services.schedule_utils import class_start_dt, local_now, utc_to_local

    if group.status == ClassStatus.COMPLETED or class_members(db, group.id):
        return None
    now = now or local_now()
    left = departures(db, group)
    start = class_start_dt(group)
    if left:
        last = max((row.created_at for row in left if row.created_at), default=None)
        removes = utc_to_local(last + GRACE) if last else now
        return {"emptied": True, "why": why(db, left), "removes_at": max(removes, now), "start": start}
    return {"emptied": False, "why": why(db, [], created=group.created_at), "removes_at": start, "start": start}


def _professor_words(db: Session, group: ClassGroup, reason_fr: str, *, ended: bool) -> bool:
    """Whether the professor was reached (in the application, by email, WhatsApp or SMS)."""
    from app.services import team_messages
    from app.services.schedule_utils import class_start_dt, format_date_fr
    from app.services.settings_service import public_base_url

    professor = group.professor
    if professor is None:
        return False
    first = ((professor.name or "").replace("Pr ", "").replace("Dr ", "").split() or [""])[0]
    program = group.program.name if group.program else group.name
    start = class_start_dt(group)
    if ended:
        text = (f"Bonjour {first},\n\nLa classe {group.name} ({program}) s'arrête aujourd'hui : plus aucun candidat ne la suit.\n\n"
                f"Pourquoi : {reason_fr}.\n\nLes séances à venir sont retirées de votre emploi du temps ; ce qui a été fait reste "
                "dans votre espace. Vous n'avez rien à faire.")
        subject = f"La classe {group.name} s'arrête"
    else:
        text = (f"Bonjour {first},\n\nLa classe {group.name} ({program}) est annulée : elle n'a plus aucun candidat, "
                f"et une classe ne commence jamais vide.\n\nPourquoi : {reason_fr}.\n\nSes séances sont retirées de votre "
                "emploi du temps. Vous n'avez rien à faire.")
        subject = f"La classe {group.name} est annulée"
    rows = [("Classe", group.name), ("Formation", program)]
    if start is not None:
        rows.append(("Première séance" if ended else "Première séance prévue", f"{format_date_fr(start)} à {start:%H:%M}"))
    try:
        kept = team_messages.send(db, f"professor:{professor.id}", text, ["app", "email", "whatsapp", "sms"], author="The assistant",
                                  subject=subject, rows=rows, cta=("Voir mon espace", f"{public_base_url(db)}/teacher/space"))
    except Exception:
        db.rollback()
        logger.exception("The professor of %s could not be told that it is empty.", group.id)
        return False
    return any(row.status in ("sent", "to_send") for row in kept)


def _tell_admin(db: Session, group: ClassGroup, title: str, message: str, url: str) -> None:
    from app.models.notification import Notification

    db.add(Notification(recipient_role="admin", category="CLASS", title=title, message=message, action_url=url,
                        program_id=group.program_id, payload={"class_id": group.id}))


def _who_teaches(group: ClassGroup) -> str:
    return group.professor.name if group.professor is not None else (group.professor_name or "")


def told_how(db: Session, group: ClassGroup) -> str:
    """For the class page: "Salma is told why", or what the admin does when the professor cannot be reached."""
    from app.services import team_messages

    teacher = _who_teaches(group)
    if not teacher:
        return "nobody is told: it has no professor"
    found = team_messages.person(db, f"professor:{group.professor_id}") if group.professor_id else None
    if found and found.get("ways"):
        return f"{teacher} is told why"
    return f"{teacher} cannot be reached: tell them yourself"


def _told(group: ClassGroup, reached: bool) -> str:
    teacher = _who_teaches(group)
    if not teacher:
        return ""
    if reached:
        return f" {teacher} was told."
    return f" {teacher} could not be told (no email, phone or account): tell them yourself."


def remove(db: Session, group: ClassGroup, left: list) -> bool:
    """A class not started, with nobody: its professor and the admin are told why, then it is removed."""
    from app.services.enrollment_service import delete_class, enrolled_count

    if enrolled_count(db, group.id):
        return False                        # somebody joined meanwhile: it stays
    reason_en = why(db, left, "en", created=group.created_at)
    reason_fr = why(db, left, "fr", created=group.created_at)
    name = group.name
    reached = _professor_words(db, group, reason_fr, ended=False)
    _tell_admin(db, group, "Class removed: nobody in it",
                f"{name} was removed by itself: a class never starts empty. Why: {reason_en}." + _told(group, reached), "/classes/")
    db.add(Activity(candidate_id=None, event_type="CLASS_REMOVED", details=f"{name} removed (nobody in it): {reason_en}."))
    delete_class(db, group)
    return True


def close(db: Session, group: ClassGroup, left: list, now: datetime, members: list | None = None) -> None:
    """A class already running, with nobody left to follow it: it ends today (its history stays)."""
    from app.services.enrollment_service import class_url
    from app.services.schedule_utils import format_date_en

    reason_en = why(db, left, "en")
    reason_fr = why(db, left, "fr")
    group.end_date = now - timedelta(seconds=1)       # already over when the lifecycle looks at it again
    group.status = ClassStatus.COMPLETED
    group.closed_note = f"Ended early on {format_date_en(now)}: nobody left to follow it. Why: {reason_en}."
    for registration in members or []:
        if registration.status == RegistrationStatus.ASSIGNED:
            registration.status = RegistrationStatus.COMPLETED
            registration.training_end_date = now
    db.add(Activity(candidate_id=None, event_type="CLASS_CLOSED", details=f"{group.name} ended early (nobody left): {reason_en}."))
    db.commit()                             # ended first: telling the professor never undoes it
    reached = _professor_words(db, group, reason_fr, ended=True)
    _tell_admin(db, group, "Class ended early: nobody left",
                f"{group.name} ended by itself today: nobody follows it any more. Why: {reason_en}." + _told(group, reached), class_url(group))
    db.commit()


def _stopped_rows(members: list) -> list:
    return [_Past(candidate_name=(row.candidate.full_name or row.candidate.email) if row.candidate else None, kind="stopped",
                  created_at=datetime.utcnow()) for row in members]


def sweep(db: Session, now: datetime | None = None) -> dict:
    """Every class with nobody left: removed (not started) or ended (running); the one nobody joined, when its first
    session comes (the admin told three days before). Returns {"removed": [names], "ended": [names]}."""
    from app.models.classsession import ClassSession
    from app.services import monthly
    from app.services.enrollment_service import _tell_once, class_members, class_url
    from app.services.schedule_utils import class_start_dt, format_date_en, local_now

    now = now or local_now()
    done = {"removed": [], "ended": []}
    for group in db.query(ClassGroup).filter(ClassGroup.status != ClassStatus.COMPLETED).all():
        try:
            members = class_members(db, group.id)
            start = class_start_dt(group)
            if members:
                if group.status != ClassStatus.ONGOING:
                    continue
                # Running: nobody left to follow its next session when every candidate stopped after their months.
                coming = (db.query(ClassSession.session_date).filter(ClassSession.class_group_id == group.id,
                                                                     ClassSession.session_date >= now.date())
                          .order_by(ClassSession.session_date.asc()).first())
                if coming is not None and all(monthly.ended_before(row, coming[0]) for row in members):
                    close(db, group, _stopped_rows(members), now, members)
                    done["ended"].append(group.name)
                continue
            left = departures(db, group)
            if _ran(db, group, start, now, left):
                close(db, group, left, now)
                done["ended"].append(group.name)
            elif left:
                last = max((row.created_at for row in left if row.created_at), default=None)
                if last is None or datetime.utcnow() - last >= GRACE:
                    if remove(db, group, left):
                        done["removed"].append(group.name)
            elif start is not None and start <= now:
                if remove(db, group, []):
                    done["removed"].append(group.name)
            elif start is not None and start - now <= WARN_BEFORE:
                teacher = _who_teaches(group)
                _tell_once(db, group, title="Nobody in this class yet",
                           message=f"{group.name} starts {format_date_en(start)} and nobody is in it. A class never starts empty: "
                                   f"if nobody joins by then, it is removed by itself" + (f" and {teacher} is told why." if teacher else "."),
                           about="it is removed by itself")
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("Class %s: the empty-class check failed.", group.id)
    return done

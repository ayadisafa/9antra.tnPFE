"""A professor removed for good, without leaving anything that points at them.

Each class they still teach goes to the professor chosen for it (one who teaches its program, can be given classes and
is free at its hours) or waits for one. Their sign-in account, absences, availabilities, announcements, resource
reminders and conversation in the team chat go with them; a candidate's question still waiting for their answer comes
back to the admin's inbox. A finished class keeps their name (who taught it), and the reviews about them stay.
"""
from __future__ import annotations

import json

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import ClassGroup, ClassStatus, Professor
from app.models.edit_log import EditLog
from app.models.user import User, UserSession
from app.services.enrollment_service import _weeks_of, clash_message, professor_clash, teaches
from app.services.schedule_utils import parse_days
from app.services.ui_words import t as _t

PLACE_TO_NAME = "À nommer"          # the places once made for professors still to find: never given a class


def classes_of(db: Session, professor: Professor) -> list[ClassGroup]:
    """The classes that still need this professor (not finished): by their link, or by the name for the oldest ones."""
    return (db.query(ClassGroup)
            .filter(ClassGroup.status != ClassStatus.COMPLETED,
                    or_(ClassGroup.professor_id == professor.id,
                        and_(ClassGroup.professor_id.is_(None), ClassGroup.professor_name == professor.name)))
            .order_by(ClassGroup.name).all())


def _clash(db: Session, successor: Professor, group: ClassGroup) -> ClassGroup | None:
    first, _ = _weeks_of(group)
    return professor_clash(db, successor.id, parse_days(group.days_of_week), group.start_time, group.end_time,
                           first, group.total_weeks, exclude_id=group.id)


def successors(db: Session, professor: Professor, group: ClassGroup) -> list[Professor]:
    """Who could take this class instead: teaches its program, is available, and is free at its hours (the best first)."""
    from app.services.enrollment_service import able_to_take

    return able_to_take(db, group, exclude_id=professor.id)


def remove(db: Session, professor: Professor, handover: dict | None = None, who: str | None = None) -> dict:
    """Removes the professor. `handover`: class id -> id of the professor who takes that class (empty: it waits for one).
    Raises ValueError, with nothing changed, when a chosen professor cannot take a class. Returns what happened."""
    from app.models.candidate_email import CandidateEmail
    from app.models.email_thread import EmailThread
    from app.models.resource_reminder import ResourceReminder
    from app.models.scheduling import BatchSession
    from app.models.team_message import TeamMessage
    from app.services import course_questions
    from app.services.notification_service import create_notification
    from app.services.professor_announcements import ProfessorAnnouncement

    handover = handover or {}
    name, key = professor.name, f"professor:{professor.id}"
    given, waiting = [], []
    for group in classes_of(db, professor):
        chosen = str(handover.get(group.id) or "").strip()
        if not chosen:
            group.professor_id, group.professor_name = None, None
            waiting.append(group.name)
            continue
        successor = db.get(Professor, chosen)
        if successor is None or successor.id == professor.id:
            db.rollback()
            raise ValueError(_t("The professor chosen for {0} was not found.", group.name))
        if group.program is not None and not teaches(successor, group.program):
            db.rollback()
            raise ValueError(_t("{0} does not teach {1}: choose someone else for {2}.", successor.name, group.program.name, group.name))
        other = _clash(db, successor, group)          # also sees the classes just given to them above
        if other is not None:
            db.rollback()
            raise ValueError(clash_message(successor, other))
        group.professor_id, group.professor_name = successor.id, successor.name
        db.flush()                                    # the next class's check must see this one (no autoflush here)
        given.append({"id": group.id, "class": group.name, "to": successor.name})
    # A finished class keeps the name of who taught it; only the link to the record goes.
    db.query(ClassGroup).filter(ClassGroup.professor_id == professor.id).update({ClassGroup.professor_id: None}, synchronize_session=False)

    # The candidates' questions still waiting for their answer come back to the inbox.
    questions = course_questions.waiting_for(db, key)
    for row in questions:
        asked = db.get(CandidateEmail, row.question_message_id) if row.question_message_id else None
        if asked is not None:
            asked.requires_admin = True
            thread = db.get(EmailThread, asked.thread_id) if asked.thread_id else None
            if thread is not None:
                thread.needs_admin = True

    account = db.query(User).filter(User.professor_id == professor.id).first()
    if account is not None:
        db.query(UserSession).filter(UserSession.user_id == account.id).delete(synchronize_session=False)
        db.delete(account)
    db.query(TeamMessage).filter(TeamMessage.person == key).delete(synchronize_session=False)
    db.query(ProfessorAnnouncement).filter(ProfessorAnnouncement.professor_id == professor.id).delete(synchronize_session=False)
    db.query(ResourceReminder).filter(ResourceReminder.professor_id == professor.id).delete(synchronize_session=False)
    db.query(BatchSession).filter(BatchSession.professor_id == professor.id).delete(synchronize_session=False)
    record = {"record": name, "email": professor.email, "specialties": professor.specialties,
              "given": given, "waiting": waiting, "account": account is not None}
    db.add(EditLog(table="professors", row_id=professor.id, action="delete", who=who, changes=json.dumps(record, ensure_ascii=False)))
    db.delete(professor)             # their absences, availabilities and specialities go with them
    db.commit()
    if questions:
        create_notification(db, title="Candidate questions are back in your inbox",
                            message=f"{name} · {len(questions)}: " + "they waited for this professor's answer. Answer them from the inbox.",
                            category="CANDIDATE_EMAIL", action_url="/inbox")
    return {"name": name, "given": given, "waiting": waiting, "account": account is not None, "questions": len(questions)}


def notice(done: dict) -> str:
    """What the admin reads after the removal, in the page's language."""
    parts = [_t("{0} is deleted.", done["name"])]
    for item in done["given"]:
        parts.append(_t("{0} now goes to {1}.", item["class"], item["to"]))
    if done["waiting"]:
        parts.append(_t("Waiting for a professor: {0}.", ", ".join(done["waiting"])))
    if done["account"]:
        parts.append(_t("Their access is removed."))
    if done["questions"]:
        parts.append(_t("{0} question(s) of candidates that waited for them are back in your inbox.", done["questions"]))
    return " ".join(parts)

"""A professor given a class is told at once: which class, which program, its days and hours, its first session, how
many candidates, and what to prepare (the course of the first weeks, the meeting link). In the application, by email
and on WhatsApp (team_messages), once per class and professor.

Whenever a class gets a professor (made by hand or by itself, or its professor changed), a line is written in the same
save (install_hooks); the reminders job sends the lines waiting (send_due). Nothing is sent from inside a save.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Session

from app.database import Base

logger = logging.getLogger(__name__)


class ProfessorAnnouncement(Base):
    __tablename__ = "professor_announcements"
    __table_args__ = (UniqueConstraint("class_group_id", "professor_id", name="uq_professor_announcement"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    class_group_id = Column(String, nullable=False, index=True)
    professor_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_at = Column(DateTime, nullable=True)


TEXT = (
    "Bonjour {professor},\n\n"
    "Vous êtes désigné(e) pour enseigner la classe {class_name} ({program}).\n"
    "Horaire : {schedule}. Première séance : {start}. Candidats inscrits : {count}.\n\n"
    "Pour bien la préparer :\n"
    "- ajoutez les ressources des premières semaines (supports, exercices, vidéos) ;\n"
    "- indiquez le lien de la réunion de la première séance (ou laissez la salle privée se créer) ;\n"
    "- regardez le niveau du groupe (tests de niveau) pour savoir par où commencer.\n\n"
    "Tout est ici : {link}"
)


def install_hooks(session_factory) -> None:
    """A class that gets a professor (new, or another one) leaves a line to send."""
    from sqlalchemy import event, inspect

    @event.listens_for(session_factory, "before_flush")
    def _note(session, flush_context, instances):
        from app.models import ClassGroup, ClassStatus

        for obj in list(session.new) + list(session.dirty):
            if type(obj).__name__ != "ClassGroup" or not obj.professor_id or obj.status == ClassStatus.COMPLETED:
                continue
            if obj in session.dirty and not inspect(obj).attrs.professor_id.history.has_changes():
                continue
            if not obj.id:
                obj.id = str(uuid.uuid4())
            if any(isinstance(item, ProfessorAnnouncement) and item.class_group_id == obj.id and item.professor_id == obj.professor_id
                   for item in session.new):
                continue
            with session.no_autoflush:
                known = (session.query(ProfessorAnnouncement.id)
                         .filter(ProfessorAnnouncement.class_group_id == obj.id, ProfessorAnnouncement.professor_id == obj.professor_id).first())
            if known is None:
                session.add(ProfessorAnnouncement(class_group_id=obj.id, professor_id=obj.professor_id))


def text_for(db: Session, group, professor, base_url: str = "") -> str:
    from app.services import enrollment_service
    from app.services.schedule_utils import class_start_dt, describe_schedule_fr, format_date_fr

    start = class_start_dt(group)
    return TEXT.format(
        professor=(professor.name or "").split()[0] if professor.name else "",
        class_name=group.name, program=group.program.name if group.program else "",
        schedule=describe_schedule_fr(group) or "à fixer", start=format_date_fr(start) if start else "à fixer",
        count=enrollment_service.enrolled_count(db, group.id), link=f"{base_url.rstrip('/')}/classes/{group.id}/course",
    )


def send_due(db: Session, base_url: str = "") -> int:
    """Send the announcements waiting. Returns how many went."""
    from app.models import ClassGroup, ClassStatus, Professor
    from app.services import team_messages

    sent = 0
    for row in db.query(ProfessorAnnouncement).filter(ProfessorAnnouncement.sent_at.is_(None)).order_by(ProfessorAnnouncement.created_at).limit(20).all():
        group = db.get(ClassGroup, row.class_group_id)
        professor = db.get(Professor, row.professor_id)
        row.sent_at = datetime.utcnow()                     # kept first: never sent twice, whatever happens next
        db.commit()
        if group is None or professor is None or group.professor_id != professor.id or group.status == ClassStatus.COMPLETED:
            continue                                        # the class changed meanwhile
        if (professor.name or "").startswith("À nommer"):
            continue
        try:
            team_messages.send(db, f"professor:{professor.id}", text_for(db, group, professor, base_url),
                               ["app", "email", "whatsapp"], author="The assistant")
            sent += 1
        except Exception:
            logger.exception("The announcement of class %s to professor %s could not be sent.", group.id, professor.id)
    return sent

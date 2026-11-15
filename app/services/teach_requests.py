"""What a professor teaches is decided by the admin. A professor never adds a program to their specialities alone:
only the admin adds it (Professors › the professor's card › Edit, or "Also teaches it"). When the admin allows it
(Settings › Classes › "Professors may ask to teach a program"), a professor can ASK from their profile; the request
waits for the admin on Professors › Who teaches what, who approves it (the program joins their specialities, and a
class waiting for a professor of it may take them) or refuses it with a word. The professor is told either way.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Professor, Program
from app.models.notification import Notification
from app.services.ui_words import t as _t

logger = logging.getLogger(__name__)


class TeachRequest(Base):
    """A professor asks to teach a program; the admin decides."""
    __tablename__ = "teach_requests"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    professor_id = Column(String, nullable=False, index=True)
    program_id = Column(String, nullable=False)
    note = Column(Text, nullable=True)                       # why they can teach it (their words)
    status = Column(String, nullable=False, default="requested")   # requested | approved | refused
    decided_by = Column(String, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


def allowed(db: Session) -> bool:
    """Whether the admin lets professors ask (Settings › Classes)."""
    from app.services.settings_service import get_settings

    return bool(get_settings(db).professor_program_requests)


def _url(row: TeachRequest) -> str:
    return f"/professors?teach={row.id}#teach-requests"


def can_ask_for(db: Session, professor: Professor) -> list[Program]:
    """The programs they could ask for: approved programs they do not teach and have not asked for already."""
    from app.services.enrollment_service import teaches

    asked = {row.program_id for row in db.query(TeachRequest).filter(TeachRequest.professor_id == professor.id,
                                                                     TeachRequest.status == "requested").all()}
    return [program for program in db.query(Program).order_by(Program.name).all()
            if program.is_approved is not False and program.id not in asked and not teaches(professor, program)]


def ask(db: Session, professor: Professor, program: Program, note: str = "") -> TeachRequest:
    """A professor asks to teach a program. Raises ValueError when they may not."""
    from app.services.enrollment_service import teaches

    if not allowed(db):
        raise ValueError(_t("Only the admin adds a program you teach: write to them in Messages."))
    if teaches(professor, program):
        raise ValueError(_t("You already teach {0}.", program.name))
    same = (db.query(TeachRequest).filter(TeachRequest.professor_id == professor.id, TeachRequest.program_id == program.id,
                                          TeachRequest.status == "requested").first())
    if same is not None:
        return same
    row = TeachRequest(professor_id=professor.id, program_id=program.id, note=(note or "").strip()[:600] or None)
    db.add(row)
    db.flush()
    db.add(Notification(recipient_role="admin", category="TEACH_REQUEST", title=f"{professor.name} asks to teach {program.name}",
                        message=(_t("{0} asks to teach {1}: approve or refuse it.", professor.name, program.name)
                                 + (f" “{row.note[:200]}”" if row.note else ""))[:500], action_url=_url(row), program_id=program.id))
    db.commit()
    return row


def _settle(db: Session, row: TeachRequest) -> None:
    for note in db.query(Notification).filter(Notification.action_url == _url(row)).all():
        note.is_read, note.status, note.read_at = True, "settled", datetime.utcnow()


def _tell(db: Session, row: TeachRequest, text: str, subject: str) -> None:
    from app.services import team_messages
    from app.services.settings_service import public_base_url

    try:
        team_messages.send(db, f"professor:{row.professor_id}", text, ["app", "email"], author="The assistant", subject=subject,
                           cta=("Mon profil", f"{public_base_url(db).rstrip('/')}/teacher/profile"))
    except Exception:
        db.rollback()
        logger.exception("Teach request %s: the professor could not be told.", row.id)


def approve(db: Session, row: TeachRequest, *, by: str = "Admin") -> str | None:
    """The program joins their specialities. Returns what is wrong, or None."""
    from app.services.enrollment_service import sync_class_lifecycle, teaches

    professor, program = db.get(Professor, row.professor_id), db.get(Program, row.program_id)
    if row.status != "requested" or professor is None or program is None:
        return _t("This request is already decided.")
    if not teaches(professor, program):
        known = [item.strip() for item in (professor.specialties or "").split(",") if item.strip()]
        professor.specialties = ", ".join(dict.fromkeys(known + [program.name]))[:500]
    row.status, row.decided_by, row.decided_at = "approved", by, datetime.utcnow()
    _settle(db, row)
    db.commit()
    sync_class_lifecycle(db)            # a class waiting for a professor of this program may take them
    first = ((professor.name or "").split() or [""])[0]
    _tell(db, row, f"Bonjour {first},\n\nC'est validé : « {program.name} » fait maintenant partie des formations que vous enseignez. "
                   "Des classes de cette formation peuvent vous être confiées.", f"Vous enseignez maintenant {program.name}")
    return None


def refuse(db: Session, row: TeachRequest, *, by: str = "Admin", note: str = "") -> str | None:
    professor, program = db.get(Professor, row.professor_id), db.get(Program, row.program_id)
    if row.status != "requested" or professor is None or program is None:
        return _t("This request is already decided.")
    row.status, row.decided_by, row.decided_at = "refused", by, datetime.utcnow()
    row.decision_note = (note or "").strip()[:600] or None
    _settle(db, row)
    db.commit()
    first = ((professor.name or "").split() or [""])[0]
    _tell(db, row, f"Bonjour {first},\n\nVotre demande d'enseigner « {program.name} » n'est pas acceptée pour le moment."
                   + (f"\n\n[box] {row.decision_note}" if row.decision_note else ""), f"Votre demande pour {program.name}")
    return None


def _view(db: Session, row: TeachRequest) -> dict:
    return {"request": row, "professor": db.get(Professor, row.professor_id), "program": db.get(Program, row.program_id)}


def waiting(db: Session) -> list[dict]:
    rows = db.query(TeachRequest).filter(TeachRequest.status == "requested").order_by(TeachRequest.created_at).all()
    return [item for item in (_view(db, row) for row in rows) if item["professor"] is not None and item["program"] is not None]


def of_professor(db: Session, professor: Professor) -> list[dict]:
    rows = (db.query(TeachRequest).filter(TeachRequest.professor_id == professor.id).order_by(TeachRequest.created_at.desc()).limit(10).all())
    return [item for item in (_view(db, row) for row in rows) if item["program"] is not None]

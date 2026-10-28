import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from app.database import Base


class ClassWeek(Base):
    """One week of a class: what was taught, and where its questions stand."""

    __tablename__ = "class_weeks"
    __table_args__ = (UniqueConstraint("class_group_id", "week_number", name="uq_class_week"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    class_group_id = Column(String, ForeignKey("class_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    week_number = Column(Integer, nullable=False)

    # What the professor says was covered; the system learns from it like from a file.
    notes = Column(Text, nullable=True)

    # idle | queued | writing | ready | failed
    questions_status = Column(String, default="idle", nullable=False)
    questions_note = Column(Text, nullable=True)    # why nothing could be written, in plain words
    questions_updated_at = Column(DateTime, nullable=True)

    sent_at = Column(DateTime, nullable=True)       # when the candidates received their questions

    # The week's Google Form, when answers go through Google Forms and Firebase.
    # form_map (JSON) says which form question is which: see answer_sync.
    form_id = Column(String, nullable=True, index=True)
    form_url = Column(String, nullable=True)
    form_map = Column(Text, nullable=True)
    form_checked_at = Column(DateTime, nullable=True)
    form_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class ClassMaterial(Base):
    """A course file the professor added to one week of a class.

    The content is on disk; `learned_text` is what the system could read in it.
    """

    __tablename__ = "class_materials"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    class_group_id = Column(String, ForeignKey("class_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    week_number = Column(Integer, nullable=False, index=True)

    filename = Column(String, nullable=False)       # the name people see
    stored_name = Column(String, nullable=False)    # the file inside the class files folder
    content_type = Column(String, nullable=False)
    size = Column(Integer, default=0, nullable=False)
    kind = Column(String, nullable=False)           # document | video | photo | audio

    note = Column(Text, nullable=True)              # what the file shows, written by the professor
    learned_text = Column(Text, nullable=True)
    # read | nothing | unreadable | notes_only
    learned_status = Column(String, default="nothing", nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    # Checked against the class's program (program_match): None or "ok" = used; "waiting" = the admin decides.
    review_status = Column(String, nullable=True, index=True)
    fit = Column(String, nullable=True)                 # fits | other | off | unknown
    fit_program_id = Column(String, nullable=True)      # the program it looks like part of
    fit_words = Column(Text, nullable=True)             # JSON list: the words that decided
    uploaded_by = Column(String, nullable=True)
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)



class WeekQuestion(Base):
    """One question about what a class learned in a week."""

    __tablename__ = "week_questions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    class_group_id = Column(String, ForeignKey("class_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    week_number = Column(Integer, nullable=False, index=True)

    question = Column(Text, nullable=False)
    options = Column(Text, nullable=False)          # JSON list of the choices
    correct_answer = Column(Integer, nullable=False)
    explanation = Column(Text, nullable=True)

    # Where the answer is in the course: the passage it was written from.
    material_id = Column(String, nullable=True)
    source_name = Column(String, nullable=True)
    source_excerpt = Column(Text, nullable=True)

    # ready = candidates can get it | review = waits for the professor | off = set aside
    status = Column(String, default="review", nullable=False, index=True)
    review_reason = Column(String, nullable=True)
    author = Column(String, default="system", nullable=False)   # system | admin

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    @property
    def choices(self) -> list[str]:
        try:
            loaded = json.loads(self.options or "[]")
        except (TypeError, ValueError):
            return []
        return [str(item) for item in loaded] if isinstance(loaded, list) else []


class AdviceSnooze(Base):
    """A recommendation the admin asked not to see for a while."""

    __tablename__ = "advice_snoozes"

    key = Column(String, primary_key=True)
    until = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class ProgramFile(Base):
    """A file of a program itself (its plan, a brochure, a course), read like a course file. The program's details can be
    proposed from it, and the assistant learns from it once it is accepted (checked against the program like course files)."""

    __tablename__ = "program_files"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    program_id = Column(String, ForeignKey("programs.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    stored_name = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    size = Column(Integer, default=0, nullable=False)
    kind = Column(String, nullable=False)
    note = Column(Text, nullable=True)
    learned_text = Column(Text, nullable=True)
    learned_status = Column(String, nullable=True)
    review_status = Column(String, nullable=True, index=True)
    fit = Column(String, nullable=True)
    fit_program_id = Column(String, nullable=True)
    fit_words = Column(Text, nullable=True)
    uploaded_by = Column(String, nullable=True)
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

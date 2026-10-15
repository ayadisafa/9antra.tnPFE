import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Date, DateTime, Time, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class ProfessorAvailability(Base):
    __tablename__ = "professor_availability"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    professor_id = Column(String, ForeignKey("professors.id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # Monday=0, Sunday=6
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    professor = relationship("Professor", back_populates="availability_slots")


class CourseBatch(Base):
    __tablename__ = "course_batches"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    program_id = Column(String, ForeignKey("programs.id"), nullable=False)
    name = Column(String, nullable=False)
    start_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    program = relationship("Program")
    sessions = relationship("BatchSession", back_populates="batch", cascade="all, delete-orphan")


class BatchSession(Base):
    __tablename__ = "batch_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id = Column(String, ForeignKey("course_batches.id", ondelete="CASCADE"), nullable=False)
    module_id = Column(String, ForeignKey("weekly_curriculum.id"), nullable=False)
    professor_id = Column(String, ForeignKey("professors.id"), nullable=False)
    session_start = Column(DateTime, nullable=False)
    session_end = Column(DateTime, nullable=False)
    batch = relationship("CourseBatch", back_populates="sessions")
    module = relationship("WeeklyCurriculum")
    professor = relationship("Professor")

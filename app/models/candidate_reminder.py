import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class CandidateReminder(Base):
    __tablename__ = "candidate_reminders"
    __table_args__ = (
        UniqueConstraint("registration_id", "reminder_key", name="uq_registration_reminder"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id = Column(String, ForeignKey("candidates.id"), nullable=False, index=True)
    registration_id = Column(String, ForeignKey("registrations.id"), nullable=False, index=True)
    reminder_key = Column(String, nullable=False)
    reminder_type = Column(String, nullable=False)
    planned_for = Column(DateTime, nullable=False, index=True)
    sent_at = Column(DateTime, nullable=True)
    status = Column(String, default="planned", nullable=False, index=True)
    strategy = Column(Text, nullable=True)
    attempts = Column(Integer, default=0, nullable=False)
    last_error = Column(Text, nullable=True)

    candidate = relationship("Candidate")
    registration = relationship("Registration")

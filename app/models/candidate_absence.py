import uuid
from datetime import date
from sqlalchemy import Column, Date, ForeignKey, String, Text
from sqlalchemy.orm import relationship
from app.database import Base


class CandidateAbsence(Base):
    __tablename__ = "candidate_absences"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id = Column(String, ForeignKey("candidates.id"), nullable=False)
    registration_id = Column(String, ForeignKey("registrations.id"), nullable=False)
    session_id = Column(String, ForeignKey("class_sessions.id"), nullable=True)
    absence_date = Column(Date, nullable=False)
    reason = Column(Text, nullable=True)
    status = Column(String, default="reported", nullable=False)

    candidate = relationship("Candidate")
    registration = relationship("Registration")
    session = relationship("ClassSession")
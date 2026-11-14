import uuid
from datetime import datetime

from sqlalchemy import Column, String, Date, DateTime, Text, Time, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class ProfessorAbsence(Base):
    """A professor away: one day or a period (absence_date to end_date), the whole day or some hours of each day.

    status: "requested" (the professor asked; the admin decides because nobody can replace them for a session),
    "accepted" (noted by the admin, or accepted: their sessions look for a replacement), "refused" (the admin said no;
    the professor answers with the link in their message), "insists" (refused, and the professor answered they still
    cannot come: the admin decides again), "cancelled". An absence noted before requests existed has no status:
    it counts as accepted. See services/absences.py.
    """
    __tablename__ = "professor_absences"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    professor_id = Column(String, ForeignKey("professors.id", ondelete="CASCADE"), nullable=False)
    absence_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)                 # the last day away (empty: that day only)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    reason = Column(String, nullable=True)
    status = Column(String, nullable=True, default="accepted")
    requested_by = Column(String, nullable=True)           # "professor" | "admin"
    decided_by = Column(String, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, nullable=True)            # the admin's words when refusing
    token = Column(String, nullable=True, index=True)      # the professor's link to answer a refusal
    response = Column(Text, nullable=True)                 # what the professor answered to the refusal
    responded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.utcnow)
    professor = relationship("Professor", back_populates="absences")

    @property
    def last_day(self):
        return self.end_date if self.end_date and self.end_date >= self.absence_date else self.absence_date

    @property
    def state(self) -> str:
        return self.status or "accepted"

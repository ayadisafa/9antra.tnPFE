import uuid
from sqlalchemy import Column, String, Date, Time, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class ProfessorAbsence(Base):
    __tablename__ = "professor_absences"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    professor_id = Column(String, ForeignKey("professors.id", ondelete="CASCADE"), nullable=False)
    absence_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    reason = Column(String, nullable=True)
    professor = relationship("Professor", back_populates="absences")

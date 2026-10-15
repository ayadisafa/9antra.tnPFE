import uuid
from sqlalchemy import Column, String, Text, Boolean, Date
from sqlalchemy.orm import relationship
from .skill import professor_skills
from app.database import Base


class Professor(Base):
    __tablename__ = "professors"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    specialties = Column(Text, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    is_available = Column(Boolean, default=True, nullable=False)
    available_from = Column(Date, nullable=True)
    available_until = Column(Date, nullable=True)
    skills = relationship("Skill", secondary=professor_skills, back_populates="professors")
    availability_slots = relationship("ProfessorAvailability", back_populates="professor", cascade="all, delete-orphan")
    absences = relationship("ProfessorAbsence", back_populates="professor", cascade="all, delete-orphan")

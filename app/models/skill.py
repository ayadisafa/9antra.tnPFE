import uuid
from sqlalchemy import Column, String, Table, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


professor_skills = Table(
    "professor_skills",
    Base.metadata,
    Column("professor_id", String, ForeignKey("professors.id", ondelete="CASCADE"), primary_key=True),
    Column("skill_id", String, ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True),
)


class Skill(Base):
    __tablename__ = "skills"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True, index=True)
    professors = relationship("Professor", secondary=professor_skills, back_populates="skills")

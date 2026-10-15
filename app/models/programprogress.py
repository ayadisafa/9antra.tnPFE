import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, Float, ForeignKey, DateTime, Enum
from sqlalchemy.orm import relationship, validates

from app.database import Base


class EngagementLevel(str, enum.Enum):
    HIGH = "HIGH"         # Active participant, submits on time
    MEDIUM = "MEDIUM"     # Moderate interaction
    LOW = "LOW"           # Minimal interaction, missing sessions
    INACTIVE = "INACTIVE" # At risk of dropping out


class ProgramProgress(Base):
    __tablename__ = "program_progress"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    
    # Foreign Keys with cascading deletes
    candidate_id = Column(
        String, 
        ForeignKey("candidates.id", ondelete="CASCADE"), 
        nullable=False,
        index=True,
    )
    registration_id = Column(
        String, 
        ForeignKey("registrations.id", ondelete="CASCADE"), 
        nullable=False,
        index=True,
    )
    current_curriculum_id = Column(
        String,
        ForeignKey("weekly_curriculum.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    # Progress Tracking
    current_week = Column(Integer, default=1, nullable=False)
    total_weeks = Column(Integer, default=12, nullable=False)
    completion_percentage = Column(Float, default=0.0, nullable=False)
    
    # Engagement Tracking
    engagement_score = Column(Float, default=5.0, nullable=False)
    engagement_level = Column(
        Enum(EngagementLevel), 
        default=EngagementLevel.MEDIUM, 
        nullable=False
    )
    attendance_rate = Column(Float, default=100.0, nullable=False)
    
    last_activity_at = Column(
        DateTime, 
        default=lambda: datetime.now(timezone.utc), 
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    candidate = relationship("Candidate", back_populates="program_progress")
    registration = relationship("Registration", back_populates="program_progress")    
    current_curriculum = relationship(
        "WeeklyCurriculum", 
        back_populates="program_progress"
    )
    
    @validates("current_week", "total_weeks")
    def update_completion_percentage(self, key, value):
        """Automatically keeps completion_percentage in sync whenever current_week or total_weeks is updated."""
        weeks = value if key == "current_week" else (self.current_week or 1)
        total = value if key == "total_weeks" else (self.total_weeks or 12)

        if total and total > 0:
            self.completion_percentage = round(min((weeks / total) * 100.0, 100.0), 2)
        return value
# app/models/class_group.py
import os
import uuid
from enum import Enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column, 
    Integer, 
    String, 
    ForeignKey, 
    DateTime, 
    Boolean, 
    Time, 
    Enum as SQLEnum
)
from sqlalchemy.orm import relationship

from app.database import Base


class ClassStatus(str, Enum):
    NOT_STARTED = "not_started"
    ONGOING = "ongoing"
    COMPLETED = "completed"

    # Compatibility aliases for legacy routes
    UPCOMING = "not_started"
    IN_PROGRESS = "ongoing"


class ClassGroup(Base):
    __tablename__ = "class_groups"

    id = Column(
        String, 
        primary_key=True, 
        default=lambda: str(uuid.uuid4()), 
        index=True
    )
    name = Column(
        String, 
        nullable=False, 
        unique=True, 
        index=True
    )
    
    # Foreign Keys
    program_id = Column(
        String, 
        ForeignKey("programs.id", ondelete="CASCADE"), 
        nullable=True,
        index=True
    )
    professor_id = Column(
        String, 
        ForeignKey("professors.id", ondelete="SET NULL"), 
        nullable=True,
        index=True
    )
    professor_name = Column(String, nullable=True)

    # Capacity & Schedule Attributes
    capacity = Column(
        Integer, 
        default=lambda: int(os.getenv("DEFAULT_CLASS_CAPACITY", "30")),
        nullable=False
    )
    max_capacity = Column(
        Integer, 
        default=lambda: int(os.getenv("DEFAULT_CLASS_CAPACITY", "30")),
        nullable=False
    )
    
    status = Column(
        SQLEnum(ClassStatus), 
        default=ClassStatus.NOT_STARTED, 
        nullable=False
    )
    
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    total_weeks = Column(Integer, default=12, nullable=False)
    current_week = Column(Integer, default=0, nullable=False)
    
    days_of_week = Column(String, nullable=True)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    room = Column(String, nullable=True)
    
    auto_start_on_full = Column(Boolean, default=False, nullable=False)

    # Start rule, editable per class from its parameters section.
    # days_after_creation | min_enrollment | fixed_date
    start_mode = Column(String, default="days_after_creation", nullable=False)
    start_delay_days = Column(Integer, default=10, nullable=False)
    min_students_to_start = Column(Integer, default=5, nullable=False)
    min_start_notice_days = Column(Integer, default=3, nullable=False)
    reminder_days = Column(String, default="7,3,1", nullable=False)
    auto_created = Column(Boolean, default=False, nullable=False)

    # Weekly questions: "manual" = sent when the professor asks, "auto" = sent
    # by the system once the week's session is over.
    weekly_questions_mode = Column(String, default="manual", nullable=True)
    weekly_questions_count = Column(Integer, default=5, nullable=True)

    # How the class is recognised at a glance: an emoji and a colour.
    # Empty = chosen by the system from the class's name (see class_looks).
    symbol = Column(String, nullable=True)
    color = Column(String, nullable=True)

    created_at = Column(
        DateTime, 
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Bidirectional Relationships
    program = relationship("Program", back_populates="class_groups")
    professor = relationship("Professor")
    registrations = relationship("Registration", back_populates="class_group")
    sessions = relationship(
        "ClassSession", 
        back_populates="class_group", 
        cascade="all, delete-orphan"
    )
    
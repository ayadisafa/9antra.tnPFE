import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Float,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Program(Base):
    __tablename__ = "programs"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    name = Column(
        String,
        nullable=False,
        index=True,
    )

    description = Column(
        Text,
        nullable=True,
    )

    total_duration_days = Column(
        Integer,
        nullable=True,
    )

    embedding = Column(
        Text,
        nullable=True,
    )

    price_online = Column(
        Float,
        default=390.0,
        nullable=False,
    )

    price_onsite = Column(
        Float,
        default=490.0,
        nullable=False,
    )

    hours_per_week = Column(
        Integer,
        default=0,
        nullable=False,
    )

    training_hours = Column(
        Integer,
        default=0,
        nullable=False,
    )

    supervision_hours = Column(
        Integer,
        default=0,
        nullable=False,
    )

    class_session_time = Column(
        String,
        nullable=True,
    )

    is_approved = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    # The picture shown next to the program. The file is in the program icons
    # folder; icon_ref says where it came from ("logos:flutter" for an icon
    # found on the web, "upload" for the admin's own, "none" when removed).
    icon_file = Column(
        String,
        nullable=True,
    )

    icon_ref = Column(
        String,
        nullable=True,
    )

    icon_checked_at = Column(
        DateTime,
        nullable=True,
    )

    # The formation of the public website this program follows, and the values
    # last taken from it (a field the admin changed since is left alone).
    website_slug = Column(
        String,
        nullable=True,
        index=True,
    )

    website_values = Column(
        Text,
        nullable=True,
    )

    website_synced_at = Column(
        DateTime,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    levels = relationship(
        "ProgramLevel",
        back_populates="program",
        cascade="all, delete-orphan",
        order_by="ProgramLevel.level_order",
    )

    notifications = relationship(
        "Notification",
        back_populates="program",
        cascade="all, delete-orphan",
    )

    class_groups = relationship(
        "ClassGroup",
        back_populates="program",
        cascade="all, delete-orphan",
    )

    def set_embedding(self, vector_list: list[float]) -> None:
        """Serialize an embedding vector for SQLite storage."""
        self.embedding = json.dumps(vector_list)

    def get_embedding(self) -> list[float] | None:
        """Deserialize the stored embedding vector."""
        if not self.embedding:
            return None

        try:
            value = json.loads(self.embedding)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

        return value if isinstance(value, list) else None


class ProgramLevel(Base):
    __tablename__ = "program_levels"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    program_id = Column(
        String,
        ForeignKey(
            "programs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    level_order = Column(
        Integer,
        nullable=False,
    )

    title = Column(
        String,
        nullable=False,
    )

    description = Column(
        Text,
        nullable=True,
    )

    hours_per_week = Column(
        Integer,
        nullable=True,
    )

    class_session_time = Column(
        String,
        nullable=True,
    )

    program = relationship(
        "Program",
        back_populates="levels",
    )

    weeks = relationship(
        "WeeklyCurriculum",
        back_populates="level",
        cascade="all, delete-orphan",
        order_by="WeeklyCurriculum.week_number",
    )


class WeeklyCurriculum(Base):
    """
    Shared curriculum content for one program level and week.

    This table must not contain candidate-specific progress. The same
    curriculum is shared by all candidates taking the program.
    """

    __tablename__ = "weekly_curriculum"

    __table_args__ = (
        UniqueConstraint(
            "level_id",
            "week_number",
            name="uq_curriculum_level_week",
        ),
    )

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    level_id = Column(
        String,
        ForeignKey(
            "program_levels.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    week_number = Column(
        Integer,
        nullable=False,
    )

    topic_title = Column(
        String,
        nullable=False,
    )

    learning_objectives = Column(
        Text,
        nullable=True,
    )

    required_skill_id = Column(
        String,
        ForeignKey(
            "skills.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    # Relationships
    level = relationship(
        "ProgramLevel",
        back_populates="weeks",
    )

    required_skill = relationship(
        "Skill",
    )

    resources = relationship(
        "CurriculumResource",
        back_populates="week",
        cascade="all, delete-orphan",
        order_by="CurriculumResource.created_at",
    )

    # Candidate progress tracking relationship
    program_progress = relationship(
        "ProgramProgress",
        back_populates="current_curriculum",
    )


class CurriculumResource(Base):
    """
    PDF, video, or external learning resource attached to a curriculum week.
    """

    __tablename__ = "curriculum_resources"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    week_id = Column(
        String,
        ForeignKey(
            "weekly_curriculum.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    resource_type = Column(
        String,
        nullable=False,
    )
    # Supported values: pdf, video, link

    title = Column(
        String,
        nullable=False,
    )

    url = Column(
        String,
        nullable=False,
    )

    description = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    week = relationship(
        "WeeklyCurriculum",
        back_populates="resources",
    )
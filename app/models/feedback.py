import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class CandidateFeedback(Base):
    __tablename__ = "candidate_feedback"

    __table_args__ = (
        UniqueConstraint(
            "source",
            "external_id",
            name="uq_feedback_source_external_id",
        ),
    )

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Nullable because Facebook reviews may not identify a local candidate.
    candidate_id = Column(
        String,
        ForeignKey("candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    registration_id = Column(
        String,
        ForeignKey("registrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # facebook, email, google_form, manual
    source = Column(
        String,
        nullable=False,
        index=True,
    )

    # Facebook review ID, email message ID, or local/manual identifier.
    external_id = Column(
        String,
        nullable=True,
    )

    author_name = Column(
        String,
        nullable=True,
    )

    author_email = Column(
        String,
        nullable=True,
    )

    author_profile_url = Column(
        String,
        nullable=True,
    )

    review_url = Column(
        String,
        nullable=True,
    )

    program_name = Column(
        String,
        nullable=True,
        index=True,
    )

    rating = Column(
        Integer,
        nullable=True,
    )

    comment = Column(
        Text,
        nullable=True,
    )

    language = Column(
        String,
        nullable=True,
    )

    sentiment = Column(
        String,
        nullable=True,
    )

    sentiment_score = Column(
        Integer,
        nullable=True,
    )

    # pending, published, hidden, needs_review
    status = Column(
        String,
        default="published",
        nullable=False,
        index=True,
    )

    submitted_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    imported_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    raw_payload = Column(
        Text,
        nullable=True,
    )

    candidate = relationship(
        "Candidate",
    )

    registration = relationship(
        "Registration",
    )

    @property
    def source_label(self) -> str:
        return {
            "facebook": "Facebook",
            "email": "Email",
            "google_form": "Google Forms",
            "manual": "Manual",
        }.get(self.source, self.source.title())

    @property
    def rating_value(self) -> int:
        return self.rating or 0
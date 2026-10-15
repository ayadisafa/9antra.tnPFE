import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.orm import relationship, validates

from app.database import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    avatar_url = Column(String, nullable=True)
    # Add these fields to Candidate class:
    photo_url = Column(String, nullable=True)  # Upload to storage
    instagram_handle = Column(String, nullable=True)  # @username
    facebook_url = Column(String, nullable=True)  # Full URL
    admin_notes = Column(Text, nullable=True)  # Admin-only annotations
    preferred_language = Column(String, default="fr", nullable=False)

    full_name = Column(
        String,
        nullable=True,
    )

    email = Column(
        String,
        unique=True,
        index=True,
        nullable=False,
    )

    phone = Column(
        String,
        nullable=True,
    )

    @validates("phone")
    def _international_phone(self, key, value):
        """+216 goes in front unless the number names another country."""
        from app.services.whatsapp import normalise     # here: the service imports this model

        return normalise(value) or (str(value).strip() or None if value is not None else None)

    university = Column(
        String,
        nullable=True,
    )

    city = Column(
        String,
        nullable=True,
    )

    status = Column(
        String,
        default="pending",
        nullable=True,
    )

    # Candidate profile fields
    photo_url = Column(
        String,
        nullable=True,
    )

    instagram_url = Column(
        String,
        nullable=True,
    )

    facebook_url = Column(
        String,
        nullable=True,
    )

    preferred_language = Column(
        String,
        default="fr",
        nullable=False,
    )

    admin_notes = Column(
        Text,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
    )

    # Relationships
    registrations = relationship(
        "Registration",
        back_populates="candidate",
        cascade="all, delete-orphan",
    )

    activities = relationship(
        "Activity",
        back_populates="candidate",
        cascade="all, delete-orphan",
    )

    notifications = relationship(
        "Notification",
        back_populates="candidate",
        cascade="all, delete-orphan",
    )

    program_progress = relationship(
        "ProgramProgress",
        back_populates="candidate",
        uselist=False,
    )

    
    # Dynamic totals for the candidate profile
    @property
    def overall_total_price(self) -> float:
        """Return the total price of all registrations."""
        return sum(
            (
                registration.total_amount
                or registration.total_price
                or 0.0
            )
            for registration in self.registrations
        )

    @property
    def overall_paid_amount(self) -> float:
        """Return the amount paid across all registrations."""
        return sum(
            registration.paid_amount or 0.0
            for registration in self.registrations
        )

    @property
    def overall_remaining_amount(self) -> float:
        """Return the remaining balance across all registrations."""
        return max(
            0.0,
            self.overall_total_price - self.overall_paid_amount,
        )
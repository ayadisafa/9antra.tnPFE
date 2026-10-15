import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import relationship

from app.database import Base


class RegistrationBias(Base):
    """
    Stores ambiguous or potentially duplicated program names.

    There are two supported uses:

    1. Global program aliases approved by the admin:
       - registration_id = None
       - candidate_id = None

    2. Candidate-specific program reviews:
       - registration_id = the affected registration
       - candidate_id = the affected candidate
    """

    __tablename__ = "registration_biases"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Nullable for global aliases seeded independently of a candidate.
    registration_id = Column(
        String,
        ForeignKey(
            "registrations.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    # Nullable for global aliases seeded independently of a candidate.
    candidate_id = Column(
        String,
        ForeignKey(
            "candidates.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    # Original program name received from Firebase or another external source.
    raw_input_program = Column(
        String,
        nullable=False,
        index=True,
    )

    # Closest existing program, when one was found.
    suggested_program_id = Column(
        String,
        ForeignKey(
            "programs.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    # Cosine similarity score, normally between 0.0 and 1.0.
    similarity_score = Column(
        Float,
        nullable=False,
        default=0.0,
    )

    # Supported values:
    # pending      -> administrator review is required
    # approved     -> use suggested_program_id
    # created_new  -> administrator approved a new program
    admin_status = Column(
        String,
        nullable=False,
        default="pending",
        index=True,
    )

    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    registration = relationship(
        "Registration",
        back_populates="bias_reviews",
    )

    candidate = relationship(
        "Candidate",
    )

    suggested_program = relationship(
        "Program",
    )
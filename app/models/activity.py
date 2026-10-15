from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base
from datetime import datetime
import uuid


class Activity(Base):

    __tablename__ = "activities"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4())
    )

    candidate_id = Column(
        String,
        ForeignKey("candidates.id")
    )

    event_type = Column(String)

    details = Column(String)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    candidate = relationship(
        "Candidate",
        back_populates="activities"
    )
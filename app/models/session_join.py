import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.database import Base


class SessionJoin(Base):
    """A candidate's own link to one session's online meeting, and when they used it.

    The meeting's address is never sent: each candidate gets this private link,
    which lets them in and notes the time they arrived.
    """

    __tablename__ = "session_joins"
    __table_args__ = (UniqueConstraint("session_id", "registration_id", name="uq_session_join"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("class_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    registration_id = Column(String, ForeignKey("registrations.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id = Column(String, nullable=True, index=True)
    token = Column(String, nullable=False, unique=True, index=True)
    sent_at = Column(DateTime, nullable=True)               # when the link was emailed
    first_joined_at = Column(DateTime, nullable=True)       # local time of the first entry
    last_joined_at = Column(DateTime, nullable=True)
    joins = Column(Integer, default=0, nullable=False)      # how many times the link was used
    minutes_late = Column(Integer, nullable=True)           # at the first entry; 0 = on time
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

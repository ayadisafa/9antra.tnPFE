import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import foreign, relationship

from app.database import Base
from app.models.candidate_email import CandidateEmail


class EmailThread(Base):
    """One discussion with a candidate: every message of it, in order."""

    __tablename__ = "email_threads"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id = Column(String, ForeignKey("candidates.id"), nullable=False, index=True)
    registration_id = Column(String, ForeignKey("registrations.id"), nullable=True, index=True)
    subject = Column(String, nullable=True)

    # processing | needs_admin | answered | received | sent | closed
    status = Column(String, default="sent", nullable=False, index=True)
    needs_admin = Column(Boolean, default=False, nullable=False, index=True)
    unread = Column(Boolean, default=False, nullable=False)

    message_count = Column(Integer, default=0, nullable=False)
    inbound_count = Column(Integer, default=0, nullable=False)
    last_message_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    last_direction = Column(String, nullable=True)
    last_author = Column(String, nullable=True)
    last_status = Column(String, nullable=True)
    last_snippet = Column(String, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    closed_at = Column(DateTime, nullable=True)

    candidate = relationship("Candidate")
    registration = relationship("Registration")
    messages = relationship(
        CandidateEmail,
        primaryjoin=lambda: EmailThread.id == foreign(CandidateEmail.thread_id),
        order_by=lambda: CandidateEmail.received_at,
        viewonly=True,
    )


class MailboxState(Base):
    """Where the inbox reader stopped, so each message is imported once."""

    __tablename__ = "mailbox_state"

    id = Column(String, primary_key=True, default="default")
    uidvalidity = Column(String, nullable=True)
    last_uid = Column(Integer, default=0, nullable=False)
    last_checked_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    imported_count = Column(Integer, default=0, nullable=False)

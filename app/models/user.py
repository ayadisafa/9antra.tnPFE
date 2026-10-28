import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, Text
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    """Someone who can sign in: the admin, or a professor."""

    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, nullable=False, unique=True, index=True)     # what they sign in with
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="admin", nullable=False)             # admin | professor
    professor_id = Column(String, ForeignKey("professors.id", ondelete="CASCADE"), nullable=True, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    # A team member's access: {part: none | view | edit} as JSON (see services/access.py).
    permissions = Column(Text, nullable=True)
    phone = Column(String, nullable=True)        # for WhatsApp and SMS from the team chat
    # Forgot password: the fingerprint of the one-use link sent by email, and until when it works.
    reset_hash = Column(String, nullable=True, index=True)
    reset_until = Column(DateTime, nullable=True)

    professor = relationship("Professor")


class UserSession(Base):
    """A browser that is signed in. The cookie holds a random token; only its
    fingerprint is kept here, so reading this table does not let anyone in."""

    __tablename__ = "user_sessions"

    token_hash = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    device = Column(String, nullable=True)           # "Chrome on Windows", from the browser
    last_seen_at = Column(DateTime, nullable=True)


class Attendance(Base):
    """Whether a candidate was at one session of their class."""

    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("session_id", "registration_id", name="uq_attendance_session_registration"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    class_group_id = Column(String, ForeignKey("class_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(String, ForeignKey("class_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    registration_id = Column(String, ForeignKey("registrations.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id = Column(String, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String, default="present", nullable=False)          # present | absent | late
    note = Column(String, nullable=True)
    marked_by = Column(String, nullable=True)                           # who recorded it
    # What the professor noted of the candidate in this session.
    participation = Column(Integer, nullable=True)                      # 1 quiet, 2 takes part, 3 very active
    questions = Column(Integer, nullable=True)                          # questions they asked
    marked_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

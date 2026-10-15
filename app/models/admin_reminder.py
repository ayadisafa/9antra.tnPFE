import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database import Base


class AdminReminder(Base):
    """A reminder the admin wrote: to whom, what it says, and when it goes."""

    __tablename__ = "admin_reminders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    audience = Column(String, nullable=False)          # candidate | class | program | unpaid | everyone
    audience_id = Column(String, nullable=True)
    audience_label = Column(String, nullable=False, default="")
    subject = Column(String, nullable=False)
    title = Column(String, nullable=True)
    body = Column(Text, nullable=False)
    style = Column(String, nullable=False, default="brand")
    send_at = Column(DateTime, nullable=False, index=True)      # UTC
    status = Column(String, nullable=False, default="scheduled", index=True)   # scheduled | sending | sent | cancelled
    sent_at = Column(DateTime, nullable=True)
    sent_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

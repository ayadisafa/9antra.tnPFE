import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database import Base


class AdminReminder(Base):
    """A reminder the admin wrote: to whom, what it says, and when it goes."""

    __tablename__ = "admin_reminders"
    design = Column(Text, nullable=True)      # email_layout.clean_design, as JSON (see the automatic emails)

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
    channel = Column(String, nullable=True, default="email")     # email | both | whatsapp
    repeat = Column(String, nullable=True, default="once")       # once | weekly | monthly | absence (each time someone is marked absent)
    until = Column(DateTime, nullable=True)                      # a repeated reminder stops after this day (UTC)
    times = Column(Integer, nullable=False, default=0)           # how many times it went
    done_keys = Column(Text, nullable=True)                      # absence / session: what was already answered (JSON)
    hours_before = Column(Integer, nullable=True)                # session: how many hours before each session it goes
    kind = Column(String, nullable=True)       # its kind in the catalogue (services/reminder_kinds.py): its picture on the board
    every = Column(String, nullable=True)      # repeat "every": how often ("30min", "2w", "1mo"…: services/durations.py)
    offset = Column(String, nullable=True)     # a watching reminder: how long before / after what it waits for
    created_at = Column(DateTime, default=datetime.utcnow)

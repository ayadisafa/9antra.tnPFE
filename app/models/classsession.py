import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Date, Time, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class ClassSession(Base):
    __tablename__ = "class_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    class_group_id = Column(String, ForeignKey("class_groups.id", ondelete="CASCADE"), nullable=False)
    week_number = Column(Integer, nullable=False)
    session_date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    topic = Column(String, nullable=False)
    notes = Column(Text, nullable=True)
    # The online meeting of this session. Candidates never get it: each has a link of their own (see meetings).
    meeting_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    class_group = relationship("ClassGroup", back_populates="sessions")

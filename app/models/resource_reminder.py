"""A reminder sent to a professor to add the resources of one week of one class (services/resource_reminders.py)."""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint

from app.database import Base


class ResourceReminder(Base):
    """One reminder sent for one week of one class (never twice)."""
    __tablename__ = "resource_reminders"
    __table_args__ = (UniqueConstraint("class_group_id", "week_number", name="uq_resource_reminder_week"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    class_group_id = Column(String, nullable=False, index=True)
    week_number = Column(Integer, nullable=False)
    professor_id = Column(String, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)

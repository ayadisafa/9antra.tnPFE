import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, Text

from app.database import Base


class TestMark(Base):
    """What the admin found when testing one functionality (System › Control room): it works, or a problem and what."""
    __tablename__ = "test_marks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    key = Column(String, nullable=False, unique=True, index=True)      # "new:…", "page:/inbox", "auto:welcome", "yasmine:send"
    status = Column(String, nullable=False)                            # ok | problem
    note = Column(Text, nullable=True)
    by = Column(String, nullable=True)
    at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

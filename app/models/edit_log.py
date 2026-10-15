import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from app.database import Base


class EditLog(Base):
    """A change made from the data pages: who, when, what it was and what it became."""

    __tablename__ = "edit_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    at = Column(DateTime, default=datetime.utcnow, index=True)
    who = Column(String, nullable=True)
    table = Column(String, nullable=False, index=True)
    row_id = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)          # add | edit | delete
    changes = Column(Text, nullable=True)            # {field: [before, after]}

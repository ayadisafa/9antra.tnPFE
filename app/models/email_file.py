import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.database import Base


class EmailFile(Base):
    """A photo or a document that goes with an email: added by the admin, or
    received from a candidate. The content is on disk; this row describes it."""

    __tablename__ = "email_files"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String, nullable=False)       # the name people see
    content_type = Column(String, nullable=False)
    size = Column(Integer, default=0, nullable=False)
    stored_name = Column(String, nullable=False)    # the file inside the email files folder
    is_image = Column(Boolean, default=False, nullable=False)
    width = Column(Integer, nullable=True)          # pictures only
    height = Column(Integer, nullable=True)
    origin = Column(String, default="admin", nullable=False)  # admin | candidate
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

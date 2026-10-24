import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String, Text

from app.database import Base


class EmailTemplate(Base):
    """A reusable email: one the admin wrote, or the admin's version of an
    email the system sends by itself (then `key` names that automatic email)."""

    __tablename__ = "email_templates"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    key = Column(String, nullable=True, index=True)  # automatic email it replaces; empty for the admin's own
    name = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    title = Column(String, nullable=True)       # headline shown in the email header
    body = Column(Text, nullable=False)         # plain text with {variables}
    closing = Column(Text, nullable=True)       # text after the details table (automatic emails)
    style = Column(String, default="brand", nullable=False)  # brand | letter | announcement
    image_id = Column(String, nullable=True)    # photo shown in the email
    image_position = Column(String, default="top", nullable=True)  # top | bottom
    attachment_ids = Column(Text, nullable=True)  # JSON list of attached file ids
    show_details = Column(Boolean, default=True, nullable=True)   # automatic emails: the class details table
    is_active = Column(Boolean, default=True, nullable=True)      # automatic emails: sent by the system or not
    cta_label = Column(String, nullable=True)   # button
    cta_url = Column(String, nullable=True)
    channel = Column(String, nullable=True)     # automatic emails: email | both | whatsapp (empty: email)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

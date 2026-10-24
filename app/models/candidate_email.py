import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class CandidateEmail(Base):
    __tablename__ = "candidate_emails"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id = Column(String, ForeignKey("candidates.id"), nullable=False, index=True)
    registration_id = Column(String, ForeignKey("registrations.id"), nullable=True, index=True)
    direction = Column(String, nullable=False, index=True)  # inbound or outbound
    message_id = Column(String, nullable=True, unique=True, index=True)
    thread_id = Column(String, nullable=True, index=True)
    sender = Column(String, nullable=False)
    recipient = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    body = Column(Text, nullable=False)
    language = Column(String, nullable=True)
    intent = Column(String, nullable=True)
    sentiment = Column(String, nullable=True)
    excitement_score = Column(Integer, nullable=True)
    analysis = Column(Text, nullable=True)
    status = Column(String, default="received", nullable=False, index=True)
    requires_admin = Column(Boolean, default=False, nullable=False, index=True)
    received_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    replied_at = Column(DateTime, nullable=True)

    # Discussion tracking: who wrote the message and how the assistant handled it.
    author = Column(String, nullable=True, index=True)  # candidate | system | admin
    html_body = Column(Text, nullable=True)             # the email exactly as it was sent
    in_reply_to = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)           # 0..1, the KPI behind "answer or ask the admin"
    sources = Column(Text, nullable=True)               # JSON: knowledge used and the score breakdown
    question = Column(Text, nullable=True)              # what the candidate is asking, in one sentence
    suggested_reply = Column(Text, nullable=True)       # the assistant's draft, waiting for the admin
    escalation_reason = Column(Text, nullable=True)     # why the assistant asked the admin
    attachments = Column(Text, nullable=True)           # JSON: the files that came with the email
    channel = Column(String, default="email", nullable=True, index=True)   # email | whatsapp
    emotions = Column(Text, nullable=True)        # what the candidate felt, read with word lists (services/emotions.py), as JSON
    admin_score = Column(Integer, nullable=True)        # the admin's verdict on an assistant's answer: 1 right, -1 wrong

    candidate = relationship("Candidate")
    registration = relationship("Registration")

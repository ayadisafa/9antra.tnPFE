from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text

from app.database import Base


class QuizAgentMessage(Base):
    __tablename__ = "quiz_agent_messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    registration_id = Column(String, ForeignKey("registrations.id"), nullable=False, index=True)
    role = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

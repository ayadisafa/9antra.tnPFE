"""One request handled by one agent (services/orchestrator.py): which agent the orchestrator chose, why, and what came of it."""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    at = Column(DateTime, default=datetime.utcnow, index=True)
    agent = Column(String, nullable=False, index=True)          # a key of orchestrator.AGENTS
    category = Column(String, nullable=True, index=True)        # a key of message_categories.CATEGORIES
    channel = Column(String, nullable=True, index=True)         # chat | professor | email | whatsapp | sms
    outcome = Column(String, nullable=True)                     # answered | sent | draft | admin | none
    read_by = Column(String, nullable=True)                     # words | meaning | none: how the category was found
    why = Column(Text, nullable=True)                           # the words that decided, as JSON
    asked = Column(String, nullable=True)                       # the beginning of the request
    ref = Column(String, nullable=True, index=True)             # the message (candidate_emails.id) when there is one
    user_id = Column(String, nullable=True)
    took_ms = Column(Integer, nullable=True)

"""A message between the admin and someone of the team: a professor or a team member (see services/team_messages.py)."""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from app.database import Base


class TeamMessage(Base):
    __tablename__ = "team_messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person = Column(String, nullable=False, index=True)       # "professor:<id>" or "user:<id>": whom the conversation is with
    direction = Column(String, nullable=False)                 # out: from the centre | in: from them
    channel = Column(String, nullable=False, default="app")    # app | email | whatsapp | sms
    body = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="sent")    # sent | to_send (waits for the admin's phone) | failed | received
    author = Column(String, nullable=True)                     # who wrote it: the admin's name, "The assistant", or theirs
    batch = Column(String, nullable=True, index=True)          # one message sent several ways: its copies share this
    suggestion = Column(Text, nullable=True)                   # on a message they sent: the answer the assistant found
    suggestion_source = Column(String, nullable=True)          # taught | website | knowledge
    message_id = Column(String, nullable=True, index=True)     # the email's or WhatsApp's own id: never kept twice
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    read_at = Column(DateTime, nullable=True)                  # in: when the admin saw it | out in the application: when they did
    # A candidate's question sent to their professor (services/course_questions.py): which message, whose, and its follow-up.
    question_message_id = Column(String, nullable=True, index=True)
    question_candidate_id = Column(String, nullable=True)
    answered_at = Column(DateTime, nullable=True)
    reminded_at = Column(DateTime, nullable=True)
    admin_told_at = Column(DateTime, nullable=True)

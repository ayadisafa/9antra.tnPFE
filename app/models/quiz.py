import json
import logging
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text

from app.database import Base

logger = logging.getLogger(__name__)


class Quiz(Base):
    __tablename__ = "quizzes"

    id = Column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4())
    )

    candidate_id = Column(String, index=True)
    registration_id = Column(String, nullable=True, index=True)

    course_name = Column(String)
    angle = Column(String, nullable=True)  # e.g., 'debugging', 'architecture', 'edge_cases'

    questions = Column(Text, default="[]")  # JSON stored as string[cite: 3]
    answers = Column(Text, default="[]")    # JSON stored as string[cite: 3]

    level = Column(String, default="intermediate")  # basic, intermediate, advanced[cite: 3]
    score = Column(Float, default=0.0)

    google_form_url = Column(String, nullable=True)

    sent_at = Column(DateTime, nullable=True)
    scheduled_for = Column(DateTime, nullable=True, index=True)
    prepared_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    
    email_subject = Column(String, nullable=True)
    email_body = Column(Text, nullable=True)

    completed_at = Column(DateTime, nullable=True)
    analysis = Column(Text, nullable=True)
    improvement_areas = Column(Text, nullable=True)
    assessed_level = Column(String, nullable=True)
    sentiment = Column(String, nullable=True)
    sentiment_summary = Column(Text, nullable=True)

    # "level" for the quizzes of a registration, "weekly" for the questions of
    # one class week. Weekly ones are opened with a private link (access_token).
    kind = Column(String, default="level", nullable=True)
    class_group_id = Column(String, nullable=True, index=True)
    week_number = Column(Integer, nullable=True)
    access_token = Column(String, nullable=True, index=True)
    # A level check left unanswered is reminded once: when that was.
    reminded_at = Column(DateTime, nullable=True)

    # The weekly check-in. `feedback` is what the candidate said about the week
    # (mood, pace, confidence, their own words); `insight` is what the system
    # reads in it (see week_insights). Both are JSON.
    feedback = Column(Text, nullable=True)
    insight = Column(Text, nullable=True)
    answer_source = Column(String, nullable=True)   # page | google_form
    external_id = Column(String, nullable=True, index=True)     # the Google Forms response


def create_google_form_from_quiz(title: str, questions: list) -> str:
    """
    Simulates Google Form URL generation containing the quiz ID in query params.
    Replace with Google Workspace / Forms API calls if live API creation is active[cite: 3].
    """
    try:
        form_id = str(uuid.uuid4())[:8]
        logger.info(f"Generated Google Form '{title}' with {len(questions)} questions.")
        return f"https://docs.google.com/forms/d/e/{form_id}/viewform"
    except Exception as e:
        logger.error(f"Failed to create Google Form: {e}")
        return ""
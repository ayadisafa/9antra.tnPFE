import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.database import Base


class FaqItem(Base):
    """A frequent question (services/faq.py): from the centre's own posts, from what candidates ask most, or written by the
    admin. Its answer for the assistants is a taught answer (`entry_id`); `post_answer` is how a post says it."""
    __tablename__ = "faq_items"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    key = Column(String, nullable=False, unique=True, index=True)      # the question, folded: one line per question
    question = Column(String, nullable=False)                          # as a candidate puts it (the taught answer's question)
    post_question = Column(String, nullable=True)                      # as the post asks it (Tunisian, French…)
    post_answer = Column(Text, nullable=True)                          # the answer as the post says it (Tunisian, French…)
    entry_id = Column(String, nullable=True, index=True)               # the taught answer the assistants use (knowledge_entries)
    theme = Column(String, nullable=True, index=True)                  # summer_camp … : used only when the question names it
    source = Column(String, nullable=False, default="admin")           # post | messages | admin
    asked = Column(Integer, nullable=False, default=0)                 # how many messages asked it (90 days)
    last_asked = Column(DateTime, nullable=True)
    position = Column(Integer, nullable=True)                          # its place in the post's carousel
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

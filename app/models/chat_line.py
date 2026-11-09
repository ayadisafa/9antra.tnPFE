"""One line of a conversation with Yasmine (services/conversation.py): what someone asked, or what she answered, kept so
the conversation goes on from page to page and from one day to the next."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database import Base


class ChatLine(Base):
    __tablename__ = "assistant_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)                       # user | bot | new (a new conversation starts here)
    text = Column(Text, nullable=False, default="")
    data = Column(Text, nullable=True)                          # JSON: what the window shows with an answer (links, sources, cards…)
    path = Column(String, nullable=True)                        # the page it was said on
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

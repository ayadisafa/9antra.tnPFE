"""A piece of news of the journal (services/journal.py): an article of a public news feed, a post of the centre's LinkedIn
page, or something the admin pasted (a LinkedIn post, an article)."""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from app.database import Base


class NewsItem(Base):
    __tablename__ = "news_items"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    domain = Column(String, nullable=False, index=True)          # python | devops | ai | cyber | … | general
    source = Column(String, nullable=False)                      # the feed's name, "LinkedIn", "Pasted"
    title = Column(String, nullable=False)
    url = Column(String, nullable=True, unique=True, index=True)
    summary = Column(Text, nullable=True)                        # its first sentences, as the feed writes them
    terms = Column(Text, nullable=True)                          # JSON: the words of its field it names
    published_at = Column(DateTime, nullable=True, index=True)
    fetched_at = Column(DateTime, default=datetime.utcnow, index=True)

    def term_list(self) -> list[str]:
        import json

        try:
            found = json.loads(self.terms or "[]")
        except ValueError:
            return []
        return [str(term) for term in found] if isinstance(found, list) else []

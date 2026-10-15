import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint

from app.database import Base


class WebsiteItem(Base):
    """Something published on the public website: a formation, an event, an
    internship offer or a news article, as it was last read there."""

    __tablename__ = "website_items"
    __table_args__ = (UniqueConstraint("kind", "external_id", name="uq_website_item"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    kind = Column(String, nullable=False, index=True)       # formation | event | offer | news
    external_id = Column(String, nullable=False)            # the website's own identifier
    slug = Column(String, nullable=True)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    category = Column(String, nullable=True)
    url = Column(String, nullable=True)                     # the public page
    starts_at = Column(DateTime, nullable=True, index=True)  # events: when it takes place (UTC)
    data = Column(Text, nullable=False, default="{}")       # everything kept from the website, as JSON
    fingerprint = Column(String, nullable=True)             # changes when the website's content does
    reading = Column(Text, nullable=True)                   # feedback: what was read in it and its tone, as JSON

    program_id = Column(String, nullable=True, index=True)  # formations: the program that follows it

    first_seen_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    changed_at = Column(DateTime, nullable=True)            # last time its content changed
    last_seen_at = Column(DateTime, nullable=True)
    gone_at = Column(DateTime, nullable=True)               # no longer on the website
    removed_at = Column(DateTime, nullable=True)            # the admin removed it from here; nothing else ever does

    @property
    def details(self) -> dict:
        try:
            loaded = json.loads(self.data or "{}")
        except (TypeError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @property
    def read(self) -> dict:
        try:
            loaded = json.loads(self.reading or "{}")
        except (TypeError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}


class WebsiteCheck(Base):
    """One reading of the website: when, whether it worked, and what changed."""

    __tablename__ = "website_checks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    checked_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    ok = Column(Boolean, default=True, nullable=False)
    note = Column(Text, nullable=True)                      # why it failed, in plain words
    changes = Column(Text, nullable=False, default="[]")    # JSON list of {"icon", "text", "url"}
    by = Column(String, default="system", nullable=False)   # system | admin

    @property
    def lines(self) -> list[dict]:
        try:
            loaded = json.loads(self.changes or "[]")
        except (TypeError, ValueError):
            return []
        return [item for item in loaded if isinstance(item, dict)]

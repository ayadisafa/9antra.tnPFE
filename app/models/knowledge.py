import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.database import Base


class KnowledgeEntry(Base):
    """One piece of knowledge the email assistant can answer from.

    `system` entries are rebuilt from programs, classes and settings.
    `admin` entries are written by the admin; `learned` ones were saved from
    an answer the admin gave to a candidate.
    """

    __tablename__ = "knowledge_entries"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    kind = Column(String, nullable=False, index=True)       # faq | program | curriculum | class | process
    origin = Column(String, default="system", nullable=False, index=True)  # system | admin | learned
    source_key = Column(String, nullable=True, unique=True, index=True)
    program_id = Column(String, nullable=True, index=True)

    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Text, nullable=True)
    # Other ways the same question is asked: JSON list of {"q": wording, "by":
    # "admin" (a candidate's wording the admin answered) or "system" (a guess)}.
    variants = Column(Text, nullable=True)
    wordings_at = Column(DateTime, nullable=True)           # when the system last guessed other wordings
    content_hash = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    source_email_id = Column(String, nullable=True)
    use_count = Column(Integer, default=0, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def get_embedding(self) -> list[float] | None:
        if not self.embedding:
            return None
        try:
            value = json.loads(self.embedding)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, list) else None

    def get_variants(self) -> list[dict]:
        try:
            value = json.loads(self.variants) if self.variants else []
        except (TypeError, ValueError):
            return []
        return [item for item in value if isinstance(item, dict) and item.get("q")] if isinstance(value, list) else []

    @property
    def confirmed_wordings(self) -> list[str]:
        """Other wordings real candidates used, which the admin answered."""
        return [item["q"] for item in self.get_variants() if item.get("by") != "system"]

    @property
    def guessed_wordings(self) -> list[str]:
        """Other wordings the system thinks of; they only ever give a draft."""
        return [item["q"] for item in self.get_variants() if item.get("by") == "system"]

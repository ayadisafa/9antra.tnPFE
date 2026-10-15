import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text

from app.database import Base


class ProgramProposal(Base):
    """What the system proposes for a program whose details are missing.

    Nothing here changes the program: the admin reads the proposal, corrects
    it and accepts it, or dismisses it.
    """

    __tablename__ = "program_proposals"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    program_id = Column(String, ForeignKey("programs.id", ondelete="CASCADE"), nullable=False, index=True)

    # queued | writing | ready | failed | accepted | dismissed
    status = Column(String, default="queued", nullable=False, index=True)
    data = Column(Text, nullable=True)      # JSON: the proposed details
    basis = Column(Text, nullable=True)     # JSON: where each proposed value comes from
    error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    decided_at = Column(DateTime, nullable=True)

    def _load(self, value) -> dict:
        try:
            loaded = json.loads(value) if value else {}
        except (TypeError, ValueError):
            loaded = {}
        return loaded if isinstance(loaded, dict) else {}

    @property
    def details(self) -> dict:
        return self._load(self.data)

    @property
    def sources(self) -> dict:
        return self._load(self.basis)

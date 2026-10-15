import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint

from app.database import Base


class MarketOffer(Base):
    """A job offer kept to see what employers ask for: read from a public job
    board, or pasted by the admin (from LinkedIn, for instance)."""

    __tablename__ = "market_offers"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_market_offer"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source = Column(String, nullable=False, index=True)     # remotive | pasted
    external_id = Column(String, nullable=False)
    title = Column(String, nullable=False)
    company = Column(String, nullable=True)
    location = Column(String, nullable=True)
    url = Column(String, nullable=True)
    text = Column(Text, nullable=False, default="")         # what the offer says, without its layout
    posted_at = Column(DateTime, nullable=True)
    added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

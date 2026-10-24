"""One day's reading of one skill of a field: how many job offers ask it, and how busy the developers' community is
with it (services/recommender.py). Kept day after day, so what rises and what falls can be seen."""
import uuid
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Integer, String, UniqueConstraint

from app.database import Base


class MarketSignal(Base):
    __tablename__ = "market_signals"
    __table_args__ = (UniqueConstraint("day", "subject", "skill", name="uq_market_signal_day"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    day = Column(Date, nullable=False, index=True)
    subject = Column(String, nullable=False, index=True)     # the field (devops, mern, bi…: market.SKILLS)
    skill = Column(String, nullable=False)
    offers = Column(Integer, nullable=False, default=0)      # offers of the field that name it
    total = Column(Integer, nullable=False, default=0)       # offers of the field read
    github = Column(Integer, nullable=True)                  # new public GitHub projects about it, last 30 days
    devto = Column(Integer, nullable=True)                   # dev.to articles about it, last 30 days
    hn = Column(Integer, nullable=True)                      # Hacker News “Who is hiring” posts that name it (this month)
    created_at = Column(DateTime, default=datetime.utcnow)

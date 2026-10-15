import uuid

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from app.database import Base


class UiTranslation(Base):
    """One text of the interface in one language."""

    __tablename__ = "ui_translations"
    __table_args__ = (UniqueConstraint("lang", "source_hash", name="uq_ui_translation"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    lang = Column(String, nullable=False, index=True)           # fr | ar
    source_hash = Column(String, nullable=False, index=True)    # of the English text
    source = Column(Text, nullable=False)
    text = Column(Text, nullable=True)                          # None: not translated yet
    origin = Column(String, nullable=True)                      # seed | model | admin
    tries = Column(Integer, nullable=False, default=0)
    seen_at = Column(DateTime, nullable=True)                   # shown on a page without a translation
    updated_at = Column(DateTime, nullable=True)

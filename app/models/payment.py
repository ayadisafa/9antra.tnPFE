import uuid
from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import relationship

from app.database import Base


class Payment(Base):
    __tablename__ = "payments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    registration_id = Column(
        String, ForeignKey("registrations.id"), nullable=False
    )
    amount = Column(Float, nullable=False, default=0.0)
    payment_method = Column(String, nullable=True)
    payment_reference = Column(String, nullable=True)
    payment_date = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    registration = relationship("Registration", back_populates="payments")
import uuid
from datetime import datetime, timezone
from enum import Enum
from sqlalchemy import Boolean, Integer, Column, DateTime, Float, ForeignKey, String, Enum as SQLEnum
from sqlalchemy.orm import relationship

from app.database import Base


class RegistrationStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PaymentStatus(str, Enum):
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"


class Registration(Base):
    __tablename__ = "registrations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    candidate_id = Column(String, ForeignKey("candidates.id"), nullable=False)
    
    # FIXED: Single program_id definition with String ForeignKey matching Program.id
    program_id = Column(String, ForeignKey("programs.id"), nullable=True)
    program_name = Column(String, nullable=True)
    
    delivery_mode = Column(String, default="online")  # online, onsite, or recording
    firebase_id = Column(String, unique=True, index=True, nullable=True)

    registration_date = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    training_start_date = Column(DateTime, nullable=True)
    training_end_date = Column(DateTime, nullable=True)

    status = Column(SQLEnum(RegistrationStatus), default=RegistrationStatus.PENDING)
    payment_status = Column(SQLEnum(PaymentStatus), default=PaymentStatus.UNPAID)

    # ADDED ALIAS/FIELD: total_price added so both total_price and total_amount work seamlessly
    total_amount = Column(Float, default=0.0)
    total_price = Column(Float, default=0.0)
    paid_amount = Column(Float, default=0.0)

    @property
    def remaining_amount(self) -> float:
        price = self.total_amount or self.total_price or 0.0
        return max(0.0, price - (self.paid_amount or 0.0))

    quiz_score = Column(Float, default=0.0)
    certificate_generated = Column(Boolean, default=False)

    # Reminder Tracking (Firebase Compatible)
    payment_reminder_sent = Column(Boolean, default=False)
    reminder_sent_at = Column(String, nullable=True)
    reminder_count = Column(Integer, default=0)

    firebase_status = Column(String, nullable=True)
    firebase_id = Column(String, unique=True, index=True, nullable=True)
    firebase_timestamp = Column(DateTime, nullable=True)  # EXACT Firebase time

    notifications = relationship("Notification", back_populates="registration")
    

    class_group_id = Column(String, ForeignKey("class_groups.id"), nullable=True)

    # Relationships
    candidate = relationship("Candidate", back_populates="registrations")
    program = relationship("Program")
      
    payments = relationship("Payment", back_populates="registration", cascade="all, delete-orphan")
    class_group = relationship("ClassGroup", back_populates="registrations")
    bias_reviews = relationship("RegistrationBias", back_populates="registration", cascade="all, delete-orphan")
    program_progress = relationship("ProgramProgress", back_populates="registration", uselist=False)
    
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.database import Base
from sqlalchemy import Float


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Recipient & Target Entity
    recipient_role = Column(String, default="admin", index=True)  # "admin", "candidate"
    recipient_id = Column(String, nullable=True, index=True)      # Target user ID (None = All Admins)
    
    # Payload & Categorization
    category = Column(String, nullable=False, index=True)         # "PROGRAM_APPROVAL", "REGISTRATION", "PAYMENT"
    status = Column(String, default="unread", nullable=False, index=True)
    title = Column(String, nullable=False)                         # e.g., "Program Approval Pending"
    message = Column(Text, nullable=False)                        # Plain text fallback
    html_content = Column(Text, nullable=True)  
    registration = relationship("Registration", back_populates="notifications")                  # Rich HTML for UI drawer
    
    # Deep-linking & Metadata
    action_url = Column(String, nullable=True)                    # e.g., "/programs/123"
    payload = Column(JSON, nullable=True)    
    similarity_score = Column(Float, nullable=True, default=0.0)                     # Additional metadata
    
    # Status & Analytics
    is_read = Column(Boolean, default=False, index=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    # Foreign Keys & Relationships
    candidate_id = Column(String, ForeignKey("candidates.id"), nullable=True)
    candidate = relationship("Candidate", back_populates="notifications")

    program_id = Column(String, ForeignKey("programs.id"), nullable=True)
    program = relationship("Program", back_populates="notifications")

    registration_id = Column(String, ForeignKey("registrations.id"), nullable=True, index=True)
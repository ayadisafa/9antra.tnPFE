"""A payment proof a candidate sent (a receipt, a transfer, an invoice), as read and checked (services/payment_proofs.py)."""
import uuid
from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, String, Text

from app.database import Base


class PaymentProof(Base):
    __tablename__ = "payment_proofs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id = Column(String, nullable=True, index=True)
    registration_id = Column(String, nullable=True, index=True)   # the registration it pays, once known
    file_id = Column(String, nullable=False)                      # the picture or PDF (email_files)
    message_id = Column(String, nullable=True, index=True)        # the email or WhatsApp message it came with
    source = Column(String, nullable=False, default="email")      # email | whatsapp | upload
    status = Column(String, nullable=False, default="reading")    # reading | waiting | confirmed | refused
    verdict = Column(String, nullable=True)                       # consistent | to_check | doubtful | unreadable
    amount = Column(Float, nullable=True)
    bank = Column(String, nullable=True)                          # "08 · BIAT"
    reference = Column(String, nullable=True, index=True)
    proof_date = Column(Date, nullable=True)
    ribs = Column(Text, nullable=True)                            # JSON list of the RIBs written on it
    checks = Column(Text, nullable=True)                          # JSON list of {key, ok, text}
    text = Column(Text, nullable=True)                            # every word read on it
    fingerprint = Column(String, nullable=True, index=True)       # the same file sent twice
    editor = Column(String, nullable=True)                        # a photo editor named inside the file
    asked_program_at = Column(DateTime, nullable=True)            # the candidate was asked which program it pays
    recorded_amount = Column(Float, nullable=True)                # what the admin recorded when confirming
    decided_at = Column(DateTime, nullable=True)
    decided_by = Column(String, nullable=True)
    decision_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

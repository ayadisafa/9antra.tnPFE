import json
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, Boolean, Integer, Float, JSON
from app.database import Base


class SystemSettings(Base):
    """Centralized system configuration storage"""
    __tablename__ = "system_settings"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Email Configuration
    smtp_email = Column(String, nullable=True)
    smtp_host = Column(String, default="smtp.gmail.com")
    smtp_port = Column(Integer, default=465)
    use_ai_enhancement = Column(Boolean, default=True)
    email_retry_count = Column(Integer, default=3)
    email_retry_delay = Column(Integer, default=2)  # seconds
    
    # Reminder Settings
    payment_reminder_hours = Column(Integer, default=24)
    training_reminder_days = Column(String, default="7,3,1")  # CSV format
    enable_automated_reminders = Column(Boolean, default=True)
    default_language = Column(String, default="fr")
    
    # Class Settings
    default_class_capacity = Column(Integer, default=30)
    default_class_start_days = Column(Integer, default=10)
    auto_create_class_on_payment = Column(Boolean, default=True)
    auto_assign_professor = Column(Boolean, default=True)
    # days_after_creation | min_enrollment
    class_start_mode = Column(String, default="days_after_creation")
    min_students_to_start = Column(Integer, default=5)
    min_start_notice_days = Column(Integer, default=3)
    default_session_days = Column(String, default="sat")  # CSV of mon..sun
    default_session_start_time = Column(String, default="09:00")
    default_session_end_time = Column(String, default="13:00")

    # Email assistant: how candidate emails are answered and how emails look
    # auto = answer alone when confident | review = always ask the admin | off
    email_auto_reply_mode = Column(String, default="auto")
    email_confidence_threshold = Column(Float, default=0.7)
    # An answer the admin taught: written afresh for each candidate, or sent in the admin's own words.
    email_rewrite_taught = Column(Boolean, default=True)
    # The look of a program's PDF when nothing else is asked: brochure, classic or one_page.
    program_pdf_style = Column(String, nullable=True)
    # When the public job boards were last read (UTC), whether or not they had anything new.
    market_read_at = Column(DateTime, nullable=True)
    # A second folder, on another disk, where each daily copy of the data also goes.
    backup_second_dir = Column(String, nullable=True)
    # The design: the side of the menu (left | right) and the background (animated | plain).
    ui_menu_side = Column(String, nullable=True)
    ui_background = Column(String, nullable=True)
    # Day or night: soft (the default) | colours | night | night-blue.
    ui_mode = Column(String, nullable=True)
    email_signature = Column(Text, nullable=True)
    email_footer_note = Column(Text, nullable=True)
    email_accent_color = Column(String, default="#a42c67")

    # Program Settings
    price_online = Column(Float, default=390.0)
    price_onsite = Column(Float, default=490.0)
    program_similarity_threshold = Column(Float, default=0.85)
    enable_ai_program_suggestions = Column(Boolean, default=True)
    default_program_weeks = Column(Integer, default=12)
    
    # Admin Settings
    admin_email = Column(String, nullable=True)
    platform_name = Column(String, default="9antra")
    timezone = Column(String, default="Africa/Tunis")
    # Address candidates use to reach this application (links in emails).
    public_base_url = Column(String, nullable=True)
    # Main colour of the application's design; empty = the original berry.
    ui_color = Column(String, nullable=True)
    app_name = Column(String, nullable=True)      # the name shown in the menu and on the sign-in page

    # Audit
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict:
        """Convert settings to dictionary"""
        return {
            "email_settings": {
                "smtp_email": self.smtp_email,
                "smtp_host": self.smtp_host,
                "smtp_port": self.smtp_port,
                "use_ai_enhancement": self.use_ai_enhancement,
                "retry_count": self.email_retry_count,
                "retry_delay": self.email_retry_delay,
            },
            "reminder_settings": {
                "payment_reminder_hours": self.payment_reminder_hours,
                "training_reminder_days": [int(x) for x in self.training_reminder_days.split(",")],
                "enable_automated": self.enable_automated_reminders,
                "default_language": self.default_language,
            },
            "class_settings": {
                "default_capacity": self.default_class_capacity,
                "default_start_days": self.default_class_start_days,
                "auto_create_on_payment": self.auto_create_class_on_payment,
                "auto_assign_professor": self.auto_assign_professor,
                "start_mode": self.class_start_mode,
                "min_students_to_start": self.min_students_to_start,
                "min_start_notice_days": self.min_start_notice_days,
                "default_session_days": self.default_session_days,
                "default_session_start_time": self.default_session_start_time,
                "default_session_end_time": self.default_session_end_time,
            },
            "program_settings": {
                "price_online": self.price_online,
                "price_onsite": self.price_onsite,
                "similarity_threshold": self.program_similarity_threshold,
                "enable_ai_suggestions": self.enable_ai_program_suggestions,
                "default_weeks": self.default_program_weeks,
            },
            "admin_email": self.admin_email,
            "platform_name": self.platform_name,
            "timezone": self.timezone,
        }
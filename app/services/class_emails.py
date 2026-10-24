"""Candidate-facing emails about their class: enrollment details, schedule
changes and the reminders sent before the first session.

The wording of each email is a template the admin can change (see
app.services.email_templates). Every message is logged in the candidate's
discussions whether or not SMTP delivery succeeded, so the admin can always
see what was sent or missed.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.registration import Registration
from app.services import email_templates
from app.services.email_templates import detail_rows as _detail_rows  # noqa: F401 (used by the email assistant)
from app.services.email_threads import flag_smtp_not_configured, smtp_configured  # noqa: F401 (used by callers)
from app.services.schedule_utils import class_start_dt

logger = logging.getLogger(__name__)

PLATFORM = "9antra.tn"


def send_enrollment_email(db: Session, registration: Registration) -> bool:
    """Payment confirmation with the full details of the program and class."""
    key = "enrollment" if registration.class_group else "payment_received"
    return email_templates.delivered(email_templates.send(db, key, registration=registration))


def send_class_update_email(db: Session, registration: Registration, reason: str = "rescheduled") -> bool:
    """Tell a candidate their class, start date or schedule changed."""
    if not registration.class_group:
        return False
    key = f"class_{reason}" if f"class_{reason}" in email_templates.AUTO_EMAILS else "class_rescheduled"
    return email_templates.delivered(email_templates.send(db, key, registration=registration))


def professor_coordinates(professor) -> str:
    """How a candidate reaches their professor: the email and phone known, and always a reply to the centre's email
    (a question about the course is forwarded to the professor: see course_questions)."""
    lines = []
    if professor.email:
        lines.append(f"Email : {professor.email}")
    if professor.phone:
        lines.append(f"Téléphone / WhatsApp : {professor.phone}")
    lines.append("Vos questions sur le cours peuvent aussi être envoyées en réponse à cet email : elles lui sont transmises.")
    return "\n".join(lines)


def send_professor_email(db: Session, registration: Registration) -> bool:
    """“Your professor”: who teaches the candidate's class and how to write to them."""
    class_group = registration.class_group
    professor = class_group.professor if class_group else None
    if professor is None:
        return False
    message = email_templates.send(db, "class_professor", registration=registration,
                                   values={"professor_coordinates": professor_coordinates(professor)})
    return email_templates.delivered(message)


def send_training_reminder(db: Session, registration: Registration, days_before: int | None = None) -> bool:
    """Reminder before the first session, with its exact date and time.

    Without `days_before` the admin asked for it, so it goes out even when
    the automatic reminders are switched off.
    """
    class_group = registration.class_group
    if not class_group or not class_start_dt(class_group):
        return False
    message = email_templates.send(
        db, "training_reminder", registration=registration, manual=days_before is None,
    )
    return email_templates.delivered(message)


# ------------------------------------------------------------------
# Background entry points (own DB session)
# ------------------------------------------------------------------

def _with_registration(registration_id: str, action) -> None:
    db = SessionLocal()
    try:
        registration = db.get(Registration, registration_id)
        if registration:
            action(db, registration)
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Class email task failed for registration %s.", registration_id)
    finally:
        db.close()


def send_enrollment_email_task(registration_id: str) -> None:
    _with_registration(registration_id, send_enrollment_email)


def send_professor_email_task(registration_id: str) -> None:
    _with_registration(registration_id, send_professor_email)


def send_class_update_email_task(registration_id: str, reason: str = "rescheduled") -> None:
    _with_registration(
        registration_id,
        lambda db, registration: send_class_update_email(db, registration, reason),
    )

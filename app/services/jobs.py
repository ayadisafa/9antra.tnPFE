"""Registration changes used by the candidate and class pages. (The automatic work is in app/scheduler/jobs.py.)"""
from sqlalchemy.orm import Session

from app.models.candidate import Candidate
from app.models.registration import Registration, RegistrationStatus, PaymentStatus
from app.models.classgroup import ClassGroup
from app.models.notification import Notification


def assign_and_update_registration(
    db_session: Session, 
    registration_id: str | int, 
    new_status: str | RegistrationStatus = None,
    class_group_id: str | int = None
) -> Registration:
    """
    Updates registration lifecycle status and assigns it to a ClassGroup
    with dynamic capacity enforcement.
    """
    registration = db_session.get(Registration, registration_id)
    if not registration:
        raise ValueError(f"Registration '{registration_id}' not found.")

    # Validate and assign class group capacity if provided
    if class_group_id:
        class_group = db_session.get(ClassGroup, class_group_id)
        if not class_group:
            raise ValueError(f"Class Group '{class_group_id}' not found.")

        # Determine dynamic group limit from model or fallback default
        max_capacity = getattr(class_group, "max_capacity", 12) or 12

        current_count = db_session.query(Registration).filter(
            Registration.class_group_id == class_group_id,
            Registration.status != RegistrationStatus.CANCELLED,
            Registration.id != registration_id
        ).count()

        if current_count >= max_capacity:
            raise ValueError(
                f"Class group '{class_group.name}' is full ({current_count}/{max_capacity} occupied)."
            )

        registration.class_group_id = class_group_id
        registration.status = RegistrationStatus.ASSIGNED

    # Update lifecycle status if explicitly provided
    if new_status:
        if isinstance(new_status, str):
            try:
                new_status = RegistrationStatus(new_status.lower().strip())
            except ValueError:
                allowed = [e.value for e in RegistrationStatus]
                raise ValueError(f"Invalid status '{new_status}'. Allowed values: {', '.join(allowed)}")
        registration.status = new_status

    db_session.commit()
    db_session.refresh(registration)
    return registration


def update_registration_status(
    db_session: Session, 
    registration_id: str | int, 
    new_status: str | RegistrationStatus
) -> Registration:
    """Updates registration training lifecycle status using model Enums."""
    return assign_and_update_registration(db_session, registration_id, new_status=new_status)


def settle_registration_payment(
    db_session: Session, 
    registration_id: str | int, 
    payment_status: str | PaymentStatus, 
    amount_paid: float = None
) -> Registration:
    """Settles payment status, updates paid amounts, and auto-assigns if fully paid."""
    if isinstance(payment_status, str):
        try:
            payment_status = PaymentStatus(payment_status.lower().strip())
        except ValueError:
            allowed = [e.value for e in PaymentStatus]
            raise ValueError(f"Invalid payment status '{payment_status}'. Allowed values: {', '.join(allowed)}")

    registration = db_session.get(Registration, registration_id)
    if not registration:
        raise ValueError(f"Registration '{registration_id}' not found.")

    if amount_paid is not None:
        from app.services.payment_service import confirm_payment
        confirm_payment(db_session, registration_id, float(amount_paid), "manual")
        registration = db_session.get(Registration, registration_id)
    else:
        registration.payment_status = payment_status
        db_session.commit()
    # Payment alone never assigns a class; an administrator must choose a staffed class.

    db_session.commit()
    db_session.refresh(registration)
    return registration


def purge_candidate_registrations(db_session: Session, candidate_id: str | int) -> int:
    """Deletes all prior registrations for a candidate to free reserved spots."""
    deleted_count = db_session.query(Registration).filter(
        Registration.candidate_id == candidate_id
    ).delete(synchronize_session='fetch')
    
    db_session.commit()
    return deleted_count


def update_candidate_status(db_session: Session, candidate_id: int, new_status: str) -> Candidate:
    """Updates candidate status in the database."""
    candidate = db_session.get(Candidate, candidate_id)
    if not candidate:
        raise ValueError(f"Candidate with ID {candidate_id} not found.")
    
    candidate.status = new_status
    db_session.add(Notification(
        recipient_role="admin",
        category="CANDIDATE_STATUS",
        title="Candidate status changed",
        message=f"{candidate.full_name or candidate.email}: {new_status}",
        action_url=f"/candidate/{candidate.id}",
        candidate_id=candidate.id,
    ))
    db_session.commit()
    db_session.refresh(candidate)
    return candidate

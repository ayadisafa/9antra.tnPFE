from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.registration import (
    PaymentStatus,
    Registration,
    RegistrationStatus,
)
from app.database import SessionLocal
from app.services import email_templates, level_check
from app.services.background import run_in_background
from app.services.enrollment_service import enroll_paid_registration

logger = logging.getLogger(__name__)


def _payment_status_from_amount(
    paid_amount: float,
    total_amount: float,
) -> PaymentStatus:
    """
    Calculate the payment status from the total paid amount.
    """
    if total_amount <= 0:
        return PaymentStatus.UNPAID

    if paid_amount >= total_amount - 0.01:
        return PaymentStatus.PAID

    if paid_amount > 0:
        return PaymentStatus.PARTIAL

    return PaymentStatus.UNPAID


def _get_program_name(registration: Registration) -> str:
    """
    Safely obtain the program name from a registration.
    """
    if registration.program:
        return registration.program.name

    return registration.program_name or "votre formation"


def _create_payment_notification(
    db: Session,
    registration: Registration,
) -> Notification:
    """
    Notify the administrator about the payment and class status.
    """
    is_paid = registration.payment_status == PaymentStatus.PAID
    has_class = bool(registration.class_group_id)

    if is_paid and has_class:
        title = "Paiement confirmé"
        message = (
            f"L'inscription de {registration.candidate.full_name or registration.candidate.email} "
            f"pour {registration.program_name or 'la formation'} est payée et assignée à une classe."
        )
    elif is_paid:
        title = "Paiement confirmé — classe à assigner"
        message = (
            f"L'inscription de {registration.candidate.full_name or registration.candidate.email} "
            f"pour {registration.program_name or 'la formation'} est entièrement payée, "
            "mais aucune classe encadrée ne lui est encore assignée."
        )
    else:
        title = "Paiement partiel enregistré"
        message = (
            f"Un paiement partiel a été enregistré pour "
            f"{registration.candidate.full_name or registration.candidate.email}. "
            "Le solde reste à payer avant l'affectation à une classe."
        )

    notification = Notification(
        recipient_role="admin",
        candidate_id=registration.candidate_id,
        registration_id=registration.id,
        program_id=registration.program_id,
        category="PAYMENT",
        title=title,
        message=message,
        action_url=f"/candidate/{registration.candidate_id}",
        payload={
            "registration_id": registration.id,
            "payment_status": registration.payment_status.value,
            "paid_amount": registration.paid_amount or 0.0,
            "remaining_amount": registration.remaining_amount,
            "class_assigned": has_class,
        },
        is_read=False,
        status="unread",
    )

    db.add(notification)
    return notification


def _send_payment_confirmation_email(
    db: Session,
    registration: Registration,
    payment_status: PaymentStatus,
) -> bool:
    """
    Send the French payment email, recorded in the candidate's discussions.

    A partial payment receives the balance still due.
    A fully paid registration without a class receives a confirmation that
    does not promise a class yet.
    The wording of both is a template the admin can change.
    """
    candidate = registration.candidate

    if not candidate or not candidate.email:
        logger.warning(
            "No email available for registration %s.",
            registration.id,
        )
        return False

    key = "payment_received" if payment_status == PaymentStatus.PAID else "payment_partial"
    return email_templates.delivered(
        email_templates.send(db, key, registration=registration)
    )


def confirm_payment(
    db: Session,
    registration_id: str,
    amount: float,
    method: str = "manual",
) -> Payment:
    """
    Confirm a payment for a registration.

    Workflow:

        1. Validate the registration.
        2. Validate the payment amount.
        3. Create a Payment record.
        4. Update paid_amount and payment_status.
        5. On full payment, enroll the candidate in a class immediately.
        6. Notify the administrator.
        7. In the background: send the French email and prepare the level check.
    """
    registration = db.get(
        Registration,
        registration_id,
    )

    if not registration:
        raise ValueError("Registration not found.")

    try:
        amount = float(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError("Payment amount must be a valid number.") from exc

    if amount <= 0:
        raise ValueError("Payment amount must be positive.")

    total_amount = float(
        registration.total_amount
        or registration.total_price
        or 0.0
    )

    paid_amount = float(
        registration.paid_amount or 0.0
    )

    remaining_amount = max(
        0.0,
        total_amount - paid_amount,
    )

    if amount > remaining_amount + 0.01:
        raise ValueError(
            f"Payment exceeds remaining balance "
            f"({remaining_amount:.2f} DT)."
        )

    payment = Payment(
        registration_id=registration.id,
        amount=amount,
        payment_method=method or "manual",
    )

    db.add(payment)

    registration.paid_amount = round(
        paid_amount + amount,
        2,
    )

    registration.payment_status = _payment_status_from_amount(
        paid_amount=registration.paid_amount,
        total_amount=total_amount,
    )

    # The registration stays PENDING here; once the payment is committed
    # below, a fully paid registration is enrolled in a class right away.
    if registration.payment_status == PaymentStatus.PAID:
        if not registration.class_group_id:
            registration.status = RegistrationStatus.PENDING
    elif registration.status not in {
        RegistrationStatus.ASSIGNED,
        RegistrationStatus.COMPLETED,
        RegistrationStatus.CANCELLED,
    }:
        registration.status = RegistrationStatus.PENDING

    db.add(
        Activity(
            candidate_id=registration.candidate_id,
            event_type="PAYMENT_RECORDED",
            details=(
                f"Payment of {amount:.2f} DT recorded for "
                f"{registration.program_name or 'training program'}. "
                f"Status: {registration.payment_status.value}."
            ),
        )
    )

    fully_paid = registration.payment_status == PaymentStatus.PAID
    if not fully_paid:
        _create_payment_notification(
            db=db,
            registration=registration,
        )

    # Commit the payment before anything else, so it stays recorded even if
    # class assignment, email or AI fails afterwards.
    db.commit()
    db.refresh(payment)
    db.refresh(registration)

    send_payment_email = True
    if fully_paid:
        # Full payment completes the assignment: the candidate joins an open
        # class of the program, or a new class is created. The enrollment
        # email with the program details is sent by the enrollment service.
        result = enroll_paid_registration(db, registration)
        if result.class_group is not None and result.reason is None:
            send_payment_email = False
        else:
            _create_payment_notification(db=db, registration=registration)
            db.commit()
        db.refresh(payment)

    # Emails are slow; they must not delay the response.
    run_in_background(_payment_followup_task, registration.id, send_payment_email)

    return payment


def _payment_followup_task(registration_id: str, send_payment_email: bool) -> None:
    """Background work after a payment: confirmation email and the level check."""
    db = SessionLocal()
    try:
        registration = db.get(Registration, registration_id)
        if not registration:
            return

        if send_payment_email:
            # The email and its outcome are recorded in the discussion and the activity log.
            _send_payment_confirmation_email(
                db=db,
                registration=registration,
                payment_status=registration.payment_status,
            )
            db.commit()

        if registration.payment_status == PaymentStatus.PAID:
            # Paid in full: where do they start from? Their level check is prepared (and sent
            # when candidates can open its link), once per registration.
            level_check.after_payment(db, registration)
    except Exception:
        db.rollback()
        logger.exception("Payment follow-up failed for registration %s.", registration_id)
    finally:
        db.close()
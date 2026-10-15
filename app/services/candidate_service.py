import uuid
from datetime import datetime, timezone

from sentence_transformers import util
from sqlalchemy.orm import Session, joinedload

from app.models.activity import Activity
from app.models.bias import RegistrationBias
from app.models.candidate import Candidate
from app.models.notification import Notification
from app.models.program import Program
from app.models.registration import (
    PaymentStatus,
    Registration,
    RegistrationStatus,
)
from app.services.embedding_service import get_similarity_model
from app.services.enrollment_service import enroll_paid_registration


def get_all_candidates(db: Session) -> list[Candidate]:
    return (
        db.query(Candidate)
        .options(joinedload(Candidate.registrations))
        .all()
    )


def get_candidate(db: Session, candidate_id: str) -> Candidate | None:
    return (
        db.query(Candidate)
        .options(
            joinedload(Candidate.registrations),
            joinedload(Candidate.activities),
            joinedload(Candidate.notifications),
        )
        .filter(Candidate.id == candidate_id)
        .first()
    )


def create_candidate(db: Session, data: dict) -> Candidate:
    candidate = Candidate(**data)
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def update_candidate_status(
    db: Session,
    candidate_id: str,
    new_status: str,
) -> Candidate:
    candidate = db.get(Candidate, candidate_id)

    if not candidate:
        raise ValueError(
            f"Candidate with ID {candidate_id} not found."
        )

    candidate.status = new_status

    db.add(
        Notification(
            recipient_role="admin",
            category="CANDIDATE_STATUS",
            title="Candidate status changed",
            message=(
                f"{candidate.full_name or candidate.email}: "
                f"{new_status}"
            ),
            action_url=f"/candidate/{candidate.id}",
            candidate_id=candidate.id,
            is_read=False,
        )
    )

    db.commit()
    db.refresh(candidate)
    return candidate


def delete_candidate(
    db: Session,
    candidate_id: str,
) -> Candidate | None:
    candidate = get_candidate(db, candidate_id)

    if candidate:
        db.delete(candidate)
        db.commit()

    return candidate


def purge_candidate_registrations(
    db: Session,
    candidate_id: str | int,
) -> int:
    deleted_count = (
        db.query(Registration)
        .filter(Registration.candidate_id == candidate_id)
        .delete(synchronize_session="fetch")
    )

    db.commit()
    return deleted_count


def log_activity(
    db: Session,
    candidate_id: str | int,
    event: str,
    details: str,
) -> None:
    activity = Activity(
        candidate_id=candidate_id,
        event_type=event,
        details=details,
    )

    db.add(activity)
    db.commit()


def _normalise_program_name(value: str) -> str:
    return " ".join(str(value).strip().split())


def resolve_program_with_transformers(
    db: Session,
    raw_program_name: str,
) -> tuple[Program | None, bool, float, Program | None]:
    """
    Resolves a Firebase program name using semantic similarity.

    Returns:
        (
            assigned_program,
            needs_admin_review,
            similarity_score,
            suggested_program,
        )

    Rules:
        score >= 0.85:
            Reuse the existing program.

        0.60 <= score < 0.85:
            Do not assign a program automatically.
            Create a pending admin review.

        score < 0.60:
            Create a new program.
    """
    if not raw_program_name or not str(raw_program_name).strip():
        return None, False, 0.0, None

    clean_name = _normalise_program_name(raw_program_name)

    programs = (
        db.query(Program)
        .filter(Program.name.isnot(None))
        .order_by(Program.name.asc())
        .all()
    )

    if not programs:
        new_program = Program(
            id=str(uuid.uuid4()),
            name=clean_name,
        )

        db.add(new_program)
        db.flush()

        return new_program, False, 0.0, None

    # Another way of writing a program's name is that program: no review is needed.
    from app.services import program_names as names_of

    synonym = names_of.same_program(clean_name, programs)
    if synonym is not None:
        return synonym, False, 1.0, None

    # Keep program objects and names in exactly the same order.
    program_names = [program.name for program in programs]

    model = get_similarity_model()

    target_embedding = model.encode(
        clean_name,
        convert_to_tensor=True,
    )

    database_embeddings = model.encode(
        program_names,
        convert_to_tensor=True,
    )

    cosine_scores = util.cos_sim(
        target_embedding,
        database_embeddings,
    )[0]

    best_index = int(cosine_scores.argmax())
    best_score = float(cosine_scores[best_index])
    best_match = programs[best_index]

    if best_score >= 0.85:
        return best_match, False, best_score, None

    if best_score >= 0.60:
        return None, True, best_score, best_match

    new_program = Program(
        id=str(uuid.uuid4()),
        name=clean_name,
    )

    db.add(new_program)
    db.flush()

    return new_program, False, best_score, None


def _get_program_price(
    program: Program | None,
    delivery_mode: str,
) -> float:
    if not program:
        return 0.0

    if delivery_mode == "onsite":
        return float(program.price_onsite or 0.0)

    return float(program.price_online or 0.0)


def _find_existing_registration(
    db: Session,
    candidate_id: str,
    program_id: str | None,
    program_name: str,
) -> Registration | None:
    query = (
        db.query(Registration)
        .filter(
            Registration.candidate_id == candidate_id,
            Registration.status.notin_(
                [
                    RegistrationStatus.CANCELLED,
                    RegistrationStatus.COMPLETED,
                ]
            ),
        )
    )

    if program_id:
        query = query.filter(
            Registration.program_id == program_id,
        )
    else:
        query = query.filter(
            Registration.program_id.is_(None),
            Registration.program_name == program_name,
        )

    return query.first()


def _update_firebase_metadata(
    registration: Registration,
    firebase_id: str | None,
    registration_date: datetime | None,
    firebase_timestamp: datetime | None,
    firebase_status: str | None,
    payment_reminder_sent: bool,
    reminder_sent_at: datetime | None,
) -> None:
    if firebase_id:
        registration.firebase_id = str(firebase_id)

    if registration_date:
        registration.registration_date = registration_date

    if hasattr(registration, "firebase_timestamp"):
        registration.firebase_timestamp = (
            firebase_timestamp or registration_date
        )

    if hasattr(registration, "firebase_status"):
        registration.firebase_status = firebase_status

    registration.payment_reminder_sent = payment_reminder_sent

    if reminder_sent_at:
        registration.reminder_sent_at = reminder_sent_at


def _update_candidate_from_registration(
    candidate: Candidate,
    full_name: str | None,
    phone: str | None,
    university: str | None,
    city: str | None,
) -> None:
    if full_name:
        candidate.full_name = full_name.strip()

    if phone:
        candidate.phone = phone.strip()

    if university:
        candidate.university = university.strip()

    if city:
        candidate.city = city.strip()


def process_registration_event(
    db: Session,
    email: str,
    courses_list: list[str] | None = None,
    delivery_mode: str = "online",
    full_name: str | None = None,
    phone: str | None = None,
    university: str | None = None,
    city: str | None = None,
    paid_amount: float = 0.0,
    training_start_date: datetime | None = None,
    firebase_id: str | None = None,
    registration_date: datetime | None = None,
    firebase_timestamp: datetime | None = None,
    firebase_status: str | None = None,
    payment_reminder_sent: bool = False,
    reminder_sent_at: datetime | None = None,
) -> Candidate:
    """
    Creates or updates a candidate and their registrations.

    Firebase rules:
        - Firebase timestamp is the authoritative registration date.
        - Repeated Firebase events update existing records.
        - Repeated Firebase events do not create duplicates.
        - Firebase reminder metadata is persisted.

    Program rules:
        - Similar programs are reused.
        - Ambiguous programs require admin review.
        - New programs are created when no close match exists.

    Payment rules:
        - A registration that arrives fully paid is enrolled in a class
          as soon as it is saved (see enrollment_service).
        - Unpaid and partially paid registrations stay PENDING.
    """
    if not email or not str(email).strip():
        raise ValueError(
            "Candidate email is required to process registration."
        )

    clean_email = str(email).strip().lower()

    mode_clean = (
        str(delivery_mode or "online")
        .strip()
        .lower()
    )

    if mode_clean not in {"online", "onsite", "recording"}:
        mode_clean = "online"

    # Firebase timestamp must be used exactly when supplied.
    authoritative_registration_date = (
        registration_date
        or firebase_timestamp
        or datetime.now(timezone.utc)
    )

    # Handle repeated Firebase events by updating the existing record.
    existing_firebase_registration = None

    if firebase_id:
        existing_firebase_registration = (
            db.query(Registration)
            .filter(
                Registration.firebase_id == str(firebase_id),
            )
            .first()
        )

    if existing_firebase_registration:
        candidate = db.get(
            Candidate,
            existing_firebase_registration.candidate_id,
        )

        if not candidate:
            raise ValueError(
                "Candidate linked to the existing registration was not found."
            )

        _update_candidate_from_registration(
            candidate=candidate,
            full_name=full_name,
            phone=phone,
            university=university,
            city=city,
        )

        _update_firebase_metadata(
            registration=existing_firebase_registration,
            firebase_id=firebase_id,
            registration_date=registration_date,
            firebase_timestamp=firebase_timestamp,
            firebase_status=firebase_status,
            payment_reminder_sent=payment_reminder_sent,
            reminder_sent_at=reminder_sent_at,
        )

        db.commit()
        db.refresh(candidate)

        return candidate

    # Find an existing candidate by normalized email.
    candidate = (
        db.query(Candidate)
        .filter(Candidate.email == clean_email)
        .first()
    )

    if not candidate:
        candidate = Candidate(
            email=clean_email,
            full_name=full_name.strip() if full_name else None,
            phone=phone.strip() if phone else None,
            university=university.strip() if university else None,
            city=city.strip() if city else None,
        )

        db.add(candidate)
        db.flush()
    else:
        _update_candidate_from_registration(
            candidate=candidate,
            full_name=full_name,
            phone=phone,
            university=university,
            city=city,
        )

    incoming_courses = courses_list or ["No specific program"]

    resolved_courses: list[dict] = []

    for course_name in incoming_courses:
        if not course_name or not str(course_name).strip():
            continue

        raw_name = _normalise_program_name(course_name)

        program, needs_review, score, suggested_program = (
            resolve_program_with_transformers(
                db=db,
                raw_program_name=raw_name,
            )
        )

        resolved_courses.append(
            {
                "raw_name": raw_name,
                "program": program,
                "needs_review": needs_review,
                "score": score,
                "suggested_program": suggested_program,
            }
        )

    if not resolved_courses:
        resolved_courses.append(
            {
                "raw_name": "No specific program",
                "program": None,
                "needs_review": True,
                "score": 0.0,
                "suggested_program": None,
            }
        )

    remaining_payment = max(
        0.0,
        float(paid_amount or 0.0),
    )

    created_registrations: list[Registration] = []
    # Registrations that did not exist before this call. The others are seen
    # again each time the registrations are read from Firebase (at every start).
    new_registrations = 0
    ambiguous_registrations: list[tuple[Registration, dict]] = []

    for index, item in enumerate(resolved_courses):
        program = item["program"]

        program_id = program.id if program else None

        # For ambiguous programs, preserve the original Firebase name.
        program_name = (
            program.name
            if program
            else item["raw_name"]
        )

        program_price = _get_program_price(
            program=program,
            delivery_mode=mode_clean,
        )

        registration_paid = min(
            remaining_payment,
            program_price,
        )

        remaining_payment -= registration_paid

        if program_price <= 0:
            payment_status = PaymentStatus.UNPAID
        elif registration_paid >= program_price:
            payment_status = PaymentStatus.PAID
        elif registration_paid > 0:
            payment_status = PaymentStatus.PARTIAL
        else:
            payment_status = PaymentStatus.UNPAID

        existing_registration = _find_existing_registration(
            db=db,
            candidate_id=candidate.id,
            program_id=program_id,
            program_name=program_name,
        )

        if existing_registration:
            if index == 0 and firebase_id:
                _update_firebase_metadata(
                    registration=existing_registration,
                    firebase_id=firebase_id,
                    registration_date=registration_date,
                    firebase_timestamp=firebase_timestamp,
                    firebase_status=firebase_status,
                    payment_reminder_sent=payment_reminder_sent,
                    reminder_sent_at=reminder_sent_at,
                )

            created_registrations.append(existing_registration)

            if item["needs_review"]:
                ambiguous_registrations.append(
                    (existing_registration, item)
                )

            continue

        registration = Registration(
            id=str(uuid.uuid4()),
            firebase_id=(
                str(firebase_id)
                if index == 0 and firebase_id
                else None
            ),
            candidate_id=candidate.id,
            program_id=program_id,
            program_name=program_name,
            delivery_mode=mode_clean,
            total_amount=program_price,
            total_price=program_price,
            paid_amount=registration_paid,
            payment_status=payment_status,

            # Do not use datetime.now() when Firebase supplied a timestamp.
            registration_date=authoritative_registration_date,

            training_start_date=training_start_date,

            # A paid registration is not automatically assigned to a class.
            status=RegistrationStatus.PENDING,

            payment_reminder_sent=payment_reminder_sent,
            reminder_sent_at=reminder_sent_at,
        )

        if hasattr(registration, "firebase_status"):
            registration.firebase_status = firebase_status

        if hasattr(registration, "firebase_timestamp"):
            registration.firebase_timestamp = (
                firebase_timestamp
                or registration_date
            )

        db.add(registration)
        db.flush()

        created_registrations.append(registration)
        new_registrations += 1

        if item["needs_review"]:
            ambiguous_registrations.append(
                (registration, item)
            )

    # Create one bias review for every ambiguous program.
    for registration, item in ambiguous_registrations:
        suggested_program = item["suggested_program"]

        existing_bias = (
            db.query(RegistrationBias)
            .filter(
                RegistrationBias.registration_id == registration.id,
                RegistrationBias.raw_input_program
                == item["raw_name"],
            )
            .first()
        )

        if not existing_bias:
            db.add(
                RegistrationBias(
                    id=str(uuid.uuid4()),
                    registration_id=registration.id,
                    candidate_id=candidate.id,
                    raw_input_program=item["raw_name"],
                    suggested_program_id=(
                        suggested_program.id
                        if suggested_program
                        else None
                    ),
                    similarity_score=item["score"],
                    admin_status="pending",
                )
            )

        suggested_name = (
            suggested_program.name
            if suggested_program
            else "No close existing program"
        )

        notification_exists = (
            db.query(Notification)
            .filter(
                Notification.registration_id == registration.id,
                Notification.category == "PROGRAM_MATCH",
            )
            .first()
        )

        if not notification_exists:
            db.add(
                Notification(
                    recipient_role="admin",
                    candidate_id=candidate.id,
                    registration_id=registration.id,
                    program_id=(
                        suggested_program.id
                        if suggested_program
                        else None
                    ),
                    category="PROGRAM_MATCH",
                    title="Program requires admin review",
                    message=(
                        f"'{item['raw_name']}' may match "
                        f"'{suggested_name}' "
                        f"({item['score'] * 100:.1f}% similarity)."
                    ),
                    action_url=f"/candidate/{candidate.id}",
                    payload={
                        "registration_id": registration.id,
                        "raw_program": item["raw_name"],
                        "suggested_program_id": (
                            suggested_program.id
                            if suggested_program
                            else None
                        ),
                        "similarity_score": item["score"],
                    },
                    is_read=False,
                    status="unread",
                )
            )

    if created_registrations:
        # Written in the history once, when the registration is new: not again
        # every time the same registration is read from Firebase.
        if new_registrations:
            db.add(
                Activity(
                    candidate_id=candidate.id,
                    event_type="REGISTRATION",
                    details=(
                        f"Registered for "
                        f"{new_registrations} program(s) "
                        f"({mode_clean.upper()}). "
                        "Payment and class assignment remain separate "
                        "administrative steps."
                    ),
                )
            )

        for registration in created_registrations:
            notification_exists = (
                db.query(Notification)
                .filter(
                    Notification.registration_id == registration.id,
                    Notification.category == "NEW_REGISTRATION",
                )
                .first()
            )

            if notification_exists:
                continue

            db.add(
                Notification(
                    recipient_role="admin",
                    candidate_id=candidate.id,
                    registration_id=registration.id,
                    program_id=registration.program_id,
                    category="NEW_REGISTRATION",
                    title=(
                        "New candidate registration: "
                        f"{candidate.full_name or candidate.email}"
                    ),
                    message=(
                        f"{candidate.full_name or candidate.email} "
                        f"registered for "
                        f"{registration.program_name}."
                    ),
                    action_url=f"/candidate/{candidate.id}",
                    payload={
                        "registration_id": registration.id,
                        "firebase_id": firebase_id,
                    },
                    is_read=False,
                    status="unread",
                )
            )

    db.commit()
    db.refresh(candidate)

    # A registration that arrives already fully paid joins a class right away.
    for registration in created_registrations:
        if (
            registration.payment_status == PaymentStatus.PAID
            and not registration.class_group_id
        ):
            enroll_paid_registration(db, registration)

    return candidate


def settle_registration_payment(
    db: Session,
    registration_id: str | int,
    payment_status: str | PaymentStatus,
    amount_paid: float | None = None,
) -> Registration:
    """
    Update payment without automatically assigning a class.

    A registration may become fully paid while still remaining pending:

        payment_status = PAID
        status = PENDING
        class_group_id = NULL

    The class-assignment workflow must assign a staffed class separately.
    """
    if isinstance(payment_status, str):
        try:
            payment_status = PaymentStatus(
                payment_status.strip().lower()
            )
        except ValueError as exc:
            allowed = ", ".join(
                status.value
                for status in PaymentStatus
            )

            raise ValueError(
                f"Invalid payment status. "
                f"Allowed values: {allowed}"
            ) from exc

    registration = db.get(
        Registration,
        registration_id,
    )

    if not registration:
        raise ValueError(
            f"Registration '{registration_id}' not found."
        )

    total_amount = float(
        registration.total_amount
        or registration.total_price
        or 0.0
    )

    if amount_paid is not None:
        amount_paid = float(amount_paid)

        if amount_paid < 0:
            raise ValueError(
                "Paid amount cannot be negative."
            )

        if amount_paid > total_amount:
            raise ValueError(
                "Paid amount cannot exceed the registration amount."
            )

        registration.paid_amount = amount_paid

        if total_amount > 0 and amount_paid >= total_amount:
            registration.payment_status = PaymentStatus.PAID
        elif amount_paid > 0:
            registration.payment_status = PaymentStatus.PARTIAL
        else:
            registration.payment_status = PaymentStatus.UNPAID
    else:
        registration.payment_status = payment_status

    # Payment alone must not assign the candidate to a class.
    # Preserve COMPLETED/CANCELLED states.
    if registration.status not in {
        RegistrationStatus.COMPLETED,
        RegistrationStatus.CANCELLED,
        RegistrationStatus.ASSIGNED,
    }:
        registration.status = RegistrationStatus.PENDING

    db.add(
        Notification(
            recipient_role="admin",
            candidate_id=registration.candidate_id,
            registration_id=registration.id,
            program_id=registration.program_id,
            category="PAYMENT",
            title="Payment status updated",
            message=(
                f"Payment for {registration.program_name} is now "
                f"{registration.payment_status.value}. "
                "Class assignment remains pending until a staffed "
                "class is selected."
            ),
            action_url=(
                f"/candidate/{registration.candidate_id}"
            ),
            is_read=False,
            status="unread",
        )
    )

    db.commit()
    db.refresh(registration)

    return registration
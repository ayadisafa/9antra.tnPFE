from datetime import datetime, timezone
import logging
import os

import firebase_admin
from firebase_admin import credentials, db

from app.database import SessionLocal
from app.models.candidate import Candidate
from app.models.registration import Registration
from app.services.candidate_service import process_registration_event

logger = logging.getLogger(__name__)

DEFAULT_KEY_PATH = "app/firebase_key.json"

DATABASE_URL = os.getenv(
    "FIREBASE_DATABASE_URL",
    "https://pfe2-c675d-default-rtdb.europe-west1.firebasedatabase.app/",
)


def _initialize_firebase() -> None:
    """
    Initialize Firebase only once.

    The service-account key must be provided through an environment variable.
    Do not commit the JSON key file or its private key to the repository.
    """
    if firebase_admin._apps:
        return

    firebase_key_path = os.getenv(
        "FIREBASE_CREDENTIALS_PATH",
        DEFAULT_KEY_PATH,
    )

    if not os.path.exists(firebase_key_path):
        logger.warning(
            "Firebase key file not found at %s. "
            "Firebase synchronization is disabled.",
            firebase_key_path,
        )
        return

    try:
        cred = credentials.Certificate(firebase_key_path)
        firebase_admin.initialize_app(
            cred,
            {"databaseURL": DATABASE_URL},
        )
        logger.info("Firebase Admin initialized successfully.")
    except Exception:
        logger.exception("Firebase Admin initialization failed.")


_initialize_firebase()


def parse_datetime(value: object) -> datetime | None:
    """
    Parse Firebase date values into timezone-aware UTC datetimes.

    Supported examples:

    - 8/14/2026 12:50:22
    - 2026-08-15T11:05:46.801Z
    - 2026-08-15T11:05:46+00:00
    - 2026-08-15 11:05:46
    """
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        cleaned_value = value.strip()

        formats = (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
            "%Y-%m-%d",
        )

        parsed = None

        for date_format in formats:
            try:
                parsed = datetime.strptime(cleaned_value, date_format)
                break
            except ValueError:
                continue

        if parsed is None:
            try:
                parsed = datetime.fromisoformat(
                    cleaned_value.replace("Z", "+00:00")
                )
            except ValueError:
                logger.warning("Unsupported Firebase datetime value: %r", value)
                return None
    else:
        logger.warning("Unsupported Firebase datetime type: %s", type(value))
        return None

    if parsed.tzinfo is None:
        # Firebase's timestamp example is interpreted as UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)

    return parsed


def _extract_courses(data: dict) -> list[str]:
    """
    Extract program names from Firebase.

    Priority:
    1. platform
    2. courses
    3. training
    """
    platform_value = data.get("platform")

    if isinstance(platform_value, str):
        courses = [
            item.strip()
            for item in platform_value.split(",")
            if item and item.strip()
        ]
        if courses:
            return courses

    courses_value = data.get("courses")

    if isinstance(courses_value, list):
        courses = [
            str(item).strip()
            for item in courses_value
            if item is not None and str(item).strip()
        ]
        if courses:
            return courses

    if courses_value:
        return [str(courses_value).strip()]

    training_value = data.get("training")

    if training_value:
        return [str(training_value).strip()]

    return ["No specific program"]


def _update_existing_registration(
    session,
    registration: Registration,
    candidate_data: dict,
    registration_timestamp: datetime | None,
    reminder_sent_at: datetime | None,
) -> None:
    """
    Update an already-synchronized Firebase registration.

    This keeps synchronization idempotent without ignoring later Firebase
    changes such as payment-reminder status or candidate profile updates.
    """
    candidate = session.get(Candidate, registration.candidate_id)

    if candidate:
        full_name = (
            candidate_data.get("nom")
            or candidate_data.get("full_name")
            or candidate_data.get("name")
        )

        if full_name:
            candidate.full_name = str(full_name).strip()

        for field in ("phone", "university", "city"):
            value = candidate_data.get(field)
            if value is not None and str(value).strip():
                setattr(candidate, field, str(value).strip())

        email = candidate_data.get("email")
        if email:
            normalized_email = str(email).strip().lower()
            if normalized_email != candidate.email:
                duplicate_candidate = (
                    session.query(Candidate)
                    .filter(
                        Candidate.email == normalized_email,
                        Candidate.id != candidate.id,
                    )
                    .first()
                )

                if not duplicate_candidate:
                    candidate.email = normalized_email
                else:
                    logger.warning(
                        "Cannot update candidate email for Firebase ID %s: "
                        "email already belongs to another candidate.",
                        registration.firebase_id,
                    )

    # Firebase timestamp remains the authoritative registration timestamp.
    if registration_timestamp:
        registration.registration_date = registration_timestamp

        if hasattr(registration, "firebase_timestamp"):
            registration.firebase_timestamp = registration_timestamp

    if hasattr(registration, "firebase_status"):
        registration.firebase_status = candidate_data.get("status")

    if "payment_reminder_sent" in candidate_data:
        registration.payment_reminder_sent = bool(
            candidate_data.get("payment_reminder_sent")
        )

    if reminder_sent_at:
        registration.reminder_sent_at = reminder_sent_at


def get_all_firebase_candidates() -> list[dict]:
    """Fetch all Firebase candidate records."""
    if not firebase_admin._apps:
        logger.warning(
            "Firebase Admin is not initialized. Returning no candidates."
        )
        return []

    try:
        snapshot = db.reference("candidates").get() or {}
        candidates = []

        if isinstance(snapshot, dict):
            for candidate_id, record in snapshot.items():
                if not isinstance(record, dict):
                    continue

                candidate_record = dict(record)
                candidate_record["firebase_id"] = candidate_id
                candidates.append(candidate_record)

        return candidates

    except Exception:
        logger.exception("Failed to fetch Firebase candidates.")
        return []


def sync_candidate_from_firebase(
    candidate_id: str,
    data: dict,
) -> None:
    """
    Synchronize one Firebase registration.

    Firebase's `timestamp` is used as the database registration date.
    A repeated Firebase event updates the existing record instead of creating
    a duplicate registration.
    """
    if not candidate_id:
        logger.warning("Cannot synchronize a Firebase record without an ID.")
        return

    if not isinstance(data, dict) or not data:
        logger.warning(
            "Skipping Firebase record %s because its data is invalid.",
            candidate_id,
        )
        return

    session = SessionLocal()

    try:
        email = data.get("email")

        if not email or not str(email).strip():
            logger.warning(
                "[SKIP] Firebase record %s has no email.",
                candidate_id,
            )
            return

        normalized_email = str(email).strip().lower()

        # This is the registration time supplied by Google Forms/Firebase.
        registration_timestamp = parse_datetime(data.get("timestamp"))

        # This is separate from the registration timestamp.
        training_start = parse_datetime(data.get("training_start_date"))

        reminder_sent_at = parse_datetime(data.get("reminder_sent_at"))

        existing_registration = (
            session.query(Registration)
            .filter(Registration.firebase_id == str(candidate_id))
            .first()
        )

        if existing_registration:
            _update_existing_registration(
                session=session,
                registration=existing_registration,
                candidate_data=data,
                registration_timestamp=registration_timestamp,
                reminder_sent_at=reminder_sent_at,
            )

            session.commit()

            logger.info(
                "[UPDATED] Firebase registration %s for %s.",
                candidate_id,
                normalized_email,
            )
            return

        full_name = (
            data.get("nom")
            or data.get("full_name")
            or data.get("name")
        )

        phone = data.get("phone")
        university = data.get("university")
        city = data.get("city")

        raw_mode = data.get("type") or data.get("mode") or "online"
        delivery_mode = (
            str(raw_mode).strip().lower()
            if raw_mode
            else "online"
        )

        # Only accept the supported delivery modes.
        if delivery_mode not in {"online", "onsite", "recording"}:
            logger.warning(
                "Unknown delivery mode %r for Firebase record %s. "
                "Using online.",
                delivery_mode,
                candidate_id,
            )
            delivery_mode = "online"

        courses_list = _extract_courses(data)

        process_registration_event(
            db=session,
            email=normalized_email,
            courses_list=courses_list,
            delivery_mode=delivery_mode,
            full_name=str(full_name).strip() if full_name else None,
            phone=str(phone).strip() if phone else None,
            university=str(university).strip() if university else None,
            city=str(city).strip() if city else None,
            training_start_date=training_start,
            firebase_id=str(candidate_id),

            # Important: this must be the exact Firebase registration time.
            registration_date=registration_timestamp,
            firebase_timestamp=registration_timestamp,

            firebase_status=data.get("status"),
            payment_reminder_sent=bool(
                data.get("payment_reminder_sent", False)
            ),
            reminder_sent_at=reminder_sent_at,
        )

        logger.info(
            "[SUCCESS] Firebase record %s synchronized for %s.",
            candidate_id,
            normalized_email,
        )

    except Exception:
        session.rollback()
        logger.exception(
            "[ERROR] Failed to synchronize Firebase record %s.",
            candidate_id,
        )

    finally:
        session.close()


def sync_all_existing_candidates() -> None:
    """Synchronize all existing Firebase records on startup."""
    if not firebase_admin._apps:
        logger.warning(
            "Firebase is not initialized. Initial synchronization skipped."
        )
        return

    logger.info("Performing initial Firebase candidate synchronization.")

    try:
        snapshot = db.reference("candidates").get() or {}

        if isinstance(snapshot, dict):
            for candidate_id, record in snapshot.items():
                if isinstance(record, dict):
                    sync_candidate_from_firebase(
                        str(candidate_id),
                        record,
                    )

        logger.info("Initial Firebase candidate synchronization complete.")

    except Exception:
        logger.exception("Initial Firebase synchronization failed.")


def listen_to_firebase_candidates() -> None:
    """Listen for new and updated Firebase candidate records."""
    if not firebase_admin._apps:
        logger.warning(
            "Firebase is not initialized. Realtime listener was not started."
        )
        return

    logger.info("Listening to Firebase 'candidates' node.")

    reference = db.reference("candidates")

    def on_event(event) -> None:
        try:
            event_path = event.path or "/"

            # A root event contains multiple candidate records.
            if event_path == "/":
                if isinstance(event.data, dict):
                    for candidate_id, record in event.data.items():
                        if isinstance(record, dict):
                            sync_candidate_from_firebase(
                                str(candidate_id),
                                record,
                            )
                return

            path_parts = [
                part for part in event_path.split("/")
                if part
            ]

            if not path_parts:
                return

            candidate_id = path_parts[0]

            # Firebase may send only a changed property. Fetch the complete
            # candidate record before synchronizing.
            child_data = db.reference(
                f"candidates/{candidate_id}"
            ).get()

            if isinstance(child_data, dict):
                sync_candidate_from_firebase(
                    str(candidate_id),
                    child_data,
                )

        except Exception:
            logger.exception("Firebase event handler failed.")

    reference.listen(on_event)


def start_firebase_listener() -> None:
    """Run the initial sync and then start the realtime listener."""
    logger.info("Starting Firebase synchronization service.")

    try:
        sync_all_existing_candidates()
        listen_to_firebase_candidates()
    except Exception:
        logger.exception("Firebase listener startup failed.")
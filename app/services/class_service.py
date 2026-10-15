import os
import re
import logging
from datetime import datetime, timedelta, timezone, time
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_

from app.models.classgroup import ClassGroup, ClassStatus
from app.models.classsession import ClassSession
from app.models.professor import Professor
from app.models.professor_absence import ProfessorAbsence
from app.models.registration import Registration, RegistrationStatus, PaymentStatus
from app.models.program import Program
from app.models.program import ProgramLevel
from app.models.notification import Notification
from app.services.schedule_utils import class_end_dt, class_start_dt, local_now, parse_days

logger = logging.getLogger(__name__)

# Default global capacity loaded from environment variables (defaults to 30)
DEFAULT_CLASS_CAPACITY = int(os.getenv("DEFAULT_CLASS_CAPACITY", 30))
DEFAULT_CLASS_START_DAYS = int(os.getenv("DEFAULT_CLASS_START_DAYS", 10))


# ==========================================
# 1. HELPER FUNCTIONS (NAMING & SCHEDULING)
# ==========================================

def generate_class_name(program_name: str | None, start_date, class_number: int) -> str:
    """
    Generates a class name using the initial letters of the program name.
    Example: "Data Science", 2026-09-24 -> "DS-20260924-1"
    """
    if not program_name or not program_name.strip():
        return f"PRG-{start_date:%Y%m%d}-{class_number}"

    words = re.findall(r'\b[A-Za-z0-9]', program_name)
    initials = "".join(words).upper()

    if not initials:
        initials = program_name.strip()[:3].upper()

    return f"{initials}-{start_date:%Y%m%d}-{class_number}"


def generate_unique_class_name(db: Session, program_name: str | None, start_date, exclude_id: str | None = None) -> str:
    """Build a readable class name and increment until it is unique."""
    prefix_number = db.query(ClassGroup).filter(
        ClassGroup.name.like(f"%{start_date:%Y%m%d}-%")
    ).count() + 1
    while True:
        name = generate_class_name(program_name, start_date, prefix_number)
        query = db.query(ClassGroup.id).filter(ClassGroup.name == name)
        if exclude_id:
            query = query.filter(ClassGroup.id != exclude_id)
        if not query.first():
            return name
        prefix_number += 1


def do_schedules_overlap(
    days1: list[str], start1: time, end1: time,
    days2: list[str], start2: time, end2: time
) -> bool:
    """
    Checks if two sets of class schedules overlap on common days and time ranges.
    """
    if not days1 or not days2 or not start1 or not end1 or not start2 or not end2:
        return False

    common_days = set(days1).intersection(set(days2))
    if not common_days:
        return False

    return max(start1, start2) < min(end1, end2)


def check_candidate_schedule_conflict(
    db: Session, 
    candidate_id: str | int, 
    target_class: ClassGroup
) -> bool:
    """
    Validates if a candidate is already assigned to another class whose schedule
    conflicts with the target class.
    """
    if not target_class.start_time or not target_class.end_time or not target_class.days_of_week:
        return False

    existing_registrations = (
        db.query(Registration)
        .options(joinedload(Registration.class_group))
        .filter(
            Registration.candidate_id == candidate_id,
            Registration.status != RegistrationStatus.CANCELLED,
            Registration.class_group_id != target_class.id
        )
        .all()
    )

    target_days = parse_days(target_class.days_of_week)

    for reg in existing_registrations:
        existing_cls = reg.class_group
        if existing_cls and existing_cls.start_time and existing_cls.end_time and existing_cls.days_of_week:
            target_start = target_class.start_date.date() if target_class.start_date else None
            target_end = (
                target_class.end_date.date()
                if target_class.end_date
                else target_start + timedelta(weeks=target_class.total_weeks or 12)
                if target_start
                else None
            )
            existing_start = existing_cls.start_date.date() if existing_cls.start_date else None
            existing_end = (
                existing_cls.end_date.date()
                if existing_cls.end_date
                else existing_start + timedelta(weeks=existing_cls.total_weeks or 12)
                if existing_start
                else None
            )
            if target_start and target_end and existing_start and existing_end:
                if target_end < existing_start or existing_end < target_start:
                    continue
            existing_days = parse_days(existing_cls.days_of_week)

            if do_schedules_overlap(
                target_days, target_class.start_time, target_class.end_time,
                existing_days, existing_cls.start_time, existing_cls.end_time
            ):
                return True

    return False


def get_class_week_and_status(class_group: ClassGroup) -> dict:
    """
    Calculates active week number and program state ('not_started', 'ongoing', 'completed').
    """
    now = local_now()
    start_date = class_start_dt(class_group)
    end_date = class_end_dt(class_group)

    total_weeks = getattr(class_group, "total_weeks", None) or 12

    if not start_date or now < start_date:
        return {"status": "not_started", "current_week": 0, "total_weeks": total_weeks}

    if end_date and now > end_date:
        return {"status": "completed", "current_week": total_weeks, "total_weeks": total_weeks}

    days_elapsed = (now - start_date).days
    current_week = (days_elapsed // 7) + 1

    return {
        "status": "ongoing",
        "current_week": min(current_week, total_weeks),
        "total_weeks": total_weeks
    }


def get_session_status(session: ClassSession) -> str:
    now = local_now()
    start = datetime.combine(session.session_date, session.start_time or time.min)
    end = datetime.combine(session.session_date, session.end_time or time.max)
    if now < start:
        return "upcoming"
    if now > end:
        return "completed"
    return "ongoing"


# ==========================================
# 2. CORE ASSIGNMENT & UPDATE WORKFLOWS
# ==========================================

def assign_candidate_to_class(
    db: Session,
    registration: Registration,
    max_capacity: int = DEFAULT_CLASS_CAPACITY
) -> ClassGroup | None:
    """
    Assigns a paid registration to an open class of its program, or creates a
    new class. Kept for existing callers; the rules live in enrollment_service.
    """
    from app.services.enrollment_service import enroll_paid_registration

    return enroll_paid_registration(db, registration).class_group


def assign_and_update_registration(
    db_session: Session,
    registration_id: str | int,
    new_status: str | RegistrationStatus = None,
    class_group_id: str | int = None
) -> Registration:
    """
    Updates registration status and/or explicitly assigns a candidate to a specific ClassGroup.
    A class does not need a professor to accept candidates.
    """
    from app.services.enrollment_service import move_registration

    registration = db_session.get(Registration, registration_id)
    if not registration:
        raise ValueError(f"Registration '{registration_id}' not found.")

    if class_group_id:
        registration = move_registration(db_session, registration_id, class_group_id)

    if new_status:
        if isinstance(new_status, str):
            new_status = RegistrationStatus(new_status.lower().strip())
        registration.status = new_status
        db_session.add(Notification(
            recipient_role="admin", category="REGISTRATION", title="Registration status changed",
            message=f"Registration {registration.id} is now {new_status.value}.",
            action_url=f"/candidate/{registration.candidate_id}", candidate_id=registration.candidate_id,
            program_id=registration.program_id,
        ))

    db_session.commit()
    db_session.refresh(registration)
    return registration


def start_due_classes(db: Session) -> int:
    """Apply automatic class status changes (not started -> ongoing -> completed)."""
    from app.services.enrollment_service import sync_class_lifecycle

    return sync_class_lifecycle(db)


def update_class_group_details(
    db_session: Session, 
    class_group_id: str | int, 
    new_name: str | None = None,
    new_capacity: int | None = None
) -> ClassGroup:
    """
    Allows explicitly updating a class group's name and/or maximum capacity.
    """
    class_group = db_session.get(ClassGroup, class_group_id)
    if not class_group:
        raise ValueError(f"Class Group '{class_group_id}' not found.")

    if new_capacity is not None:
        if new_capacity < 1:
            raise ValueError("Capacity limit must be at least 1.")
        class_group.max_capacity = new_capacity

    if new_name is not None and new_name.strip():
        class_group.name = new_name.strip()

    db_session.commit()
    db_session.refresh(class_group)
    return class_group


def update_registration_status(
    db_session: Session, 
    registration_id: str | int, 
    new_status: str | RegistrationStatus
) -> Registration:
    """
    Convenience wrapper to update registration status without altering class assignments.
    """
    return assign_and_update_registration(db_session, registration_id, new_status=new_status)


# ==========================================
# 3. DASHBOARD & SCHEDULER VIEWS
# ==========================================

def get_dashboard_classes_by_program(db: Session) -> list[dict]:
    """
    Aggregates active classes grouped by program with progress week status for dashboard UI.
    """
    programs = db.query(Program).all()
    dashboard_data = []

    for prog in programs:
        classes = db.query(ClassGroup).filter(ClassGroup.program_id == prog.id).all()
        program_classes = []

        for cls in classes:
            timing_info = get_class_week_and_status(cls)
            if timing_info["status"] in ["ongoing", "not_started"]:
                enrolled_count = db.query(Registration).filter(
                    Registration.class_group_id == cls.id,
                    Registration.status != RegistrationStatus.CANCELLED
                ).count()

                program_classes.append({
                    "class_id": cls.id,
                    "class_name": cls.name,
                    "professor_name": getattr(cls, "professor_name", "Non assigné"),
                    "status": timing_info["status"],
                    "current_week": timing_info["current_week"],
                    "total_weeks": timing_info["total_weeks"],
                    "days_of_week": getattr(cls, "days_of_week", ""),
                    "start_time": cls.start_time.strftime("%H:%M") if getattr(cls, "start_time", None) else None,
                    "end_time": cls.end_time.strftime("%H:%M") if getattr(cls, "end_time", None) else None,
                    "enrolled_candidates": enrolled_count,
                    "max_capacity": getattr(cls, "capacity", None) or cls.max_capacity
                })

        dashboard_data.append({
            "program_id": prog.id,
            "program_name": prog.name,
            "total_active_classes": len(program_classes),
            "classes": program_classes
        })

    return dashboard_data


def get_calendar_events(db: Session) -> list[dict]:
    """
    Formats class schedules into UI calendar events (e.g., FullCalendar / React Big Calendar).
    """
    classes = db.query(ClassGroup).options(joinedload(ClassGroup.program)).all()
    events = []

    for cls in classes:
        enrolled_count = sum(
            1 for registration in (cls.registrations or [])
            if registration.status != RegistrationStatus.CANCELLED
        )
        program_color = getattr(getattr(cls, 'program', None), 'color', None) or '#a42c67'
        current_week = getattr(cls, 'current_week', None)
        total_weeks = getattr(cls, 'total_weeks', None) or 12
        conflict = False
        for other in classes:
            if other.id == cls.id or not cls.professor_name or other.professor_name != cls.professor_name:
                continue
            if not cls.start_time or not cls.end_time or not other.start_time or not other.end_time:
                continue
            cls_days = {day.strip().lower() for day in (cls.days_of_week or "").split(",") if day.strip()}
            other_days = {day.strip().lower() for day in (other.days_of_week or "").split(",") if day.strip()}
            if cls_days & other_days and cls.start_time < other.end_time and other.start_time < cls.end_time:
                conflict = True
                break
        events.append({
            "id": cls.id,
            "title": f"{cls.name} ({cls.program.name if cls.program else 'PRG'})",
            "daysOfWeek": [d.strip() for d in cls.days_of_week.split(",")] if cls.days_of_week else [],
            "startTime": cls.start_time.strftime("%H:%M") if cls.start_time else "09:00",
            "endTime": cls.end_time.strftime("%H:%M") if cls.end_time else "12:00",
            "room": getattr(cls, "room_number", "Non assigné"),
            "professor": getattr(cls, "professor_name", "Non assigné"),
            "capacity": getattr(cls, 'max_capacity', None) or cls.capacity or 30,
            "enrolled": enrolled_count,
            "color": program_color,
            "status": getattr(getattr(cls, 'status', None), 'value', str(getattr(cls, 'status', 'not_started'))),
            "current_week": current_week,
            "total_weeks": total_weeks,
            "url": f"/classes/{cls.id}",
            "conflict": conflict,
        })

    return events


def get_candidate_session_calendar(db: Session) -> list[dict]:
    """Build session rows with candidate progress and assignment conflicts."""
    classes = (
        db.query(ClassGroup)
        .options(
            joinedload(ClassGroup.program),
            joinedload(ClassGroup.sessions),
            joinedload(ClassGroup.registrations).joinedload(Registration.candidate),
            joinedload(ClassGroup.registrations).joinedload(Registration.program_progress),
        )
        .filter(ClassGroup.status != ClassStatus.COMPLETED)
        .all()
    )
    rows = []

    for class_group in classes:
        for session in sorted(class_group.sessions, key=lambda item: (item.session_date, item.start_time or time.min)):
            session_start = session.start_time or class_group.start_time or time.min
            session_end = session.end_time or class_group.end_time or time.max
            for registration in class_group.registrations:
                if registration.status == RegistrationStatus.CANCELLED:
                    continue
                progress = registration.program_progress
                current_week = (
                    progress.current_week
                    if progress and progress.current_week is not None
                    else class_group.current_week or session.week_number
                )
                total_weeks = (
                    progress.total_weeks
                    if progress and progress.total_weeks
                    else class_group.total_weeks or 12
                )
                completion = (
                    progress.completion_percentage
                    if progress and progress.completion_percentage is not None
                    else round(min((current_week / total_weeks) * 100, 100), 2)
                )
                candidate = registration.candidate
                rows.append({
                    "session_date": session.session_date,
                    "date_label": session.session_date.strftime("%a, %d %b %Y"),
                    "start_time": session_start.strftime("%H:%M"),
                    "end_time": session_end.strftime("%H:%M"),
                    "_start": session_start,
                    "_end": session_end,
                    "class_name": class_group.name,
                    "class_id": class_group.id,
                    "program_name": class_group.program.name if class_group.program else "Program pending",
                    "candidate_name": (
                        candidate.full_name or candidate.email
                        if candidate else "Candidate unavailable"
                    ),
                    "candidate_id": registration.candidate_id,
                    "week_number": session.week_number,
                    "current_week": current_week,
                    "total_weeks": total_weeks,
                    "completion_percentage": completion,
                    "topic": session.topic,
                    "session_status": getattr(session, "session_status", get_session_status(session)),
                    "conflict_reason": None,
                })

    for row in rows:
        conflicts = [
            other for other in rows
            if other["candidate_id"] == row["candidate_id"]
            and other["class_id"] != row["class_id"]
            and other["session_date"] == row["session_date"]
            and row["_start"] < other["_end"]
            and other["_start"] < row["_end"]
        ]
        if conflicts:
            other = conflicts[0]
            row["conflict_reason"] = (
                f"Same-time assignment: {other['class_name']} "
                f"({other['start_time']}-{other['end_time']})"
            )

    for row in rows:
        row.pop("_start", None)
        row.pop("_end", None)
    return sorted(rows, key=lambda item: (item["session_date"], item["start_time"], item["candidate_name"]))
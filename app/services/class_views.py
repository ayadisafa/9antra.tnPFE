"""Display values for classes, shared by the class pages and the candidate profile."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.candidate_reminder import CandidateReminder
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.registration import Registration, RegistrationStatus
from app.services.enrollment_service import (
    START_AFTER_DAYS,
    START_MIN_ENROLLMENT,
    class_capacity,
    hold_reasons,
)
from app.services.schedule_utils import (
    class_end_dt,
    class_start_dt,
    describe_schedule_en,
    format_date_en,
    humanize_until,
)


def start_rule_label(class_group: ClassGroup) -> str:
    if class_group.start_mode == START_MIN_ENROLLMENT:
        return f"Starts when {class_group.min_students_to_start} candidates are enrolled"
    if class_group.start_mode == START_AFTER_DAYS:
        return f"Starts {class_group.start_delay_days} days after creation"
    return "Fixed start date"


def class_summary(class_group: ClassGroup, now: datetime) -> dict:
    """Enrollment, timing and status of a class, ready for templates."""
    members = [item for item in class_group.registrations if item.status != RegistrationStatus.CANCELLED]
    capacity = class_capacity(class_group)
    start, end = class_start_dt(class_group), class_end_dt(class_group)
    if start:
        starts_label = format_date_en(start)
        if class_group.start_time:
            starts_label += f" · {class_group.start_time:%H:%M}"
    else:
        missing = max(0, (class_group.min_students_to_start or 0) - len(members))
        starts_label = f"Waiting for {missing} more candidate(s)" if missing else "Not scheduled"
    # What it still needs: somebody in it, and a professor of its speciality.
    needs = hold_reasons(class_group, len(members)) if class_group.status != ClassStatus.COMPLETED else []
    on_hold = bool(needs and start and start <= now and class_group.status == ClassStatus.NOT_STARTED)
    return {
        "needs": needs,
        "on_hold": on_hold,
        "enrolled": len(members),
        "capacity": capacity,
        "fill_pct": min(100, round(len(members) / capacity * 100)) if capacity else 0,
        "start": start,
        "end": end,
        "starts_label": starts_label,
        "countdown": humanize_until(start, now) if start else None,
        "schedule_label": describe_schedule_en(class_group),
        "rule_label": start_rule_label(class_group),
        "status": class_group.status.value,
        "total_weeks": class_group.total_weeks or 12,
        "current_week": class_group.current_week or 0,
    }


def reminder_view(reminder: CandidateReminder, now: datetime) -> dict:
    days = reminder.reminder_key.removeprefix("training_").removesuffix("d")
    if reminder.status == "sent":
        state, label = "sent", (f"Sent {reminder.sent_at:%d %b %H:%M}" if reminder.sent_at else "Sent")
    elif reminder.status == "planned" and reminder.planned_for <= now:
        state, label = "due", "Due · waiting for email"
    elif reminder.status == "planned":
        state, label = "planned", f"Planned {reminder.planned_for:%d %b %H:%M}"
    elif reminder.status == "failed":
        state, label = "failed", "Delivery failed"
    else:
        state, label = "skipped", "Skipped"
    return {"days": days, "state": state, "label": label, "planned_for": reminder.planned_for}


def reminders_by_registration(db: Session, registration_ids: list[str], now: datetime) -> dict[str, list[dict]]:
    """Session reminders of each registration, furthest from the first session first."""
    grouped: dict[str, list[dict]] = {}
    if not registration_ids:
        return grouped
    rows = (
        db.query(CandidateReminder)
        .filter(
            CandidateReminder.registration_id.in_(registration_ids),
            CandidateReminder.reminder_type == "training",
        )
        .order_by(CandidateReminder.planned_for.asc())
        .all()
    )
    for row in rows:
        grouped.setdefault(row.registration_id, []).append(reminder_view(row, now))
    return grouped


def move_options(db: Session, program_id: str | None, current_class_id: str | None, now: datetime) -> list[dict]:
    """Other classes of the program a candidate can be moved to."""
    if not program_id:
        return []
    options = []
    query = db.query(ClassGroup).filter(
        ClassGroup.program_id == program_id,
        ClassGroup.status != ClassStatus.COMPLETED,
    ).order_by(ClassGroup.start_date.asc())
    for other in query:
        if other.id == current_class_id:
            continue
        summary = class_summary(other, now)
        options.append({
            "id": other.id,
            "label": f"{other.name} · {summary['starts_label']} · {summary['enrolled']}/{summary['capacity']}",
            "full": summary["enrolled"] >= summary["capacity"],
        })
    return options


def upcoming_sessions(registration: Registration, now: datetime, limit: int = 6) -> list[dict]:
    """Next sessions of a registration's class: the weekly plan, or the schedule when no plan exists."""
    class_group = registration.class_group
    if not class_group or class_group.status == ClassStatus.COMPLETED:
        return []
    program = registration.program.name if registration.program else registration.program_name
    today = now.date()
    sessions = []
    if class_group.sessions:
        for session in sorted(class_group.sessions, key=lambda item: (item.session_date, item.week_number or 0)):
            if session.session_date >= today:
                sessions.append({
                    "date": session.session_date,
                    "start_time": session.start_time or class_group.start_time,
                    "end_time": session.end_time or class_group.end_time,
                    "topic": session.topic,
                    "week": session.week_number,
                })
    elif class_start_dt(class_group):
        first_day = class_start_dt(class_group).date()
        for index in range(class_group.total_weeks or 12):
            day = first_day + timedelta(weeks=index)
            if day >= today:
                sessions.append({
                    "date": day,
                    "start_time": class_group.start_time,
                    "end_time": class_group.end_time,
                    "topic": None,
                    "week": index + 1,
                })
    for session in sessions:
        session.update({"class_id": class_group.id, "class_name": class_group.name, "program": program})
    return sessions[:limit]

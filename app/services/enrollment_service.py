"""Class enrollment lifecycle.

* A fully paid registration joins an open class of its program, or a new
  class is created for it (with a professor when one is available).
* Each class carries its own start rule and reminder plan, editable by admins.
* Class status follows its dates automatically.
* Candidates are reminded N days before the first session.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.candidate_reminder import CandidateReminder
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.classsession import ClassSession
from app.models.notification import Notification
from app.models.professor import Professor
from app.models.program import Program, ProgramLevel
from app.models.registration import PaymentStatus, Registration, RegistrationStatus
from app.services import class_emails, email_templates
from app.services.background import run_in_background
from app.services.schedule_utils import (
    class_end_dt,
    class_start_dt,
    first_session_on_or_after,
    format_date_en,
    format_days,
    local_now,
    parse_days,
    parse_session_time_text,
    parse_time,
    reminder_anchor,
    utc_to_local,
)
from app.services.settings_service import (
    format_reminder_days,
    get_settings,
    parse_reminder_days,
)

logger = logging.getLogger(__name__)

START_AFTER_DAYS = "days_after_creation"
START_MIN_ENROLLMENT = "min_enrollment"
START_FIXED_DATE = "fixed_date"
START_MODES = (START_AFTER_DAYS, START_MIN_ENROLLMENT, START_FIXED_DATE)

MAX_REMINDER_ATTEMPTS = 3

# Serializes "find or create a class" and status transitions across request,
# scheduler and Firebase threads.
_lifecycle_lock = threading.RLock()


@dataclass
class EnrollmentResult:
    registration: Registration
    class_group: ClassGroup | None = None
    created_class: bool = False
    reason: str | None = None


# ------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------

def class_capacity(class_group: ClassGroup) -> int:
    return class_group.capacity or class_group.max_capacity or 30


def class_members(db: Session, class_id: str) -> list[Registration]:
    return (
        db.query(Registration)
        .filter(
            Registration.class_group_id == class_id,
            Registration.status != RegistrationStatus.CANCELLED,
        )
        .order_by(Registration.registration_date.asc())
        .all()
    )


def enrolled_count(db: Session, class_id: str, exclude_registration_id: str | None = None) -> int:
    query = db.query(Registration).filter(
        Registration.class_group_id == class_id,
        Registration.status != RegistrationStatus.CANCELLED,
    )
    if exclude_registration_id:
        query = query.filter(Registration.id != exclude_registration_id)
    return query.count()


def class_url(class_group: ClassGroup, registration: Registration | None = None) -> str:
    url = f"/classes/{class_group.id}"
    return f"{url}?registration={registration.id}" if registration else url


def _who(registration: Registration) -> str:
    candidate = registration.candidate
    return (candidate.full_name or candidate.email) if candidate else "Candidate"


def _when(class_group: ClassGroup) -> str:
    start = class_start_dt(class_group)
    if not start:
        return f"when {class_group.min_students_to_start} candidates are enrolled"
    label = format_date_en(start)
    return f"{label} at {class_group.start_time:%H:%M}" if class_group.start_time else label


def _notify(db: Session, *, category: str, title: str, message: str, action_url: str,
            registration: Registration | None = None, class_group: ClassGroup | None = None) -> None:
    db.add(Notification(
        recipient_role="admin",
        category=category,
        title=title,
        message=message,
        action_url=action_url,
        candidate_id=registration.candidate_id if registration else None,
        registration_id=registration.id if registration else None,
        program_id=(
            registration.program_id if registration
            else class_group.program_id if class_group else None
        ),
        payload={"class_id": class_group.id} if class_group else None,
    ))


def _tell_once(db: Session, class_group: ClassGroup, *, title: str, message: str, about: str) -> None:
    """A notification about a class, said once for as long as the same thing (`about`) is missing."""
    url = class_url(class_group)
    told = (
        db.query(Notification.id)
        .filter(Notification.category == "CLASS", Notification.action_url == url, Notification.message.contains(about))
        .first()
    )
    if told is None:
        _notify(db, category="CLASS", title=title, message=message, action_url=url, class_group=class_group)


# ------------------------------------------------------------------
# Sessions
# ------------------------------------------------------------------

def curriculum_weeks(db: Session, program_id: str | None) -> list:
    """Curriculum weeks of a program in teaching order (level, then week)."""
    if not program_id:
        return []
    levels = db.query(ProgramLevel).filter(ProgramLevel.program_id == program_id).all()
    levels.sort(key=lambda level: (level.level_order is None, level.level_order or 0))
    weeks = []
    for level in levels:
        weeks.extend(sorted(level.weeks, key=lambda week: week.week_number or 0))
    return weeks


def seed_class_sessions(db: Session, class_group: ClassGroup) -> None:
    """Create one weekly session per curriculum week, starting on the class start date."""
    if not class_group.start_date or class_group.sessions:
        return
    first_day = class_group.start_date.date()
    for index, week in enumerate(curriculum_weeks(db, class_group.program_id)):
        class_group.sessions.append(ClassSession(
            week_number=index + 1,
            session_date=first_day + timedelta(weeks=index),
            start_time=class_group.start_time,
            end_time=class_group.end_time,
            topic=week.topic_title,
            notes=week.learning_objectives,
        ))


def reschedule_sessions(db: Session, class_group: ClassGroup) -> None:
    """Shift the weekly plan so its first session lands on the class start date."""
    if not class_group.start_date:
        return
    if not class_group.sessions:
        seed_class_sessions(db, class_group)
        return
    shift = class_group.start_date.date() - min(item.session_date for item in class_group.sessions)
    for session in class_group.sessions:
        session.session_date = session.session_date + shift
        session.start_time = class_group.start_time
        session.end_time = class_group.end_time


# ------------------------------------------------------------------
# Class creation
# ------------------------------------------------------------------

def default_schedule(db: Session, program: Program | None, settings) -> tuple[list[int], time | None, time | None]:
    """Session weekdays and hours for a new class: the program's schedule, else the global defaults."""
    days, start, end = [], None, None
    if program:
        texts = [program.class_session_time] + [level.class_session_time for level in program.levels]
        for text in texts:
            days, start, end = parse_session_time_text(text)
            if days or start:
                break
    if not days:
        days = parse_days(settings.default_session_days)
    if not start:
        start = parse_time(settings.default_session_start_time)
        end = parse_time(settings.default_session_end_time)
    return days, start, end


def _normalize(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9àâçéèêëîïôöûùüÿœ#+]+", text))


def _specialty_rank(professor: Professor, program_name: str) -> int | None:
    """0 = teaches exactly this program, 1 = related specialty, None = no match.

    Related means whole words: "Flutter" fits "Mobile Development (Flutter + Node)",
    while "BI" does not fit "Mobile" just because the letters are in it.
    """
    target = _normalize(program_name)
    specialties = [_normalize(item) for item in (professor.specialties or "").split(",") if item.strip()]
    specialties += [_normalize(skill.name) for skill in professor.skills]
    if not target or not specialties:
        return None
    if target in specialties:
        return 0
    wanted = _words(target)
    for item in specialties:
        known = _words(item)
        if known and wanted and (known <= wanted or wanted <= known):
            return 1
    return None


def teaches(professor: Professor | None, program: Program | None) -> bool:
    """Whether this program is one of the professor's specialities."""
    return bool(professor and program and _specialty_rank(professor, program.name) is not None)


def teachers_of(db: Session, program: Program | None) -> list[Professor]:
    """The professors who have this program among their specialities, closest first."""
    if program is None:
        return []
    ranked = [
        (_specialty_rank(professor, program.name), professor.name.lower(), professor)
        for professor in db.query(Professor).all()
    ]
    return [item[2] for item in sorted((item for item in ranked if item[0] is not None), key=lambda item: item[:2])]


def hold_reasons(class_group: ClassGroup, enrolled: int) -> list[str]:
    """What keeps a class from starting, in plain words; empty when nothing does.

    A class needs somebody to attend it and a professor of its speciality.
    """
    reasons = []
    if not enrolled:
        reasons.append("no candidate is enrolled")
    professor = class_group.professor
    if professor is None:
        if not class_group.professor_name:       # older classes only carry a name: taken as assigned
            reasons.append("no professor is assigned")
    elif class_group.program is not None and not teaches(professor, class_group.program):
        reasons.append(f"{professor.name} does not have {class_group.program.name} among their specialities")
    return reasons


def start_blockers(db: Session, class_group: ClassGroup) -> list[str]:
    return hold_reasons(class_group, enrolled_count(db, class_group.id))


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _weeks_of(group: ClassGroup) -> tuple[date | None, date | None]:
    """The first and the last day a class runs (None when its date is not known yet)."""
    start = class_start_dt(group)
    if start is None:
        return None, None
    return start.date(), start.date() + timedelta(weeks=max(1, group.total_weeks or 1))


def professor_clash(db: Session, professor_id: str | None, days: list[int], start_time: time | None, end_time: time | None,
                    first_day: date | None = None, weeks: int | None = None, exclude_id: str | None = None) -> ClassGroup | None:
    """Another class of this professor on the same days and hours, in the same weeks: a professor cannot be in two places."""
    if not professor_id or not days or not start_time or not end_time:
        return None
    last_day = first_day + timedelta(weeks=max(1, weeks or 1)) if first_day else None
    for other in (db.query(ClassGroup).filter(ClassGroup.professor_id == professor_id, ClassGroup.status != ClassStatus.COMPLETED,
                                              ClassGroup.id != (exclude_id or "")).all()):
        if not (set(days) & set(parse_days(other.days_of_week))) or not other.start_time or not other.end_time:
            continue
        if not max(start_time, other.start_time) < min(end_time, other.end_time):
            continue
        other_first, other_last = _weeks_of(other)
        if first_day and other_first and (last_day <= other_first or other_last <= first_day):
            continue                    # the same hours, but not in the same weeks
        return other
    return None


def clash_message(professor: Professor, other: ClassGroup) -> str:
    hours = f"{other.start_time:%H:%M}–{other.end_time:%H:%M}" if other.start_time and other.end_time else ""
    return (f"{professor.name} already teaches {other.name} on the same days and hours ({other.days_of_week} {hours}). "
            "A professor cannot have two classes at the same time: choose another professor, or other hours.")


def find_available_professor(db: Session, program: Program | None, days: list[int],
                             start_time: time | None, end_time: time | None,
                             on_date: date | None = None) -> Professor | None:
    """Best professor of the program's specialty who is free on the class slot."""
    if not program:
        return None
    on_date = on_date or local_now().date()
    active_classes = (
        db.query(ClassGroup)
        .filter(ClassGroup.professor_id.is_not(None), ClassGroup.status != ClassStatus.COMPLETED)
        .all()
    )
    ranked = []
    for professor in db.query(Professor).filter(Professor.is_available.is_(True)).all():
        rank = _specialty_rank(professor, program.name)
        if rank is None:
            continue
        if professor.available_from and professor.available_from > on_date:
            continue
        if professor.available_until and professor.available_until < on_date:
            continue
        own_classes = [item for item in active_classes if item.professor_id == professor.id]
        busy = any(
            set(days) & set(parse_days(item.days_of_week))
            and start_time and end_time and item.start_time and item.end_time
            and max(start_time, item.start_time) < min(end_time, item.end_time)
            for item in own_classes
        )
        if busy:
            continue
        ranked.append((rank, len(own_classes), professor.name.lower(), professor))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[:3])
    return ranked[0][3]


def planned_start_after_days(class_group: ClassGroup, now: datetime | None = None) -> datetime:
    """First session on or after `start_delay_days` days from the class creation."""
    created = utc_to_local(class_group.created_at) or now or local_now()
    target = (created + timedelta(days=class_group.start_delay_days or 0)).date()
    first_day = first_session_on_or_after(target, parse_days(class_group.days_of_week))
    return datetime.combine(first_day, class_group.start_time or time(0, 0))


def create_class(db: Session, program: Program, *, settings=None,
                 professor: Professor | None = None, pick_professor: bool | None = None,
                 start_date: date | None = None, capacity: int | None = None,
                 total_weeks: int | None = None, days_of_week: str | None = None,
                 start_time: time | None = None, end_time: time | None = None,
                 auto_created: bool = False) -> ClassGroup:
    """Create a class using the admin defaults; explicit arguments override them."""
    from app.services.class_service import generate_unique_class_name

    if professor is not None and start_time and end_time:
        other = professor_clash(db, professor.id, parse_days(days_of_week), start_time, end_time, start_date, total_weeks)
        if other is not None:
            raise ValueError(clash_message(professor, other))
    if professor is not None and not teaches(professor, program):
        raise ValueError(
            f"{professor.name} does not have {program.name} among their specialities. "
            "Add it to their profile first, or choose another professor."
        )
    settings = settings or get_settings(db)
    now = local_now()
    default_days, default_start, default_end = default_schedule(db, program, settings)
    days = parse_days(days_of_week) or default_days
    if not start_time:
        start_time, end_time = default_start, end_time or default_end

    capacity = capacity or settings.default_class_capacity or 30
    class_group = ClassGroup(
        program_id=program.id,
        capacity=capacity,
        max_capacity=capacity,
        total_weeks=(
            total_weeks
            or len(curriculum_weeks(db, program.id))
            or settings.default_program_weeks
            or 12
        ),
        days_of_week=format_days(days),
        start_time=start_time,
        end_time=end_time,
        start_mode=START_FIXED_DATE if start_date else (settings.class_start_mode or START_AFTER_DAYS),
        start_delay_days=settings.default_class_start_days if settings.default_class_start_days is not None else 10,
        min_students_to_start=settings.min_students_to_start or 5,
        min_start_notice_days=settings.min_start_notice_days if settings.min_start_notice_days is not None else 3,
        reminder_days=format_reminder_days(parse_reminder_days(settings.training_reminder_days)),
        auto_created=auto_created,
        status=ClassStatus.NOT_STARTED,
        created_at=datetime.utcnow(),
    )
    if class_group.start_mode not in START_MODES or (
        class_group.start_mode == START_FIXED_DATE and not start_date
    ):
        class_group.start_mode = START_AFTER_DAYS

    if start_date:
        class_group.start_date = datetime.combine(start_date, start_time or time(0, 0))
    elif class_group.start_mode == START_AFTER_DAYS:
        class_group.start_date = planned_start_after_days(class_group, now)

    if professor is None and (settings.auto_assign_professor if pick_professor is None else pick_professor):
        professor = find_available_professor(
            db, program, days, start_time, end_time,
            class_group.start_date.date() if class_group.start_date else None,
        )
    if professor:
        class_group.professor_id = professor.id
        class_group.professor_name = professor.name

    class_group.name = generate_unique_class_name(db, program.name, class_group.start_date or now)
    db.add(class_group)
    db.flush()
    seed_class_sessions(db, class_group)
    refresh_class_status(db, class_group, now, announce=False)
    db.flush()
    return class_group


# ------------------------------------------------------------------
# Reminders
# ------------------------------------------------------------------

def plan_reminders(db: Session, registration: Registration, class_group: ClassGroup | None,
                   now: datetime | None = None) -> None:
    """Keep the registration's reminders in line with the class start and its reminder days."""
    now = now or local_now()
    anchor = reminder_anchor(class_group) if class_group else None
    offsets = parse_reminder_days(class_group.reminder_days) if class_group else []
    wanted = {f"training_{days}d": days for days in offsets} if anchor else {}
    db.flush()

    existing = {
        reminder.reminder_key: reminder
        for reminder in db.query(CandidateReminder).filter(
            CandidateReminder.registration_id == registration.id,
            CandidateReminder.reminder_type == "training",
        )
    }
    for key, reminder in existing.items():
        if key not in wanted and reminder.status != "sent":
            db.delete(reminder)

    for key, days in wanted.items():
        planned_for = anchor - timedelta(days=days)
        status = "planned" if planned_for > now else "skipped"
        strategy = (
            f"Reminder {days} day(s) before the first session of {class_group.name} "
            f"({anchor:%d/%m/%Y %H:%M})."
        )
        reminder = existing.get(key)
        if reminder is None:
            db.add(CandidateReminder(
                candidate_id=registration.candidate_id,
                registration_id=registration.id,
                reminder_key=key,
                reminder_type="training",
                planned_for=planned_for,
                status=status,
                strategy=strategy,
            ))
        elif reminder.planned_for != planned_for:
            # The first session moved. A reminder that already went out is
            # only planned again when its new date is still ahead.
            if reminder.status == "sent" and planned_for <= now:
                continue
            reminder.planned_for = planned_for
            reminder.status = status
            reminder.sent_at = None
            reminder.attempts = 0
            reminder.last_error = None
            reminder.strategy = strategy
        elif reminder.status != "sent" and reminder.strategy != strategy:
            reminder.strategy = strategy


def sync_members(db: Session, class_group: ClassGroup, now: datetime | None = None) -> None:
    """Propagate the class dates to its registrations and their reminders."""
    start, end = class_start_dt(class_group), class_end_dt(class_group)
    db.flush()
    for registration in class_members(db, class_group.id):
        registration.training_start_date = start
        registration.training_end_date = end
        plan_reminders(db, registration, class_group, now)


def dispatch_due_reminders(db: Session, now: datetime | None = None) -> int:
    """Send every reminder whose time has come. Returns the number sent."""
    settings = get_settings(db)
    if not settings.enable_automated_reminders:
        return 0
    now = now or local_now()
    due = (
        db.query(CandidateReminder)
        .filter(
            CandidateReminder.reminder_type == "training",
            CandidateReminder.status == "planned",
            CandidateReminder.planned_for <= now,
        )
        .order_by(CandidateReminder.planned_for.asc())
        .all()
    )
    if not due:
        return 0

    by_registration: dict[str, list[CandidateReminder]] = {}
    for reminder in due:
        by_registration.setdefault(reminder.registration_id, []).append(reminder)

    sent_total = 0
    sent_by_class: dict[str, tuple[ClassGroup, int]] = {}
    mail_ready = class_emails.smtp_configured()
    reminders_on = email_templates.is_on(db, "training_reminder")
    for registration_id, reminders in by_registration.items():
        registration = db.get(Registration, registration_id)
        class_group = registration.class_group if registration else None
        start = class_start_dt(class_group) if class_group else None
        still_relevant = (
            registration is not None
            and registration.status == RegistrationStatus.ASSIGNED
            and registration.payment_status == PaymentStatus.PAID
            and start is not None and start > now
        )
        if not still_relevant or not reminders_on:
            # Also when the admin switched this automatic email off in Email templates.
            for reminder in reminders:
                reminder.status = "skipped"
            continue

        # After downtime several reminders can be due at once: only the one
        # closest to the first session is worth sending.
        current = reminders[-1]
        for reminder in reminders[:-1]:
            reminder.status = "skipped"
        if not mail_ready:
            # Stay planned so the reminder goes out as soon as email works.
            continue

        days_before = int(current.reminder_key.removeprefix("training_").removesuffix("d") or 0)
        delivered = class_emails.send_training_reminder(db, registration, days_before)
        current.attempts = (current.attempts or 0) + 1
        if delivered:
            current.status = "sent"
            current.sent_at = now
            current.last_error = None
            sent_total += 1
            _, count = sent_by_class.get(class_group.id, (class_group, 0))
            sent_by_class[class_group.id] = (class_group, count + 1)
        else:
            current.last_error = "Email delivery failed"
            if current.attempts >= MAX_REMINDER_ATTEMPTS:
                current.status = "failed"
        db.commit()

    if not mail_ready:
        class_emails.flag_smtp_not_configured(db)
    for class_group, count in sent_by_class.values():
        _notify(
            db, category="REMINDER", title="Session reminders sent",
            message=f"{count} candidate(s) of {class_group.name} were reminded that the first session is {_when(class_group)}.",
            action_url=class_url(class_group), class_group=class_group,
        )
    db.commit()
    return sent_total


def send_reminder_now(db: Session, registration: Registration) -> bool:
    """Admin-triggered reminder for one candidate, outside the automatic plan."""
    delivered = class_emails.send_training_reminder(db, registration, None)
    db.commit()
    return delivered


# ------------------------------------------------------------------
# Status and start rules
# ------------------------------------------------------------------

def refresh_class_status(db: Session, class_group: ClassGroup, now: datetime | None = None,
                         announce: bool = True) -> ClassStatus | None:
    """Derive status and current week from the class dates. Returns the new status if it changed.

    The dates are not enough: a class runs only with somebody in it, and it
    starts only with a professor of its speciality. Until then it waits, and
    starts by itself once this is solved.
    """
    now = now or local_now()
    start, end = class_start_dt(class_group), class_end_dt(class_group)
    total_weeks = class_group.total_weeks or 12
    if not start or now < start:
        target, week = ClassStatus.NOT_STARTED, 0
    elif end and now > end:
        target, week = ClassStatus.COMPLETED, total_weeks
    else:
        target = ClassStatus.ONGOING
        week = min(total_weeks, (now.date() - start.date()).days // 7 + 1)

    previous = class_group.status
    members = class_members(db, class_group.id) if class_group.id else []
    held: list[str] = []
    if target == ClassStatus.ONGOING and not members:
        held = hold_reasons(class_group, 0)
    elif target != ClassStatus.NOT_STARTED and previous == ClassStatus.NOT_STARTED:
        # Not running yet: it does not begin (nor end without having begun) while something is missing.
        held = hold_reasons(class_group, len(members))
    if held:
        target, week = ClassStatus.NOT_STARTED, 0

    if class_group.current_week != week:
        class_group.current_week = week
    if previous == target:
        if held and announce:
            _tell_once(
                db, class_group, title="Class cannot start",
                message=f"{class_group.name} was due to start {_when(class_group)} but waits: {_listed(held)}. "
                        "It starts by itself once this is solved.",
                about=_listed(held),
            )
        return None
    class_group.status = target
    db.flush()

    if target == ClassStatus.COMPLETED:
        for registration in members:
            if registration.status == RegistrationStatus.ASSIGNED:
                registration.status = RegistrationStatus.COMPLETED
                registration.training_end_date = end
    elif previous == ClassStatus.COMPLETED:
        for registration in members:
            if registration.status == RegistrationStatus.COMPLETED:
                registration.status = RegistrationStatus.ASSIGNED

    if announce and target == ClassStatus.COMPLETED:
        # The class has just finished: its candidates will be asked what they thought.
        from app.services import program_feedback

        program_feedback.queue_class(db, class_group, members)

    if announce:
        if target == ClassStatus.ONGOING:
            missing = "" if class_group.professor_name else " No professor is assigned yet."
            title, message = "Class started", f"{class_group.name} is now ongoing with {len(members)} candidate(s).{missing}"
        elif target == ClassStatus.COMPLETED:
            title, message = "Class completed", f"{class_group.name} has finished. {len(members)} registration(s) marked completed."
        elif held:
            title, message = "Class on hold", (
                f"{class_group.name} no longer counts as running: {_listed(held)}. It starts by itself once this is solved."
            )
        else:
            title, message = "Class not started", f"{class_group.name} is back to not started: first session {_when(class_group)}."
        _notify(db, category="CLASS", title=title, message=message,
                action_url=class_url(class_group), class_group=class_group)
    return target


def schedule_by_enrollment(db: Session, class_group: ClassGroup, now: datetime | None = None) -> bool:
    """Fix the start date of a 'minimum enrollment' class once enough candidates joined."""
    if class_group.start_mode != START_MIN_ENROLLMENT or class_group.start_date is not None:
        return False
    db.flush()
    enrolled = enrolled_count(db, class_group.id)
    if enrolled < max(1, class_group.min_students_to_start or 1):
        return False

    now = now or local_now()
    days = parse_days(class_group.days_of_week)
    target = (now + timedelta(days=class_group.min_start_notice_days or 0)).date()
    start = datetime.combine(first_session_on_or_after(target, days), class_group.start_time or time(0, 0))
    if start <= now:
        start = datetime.combine(
            first_session_on_or_after(target + timedelta(days=1), days),
            class_group.start_time or time(0, 0),
        )
    class_group.start_date = start
    reschedule_sessions(db, class_group)
    sync_members(db, class_group, now)
    _notify(
        db, category="CLASS", title="Class scheduled",
        message=f"{class_group.name} reached {enrolled} candidates and will start {_when(class_group)}.",
        action_url=class_url(class_group), class_group=class_group,
    )
    return True


def sync_class_lifecycle(db: Session) -> int:
    """Apply automatic status changes and enrollment-based scheduling. Returns classes changed."""
    changed = 0
    newly_scheduled: list[str] = []
    with _lifecycle_lock:
        now = local_now()
        for class_group in db.query(ClassGroup).populate_existing().all():
            if schedule_by_enrollment(db, class_group, now):
                newly_scheduled.append(class_group.id)
                changed += 1
            if refresh_class_status(db, class_group, now) is not None:
                changed += 1
        db.commit()
    for class_id in newly_scheduled:
        for registration in class_members(db, class_id):
            run_in_background(class_emails.send_class_update_email_task, registration.id, "scheduled")
    return changed


def start_class_now(db: Session, class_group: ClassGroup) -> None:
    blockers = start_blockers(db, class_group)
    if blockers:
        raise ValueError(f"{class_group.name} cannot start: {_listed(blockers)}.")
    now = local_now()
    class_group.start_mode = START_FIXED_DATE
    class_group.start_date = now
    class_group.end_date = None
    reschedule_sessions(db, class_group)
    sync_members(db, class_group, now)
    refresh_class_status(db, class_group, now)
    db.commit()


def complete_class(db: Session, class_group: ClassGroup) -> None:
    now = local_now()
    class_group.end_date = now - timedelta(seconds=1)
    refresh_class_status(db, class_group, now)
    db.commit()


def reopen_class(db: Session, class_group: ClassGroup) -> None:
    class_group.end_date = None
    refresh_class_status(db, class_group)
    db.commit()


def delete_class(db: Session, class_group: ClassGroup) -> None:
    if enrolled_count(db, class_group.id):
        raise ValueError("Move or remove the enrolled candidates before deleting this class.")
    for registration in db.query(Registration).filter(Registration.class_group_id == class_group.id):
        registration.class_group_id = None
    db.delete(class_group)
    db.commit()


# ------------------------------------------------------------------
# Enrollment
# ------------------------------------------------------------------

def find_open_class(db: Session, registration: Registration, now: datetime | None = None,
                    starts_from: date | None = None) -> ClassGroup | None:
    """A class of the same program that has not started and still has a seat (starting on `starts_from` or later)."""
    now = now or local_now()
    options = []
    for class_group in db.query(ClassGroup).filter(
        ClassGroup.program_id == registration.program_id,
        ClassGroup.status == ClassStatus.NOT_STARTED,
    ):
        start = class_start_dt(class_group)
        if start is not None and start <= now:
            continue
        if starts_from and (start is None or start.date() < starts_from):
            continue
        enrolled = enrolled_count(db, class_group.id)
        if enrolled >= class_capacity(class_group):
            continue
        # Soonest dated class first; among equals, the fullest one.
        options.append((start is None, start or datetime.max, -enrolled, class_group.name, class_group))
    if not options:
        return None
    options.sort(key=lambda item: item[:4])
    return options[0][4]


def _attach(db: Session, registration: Registration, class_group: ClassGroup, now: datetime) -> None:
    registration.class_group_id = class_group.id
    registration.class_group = class_group
    registration.status = RegistrationStatus.ASSIGNED
    registration.training_start_date = class_start_dt(class_group)
    registration.training_end_date = class_end_dt(class_group)
    db.flush()
    plan_reminders(db, registration, class_group, now)


def _schedule_conflict_note(db: Session, registration: Registration, class_group: ClassGroup) -> str:
    from app.services.class_service import check_candidate_schedule_conflict

    if check_candidate_schedule_conflict(db, registration.candidate_id, class_group):
        return " Note: this candidate has another class on the same time slot."
    return ""


def gap_days(settings) -> int:
    value = getattr(settings, "next_class_gap_days", None)
    return 10 if value is None else max(0, int(value))


def not_before(db: Session, registration: Registration) -> date | None:
    """The first day the candidate's next class may start: some days after the end of their latest class (None when it
    ended long ago, or they never had one)."""
    gap = gap_days(get_settings(db))
    latest = None
    for other in (db.query(Registration).filter(Registration.candidate_id == registration.candidate_id, Registration.id != registration.id,
                                                Registration.class_group_id.is_not(None)).all()):
        group = other.class_group
        ends = class_end_dt(group) if group is not None else None
        if ends is not None and (latest is None or ends.date() > latest):
            latest = ends.date()
    if latest is None:
        return None
    first = latest + timedelta(days=gap)
    return first if first > local_now().date() else None


def class_in_the_way(db: Session, registration: Registration, days: list[int] | None = None,
                     start_time: time | None = None, end_time: time | None = None) -> ClassGroup | None:
    """The class the candidate already follows (running, or about to start), if any: one program at a time,
    whatever the hours. The next one starts in a class some days after it ends (Settings, Classes)."""
    others = (
        db.query(Registration)
        .filter(Registration.candidate_id == registration.candidate_id, Registration.id != registration.id,
                Registration.class_group_id.is_not(None), Registration.status == RegistrationStatus.ASSIGNED)
        .all()
    )
    for other in others:
        current = other.class_group
        if current is None or current.status == ClassStatus.COMPLETED:
            continue
        return current
    return None


def ask_to_wait_task(registration_id: str, current_class_id: str) -> None:
    """Background: tell a candidate their new program starts after their current class, and ask if that suits them."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        registration = db.get(Registration, registration_id)
        current = db.get(ClassGroup, current_class_id)
        if registration is None or current is None or registration.candidate is None:
            return
        ends = class_end_dt(current)
        months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
        email_templates.send(
            db, "next_program_wait", registration=registration, manual=True,
            values={
                "current_program": current.program.name if current.program else current.name,
                "current_end": f"jusqu'au {ends.day} {months[ends.month - 1]}" if ends else "en ce moment",
                "gap_days": str(gap_days(get_settings(db))),
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("The candidate of registration %s could not be asked to wait.", registration_id)
    finally:
        db.close()


def enroll_paid_registration(db: Session, registration: Registration, *,
                             notify_candidate: bool = True) -> EnrollmentResult:
    """Place a fully paid registration in a class, creating the class when needed."""
    others_to_notify: list[str] = []
    with _lifecycle_lock:
        db.refresh(registration)
        if registration.class_group_id:
            return EnrollmentResult(registration, registration.class_group, reason="already_assigned")
        if registration.payment_status != PaymentStatus.PAID:
            return EnrollmentResult(registration, reason="not_paid")
        if registration.status in (RegistrationStatus.CANCELLED, RegistrationStatus.COMPLETED):
            return EnrollmentResult(registration, reason="closed")

        waiting = (
            db.query(Notification.id)
            .filter(Notification.registration_id == registration.id, Notification.category == "ASSIGNMENT_PENDING")
            .first()
        )
        if not registration.program_id:
            if not waiting:
                _notify(
                    db, category="ASSIGNMENT_PENDING", title="Paid registration needs a program",
                    message=f"{_who(registration)} paid for '{registration.program_name}', which is not linked to a program yet. Link it to assign a class.",
                    action_url=f"/candidate/{registration.candidate_id}", registration=registration,
                )
                db.commit()
            return EnrollmentResult(registration, reason="no_program")

        now = local_now()
        settings = get_settings(db)
        after = not_before(db, registration)        # their last class ended a few days ago: the next starts some days after
        class_group = find_open_class(db, registration, now, starts_from=after)
        created = False
        # Already in a class on the same hours: this program starts when that one ends.
        if class_group is not None:
            slot = (parse_days(class_group.days_of_week), class_group.start_time, class_group.end_time)
        else:
            slot = default_schedule(db, db.get(Program, registration.program_id), settings)
        current = class_in_the_way(db, registration, *slot)
        if current is not None:
            if not waiting:
                ends = class_end_dt(current)
                _notify(
                    db, category="ASSIGNMENT_PENDING", title="Waits for their current class to end",
                    message=(
                        f"{_who(registration)} paid for {registration.program_name} but already follows {current.name}"
                        f"{' until ' + format_date_en(ends) if ends else ''}. One program at a time: they are placed in a class "
                        f"of this program starting about {gap_days(settings)} days after it ends, with a professor free then. "
                        "They were told by email."
                    ),
                    action_url="/classes/?tab=decisions", registration=registration,
                )
                db.commit()
                run_in_background(ask_to_wait_task, registration.id, current.id)
            return EnrollmentResult(registration, reason="busy")
        if class_group is None:
            if not settings.auto_create_class_on_payment:
                if not waiting:
                    _notify(
                        db, category="ASSIGNMENT_PENDING", title="Paid registration awaiting a class",
                        message=f"{_who(registration)} paid for {registration.program_name} but no open class exists and automatic class creation is off.",
                        action_url="/classes/?tab=decisions", registration=registration,
                    )
                    db.commit()
                return EnrollmentResult(registration, reason="no_open_class")
            program = db.get(Program, registration.program_id)
            first_day = first_session_on_or_after(after, default_schedule(db, program, settings)[0]) if after else None
            class_group = create_class(db, program, settings=settings, auto_created=True, start_date=first_day)
            created = True

        _attach(db, registration, class_group, now)
        if schedule_by_enrollment(db, class_group, now):
            others_to_notify = [
                member.id for member in class_members(db, class_group.id)
                if member.id != registration.id
            ]

        db.add(Activity(
            candidate_id=registration.candidate_id,
            event_type="CLASS_ASSIGNED",
            details=f"Enrolled in {class_group.name} ({registration.program_name}). First session: {_when(class_group)}.",
        ))
        professor = class_group.professor_name or "no professor available yet"
        if created:
            title = "Payment confirmed · new class created"
            message = (
                f"{_who(registration)} paid for {registration.program_name}. No open class existed, so "
                f"{class_group.name} was created (professor: {professor}). First session: {_when(class_group)}."
            )
        else:
            title = "Payment confirmed · candidate enrolled"
            message = (
                f"{_who(registration)} paid for {registration.program_name} and joined {class_group.name} "
                f"({enrolled_count(db, class_group.id)}/{class_capacity(class_group)}). First session: {_when(class_group)}."
            )
        _notify(
            db, category="ASSIGNMENT", title=title,
            message=message + _schedule_conflict_note(db, registration, class_group),
            action_url=class_url(class_group, registration), registration=registration, class_group=class_group,
        )
        db.commit()
        db.refresh(registration)

    if notify_candidate:
        run_in_background(class_emails.send_enrollment_email_task, registration.id)
    for registration_id in others_to_notify:
        run_in_background(class_emails.send_class_update_email_task, registration_id, "scheduled")
    return EnrollmentResult(registration, class_group, created_class=created)


def assign_pending_paid_registrations(db: Session) -> int:
    """Safety net: enroll paid registrations that are still without a class."""
    pending = (
        db.query(Registration)
        .filter(
            Registration.payment_status == PaymentStatus.PAID,
            Registration.class_group_id.is_(None),
            Registration.program_id.is_not(None),
            Registration.status == RegistrationStatus.PENDING,
        )
        .all()
    )
    assigned = 0
    for registration in pending:
        try:
            if enroll_paid_registration(db, registration).class_group:
                assigned += 1
        except Exception:
            db.rollback()
            logger.exception("Could not enroll paid registration %s.", registration.id)
    return assigned


def move_registration(db: Session, registration_id: str, target_class_id: str, *,
                      notify_candidate: bool = True) -> Registration:
    """Assign a paid registration to a specific class, or move it from its current one."""
    others_to_notify: list[str] = []
    with _lifecycle_lock:
        registration = db.get(Registration, registration_id)
        if not registration:
            raise ValueError("Registration not found.")
        target = db.get(ClassGroup, target_class_id)
        if not target:
            raise ValueError("Class not found.")
        if registration.class_group_id == target.id:
            return registration
        if registration.payment_status != PaymentStatus.PAID:
            raise ValueError("A registration must be fully paid before class assignment.")
        if target.status == ClassStatus.COMPLETED:
            raise ValueError("Completed classes cannot accept candidates.")
        if registration.program_id and target.program_id and target.program_id != registration.program_id:
            raise ValueError(f"{target.name} belongs to another program.")
        occupied = enrolled_count(db, target.id, exclude_registration_id=registration.id)
        if occupied >= class_capacity(target):
            raise ValueError(f"{target.name} is full ({occupied}/{class_capacity(target)}).")

        now = local_now()
        previous = registration.class_group
        _attach(db, registration, target, now)
        if schedule_by_enrollment(db, target, now):
            others_to_notify = [member.id for member in class_members(db, target.id) if member.id != registration.id]

        if previous:
            details = f"Moved from {previous.name} to {target.name}. First session: {_when(target)}."
            title = "Candidate moved to another class"
            message = f"{_who(registration)} moved from {previous.name} to {target.name}. First session: {_when(target)}."
        else:
            details = f"Assigned to {target.name}. First session: {_when(target)}."
            title = "Candidate assigned to a class"
            message = f"{_who(registration)} was assigned to {target.name}. First session: {_when(target)}."
        db.add(Activity(candidate_id=registration.candidate_id, event_type="CLASS_CHANGED", details=details))
        _notify(
            db, category="ASSIGNMENT", title=title,
            message=message + _schedule_conflict_note(db, registration, target),
            action_url=class_url(target, registration), registration=registration, class_group=target,
        )
        db.commit()
        db.refresh(registration)

    if notify_candidate:
        if previous:
            run_in_background(class_emails.send_class_update_email_task, registration.id, "moved")
        else:
            run_in_background(class_emails.send_enrollment_email_task, registration.id)
    for other_id in others_to_notify:
        run_in_background(class_emails.send_class_update_email_task, other_id, "scheduled")
    return registration


def unassign_registration(db: Session, registration: Registration) -> None:
    """Take a registration out of its class; it returns to the pending decisions list."""
    class_group = registration.class_group
    registration.class_group_id = None
    registration.class_group = None
    registration.status = RegistrationStatus.PENDING
    registration.training_start_date = None
    registration.training_end_date = None
    plan_reminders(db, registration, None)
    if class_group:
        db.add(Activity(
            candidate_id=registration.candidate_id,
            event_type="CLASS_REMOVED",
            details=f"Removed from {class_group.name}.",
        ))
        _notify(
            db, category="ASSIGNMENT", title="Candidate removed from class",
            message=f"{_who(registration)} was removed from {class_group.name} and is waiting for a class.",
            action_url="/classes/?tab=decisions", registration=registration, class_group=class_group,
        )
    db.commit()


# ------------------------------------------------------------------
# Class parameters
# ------------------------------------------------------------------

def update_class_parameters(db: Session, class_group: ClassGroup, *, name: str | None,
                            professor_id: str | None, capacity: int, total_weeks: int,
                            start_mode: str, start_delay_days: int, min_students_to_start: int,
                            min_start_notice_days: int, fixed_start_date: date | None,
                            days_of_week: str | list | None, start_time: time | None,
                            end_time: time | None, room: str | None, reminder_days: str | None,
                            notify_members: bool = True) -> dict:
    """Apply the admin's parameters to a class and everything that depends on them."""
    if start_mode not in START_MODES:
        raise ValueError("Unknown start rule.")
    if capacity < 1:
        raise ValueError("Capacity must be at least 1.")
    enrolled = enrolled_count(db, class_group.id)
    if capacity < enrolled:
        raise ValueError(f"Capacity cannot be below the {enrolled} candidates already enrolled.")
    if not 1 <= total_weeks <= 104:
        raise ValueError("Duration must be between 1 and 104 weeks.")
    if not 0 <= start_delay_days <= 365:
        raise ValueError("Start delay must be between 0 and 365 days.")
    if min_students_to_start < 1:
        raise ValueError("Minimum candidates to start must be at least 1.")
    if min_students_to_start > capacity:
        raise ValueError("Minimum candidates to start cannot exceed the capacity.")
    if not 0 <= min_start_notice_days <= 60:
        raise ValueError("Notice must be between 0 and 60 days.")
    if start_time and end_time and end_time <= start_time:
        raise ValueError("Session end time must be after its start time.")
    if start_mode == START_FIXED_DATE and not fixed_start_date:
        raise ValueError("Choose a start date for the fixed-date rule.")
    offsets = parse_reminder_days(reminder_days)
    clean_name = (name or "").strip()
    if clean_name and clean_name != class_group.name:
        if db.query(ClassGroup.id).filter(ClassGroup.name == clean_name, ClassGroup.id != class_group.id).first():
            raise ValueError(f"Another class is already named {clean_name}.")

    with _lifecycle_lock:
        now = local_now()

        def schedule_snapshot():
            start = class_start_dt(class_group)
            return (start.date() if start else None, class_group.days_of_week,
                    class_group.start_time, class_group.end_time)

        before = schedule_snapshot()
        previous_mode = class_group.start_mode
        not_started = class_group.status == ClassStatus.NOT_STARTED

        if clean_name:
            class_group.name = clean_name
        professor = db.get(Professor, professor_id) if professor_id else None
        if professor and professor.id != class_group.professor_id and not teaches(professor, class_group.program):
            program_name = class_group.program.name if class_group.program else "this program"
            raise ValueError(
                f"{professor.name} does not have {program_name} among their specialities. "
                "Add it to their profile first, or choose another professor."
            )
        if professor is not None:
            first = fixed_start_date if start_mode == START_FIXED_DATE and fixed_start_date else (before[0] if before[0] else None)
            other = professor_clash(db, professor.id, parse_days(days_of_week), start_time, end_time, first, total_weeks, exclude_id=class_group.id)
            if other is not None:
                raise ValueError(clash_message(professor, other))
        newly_given = professor is not None and professor.id != class_group.professor_id
        class_group.professor_id = professor.id if professor else None
        class_group.professor_name = professor.name if professor else None
        class_group.capacity = class_group.max_capacity = capacity
        class_group.total_weeks = total_weeks
        class_group.start_mode = start_mode
        class_group.start_delay_days = start_delay_days
        class_group.min_students_to_start = min_students_to_start
        class_group.min_start_notice_days = min_start_notice_days
        class_group.days_of_week = format_days(parse_days(days_of_week))
        class_group.start_time = start_time
        class_group.end_time = end_time
        class_group.room = (room or "").strip() or None
        class_group.reminder_days = format_reminder_days(offsets)

        session_clock = start_time or time(0, 0)
        if start_mode == START_FIXED_DATE:
            if before[0] != fixed_start_date or before[2] != start_time:
                class_group.start_date = datetime.combine(fixed_start_date, session_clock)
        elif start_mode == START_AFTER_DAYS and not_started:
            class_group.start_date = planned_start_after_days(class_group, now)
        elif start_mode == START_MIN_ENROLLMENT and not_started and previous_mode != START_MIN_ENROLLMENT:
            # Wait for the minimum again; scheduled right below if it is already met.
            class_group.start_date = None
        elif class_group.start_date and before[2] != start_time and not_started:
            class_group.start_date = datetime.combine(class_group.start_date.date(), session_clock)

        scheduled_now = schedule_by_enrollment(db, class_group, now)
        if class_group.start_date:
            reschedule_sessions(db, class_group)
        sync_members(db, class_group, now)
        refresh_class_status(db, class_group, now)

        after = schedule_snapshot()
        start_changed = before[0] != after[0]
        schedule_changed = before[1:] != after[1:]
        if start_changed and not scheduled_now:
            _notify(
                db, category="CLASS", title="Class schedule updated",
                message=f"{class_group.name}: first session is now {_when(class_group)}.",
                action_url=class_url(class_group), class_group=class_group,
            )
        db.commit()
        member_ids = [member.id for member in class_members(db, class_group.id)]

    emails = 0
    if notify_members and (start_changed or schedule_changed) and class_group.status != ClassStatus.COMPLETED:
        reason = "scheduled" if before[0] is None and after[0] is not None else "rescheduled"
        for registration_id in member_ids:
            run_in_background(class_emails.send_class_update_email_task, registration_id, reason)
            emails += 1
    return {"start_changed": start_changed, "schedule_changed": schedule_changed, "emails": emails}


def reconcile_all(db: Session) -> None:
    """Bring classes, registrations and reminders in line. Run once at startup."""
    sync_class_lifecycle(db)
    now = local_now()
    for class_group in db.query(ClassGroup).filter(ClassGroup.status == ClassStatus.NOT_STARTED):
        sync_members(db, class_group, now)
    # Reminders left over for classes that already began will never be relevant.
    stale = (
        db.query(CandidateReminder)
        .join(Registration, Registration.id == CandidateReminder.registration_id)
        .outerjoin(ClassGroup, ClassGroup.id == Registration.class_group_id)
        .filter(
            CandidateReminder.reminder_type == "training",
            CandidateReminder.status == "planned",
            (ClassGroup.id.is_(None)) | (ClassGroup.status != ClassStatus.NOT_STARTED),
        )
        .all()
    )
    for reminder in stale:
        reminder.status = "skipped"
    db.commit()
    assign_pending_paid_registrations(db)


def fill_session_times(db: Session) -> int:
    """A class without days or hours takes them from its program ("Saturday from 9:00 until 13:00"),
    and so do its sessions still to come. Returns how many classes were completed."""
    from app.models.classsession import ClassSession
    from app.services import schedule_utils

    done = 0
    for class_group in db.query(ClassGroup).filter(ClassGroup.start_time.is_(None), ClassGroup.status != ClassStatus.COMPLETED).all():
        program = class_group.program
        days, start, end = schedule_utils.parse_session_time_text(getattr(program, "class_session_time", None))
        if start is None:
            continue
        class_group.start_time, class_group.end_time = start, end or class_group.end_time
        if days and not class_group.days_of_week:
            class_group.days_of_week = schedule_utils.format_days(days)
        for session in db.query(ClassSession).filter(ClassSession.class_group_id == class_group.id, ClassSession.start_time.is_(None)).all():
            session.start_time, session.end_time = start, end or session.end_time
        done += 1
    if done:
        db.commit()
    return done

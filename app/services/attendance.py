"""Presence of candidates at the sessions of a class.

The professor (or the admin) opens a session and marks each candidate
present, late or absent. From these marks come each candidate's attendance
rate and the "missed two sessions in a row" signal.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.classgroup import ClassGroup
from app.models.classsession import ClassSession
from app.models.registration import Registration, RegistrationStatus
from app.models.user import Attendance
from app.services.schedule_utils import local_now

STATUSES = ("present", "late", "absent")
LABELS = {"present": "Present", "late": "Late", "absent": "Absent"}


def members(db: Session, class_id: str) -> list[Registration]:
    rows = (
        db.query(Registration)
        .filter(Registration.class_group_id == class_id, Registration.status != RegistrationStatus.CANCELLED)
        .all()
    )
    rows.sort(key=lambda item: ((item.candidate.full_name or item.candidate.email or "") if item.candidate else "").lower())
    return rows


def sessions(db: Session, class_id: str) -> list[ClassSession]:
    return (
        db.query(ClassSession)
        .filter(ClassSession.class_group_id == class_id)
        .order_by(ClassSession.session_date.asc(), ClassSession.week_number.asc())
        .all()
    )


def default_session(all_sessions: list[ClassSession]) -> ClassSession | None:
    """The session to open first: today's, else the last one already held, else the first."""
    today = local_now().date()
    held = [session for session in all_sessions if session.session_date <= today]
    return held[-1] if held else (all_sessions[0] if all_sessions else None)


def add_session_today(db: Session, class_group: ClassGroup) -> ClassSession:
    """A session for today, for a class that has no weekly plan to pick one from."""
    today = local_now().date()
    existing = db.query(ClassSession).filter(
        ClassSession.class_group_id == class_group.id, ClassSession.session_date == today,
    ).first()
    if existing is not None:
        return existing
    session = ClassSession(
        class_group_id=class_group.id, week_number=max(1, class_group.current_week or 1), session_date=today,
        start_time=class_group.start_time, end_time=class_group.end_time, topic="Session",
    )
    db.add(session)
    db.commit()
    return session


def marks_of(db: Session, session_id: str) -> dict[str, Attendance]:
    """registration id -> the mark recorded for that session."""
    return {row.registration_id: row for row in db.query(Attendance).filter(Attendance.session_id == session_id).all()}


def save(db: Session, class_group: ClassGroup, session: ClassSession, marks: dict[str, str],
         notes: dict[str, str] | None = None, marked_by: str = "", observations: dict[str, dict] | None = None) -> dict:
    """Record the presence of a session. Returns {"marked": how many, "absent": how many of them}."""
    existing = marks_of(db, session.id)
    allowed = {item.id: item for item in members(db, class_group.id)}
    now = datetime.now(timezone.utc)
    count = absent = 0
    for registration_id, status in marks.items():
        registration = allowed.get(registration_id)
        if registration is None or status not in STATUSES:
            continue
        note = " ".join(((notes or {}).get(registration_id) or "").split())[:200] or None
        seen = (observations or {}).get(registration_id) or {}
        row = existing.get(registration_id)
        if row is None:
            row = Attendance(
                class_group_id=class_group.id, session_id=session.id, registration_id=registration_id,
                candidate_id=registration.candidate_id, status=status, note=note, marked_by=marked_by, marked_at=now,
            )
            db.add(row)
        else:
            row.status, row.note, row.marked_by, row.marked_at = status, note, marked_by, now
        if observations is not None:        # what the professor noted: nothing to note of somebody absent
            row.participation = seen.get("participation") if status != "absent" else None
            row.questions = seen.get("questions") if status != "absent" else None
        count += 1
        absent += status == "absent"
    db.commit()
    return {"marked": count, "absent": absent}


def _rate(present: int, late: int, absent: int) -> int | None:
    total = present + late + absent
    return round((present + late) / total * 100) if total else None


def class_summary(db: Session, class_group: ClassGroup) -> dict:
    """Presence over the whole class: per candidate, per session, and overall."""
    rows = db.query(Attendance).filter(Attendance.class_group_id == class_group.id).all()
    all_sessions = sessions(db, class_group.id)
    order = {session.id: index for index, session in enumerate(all_sessions)}
    by_registration: dict[str, list[Attendance]] = {}
    by_session: dict[str, dict[str, int]] = {}
    for row in rows:
        by_registration.setdefault(row.registration_id, []).append(row)
        counts = by_session.setdefault(row.session_id, {status: 0 for status in STATUSES})
        counts[row.status] = counts.get(row.status, 0) + 1

    people = []
    for registration in members(db, class_group.id):
        marks = sorted(by_registration.get(registration.id, []), key=lambda row: order.get(row.session_id, 0))
        counts = {status: sum(1 for row in marks if row.status == status) for status in STATUSES}
        streak = 0
        for row in reversed(marks):     # absences in a row, counted from the latest session
            if row.status != "absent":
                break
            streak += 1
        people.append({
            "registration": registration,
            "candidate": registration.candidate,
            "name": (registration.candidate.full_name or registration.candidate.email) if registration.candidate else "Candidate",
            **counts,
            "marked": len(marks),
            "rate": _rate(**counts),
            "absent_in_a_row": streak,
        })
    marked_sessions = [session for session in all_sessions if session.id in by_session]
    totals = {status: sum(counts[status] for counts in by_session.values()) for status in STATUSES}
    return {
        "people": people,
        "sessions_marked": len(marked_sessions),
        "by_session": by_session,
        "rate": _rate(**totals),
        "last_marked": marked_sessions[-1] if marked_sessions else None,
    }


def candidate_summary(db: Session, candidate_id: str) -> dict:
    """A candidate's presence across their classes."""
    rows = db.query(Attendance).filter(Attendance.candidate_id == candidate_id).all()
    counts = {status: sum(1 for row in rows if row.status == status) for status in STATUSES}
    return {**counts, "marked": len(rows), "rate": _rate(**counts)}

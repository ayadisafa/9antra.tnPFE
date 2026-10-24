"""How a class lives: who comes, how much they take part, whether they learn.

Everything is counted from what was recorded, never guessed:

* the presence sheet of each session (present, late, absent), and what the
  professor noted there: participation (quiet, takes part, very active) and the
  number of questions each candidate asked;
* the weekly questions: sent, answered, and their results;
* the opinions given at the end of the program.

From these come a few figures (each shown with its rule), the sessions and weeks
one after the other, the candidates who lead and those who need a word, and what
to do next. The class page shows them at its top; its PDF prints them.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.classgroup import ClassGroup
from app.models.classsession import ClassSession
from app.models.quiz import Quiz
from app.models.user import Attendance

PARTICIPATION_POINTS = {1: 30, 2: 65, 3: 100}       # quiet, takes part, very active (as in behaviour.py)
RECENT = 3                                          # "the last sessions": this many


def _rate(present: int, late: int, absent: int) -> int | None:
    total = present + late + absent
    return round((present + late) / total * 100) if total else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def of_class(db: Session, class_group: ClassGroup) -> dict:
    """{"figures", "sessions", "weeks", "people", "leaders", "to_watch", "advice", "has_data"} for one class."""
    from app.services import program_feedback
    from app.services.enrollment_service import class_members

    members = class_members(db, class_group.id)
    names = {item.id: ((item.candidate.full_name or item.candidate.email) if item.candidate else "Candidate") for item in members}
    marks = db.query(Attendance).filter(Attendance.class_group_id == class_group.id).all()
    sessions = {row.id: row for row in db.query(ClassSession).filter(ClassSession.class_group_id == class_group.id).all()}
    order = sorted({mark.session_id for mark in marks}, key=lambda key: (sessions[key].session_date, sessions[key].week_number or 0) if key in sessions else (None, 0))

    # ---- Sessions, one after the other ----
    session_rows = []
    for key in order:
        mine = [mark for mark in marks if mark.session_id == key]
        counts = {status: sum(1 for mark in mine if mark.status == status) for status in ("present", "late", "absent")}
        noted = [PARTICIPATION_POINTS[mark.participation] for mark in mine if mark.participation in PARTICIPATION_POINTS]
        asked = sum(mark.questions or 0 for mark in mine)
        session = sessions.get(key)
        session_rows.append({
            "date": session.session_date if session else None, "week": session.week_number if session else None,
            "topic": session.topic if session else None, **counts, "rate": _rate(**counts),
            "participation": _mean(noted), "questions": asked, "noted": len(noted),
        })

    # ---- Weeks of questions ----
    quizzes = db.query(Quiz).filter(Quiz.kind.in_(("weekly", "session")), Quiz.class_group_id == class_group.id, Quiz.sent_at.isnot(None)).all()
    week_rows = []
    for week in sorted({quiz.week_number for quiz in quizzes if quiz.week_number is not None}):
        mine = [quiz for quiz in quizzes if quiz.week_number == week]
        done = [quiz for quiz in mine if quiz.completed_at is not None]
        week_rows.append({"week": week, "sent": len(mine), "answered": len(done),
                          "answer_rate": round(len(done) / len(mine) * 100) if mine else None,
                          "score": _mean([quiz.score or 0 for quiz in done])})

    # ---- Each candidate ----
    people = []
    for registration in members:
        mine = sorted((mark for mark in marks if mark.registration_id == registration.id),
                      key=lambda mark: order.index(mark.session_id) if mark.session_id in order else 0)
        counts = {status: sum(1 for mark in mine if mark.status == status) for status in ("present", "late", "absent")}
        streak = 0
        for mark in reversed(mine):
            if mark.status != "absent":
                break
            streak += 1
        noted = [PARTICIPATION_POINTS[mark.participation] for mark in mine if mark.participation in PARTICIPATION_POINTS]
        answered = [quiz for quiz in quizzes if quiz.registration_id == registration.id and quiz.completed_at is not None]
        if not answered:
            answered = [quiz for quiz in quizzes if quiz.candidate_id == registration.candidate_id and quiz.completed_at is not None]
        sent = [quiz for quiz in quizzes if quiz.candidate_id == registration.candidate_id]
        people.append({
            "registration": registration, "name": names[registration.id], "rate": _rate(**counts), **counts,
            "absent_in_a_row": streak, "participation": _mean(noted), "questions": sum(mark.questions or 0 for mark in mine),
            "score": _mean([quiz.score or 0 for quiz in answered]), "answered": len(answered), "sent": len(sent),
        })

    # ---- The figures ----
    all_counts = {status: sum(row[status] for row in session_rows) for status in ("present", "late", "absent")}
    recent, before = session_rows[-RECENT:], session_rows[:-RECENT]
    recent_rate = _rate(**{status: sum(row[status] for row in recent) for status in ("present", "late", "absent")}) if recent else None
    before_rate = _rate(**{status: sum(row[status] for row in before) for status in ("present", "late", "absent")}) if before else None
    noted_points = [PARTICIPATION_POINTS[mark.participation] for mark in marks if mark.participation in PARTICIPATION_POINTS]
    attended = sum(1 for mark in marks if mark.status != "absent")
    questions_each = (sum(mark.questions or 0 for mark in marks) / attended) if attended else None
    participation = _mean(noted_points)
    # Interactivity: what the professor noted (70%), and questions asked per candidate and session (30%: 2 or more counts in full).
    interactivity = None
    if participation is not None:
        interactivity = round(0.7 * participation + 0.3 * min(100.0, (questions_each or 0) * 50))
    sent_total, done_total = len(quizzes), sum(1 for quiz in quizzes if quiz.completed_at is not None)
    scores = [quiz.score or 0 for quiz in quizzes if quiz.completed_at is not None]
    opinions = program_feedback.of_class(db, class_group.id)
    figures = {
        "attendance": _rate(**all_counts), "recent_attendance": recent_rate, "earlier_attendance": before_rate,
        "sessions_marked": len(session_rows), "participation": participation, "interactivity": interactivity,
        "questions_each": round(questions_each, 1) if questions_each is not None else None,
        "answer_rate": round(done_total / sent_total * 100) if sent_total else None, "answered": done_total, "sent": sent_total,
        "understanding": _mean(scores), "last_week_score": week_rows[-1]["score"] if week_rows else None,
        "opinions": opinions["answered"], "stars": opinions["stars"], "happy_share": opinions["happy_share"],
        "members": len(members),
    }

    leaders = sorted((person for person in people if person["participation"] is not None and person["participation"] >= 80),
                     key=lambda person: (-person["participation"], -person["questions"]))[:3]
    to_watch = []
    for person in people:
        if person["absent_in_a_row"] >= 2:
            to_watch.append({"name": person["name"], "why": f"absent from the last {person['absent_in_a_row']} sessions", "tone": "bad"})
        elif person["rate"] is not None and person["rate"] < 60 and (person["present"] + person["late"] + person["absent"]) >= 3:
            to_watch.append({"name": person["name"], "why": f"came to {person['rate']}% of the sessions", "tone": "warn"})
        if person["score"] is not None and person["score"] < 50 and person["answered"] >= 1:
            to_watch.append({"name": person["name"], "why": f"{round(person['score'])}% on average in the weekly questions", "tone": "warn"})
        elif person["sent"] >= 2 and person["answered"] == 0:
            to_watch.append({"name": person["name"], "why": f"answered none of the {person['sent']} weekly questions sent", "tone": "warn"})
        if person["participation"] is not None and person["participation"] <= 35:
            to_watch.append({"name": person["name"], "why": "noted quiet in the sessions", "tone": "muted"})

    return {"figures": figures, "sessions": session_rows, "weeks": week_rows, "people": people, "leaders": leaders,
            "to_watch": to_watch, "advice": advice(figures, week_rows), "has_data": bool(session_rows or week_rows or opinions["answered"]),
            "opinions": opinions}


def advice(figures: dict, weeks: list[dict]) -> list[dict]:
    """What to do next for the class, from its figures: [{"text", "tone"}]."""
    found = []
    recent, earlier = figures["recent_attendance"], figures["earlier_attendance"]
    if recent is not None and earlier is not None and earlier - recent >= 15:
        found.append({"text": f"Attendance fell from {earlier}% to {recent}% over the last sessions: write to those who stopped coming.", "tone": "bad"})
    elif figures["attendance"] is not None and figures["attendance"] < 70:
        found.append({"text": f"Only {figures['attendance']}% of the places were taken in the sessions: remind the class of the next one.", "tone": "warn"})
    if figures["interactivity"] is not None and figures["interactivity"] < 50:
        found.append({"text": f"The class takes little part ({figures['interactivity']}/100): plan short exercises in pairs and ask each one a question.", "tone": "warn"})
    if figures["answer_rate"] is not None and figures["answer_rate"] < 60 and figures["sent"] >= 3:
        found.append({"text": f"{figures['answer_rate']}% of the weekly questions were answered: say in class why they matter, and remind them once.", "tone": "warn"})
    low = [week for week in weeks if week["score"] is not None and week["score"] < 55 and week["answered"] >= 2]
    if low:
        weeks_named = ", ".join(f"week {week['week']} ({round(week['score'])}%)" for week in low[:3])
        found.append({"text": f"Not understood well: {weeks_named}. Go back over it at the start of the next session.", "tone": "warn"})
    if figures["happy_share"] is not None and figures["happy_share"] < 60:
        found.append({"text": f"Only {figures['happy_share']}% of the opinions are happy: read what they wrote and answer it.", "tone": "bad"})
    if figures["sessions_marked"] and figures["participation"] is None:
        found.append({"text": "The professor did not note participation yet: it takes one click per candidate on the presence sheet.", "tone": "info"})
    if not found and (figures["sessions_marked"] or figures["answered"]):
        found.append({"text": "The class is doing well: keep the rhythm.", "tone": "ok"})
    return found

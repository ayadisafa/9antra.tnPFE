"""Who may stop coming, and why. Measured, never guessed: each reason is something the application recorded.

For each candidate in a class that runs (or starts within two weeks), points are added for:

    absences in a row      the last sessions marked, from the latest              up to 55
    presence               present at less than 60% of four or more sessions        15
    questions unanswered   two of the last three sent, not answered                20
    questions failed       under 50% on average (two answered or more)             15
    money                  still owing ten days after registering (+10 reminded)    25
    feelings               frustrated, disappointed or worried in their messages   15

60 points and more: likely to drop out; 30 to 59: could drop out. Below 30 they are not shown.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

HIGH, SHOWN = 60, 30
_SAD = {"frustrated", "disappointed", "worried"}


def _naive(moment):
    return moment.replace(tzinfo=None) if moment is not None and getattr(moment, "tzinfo", None) else moment


def of_registration(db: Session, registration, now: datetime | None = None) -> dict:
    """{"score", "reasons": [sentences in the page's language], "keys": [what counted]}."""
    from app.models.classsession import ClassSession
    from app.models.quiz import Quiz
    from app.models.user import Attendance
    from app.services import candidate_insights
    from app.services.ui_words import t as _t

    now = now or datetime.utcnow()
    score, reasons, keys = 0, [], []
    marks = (db.query(Attendance.status, ClassSession.session_date).join(ClassSession, ClassSession.id == Attendance.session_id)
             .filter(Attendance.registration_id == registration.id).order_by(ClassSession.session_date.desc()).all())
    in_a_row = 0
    for status, _ in marks:
        if status != "absent":
            break
        in_a_row += 1
    if in_a_row >= 2:
        score += min(55, 35 + 10 * (in_a_row - 2))
        reasons.append(_t("Absent from the last {0} sessions", in_a_row))
        keys.append("absences")
    elif in_a_row == 1:
        score += 15
        reasons.append(_t("Absent from the last session"))
        keys.append("absent")
    if len(marks) >= 4:
        present = sum(1 for status, _ in marks if status in ("present", "late"))
        if present / len(marks) < 0.6:
            score += 15
            reasons.append(_t("Present at {0}% of the sessions", round(100 * present / len(marks))))
            keys.append("presence")

    quizzes = (db.query(Quiz).filter(Quiz.registration_id == registration.id, Quiz.kind.in_(("weekly", "session")), Quiz.sent_at.isnot(None))
               .order_by(Quiz.sent_at.desc()).all())
    settled = [quiz for quiz in quizzes if _naive(quiz.sent_at) <= now - timedelta(days=2)][:3]
    unanswered = sum(1 for quiz in settled if quiz.completed_at is None)
    if unanswered >= 2:
        score += 20
        reasons.append(_t("Did not answer {0} of their last questions", unanswered))
        keys.append("silent")
    answered = [quiz for quiz in quizzes if quiz.completed_at is not None and quiz.score is not None]
    if len(answered) >= 2:
        average = sum(quiz.score for quiz in answered) / len(answered)
        if average < 50:
            score += 15
            reasons.append(_t("{0}% on average on the questions: they struggle", round(average)))
            keys.append("struggles")

    owed = registration.remaining_amount or 0
    registered = _naive(registration.registration_date)
    if owed > 0 and registered is not None and registered <= now - timedelta(days=10):
        score += 15
        reasons.append(_t("Still owes {0} DT", f"{owed:g}"))
        keys.append("owes")
        reminded = _naive(registration.reminder_sent_at)
        if reminded is not None and reminded <= now - timedelta(days=7):
            score += 10
            reasons.append(_t("Reminded {0} days ago, nothing paid since", (now - reminded).days))

    candidate = registration.candidate
    if candidate is not None:
        feelings = candidate_insights.feelings_of(db, candidate)
        sad = [item for item in feelings["emotions"] if item["key"] in _SAD and item["last"] is not None
               and _naive(item["last"]) >= now - timedelta(days=30)]
        if sad:
            score += 15
            reasons.append(_t("Wrote they felt: {0}", ", ".join(item["label"].lower() for item in sad[:2])))
            keys.append("feelings")
    return {"score": min(100, score), "reasons": reasons, "keys": keys}


def at_risk(db: Session, limit: int = 8, now: datetime | None = None) -> list[dict]:
    """The candidates who may drop out, the likeliest first: {"candidate", "registration", "class", "score", "level",
    "reasons", "action": {"label", "url"}}."""
    from app.models import ClassGroup, ClassStatus, Registration
    from app.models.registration import RegistrationStatus
    from app.services.ui_words import t as _t

    now = now or datetime.utcnow()
    soon = date.today() + timedelta(days=14)
    found = []
    for registration in (db.query(Registration).join(ClassGroup, ClassGroup.id == Registration.class_group_id)
                         .filter(Registration.status.notin_((RegistrationStatus.CANCELLED, RegistrationStatus.COMPLETED))).all()):
        group = registration.class_group
        if group is None or group.status == ClassStatus.COMPLETED:
            continue
        if group.status != ClassStatus.ONGOING and not (group.start_date and _naive(group.start_date).date() <= soon):
            continue
        risk = of_registration(db, registration, now)
        if risk["score"] < SHOWN or registration.candidate is None:
            continue
        action = ({"label": _t("Remind them to pay"), "url": f"/candidate/{registration.candidate_id}/payment-reminder?back=/"}
                  if risk["keys"] == ["owes"] else {"label": _t("Open their file"), "url": f"/candidate/{registration.candidate_id}"})
        found.append({"candidate": registration.candidate, "registration": registration, "class": group, "score": risk["score"],
                      "level": "high" if risk["score"] >= HIGH else "medium", "reasons": risk["reasons"], "action": action})
    found.sort(key=lambda item: -item["score"])
    # One line per person: their most worrying registration.
    seen, kept = set(), []
    for item in found:
        if item["candidate"].id in seen:
            continue
        seen.add(item["candidate"].id)
        kept.append(item)
    return kept[:limit]

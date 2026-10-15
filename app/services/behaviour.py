"""How a candidate behaves, read from what the application has measured.

Nothing here is an opinion and no language model is asked: eight parameters
are counted from the database, each gives a mark out of 100 with a weight, and
the page shows every one of them with its value and its rule, so the admin
sees exactly where a score comes from.

    presence        sessions attended, of those marked                    20
    punctuality     arrivals on time, of the sessions attended            10
    answers         weekly questions answered, of those sent              15
    understanding   average result of the weekly questions                15
    reaction        how soon they answer what they are sent               10
    care            check questions not rushed (very fast and wrong)      10
    participation   what the professor noted in sessions                  12
    questions       questions they ask, by email and in sessions           8

A parameter with nothing measured yet is left out, and the others share its
weight. The words chosen describe what was done ("low effort so far"), never
what somebody is.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy.orm import Session

from app.models.candidate import Candidate
from app.models.candidate_email import CandidateEmail
from app.models.classsession import ClassSession
from app.models.quiz import Quiz
from app.models.user import Attendance
from app.services import meetings

FAST = 8            # seconds: a check question answered faster than this was barely read
SLOW = 45           # seconds: from here somebody is taking their time
SLOW_ANSWER = timedelta(days=3)     # an invitation left unanswered this long counts as no reaction
PARTICIPATION = {1: ("Quiet", 30), 2: ("Takes part", 65), 3: ("Very active", 100)}
WEIGHTS = {"presence": 20, "punctuality": 10, "answers": 15, "understanding": 15, "reaction": 10, "care": 10,
           "participation": 12, "questions": 8}


def _loaded(text: str | None) -> dict:
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def timing_of(quiz: Quiz) -> dict | None:
    """What the timer of a level check measured, with what it means:
    {"total", "median", "per": [...], "fast_wrong", "fast_right", "slow", "changes", "away"}."""
    result = _loaded(quiz.analysis)
    timing = result.get("timing")
    if not isinstance(timing, dict) or not timing.get("per"):
        return None
    try:
        items = json.loads(quiz.questions or "[]")
        answers = json.loads(quiz.answers or "[]")
    except (TypeError, ValueError):
        return None
    rows = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("type") != "check":
            continue
        seconds = timing["per"].get(str(index))
        if seconds is None:
            continue
        answer = answers[index] if index < len(answers) else None
        rows.append({"seconds": float(seconds), "right": answer == item.get("correct_answer"),
                     "unknown": answer == -1 or answer is None})
    if not rows:
        return None
    times = [row["seconds"] for row in rows]
    return {
        "total": int(timing.get("total") or 0),
        "median": round(median(times), 1),
        "asked": len(rows),
        "fast_wrong": sum(1 for row in rows if row["seconds"] < FAST and not row["right"] and not row["unknown"]),
        "fast_right": sum(1 for row in rows if row["seconds"] < FAST and row["right"]),
        "slow": sum(1 for row in rows if row["seconds"] >= SLOW),
        "right": sum(1 for row in rows if row["right"]),
        "changes": int(sum((timing.get("changes") or {}).values())) if isinstance(timing.get("changes"), dict) else 0,
        "away": int(timing.get("away") or 0),
    }


def _minutes(seconds: int) -> str:
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


def pace_words(timing: dict) -> str:
    """One line about how the level check was answered, for the pages."""
    parts = [f"{_minutes(timing['total'])} in all", f"{timing['median']:g} s per question (the middle one)"]
    if timing["changes"]:
        parts.append(f"changed {timing['changes']} answer{'s' if timing['changes'] != 1 else ''}")
    if timing["away"]:
        parts.append(f"left the page {timing['away']} time{'s' if timing['away'] != 1 else ''}")
    return " · ".join(parts)


def _parameter(key: str, label: str, value: str, score, rule: str) -> dict:
    return {"key": key, "label": label, "value": value, "score": None if score is None else max(0, min(100, round(score))),
            "weight": WEIGHTS[key], "rule": rule}


def profile(db: Session, candidate: Candidate, now: datetime | None = None) -> dict:
    """The candidate's behaviour: {"score", "headline", "tone", "traits", "params", "used"}."""
    now = now or datetime.utcnow()
    params: list[dict] = []
    traits: list[dict] = []

    def trait(label: str, tone: str, why: str) -> None:
        traits.append({"label": label, "tone": tone, "why": why})

    # ---- Sessions: presence, punctuality, what the professor noted ----
    marks = db.query(Attendance).filter(Attendance.candidate_id == candidate.id).all()
    order = {session.id: (session.session_date, session.week_number or 0) for session in
             db.query(ClassSession).filter(ClassSession.id.in_([mark.session_id for mark in marks])).all()} if marks else {}
    marks.sort(key=lambda mark: order.get(mark.session_id, (datetime.min.date(), 0)))
    present = sum(1 for mark in marks if mark.status == "present")
    late = sum(1 for mark in marks if mark.status == "late")
    attended = present + late
    arrivals = meetings.arrivals(db, candidate.id)
    if marks:
        rate = attended / len(marks) * 100
        params.append(_parameter("presence", "Presence", f"{attended} of {len(marks)} sessions ({round(rate)}%)", rate,
                                 "Sessions attended (present or late), out of the sessions marked."))
        if len(marks) >= 3 and rate >= 90:
            trait("Reliable: always there", "ok", f"Attended {attended} of {len(marks)} sessions.")
        streak = 0
        for mark in reversed(marks):
            if mark.status != "absent":
                break
            streak += 1
        if streak >= 2:
            trait("Stopped coming", "bad", f"Absent from the last {streak} sessions.")
    else:
        params.append(_parameter("presence", "Presence", "No session marked yet", None, "Sessions attended, out of the sessions marked."))
    if attended:
        on_time = 100 * (1 - late / attended)
        detail = f"late {late} of {attended} time{'s' if attended != 1 else ''}"
        if arrivals["late"]:
            detail += f", {arrivals['average_late']} min on average by their meeting link"
        params.append(_parameter("punctuality", "On time", detail.capitalize(), on_time,
                                 f"Arrivals on time, out of the sessions attended. Late = marked late, or more than {meetings.LATE_AFTER} minutes after the start by their own link."))
        if attended >= 3 and late / attended >= 0.34:
            trait("Often late", "warn", f"Late {late} of {attended} times.")
    else:
        params.append(_parameter("punctuality", "On time", "No arrival yet", None, "Arrivals on time, out of the sessions attended."))
    noted = [mark.participation for mark in marks if mark.participation in PARTICIPATION]
    if noted:
        average = sum(noted) / len(noted)
        points = sum(PARTICIPATION[value][1] for value in noted) / len(noted)
        words = PARTICIPATION[min(3, max(1, round(average)))][0]
        params.append(_parameter("participation", "In sessions", f"{words} (noted in {len(noted)} session{'s' if len(noted) != 1 else ''})", points,
                                 "What the professor noted on the presence sheet: quiet 30, takes part 65, very active 100."))
        if average >= 2.6:
            trait("Very active in sessions", "ok", f"Noted very active in {sum(1 for value in noted if value == 3)} of {len(noted)} sessions.")
        elif average <= 1.4 and len(noted) >= 2:
            trait("Quiet in sessions", "muted", f"Noted quiet in {sum(1 for value in noted if value == 1)} of {len(noted)} sessions.")
    else:
        params.append(_parameter("participation", "In sessions", "Nothing noted by the professor yet", None,
                                 "What the professor notes on the presence sheet after a session."))

    # ---- What they are sent: weekly questions and the level check ----
    quizzes = db.query(Quiz).filter(Quiz.candidate_id == candidate.id).all()
    weekly = [quiz for quiz in quizzes if quiz.kind == "weekly" and quiz.sent_at is not None]
    level = [quiz for quiz in quizzes if (quiz.kind or "level") == "level" and quiz.angle == "placement"
             and (quiz.sent_at is not None or quiz.completed_at is not None)]
    answered = [quiz for quiz in weekly if quiz.completed_at is not None]
    if weekly:
        share = len(answered) / len(weekly) * 100
        params.append(_parameter("answers", "Weekly questions answered", f"{len(answered)} of {len(weekly)} sent", share,
                                 "Weekly questions answered, out of those sent."))
        if len(weekly) >= 2 and share < 50:
            trait("Low effort so far", "bad", f"Answered {len(answered)} of the {len(weekly)} weekly questions sent.")
    else:
        params.append(_parameter("answers", "Weekly questions answered", "None sent yet", None, "Weekly questions answered, out of those sent."))
    if answered:
        average = sum(quiz.score or 0 for quiz in answered) / len(answered)
        params.append(_parameter("understanding", "Understanding", f"{round(average)}% on average over {len(answered)} week{'s' if len(answered) != 1 else ''}", average,
                                 "Average result of the weekly questions they answered."))
    else:
        params.append(_parameter("understanding", "Understanding", "No weekly answer yet", None, "Average result of the weekly questions they answered."))
    reactions, hours = [], []
    for quiz in weekly + level:
        if quiz.sent_at is None:
            continue                # answered without having been sent: nothing to measure a reaction from
        if quiz.completed_at is not None:
            delay = quiz.completed_at - quiz.sent_at
            hours.append(max(0.0, delay.total_seconds() / 3600))
            reactions.append(100 if delay <= timedelta(hours=24) else 70 if delay <= SLOW_ANSWER else 40)
        elif now - quiz.sent_at > SLOW_ANSWER:
            reactions.append(0)
    if reactions:
        middle = median(hours) if hours else None
        value = (f"answers after {round(middle)} h (the middle one)" if middle is not None and middle >= 1 else "answers within the hour") if hours else "has not answered"
        silent = reactions.count(0)
        if silent:
            value += f" · {silent} left unanswered for more than 3 days"
        params.append(_parameter("reaction", "Reaction", value.capitalize(), sum(reactions) / len(reactions),
                                 "How soon they answer what they are sent: within a day 100, within three days 70, later 40, never 0."))
    else:
        params.append(_parameter("reaction", "Reaction", "Nothing to answer yet", None, "How soon they answer what they are sent."))
    for quiz in level:
        if quiz.reminded_at is not None:
            if quiz.completed_at is None:
                trait("Did not answer their level check", "bad", "Still unanswered after the reminder.")
            else:
                trait("Needed a reminder", "muted", "Answered their level check only after the reminder.")
    timing = next((found for found in (timing_of(quiz) for quiz in level if quiz.completed_at is not None) if found), None)
    if timing:
        care = 100 * (1 - timing["fast_wrong"] / timing["asked"])
        params.append(_parameter("care", "Care with questions", pace_words(timing).capitalize(), care,
                                 f"Check questions of the level check not rushed: one answered wrongly in under {FAST} s takes points away. Being fast and right costs nothing."))
        if timing["median"] < FAST:
            if timing["right"] / timing["asked"] >= 0.7:
                trait("Very fast, and right", "ok", f"{timing['median']:g} s per question with {timing['right']} of {timing['asked']} right.")
            elif timing["fast_wrong"] * 2 >= timing["asked"]:
                trait("Rushes: fast but often wrong", "warn", f"{timing['fast_wrong']} of {timing['asked']} questions answered wrongly in under {FAST} s.")
            else:
                trait("Very fast", "info", f"{timing['median']:g} s per question.")
        elif timing["median"] >= SLOW:
            trait("Takes their time", "info", f"{timing['median']:g} s per question.")
        if timing["away"] >= 3:
            trait("Left the page while answering", "muted", f"{timing['away']} times during the level check.")
    else:
        params.append(_parameter("care", "Care with questions", "No timed answer yet", None,
                                 "Timed on the level check: a question answered wrongly in a few seconds was not read."))
    for quiz in level:
        result = _loaded(quiz.analysis) if quiz.completed_at is not None else {}
        checks = result.get("checks") or {}
        if checks.get("unknown", 0) >= 3 and checks.get("asked", 0) - checks.get("right", 0) - checks.get("unknown", 0) <= 1:
            trait("Says when they do not know", "info", f"Chose “I don't know yet” {checks['unknown']} times rather than guessing.")

    # ---- Questions they ask ----
    emails = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == candidate.id, CandidateEmail.direction == "inbound").all()
    by_email = sum(1 for email in emails if (email.question or "").strip() or "?" in (email.body or ""))
    in_sessions = sum(mark.questions or 0 for mark in marks)
    asked = by_email + in_sessions
    if emails or marks:
        value = f"{asked} question{'s' if asked != 1 else ''}" + (f" ({by_email} by email, {in_sessions} in sessions)" if asked else " so far")
        params.append(_parameter("questions", "Questions asked", value, 100 if asked >= 3 else 70 if asked else 40,
                                 "Questions asked by email or noted by the professor in sessions: three or more 100, one or two 70, none 40."))
        if asked >= 3:
            trait("Asks a lot of questions", "ok", f"{by_email} by email, {in_sessions} noted in sessions.")
    else:
        params.append(_parameter("questions", "Questions asked", "No message and no session yet", None,
                                 "Questions asked by email or noted by the professor in sessions."))

    used = [param for param in params if param["score"] is not None]
    weight = sum(param["weight"] for param in used)
    score = round(sum(param["score"] * param["weight"] for param in used) / weight) if len(used) >= 2 and weight else None
    for param in params:
        param["share"] = round(param["weight"] / weight * 100) if param["score"] is not None and weight else 0
    if score is None:
        headline, tone = "Not enough to say yet", "muted"
    elif score >= 80:
        headline, tone = "Great so far", "ok"
    elif score >= 60:
        headline, tone = "Doing well", "info"
    elif score >= 40:
        headline, tone = "Uneven", "warn"
    else:
        headline, tone = "Needs your attention", "bad"
    order_of = {"bad": 0, "warn": 1, "ok": 2, "info": 3, "muted": 4}
    traits.sort(key=lambda item: order_of.get(item["tone"], 5))
    return {"score": score, "headline": headline, "tone": tone, "traits": traits, "params": params,
            "used": len(used), "of": len(params)}


def scores(db: Session, candidates: list[Candidate]) -> dict[str, dict]:
    """candidate id -> {"score", "headline", "tone"}, for a list of people."""
    found = {}
    for candidate in candidates:
        if candidate is None:
            continue
        result = profile(db, candidate)
        found[candidate.id] = {"score": result["score"], "headline": result["headline"], "tone": result["tone"],
                               "traits": result["traits"][:2]}
    return found

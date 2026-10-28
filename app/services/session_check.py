"""After each session, each candidate is asked what they learnt in it: a few questions about the session's subject,
sent some hours after it ends (24 by default; the admin chooses, Settings › Classes, for example 4). Their answers show
next to their presence (the presence sheet, the class, the candidate's file): who answered, how well, and when.

Where the questions come from, for every course, with nothing to add by hand:
- the questions written from the week's course and checked (weekly_questions.py), when they are ready. What the
  system knows of each course is given to that writer (course_materials.sources): the professor's files and notes,
  the course's own plan for the week, and what 9antra.tn says of that week. So it writes for any course, a new one
  too, as soon as the course has its weekly plan;
- two questions made from the course plan, right by construction (no model): “what was this session about?” (the
  week's subject among others of the same course) and “which of these was covered?” (one of the week's objectives
  among others of the course). Every session gets them, so every session is asked about.

A session's questions wait a few hours for the writer when it is still writing; after that they go with what is ready.
"""
from __future__ import annotations

import json
import logging
import random
import secrets
import uuid
from datetime import datetime, time, timedelta

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session

from app.database import Base

logger = logging.getLogger(__name__)

DEFAULT_HOURS = 24
PER_CANDIDATE = 4               # questions each candidate gets after a session (the plan's two among them)
WAIT_FOR_WRITER = timedelta(hours=6)
LOOK_BACK = timedelta(days=7)   # sessions older than this when the feature starts are not asked about


class SessionCheck(Base):
    __tablename__ = "session_checks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    class_session_id = Column(String, nullable=False, unique=True, index=True)
    class_group_id = Column(String, nullable=False, index=True)
    week_number = Column(Integer, nullable=True)
    ends_at = Column(DateTime, nullable=False)          # local time the session ended
    status = Column(String, nullable=False, default="waiting")      # waiting | sent | nothing
    sent_at = Column(DateTime, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ------------------------------------------------------------------
# The admin's choice
# ------------------------------------------------------------------

def settings_of(db: Session) -> dict:
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    hours = getattr(settings, "session_check_hours", None)
    on = getattr(settings, "session_check_on", None)
    return {"on": True if on is None else bool(on), "hours": DEFAULT_HOURS if hours is None else max(1, min(72, int(hours)))}


def save_settings(db: Session, *, on: bool, hours) -> dict:
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = DEFAULT_HOURS
    settings.session_check_on = bool(on)
    settings.session_check_hours = max(1, min(72, hours))
    db.commit()
    return settings_of(db)


# ------------------------------------------------------------------
# The questions
# ------------------------------------------------------------------

def _week_texts(db: Session, group) -> list[dict]:
    from app.services import chatbot_sources

    if group is None or group.program is None:
        return []
    weeks, _ = chatbot_sources._weeks_of(db, group.program)
    return weeks


def _objectives(text: str) -> list[str]:
    parts = [" ".join(part.split()).strip(" .;:-") for part in (text or "").replace("\n", ";").split(";")]
    return [part for part in parts if 8 <= len(part) <= 160]


def plan_questions(db: Session, group, week_number: int | None, topic: str = "") -> list[dict]:
    """Two questions from the course's own plan, right by construction: the session's subject, and one thing the
    week covered, each among those of other weeks of the same course."""
    weeks = _week_texts(db, group)
    this = next((week for week in weeks if week["number"] == week_number), None)
    if this is None:
        return []
    rng = random.Random(f"{group.id}-{week_number}")
    found = []
    subject = (topic or this["title"] or "").strip()
    others = list(dict.fromkeys(week["title"].strip() for week in weeks
                                if week["number"] != week_number and week["title"] and week["title"].strip() != subject))
    if subject and len(others) >= 3:
        # The weeks furthest from this one first: their subjects are the least alike.
        others.sort(key=lambda title: -abs(next(week["number"] for week in weeks if week["title"].strip() == title) - (week_number or 0)))
        options = [subject] + rng.sample(others[:6], 3)
        rng.shuffle(options)
        found.append({"id": f"plan:{group.id}:{week_number}:subject", "question": "De quoi parlait cette séance ?",
                      "options": options, "correct_answer": options.index(subject),
                      "explanation": f"La séance portait sur « {subject} », selon le programme de la formation."})
    mine = _objectives(this["objectives"])
    theirs = [item for week in weeks if abs(week["number"] - (week_number or 0)) >= 2 for item in _objectives(week["objectives"])
              if item not in mine]
    if mine and len(set(theirs)) >= 3:
        right = rng.choice(mine)
        options = [right] + rng.sample(sorted(set(theirs)), 3)
        rng.shuffle(options)
        found.append({"id": f"plan:{group.id}:{week_number}:covered", "question": "Lequel de ces points a été vu cette semaine ?",
                      "options": options, "correct_answer": options.index(right),
                      "explanation": f"« {right} » fait partie des objectifs de cette semaine dans le programme."})
    return found


def _asked_before(db: Session, registration_id: str, class_group_id: str, week_number) -> set:
    from app.models.quiz import Quiz
    from app.services import weekly_questions

    asked = set()
    for quiz in db.query(Quiz).filter(Quiz.registration_id == registration_id, Quiz.class_group_id == class_group_id,
                                      Quiz.week_number == week_number, Quiz.kind.in_(("weekly", "session"))).all():
        asked |= {item.get("id") for item in weekly_questions.questions_in(quiz)}
    return asked


# ------------------------------------------------------------------
# When: planned as sessions end, sent some hours later
# ------------------------------------------------------------------

def _ends(session, group) -> datetime | None:
    if not session.session_date:
        return None
    return datetime.combine(session.session_date, session.end_time or group.end_time or time(23, 0))


def plan(db: Session, now: datetime) -> int:
    """A line for every session that ended (in the last days) of a class with candidates; the week's questions are
    asked for at the same time, so they are ready when the session's questions go."""
    from app.models import ClassGroup, ClassStatus
    from app.models.classsession import ClassSession
    from app.services import course_materials, weekly_questions

    from app.services.settings_service import get_settings

    settings = get_settings(db)
    since = getattr(settings, "session_check_since", None)
    if since is None:
        # The first run: only what ended within the chosen hours is asked about, never an older session.
        since = settings.session_check_since = now - timedelta(hours=settings_of(db)["hours"])
        db.commit()
    known = {row[0] for row in db.query(SessionCheck.class_session_id).all()}
    added = 0
    rows = (db.query(ClassSession, ClassGroup).join(ClassGroup, ClassGroup.id == ClassSession.class_group_id)
            .filter(ClassGroup.status != ClassStatus.COMPLETED, ClassSession.session_date >= (now - LOOK_BACK).date(),
                    ClassSession.session_date <= now.date()).all())
    for session, group in rows:
        ends = _ends(session, group)
        if session.id in known or ends is None or ends > now or now - ends > LOOK_BACK or ends < since:
            continue
        if not weekly_questions.members_of(db, group.id):
            continue
        db.add(SessionCheck(class_session_id=session.id, class_group_id=group.id, week_number=session.week_number, ends_at=ends))
        added += 1
        week = course_materials.get_week(db, group, session.week_number or 1)
        if week.questions_status in (None, "idle") and not weekly_questions.questions_of(db, group.id, session.week_number or 1):
            weekly_questions.queue(db, group, session.week_number or 1)      # written in the background (writing_tick)
    if added:
        db.commit()
    return added


def questions_written(db: Session, quiz, base_url: str) -> dict | None:
    """The questions written in the email itself, answered by replying (“1-B, 2-A”), when candidates cannot open the
    page (the same choice as the weekly questions): the email then has no button, its questions are in it."""
    from app.services import email_templates, weekly_questions

    if not weekly_questions.in_the_email(db, quiz, base_url):
        return None
    template = email_templates.get(db, "session_check")
    return {"body": f"{template.body}\n\n{weekly_questions.written_out(quiz)}", "cta_label": "", "cta_url": ""}


def send_due(db: Session, base_url: str, now: datetime | None = None) -> int:
    """Send the questions of the sessions whose time has come. Returns how many candidates were sent theirs."""
    from app.models import ClassGroup, ClassStatus
    from app.models.classsession import ClassSession
    from app.models.notification import Notification
    from app.models.quiz import Quiz
    from app.services import course_materials, email_templates, weekly_questions
    from app.services.schedule_utils import local_now

    now = now or local_now()
    plan(db, now)
    choice = settings_of(db)
    if not choice["on"]:
        return 0
    delay = timedelta(hours=choice["hours"])
    sent = 0
    for row in db.query(SessionCheck).filter(SessionCheck.status == "waiting").order_by(SessionCheck.ends_at).all():
        if row.ends_at + delay > now:
            continue
        group, session = db.get(ClassGroup, row.class_group_id), db.get(ClassSession, row.class_session_id)
        if group is None or session is None or group.status == ClassStatus.COMPLETED:
            row.status, row.note = "nothing", "The class or the session no longer exists."
            db.commit()
            continue
        week_number = row.week_number or 1
        checked = [weekly_questions._as_asked(item) for item in weekly_questions.ready_questions(db, group.id, week_number)]
        week = course_materials.get_week(db, group, week_number)
        if not checked and week.questions_status in ("queued", "queued_more", "writing") and now < row.ends_at + delay + WAIT_FOR_WRITER:
            continue                    # the writer is on it: wait a little
        topic = session.topic or course_materials.week_topic(db, group.id, week_number)
        from_plan = plan_questions(db, group, week_number, topic)
        if not checked and not from_plan:
            row.status, row.note = "nothing", "No question could be made: the course has no weekly plan and no course file for this week."
            db.add(Notification(recipient_role="admin", category="WEEK_QUESTIONS", title=f"{group.name}: no questions after the session",
                                message=f"The session of {session.session_date:%d/%m} could not be asked about: add the week's plan or a course file.",
                                action_url=f"/classes/{group.id}/course?week={week_number}", program_id=group.program_id))
            db.commit()
            continue
        program = group.program.name if group.program else group.name
        for registration in weekly_questions.members_of(db, group.id):
            candidate = registration.candidate
            if candidate is None or not (candidate.email or candidate.phone):
                continue
            if db.query(Quiz.id).filter(Quiz.registration_id == registration.id, Quiz.class_session_id == session.id).first():
                continue                # asked already (a second run of the same session)
            asked = _asked_before(db, registration.id, group.id, week_number)
            rng = random.Random(f"{registration.id}-{session.id}")
            pool = [item for item in checked if item["id"] not in asked]
            rng.shuffle(pool)
            chosen = pool[: max(0, PER_CANDIDATE - len(from_plan))] + [item for item in from_plan if item["id"] not in asked]
            if not chosen:
                chosen = list(from_plan)
            quiz = Quiz(candidate_id=candidate.id, registration_id=registration.id, course_name=f"{program} — séance du {session.session_date:%d/%m}",
                        angle="session", level="session", kind="session", class_group_id=group.id, week_number=week_number,
                        class_session_id=session.id, access_token=secrets.token_urlsafe(24),
                        questions=json.dumps(chosen, ensure_ascii=False), answers="[]", score=0.0, prepared_at=datetime.utcnow())
            db.add(quiz)
            db.commit()                 # the link exists before the message is written
            message = email_templates.send(db, "session_check", registration=registration, candidate=candidate, manual=False, values={
                "session_date": f"{session.session_date:%d/%m}", "session_topic": topic or program, "questions_count": str(len(chosen)),
                "questions_link": weekly_questions.public_link(base_url, quiz), "program_name": program,
            }, overrides=questions_written(db, quiz, base_url))
            if email_templates.delivered(message):
                quiz.sent_at = datetime.utcnow()
                sent += 1
            db.commit()
        row.status, row.sent_at = "sent", datetime.utcnow()
        db.commit()
    return sent


# ------------------------------------------------------------------
# What the answers say about presence
# ------------------------------------------------------------------

def evidence(db: Session, class_group_id: str) -> dict:
    """{(session id, registration id): {"sent", "answered", "score", "right", "total", "when"}} for a class."""
    from app.models.quiz import Quiz
    from app.services import weekly_questions

    found = {}
    for quiz in db.query(Quiz).filter(Quiz.kind == "session", Quiz.class_group_id == class_group_id, Quiz.class_session_id.isnot(None)).all():
        result = weekly_questions.result_of(quiz) if quiz.completed_at else None
        found[(quiz.class_session_id, quiz.registration_id)] = {
            "sent": quiz.sent_at is not None, "answered": quiz.completed_at is not None,
            "score": quiz.score if quiz.completed_at else None, "right": result["right"] if result else None,
            "total": result["total"] if result else len(weekly_questions.questions_in(quiz)), "when": quiz.completed_at,
        }
    return found


def session_summary(db: Session, class_session_id: str) -> dict:
    """For one session: how many were asked, answered, and the average."""
    from app.models.quiz import Quiz

    quizzes = db.query(Quiz).filter(Quiz.kind == "session", Quiz.class_session_id == class_session_id).all()
    done = [quiz for quiz in quizzes if quiz.completed_at]
    row = db.query(SessionCheck).filter(SessionCheck.class_session_id == class_session_id).first()
    return {"asked": sum(1 for quiz in quizzes if quiz.sent_at), "answered": len(done),
            "average": round(sum(quiz.score or 0 for quiz in done) / len(done)) if done else None,
            "status": row.status if row else None, "ends_at": row.ends_at if row else None}


def send_again(db: Session, group, base_url: str) -> str:
    """The admin resets the questions of the class's last session: those who did not answer get their link again;
    a session not asked about yet is asked about now."""
    from app.models.quiz import Quiz
    from app.services import email_templates, weekly_questions
    from app.services.schedule_utils import local_now

    row = (db.query(SessionCheck).filter(SessionCheck.class_group_id == group.id).order_by(SessionCheck.ends_at.desc()).first())
    if row is None:
        plan(db, local_now())
        row = (db.query(SessionCheck).filter(SessionCheck.class_group_id == group.id).order_by(SessionCheck.ends_at.desc()).first())
        if row is None:
            return "No session of this class has ended yet."
    if row.status != "sent":
        row.status, row.ends_at = "waiting", local_now() - timedelta(hours=settings_of(db)["hours"])
        db.commit()
        sent = send_due(db, base_url)
        return f"The questions of the last session went to {sent} candidate(s)."
    again = 0
    program = group.program.name if group.program else group.name
    for quiz in db.query(Quiz).filter(Quiz.kind == "session", Quiz.class_session_id == row.class_session_id, Quiz.completed_at.is_(None)).all():
        registration = next((item for item in weekly_questions.members_of(db, group.id) if item.id == quiz.registration_id), None)
        if registration is None or registration.candidate is None:
            continue
        from app.models.classsession import ClassSession

        session = db.get(ClassSession, row.class_session_id)
        message = email_templates.send(db, "session_check", registration=registration, candidate=registration.candidate, manual=True, values={
            "session_date": f"{session.session_date:%d/%m}" if session else "", "session_topic": (session.topic if session else "") or program,
            "questions_count": str(len(weekly_questions.questions_in(quiz))), "questions_link": weekly_questions.public_link(base_url, quiz),
            "program_name": program,
        }, overrides=questions_written(db, quiz, base_url))
        if email_templates.delivered(message):
            quiz.sent_at = datetime.utcnow()
            again += 1
        db.commit()
    return f"Sent again to {again} candidate(s) who had not answered." if again else "Everyone asked about the last session has answered."

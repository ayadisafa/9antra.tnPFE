"""The level check: where a candidate stands before their program starts.

One short form per registration, answered on a private page, in three parts:

1. where they come from: their experience of the subject, what they expect;
2. the program's own topics, one by one, from "I discover" to "I am at ease";
3. a few check questions of rising difficulty (see level_bank), where
   "I don't know yet" is a normal answer.

The level is worked out by rules, without a language model: the check
questions decide when the subject has some, what the candidate says about
themself otherwise. When the two disagree by much the admin is told, because
that is exactly what a professor wants to know before the first session.

A form is a row of `quizzes` (kind "level", angle "placement"): `questions`
holds the form as it was sent, `answers` what came back, `analysis` the result.
"""
from __future__ import annotations

import json
import logging
import random
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.classgroup import ClassGroup, ClassStatus
from app.models.notification import Notification
from app.models.program import Program
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, Registration, RegistrationStatus
from app.services import email_templates, level_bank
from app.services.enrollment_service import class_members, curriculum_weeks

logger = logging.getLogger(__name__)

KIND = "level"
FORMAT = "placement"        # Quiz.angle of a form written this way
MAX_TOPICS = 8
DONT_KNOW = -1              # the answer "I don't know yet" to a check question
MIN_CHECKS = 4              # below this the check questions cannot decide a level
REMIND_AFTER = timedelta(days=2)    # of silence, before the one reminder

# key, name for the admin, name for the candidate, what it means for the professor, tone of its chip
LEVELS = [
    ("beginner", "Beginner", "Débutant", "Starts from zero: the bases need time.", "info"),
    ("basics", "Has the bases", "Bases acquises", "Knows the words, has little practice yet.", "muted"),
    ("intermediate", "Intermediate", "Intermédiaire", "Has practised: the first weeks can go faster.", "ok"),
    ("advanced", "Advanced", "Avancé", "Already at ease: harder exercises, or helping the others.", "brand"),
]
_ORDER = [level[0] for level in LEVELS]
# What the candidate reads once they answered, by level.
WELCOME = {
    "beginner": "Vous partez de zéro, et c'est exactement pour cela que cette formation existe. "
                "Votre formateur prendra le temps de poser les bases.",
    "basics": "Vous avez déjà quelques repères. Les premières séances les consolideront avant d'aller plus loin.",
    "intermediate": "Vous avez déjà pratiqué. Votre formateur pourra avancer plus vite avec vous sur les débuts.",
    "advanced": "Vous êtes déjà à l'aise sur une bonne partie du programme. "
                "Votre formateur vous proposera d'aller plus loin.",
}

SCALE = ["Je découvre", "J'en ai entendu parler", "Je l'ai déjà pratiqué", "Je suis à l'aise"]
EXPERIENCE = [
    "Jamais : je débute",
    "Un peu : des vidéos, des tutoriels",
    "Oui : des projets d'études ou personnels",
    "Oui : dans mon travail",
]
GOALS = [
    "Trouver un emploi ou un stage",
    "Progresser dans mon travail actuel",
    "Réaliser un projet personnel",
    "Découvrir le domaine",
]
STEPS = {1: "Les bases", 2: "La pratique", 3: "Pour aller plus loin"}
# The same four steps, as the admin reads them.
SCALE_SAID = ["Discovers it", "Has heard of it", "Has practised it", "Is at ease with it"]
STEPS_SAID = {1: "The bases", 2: "Practice", 3: "Advanced"}

_LEVEL_PREFIX = re.compile(r"^\s*(level|niveau|module|week|semaine)\s*\d+\s*[:\-–.]\s*", re.IGNORECASE)


def level_info(key: str | None) -> dict | None:
    for name, label, french, meaning, tone in LEVELS:
        if name == key:
            return {"key": name, "label": label, "french": french, "meaning": meaning, "tone": tone}
    return None


# ------------------------------------------------------------------
# Writing the form
# ------------------------------------------------------------------

def _clean(text: str | None, limit: int = 90) -> str:
    return " ".join((text or "").split())[:limit].strip()


def topics_of(db: Session, program: Program | None) -> list[str]:
    """What the program teaches, as a candidate can place themself on it: its
    weeks when they are few, else its levels."""
    if program is None:
        return []
    weeks = [_clean(week.topic_title) for week in curriculum_weeks(db, program.id)]
    weeks = [title for title in weeks if title]
    if 0 < len(weeks) <= MAX_TOPICS:
        return weeks
    levels = [_clean(_LEVEL_PREFIX.sub("", level.title or "")) for level in program.levels]
    levels = [title for title in levels if title]
    chosen = levels or weeks
    if len(chosen) > MAX_TOPICS:
        step = len(chosen) / MAX_TOPICS
        chosen = [chosen[int(index * step)] for index in range(MAX_TOPICS)]
    return chosen


def build_form(db: Session, program: Program | None, program_name: str, seed: str) -> list[dict]:
    """The questions of one candidate's form. `seed` mixes the choices of the
    check questions the same way every time for the same candidate."""
    items: list[dict] = [
        {"type": "experience", "question": f"Avez-vous déjà pratiqué ce que couvre « {program_name} » ?", "options": list(EXPERIENCE)},
        {"type": "goal", "question": "Qu'attendez-vous surtout de cette formation ?", "options": list(GOALS)},
    ]
    items += [{"type": "topic", "question": topic} for topic in topics_of(db, program)]
    mixer = random.Random(seed)
    checks = level_bank.questions_for(program_name, program.website_slug if program else None)
    for check in sorted(checks, key=lambda item: item["step"]):
        order = list(range(len(check["options"])))
        mixer.shuffle(order)
        items.append({
            "type": "check", "step": check["step"], "question": check["question"],
            "options": [check["options"][index] for index in order],
            "correct_answer": order.index(check["correct_answer"]),
        })
    items.append({"type": "note", "question": "Autre chose à dire à votre formateur ?"})
    return items


def is_placement(quiz: Quiz) -> bool:
    return (quiz.kind or KIND) == KIND and quiz.angle == FORMAT


def current(db: Session, registration_id: str) -> Quiz | None:
    """The level check of a registration, if one was prepared."""
    return (
        db.query(Quiz)
        .filter(Quiz.registration_id == registration_id, Quiz.kind == KIND, Quiz.angle == FORMAT)
        .order_by(Quiz.prepared_at.desc())
        .first()
    )


def by_token(db: Session, token: str) -> Quiz | None:
    if not token or len(token) < 16:
        return None
    return db.query(Quiz).filter(Quiz.access_token == token, Quiz.kind == KIND, Quiz.angle == FORMAT).first()


def program_name_of(registration: Registration) -> str:
    return (registration.program.name if registration.program else registration.program_name) or "votre formation"


def can_have_one(registration: Registration) -> bool:
    """Paid, and still to be trained."""
    return (
        registration.payment_status == PaymentStatus.PAID
        and registration.status not in (RegistrationStatus.CANCELLED, RegistrationStatus.COMPLETED)
    )


def before_start(registration: Registration) -> bool:
    class_group = registration.class_group
    return class_group is None or class_group.status == ClassStatus.NOT_STARTED


def prepare(db: Session, registration: Registration) -> Quiz:
    """The registration's level check, written now if it has none."""
    quiz = current(db, registration.id)
    if quiz is not None:
        return quiz
    name = program_name_of(registration)
    quiz = Quiz(
        candidate_id=registration.candidate_id,
        registration_id=registration.id,
        class_group_id=registration.class_group_id,
        course_name=name,
        kind=KIND,
        angle=FORMAT,
        level=FORMAT,
        questions=json.dumps(build_form(db, registration.program, name, registration.id), ensure_ascii=False),
        answers="[]",
        score=0.0,
        access_token=secrets.token_urlsafe(18),
        prepared_at=datetime.utcnow(),
        sent_at=None,
        completed_at=None,
    )
    db.add(quiz)
    db.commit()
    return quiz


def prepare_missing(db: Session) -> int:
    """A level check for every paid candidate whose class has not started. Nothing is sent."""
    made = 0
    for registration in db.query(Registration).filter(Registration.payment_status == PaymentStatus.PAID).all():
        if can_have_one(registration) and before_start(registration) and current(db, registration.id) is None:
            prepare(db, registration)
            made += 1
    return made


def retire_old(db: Session) -> int:
    """Remove the quizzes written the earlier way (three per registration, by the
    language model). Weekly questions and level checks are kept."""
    old = (
        db.query(Quiz)
        .filter(or_(Quiz.kind.is_(None), Quiz.kind == KIND), or_(Quiz.angle.is_(None), Quiz.angle != FORMAT))
        .all()
    )
    for quiz in old:
        db.delete(quiz)
    if old:
        db.commit()
    return len(old)


# ------------------------------------------------------------------
# Reading the answers
# ------------------------------------------------------------------

def items_of(quiz: Quiz) -> list[dict]:
    try:
        items = json.loads(quiz.questions or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def read_answers(items: list[dict], form) -> list:
    """What the page posted, in the order of the questions: the position of
    the chosen answer, DONT_KNOW, a text, or None when left empty."""
    answers: list = []
    for index, item in enumerate(items):
        raw = (form.get(f"q{index}") or "").strip()
        kind = item.get("type")
        if kind == "note":
            answers.append(" ".join(raw.split())[:600] or None)
            continue
        choices = len(SCALE) if kind == "topic" else len(item.get("options") or [])
        try:
            value = int(raw)
        except ValueError:
            answers.append(None)
            continue
        if kind == "check" and value == DONT_KNOW:
            answers.append(DONT_KNOW)
        else:
            answers.append(value if 0 <= value < choices else None)
    return answers


def missing(items: list[dict], answers: list) -> list[int]:
    """The questions that need an answer and have none (everything but the free note)."""
    return [
        index for index, item in enumerate(items)
        if item.get("type") != "note" and (index >= len(answers) or answers[index] is None)
    ]


def _said_level(value: float) -> str:
    """The level somebody describes, from a 0-3 average."""
    if value < 0.6:
        return "beginner"
    if value < 1.5:
        return "basics"
    if value < 2.3:
        return "intermediate"
    return "advanced"


def _shown_level(steps: dict[int, list[int]]) -> str:
    """The level the check questions show. `steps` is {step: [right, asked]}."""
    def passed(step: int) -> bool:
        right, asked = steps.get(step, [0, 0])
        return asked > 0 and right * 3 >= asked * 2        # two out of three

    right = sum(item[0] for item in steps.values())
    asked = sum(item[1] for item in steps.values())
    share = right / asked if asked else 0
    if passed(3) and passed(2) and share >= 0.75:
        return "advanced"
    if passed(2) and (passed(1) or share >= 0.6):
        return "intermediate"
    if passed(1):
        return "basics"
    return "beginner"


def grade(items: list[dict], answers: list) -> dict:
    """The result of a form, by rules."""
    answers = list(answers) + [None] * (len(items) - len(answers))
    experience, goal, note = None, None, None
    topics: list[dict] = []
    steps: dict[int, list[int]] = {}
    unknown = 0
    for item, answer in zip(items, answers):
        kind = item.get("type")
        if kind == "experience" and isinstance(answer, int) and answer >= 0:
            experience = answer
        elif kind == "goal" and isinstance(answer, int) and 0 <= answer < len(item.get("options") or []):
            goal = item["options"][answer]
        elif kind == "note" and isinstance(answer, str):
            note = answer
        elif kind == "topic":
            topics.append({"title": item.get("question") or "", "value": answer if isinstance(answer, int) and answer >= 0 else 0})
        elif kind == "check":
            step = steps.setdefault(int(item.get("step") or 1), [0, 0])
            step[1] += 1
            if answer == item.get("correct_answer"):
                step[0] += 1
            elif answer == DONT_KNOW or answer is None:
                unknown += 1

    asked = sum(step[1] for step in steps.values())
    right = sum(step[0] for step in steps.values())
    told = sum(topic["value"] for topic in topics) / len(topics) if topics else None
    if told is None:
        said = _said_level(float(experience or 0))
    else:
        # What they say topic by topic counts most; their general experience corrects it a little.
        said = _said_level(told * 0.75 + (experience if experience is not None else told) * 0.25)

    if asked >= MIN_CHECKS:
        level, basis = _shown_level(steps), "checks"
        gap = _ORDER.index(said) - _ORDER.index(level)
        calibration = "over" if gap >= 2 else "under" if gap <= -2 else "ok"
    else:
        level, basis, calibration = said, "self", None

    return {
        "level": level,
        "basis": basis,                         # "checks": shown by their answers; "self": what they say
        "said": said,
        "calibration": calibration,             # over | under | ok | None
        "checks": {"right": right, "asked": asked, "unknown": unknown,
                   "steps": {str(step): values for step, values in sorted(steps.items())}},
        "experience": experience,
        "topics": topics,
        "known": [topic["title"] for topic in topics if topic["value"] >= 2],
        "new": [topic["title"] for topic in topics if topic["value"] == 0],
        "goal": goal,
        "note": note,
    }


def result_of(quiz: Quiz) -> dict | None:
    """The stored result of an answered level check, with its level's names."""
    if quiz.completed_at is None or not quiz.analysis:
        return None
    try:
        result = json.loads(quiz.analysis)
    except (TypeError, ValueError):
        return None
    if not isinstance(result, dict) or "level" not in result:
        return None
    result["info"] = level_info(result["level"]) or level_info("beginner")
    result["said_info"] = level_info(result.get("said"))
    return result


def read_timing(items: list[dict], raw) -> dict | None:
    """What the page's timer measured, kept only when it makes sense:
    {"total", "per": {question: seconds}, "changes": {question: times}, "away", "away_seconds"}."""
    try:
        data = json.loads(raw or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    def number(value, most: float) -> float | None:
        return round(min(float(value), most), 1) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None

    per, changes = {}, {}
    for index in range(len(items)):
        seconds = number((data.get("per") or {}).get(str(index)) if isinstance(data.get("per"), dict) else None, 1800)
        if seconds is not None:
            per[str(index)] = seconds
        times = number((data.get("changes") or {}).get(str(index)) if isinstance(data.get("changes"), dict) else None, 50)
        if times:
            changes[str(index)] = int(times)
    if not per:
        return None
    return {"total": int(number(data.get("total"), 7200) or sum(per.values())), "per": per, "changes": changes,
            "away": int(number(data.get("away"), 200) or 0), "away_seconds": int(number(data.get("away_seconds"), 7200) or 0)}


def submit(db: Session, quiz: Quiz, answers: list, timing: dict | None = None) -> dict:
    """Keep a candidate's answers, work out their level and tell the admin."""
    items = items_of(quiz)
    first_time = quiz.completed_at is None
    result = grade(items, answers)
    if timing:
        result["timing"] = timing      # how long each question took (see behaviour)
    checks = result["checks"]
    quiz.answers = json.dumps(answers, ensure_ascii=False)
    quiz.analysis = json.dumps(result, ensure_ascii=False)
    quiz.assessed_level = result["level"]
    quiz.score = round(checks["right"] / checks["asked"] * 100, 1) if checks["asked"] else 0.0
    quiz.completed_at = datetime.utcnow()
    quiz.answer_source = "page"
    if first_time:
        registration = db.get(Registration, quiz.registration_id) if quiz.registration_id else None
        candidate = registration.candidate if registration else None
        info = level_info(result["level"])
        who = (candidate.full_name or candidate.email) if candidate else "A candidate"
        extra = ""
        if result["calibration"] == "over":
            extra = " They describe themself as more advanced than their answers show."
        elif result["calibration"] == "under":
            extra = " Their answers show more than they say of themself."
        db.add(Notification(
            recipient_role="admin", category="LEVEL", title="Level check answered",
            message=f"{who} · {quiz.course_name}: {info['label']}. {info['meaning']}{extra}",
            action_url=f"/quiz/all?focus={quiz.id}",
            candidate_id=quiz.candidate_id, registration_id=quiz.registration_id,
            program_id=registration.program_id if registration else None,
        ))
    db.commit()
    return result


# ------------------------------------------------------------------
# Sending
# ------------------------------------------------------------------

def link_of(quiz: Quiz, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/level/{quiz.access_token}"


def send(db: Session, quiz: Quiz, base_url: str, *, manual: bool = True):
    """Email a candidate the link to their level check. Returns the stored
    message (None when nothing could be sent)."""
    registration = db.get(Registration, quiz.registration_id) if quiz.registration_id else None
    candidate = registration.candidate if registration else None
    if candidate is None or not candidate.email:
        return None
    message = email_templates.send(
        db, "quiz_invitation", candidate=candidate, registration=registration,
        values={"quiz_link": link_of(quiz, base_url), "program_name": quiz.course_name or program_name_of(registration)},
        manual=manual,
    )
    if email_templates.delivered(message):
        quiz.sent_at = datetime.utcnow()
    db.commit()
    return message


def after_payment(db: Session, registration: Registration) -> Quiz | None:
    """A candidate has just paid in full: their level check is prepared and,
    when that email is switched on and the link can be opened from outside
    this computer, sent to them at once."""
    if not can_have_one(registration) or not before_start(registration):
        return None
    quiz = prepare(db, registration)
    if quiz.sent_at is not None or quiz.completed_at is not None:
        return quiz
    from app.services import email_threads, settings_service, weekly_questions     # here: they import a lot

    base_url = settings_service.public_base_url(db)
    if (
        email_templates.is_on(db, "quiz_invitation")
        and email_threads.smtp_configured()
        and not weekly_questions.is_local_address(base_url)
    ):
        send(db, quiz, base_url, manual=False)
    return quiz


def after_payment_task(registration_id: str) -> None:
    """`after_payment`, for the background (its own database session)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        registration = db.get(Registration, registration_id)
        if registration is not None:
            after_payment(db, registration)
    except Exception:
        db.rollback()
        logger.exception("The level check of registration %s could not be prepared.", registration_id)
    finally:
        db.close()


# ------------------------------------------------------------------
# The one reminder
# ------------------------------------------------------------------

def reminders_on(db: Session) -> bool:
    return email_templates.is_on(db, "level_reminder")


def set_reminders(db: Session, on: bool) -> None:
    """Start or stop the reminders (the switch of their email, kept with its wording)."""
    template = email_templates.get(db, "level_reminder")
    email_templates.save(
        db, "level_reminder", subject=template.subject, body=template.body, title=template.title,
        closing=template.closing, style=template.style, show_details=template.show_details, is_active=on,
        image_id=template.image_id, image_position=template.image_position,
        attachment_ids=template.attachment_ids, cta_label=template.cta_label, cta_url=template.cta_url,
    )
    db.commit()


def remind_due(db: Session, base_url: str, now: datetime | None = None, limit: int = 20) -> int:
    """One reminder, never a second, to those who received their level check
    two days ago and did not answer. Returns how many went out."""
    from app.services import weekly_questions      # here: it imports a lot

    if not reminders_on(db) or weekly_questions.is_local_address(base_url):
        return 0
    now = now or datetime.utcnow()
    waiting = (
        db.query(Quiz)
        .filter(Quiz.kind == KIND, Quiz.angle == FORMAT, Quiz.sent_at.isnot(None), Quiz.completed_at.is_(None),
                Quiz.reminded_at.is_(None), Quiz.sent_at <= now - REMIND_AFTER)
        .limit(limit)
        .all()
    )
    sent = 0
    for quiz in waiting:
        registration = db.get(Registration, quiz.registration_id) if quiz.registration_id else None
        candidate = registration.candidate if registration else None
        quiz.reminded_at = now      # once, whatever happens next: a reminder is not tried twice
        db.commit()
        if candidate is None or not candidate.email or not can_have_one(registration) or not before_start(registration):
            continue                # no longer useful: their class started, or they left
        message = email_templates.send(
            db, "level_reminder", candidate=candidate, registration=registration,
            values={"quiz_link": link_of(quiz, base_url), "program_name": quiz.course_name or program_name_of(registration)},
        )
        db.commit()
        sent += email_templates.delivered(message)
    return sent


def reminder_view(quiz: Quiz | None, on: bool, now: datetime | None = None) -> dict | None:
    """What the page says about the reminder of a level check that was sent and not answered."""
    if quiz is None or quiz.sent_at is None or quiz.completed_at is not None:
        return None
    if quiz.reminded_at is not None:
        return {"state": "done", "text": f"Reminded once, {quiz.reminded_at:%d %b}"}
    if not on:
        return {"state": "off", "text": "No reminder: you stopped them"}
    due = quiz.sent_at + REMIND_AFTER
    now = now or datetime.utcnow()
    return {"state": "planned", "text": "One reminder " + ("in the next minutes" if due <= now else f"on {due:%d %b}")}


# ------------------------------------------------------------------
# The answers, one by one
# ------------------------------------------------------------------

def answers_of(quiz: Quiz) -> dict | None:
    """What a candidate answered, in words, and what it tells: for the admin and the professor."""
    if quiz.completed_at is None:
        return None
    items = items_of(quiz)
    try:
        answers = json.loads(quiz.answers or "[]")
    except (TypeError, ValueError):
        answers = []
    answers = list(answers) + [None] * (len(items) - len(answers))
    from app.services import behaviour      # here: it reads sessions and emails too

    try:
        measured = (json.loads(quiz.analysis or "{}").get("timing") or {}).get("per") or {}
    except (TypeError, ValueError, AttributeError):
        measured = {}
    you, topics, checks = [], [], []
    for index, (item, answer) in enumerate(zip(items, answers)):
        kind, options = item.get("type"), item.get("options") or []
        chosen = options[answer] if isinstance(answer, int) and not isinstance(answer, bool) and 0 <= answer < len(options) else None
        if kind in ("experience", "goal"):
            you.append({"question": item.get("question") or "", "answer": chosen or "No answer"})
        elif kind == "topic":
            value = answer if isinstance(answer, int) and 0 <= answer < len(SCALE_SAID) else 0
            topics.append({"title": item.get("question") or "", "value": value, "said": SCALE_SAID[value]})
        elif kind == "check":
            right = options[item["correct_answer"]] if 0 <= item.get("correct_answer", -1) < len(options) else ""
            state = "right" if chosen is not None and answer == item.get("correct_answer") else "wrong" if chosen is not None else "unknown"
            checks.append({"step": int(item.get("step") or 1), "step_name": STEPS_SAID.get(int(item.get("step") or 1), ""),
                           "question": item.get("question") or "", "given": chosen, "right": right, "state": state,
                           "seconds": measured.get(str(index))})
    wrong = [check for check in checks if check["state"] == "wrong"]
    unknown = [check for check in checks if check["state"] == "unknown"]
    right = [check for check in checks if check["state"] == "right"]
    reading = []
    if checks:
        top = max((check["step"] for check in right), default=0)
        if top:
            reading.append(f"Answers correctly up to “{STEPS_SAID[top].lower()}” ({len(right)} of {len(checks)} questions).")
        else:
            reading.append("No check question answered correctly yet: everything will be new.")
        if wrong:
            reading.append(f"{len(wrong)} answered wrongly, with confidence: an idea to correct early, not only something new.")
        if unknown:
            reading.append(f"Said “I don't know yet” to {len(unknown)}: new for them, and they know it.")
    sure = [topic["title"] for topic in topics if topic["value"] == 3]
    if sure:
        reading.append("Feels at ease with: " + ", ".join(sure[:3]) + ".")
    timing = behaviour.timing_of(quiz)
    if timing:
        if timing["fast_wrong"]:
            reading.append(f"{timing['fast_wrong']} answered wrongly in under {behaviour.FAST} seconds: not really read.")
        elif timing["median"] < behaviour.FAST and timing["right"] * 10 >= timing["asked"] * 7:
            reading.append("Very fast and right: this is familiar ground.")
        elif timing["median"] >= behaviour.SLOW:
            reading.append("Takes their time on each question.")
    return {"you": you, "topics": topics, "checks": checks, "reading": reading,
            "timing": timing, "pace": behaviour.pace_words(timing) if timing else None}


# ------------------------------------------------------------------
# What pages show
# ------------------------------------------------------------------

def state_of(quiz: Quiz | None) -> str:
    if quiz is None:
        return "none"
    if quiz.completed_at is not None:
        return "answered"
    return "sent" if quiz.sent_at is not None else "ready"


def row_for(registration: Registration, quiz: Quiz | None, reminders: bool = True) -> dict:
    """One candidate's line on the level checks page."""
    candidate = registration.candidate
    class_group = registration.class_group
    return {
        "registration": registration,
        "candidate": candidate,
        "name": (candidate.full_name or candidate.email) if candidate else "Candidate",
        "program": program_name_of(registration),
        "class_group": class_group,
        "started": class_group is not None and class_group.status != ClassStatus.NOT_STARTED,
        "quiz": quiz,
        "state": state_of(quiz),
        "result": result_of(quiz) if quiz is not None else None,
        "detail": answers_of(quiz) if quiz is not None else None,
        "reminder": reminder_view(quiz, reminders),
        "has_email": bool(candidate and candidate.email),
    }


def overview(db: Session) -> dict:
    """Everything the level checks page shows: each paid candidate still in training, and the totals."""
    registrations = [
        registration for registration in
        db.query(Registration).filter(Registration.payment_status == PaymentStatus.PAID).all()
        if can_have_one(registration)
    ]
    checks = {
        quiz.registration_id: quiz for quiz in
        db.query(Quiz).filter(Quiz.kind == KIND, Quiz.angle == FORMAT).order_by(Quiz.prepared_at.asc()).all()
    }
    on = reminders_on(db)
    rows = [row_for(registration, checks.get(registration.id), on) for registration in registrations]
    order = {"ready": 0, "sent": 1, "answered": 2, "none": 3}
    rows.sort(key=lambda row: (order[row["state"]], row["program"].lower(), row["name"].lower()))
    levels = {key: sum(1 for row in rows if row["result"] and row["result"]["level"] == key) for key in _ORDER}
    return {
        "rows": rows,
        "counts": {state: sum(1 for row in rows if row["state"] == state) for state in order},
        "levels": levels,
        "answered": sum(levels.values()),
        "reminders_on": on,
        "remind_after_days": REMIND_AFTER.days,
    }


def class_overview(db: Session, class_group: ClassGroup) -> dict:
    """The level of a class before it starts: who answered, how the group is
    spread, which topics it knows least, and what that means for the professor."""
    members = class_members(db, class_group.id)
    people = [row_for(registration, current(db, registration.id)) for registration in members]
    results = [person["result"] for person in people if person["result"]]
    levels = {key: sum(1 for result in results if result["level"] == key) for key in _ORDER}

    topics: dict[str, list[int]] = {}
    for result in results:
        for topic in result.get("topics") or []:
            topics.setdefault(topic["title"], []).append(topic["value"])
    topic_rows = [
        {"title": title, "average": sum(values) / len(values), "pct": round(sum(values) / (len(values) * 3) * 100),
         "discover": sum(1 for value in values if value == 0)}
        for title, values in topics.items()
    ]

    advice: list[str] = []
    if results:
        low = levels["beginner"] + levels["basics"]
        high = levels["intermediate"] + levels["advanced"]
        if low and high and min(low, high) * 3 >= len(results):
            advice.append("The levels are mixed: pair those who already practised with those who start, "
                          "and keep extra exercises ready for the fastest.")
        elif levels["beginner"] * 2 > len(results):
            advice.append("Most of the group starts from zero: take the first sessions slowly and check the bases often.")
        elif high * 2 > len(results):
            advice.append("Most of the group has already practised: the first weeks can go faster.")
        ranked = sorted(topic_rows, key=lambda row: row["average"])
        if ranked and ranked[0]["average"] < 1:
            advice.append("Least known: " + ", ".join(row["title"] for row in ranked[:2] if row["average"] < 1) + ".")
        if ranked and ranked[-1]["average"] >= 2:
            advice.append("Already familiar to most: " + ", ".join(row["title"] for row in ranked[::-1][:2] if row["average"] >= 2) + ".")
        over = [person["name"] for person in people if person["result"] and person["result"].get("calibration") == "over"]
        if over:
            advice.append("To check in the first session, they describe themselves as more advanced than their answers show: "
                          + ", ".join(over) + ".")
    return {
        "people": people,
        "total": len(people),
        "answered": len(results),
        "sent": sum(1 for person in people if person["state"] == "sent"),
        "ready": sum(1 for person in people if person["state"] == "ready"),
        "none": sum(1 for person in people if person["state"] == "none"),
        "levels": levels,
        "topics": topic_rows,
        "advice": advice,
    }

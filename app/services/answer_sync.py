"""From a Google Form to Firebase to here: the candidates' weekly answers.

The road, the same as for registrations:

    Google Form  →  Firebase (weekly_answers)  →  this application

1. When a week's questions are sent, the application creates the week's
   Google Form (the questions, then "how did the week go?"), and each
   candidate gets its link by email, with their address already filled in.
2. Every few minutes the new answers of each form are written to Firebase,
   under weekly_answers/<form>/<answer>.
3. Everything in weekly_answers that is not here yet is imported: the
   candidate's result is worked out and their words are analysed.

Step 3 does not care who wrote to Firebase, so a form connected to Firebase
some other way (the way the registration form is) arrives here too, as long
as it writes the same fields.

Until Google Forms is switched on for the project (see google_forms), the
candidates answer on this application's own page, and nothing here runs.
"""
from __future__ import annotations

import json
import logging
import random
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.classgroup import ClassGroup
from app.models.course_material import ClassWeek
from app.models.quiz import Quiz
from app.models.registration import Registration, RegistrationStatus
from app.services import google_forms, week_insights

logger = logging.getLogger(__name__)

NODE = "weekly_answers"
KEEP_COLLECTING = timedelta(days=45)     # a form is read for this long after it was sent


# ------------------------------------------------------------------
# Firebase (the same database the registrations come from)
# ------------------------------------------------------------------

def firebase_ready() -> bool:
    try:
        import firebase_admin

        return bool(firebase_admin._apps)
    except Exception:
        return False


def _put(path: str, value: dict) -> None:
    from firebase_admin import db as firebase

    firebase.reference(path).set(value)


def _read(path: str):
    from firebase_admin import db as firebase

    return firebase.reference(path).get()


def status() -> dict:
    """Where the road stands: {"google", "firebase", "ready"}."""
    google = google_forms.status()
    firebase = firebase_ready()
    return {"google": google, "firebase": firebase, "ready": bool(google["ready"] and firebase)}


def ready() -> bool:
    return status()["ready"]


# ------------------------------------------------------------------
# The week's form
# ------------------------------------------------------------------

def _map(week: ClassWeek | None) -> dict:
    try:
        loaded = json.loads(week.form_map or "{}") if week is not None else {}
    except (TypeError, ValueError):
        loaded = {}
    return loaded if isinstance(loaded, dict) else {}


def form_of(week: ClassWeek | None) -> dict | None:
    """The week's Google Form for the pages, or None when it has none."""
    if week is None or not week.form_id:
        return {"error": week.form_error} if week is not None and week.form_error else None
    mapping = _map(week)
    return {
        "id": week.form_id, "url": week.form_url, "error": week.form_error, "checked_at": week.form_checked_at,
        "unmatched": mapping.get("unmatched", []), "answers": len(mapping.get("seen", [])),
    }


def _items(questions: list[dict]) -> list[dict]:
    """What the form asks: who answers, the questions of the course, then how the week went."""
    items = [{"key": "email", "title": "Votre adresse e-mail (celle de votre inscription)", "kind": "text", "required": True}]
    for index, question in enumerate(questions):
        items.append({
            "key": f"q{index}", "title": question["question"], "kind": "choice", "required": True,
            "options": list(dict.fromkeys(" ".join(str(option).split()) for option in question["options"])),
        })
    asked = week_insights.QUESTIONS
    for key in ("mood", "pace", "confidence"):
        items.append({"key": key, "title": asked[key], "kind": "choice", "required": True,
                      "options": [words for _, words in week_insights.choices(key)]})
    items.append({"key": "hard", "title": asked["hard"], "kind": "paragraph", "required": False})
    items.append({"key": "message", "title": asked["message"] + " (facultatif)", "kind": "paragraph", "required": False})
    return items


def ensure_form(db: Session, class_group: ClassGroup, week: ClassWeek, questions: list[dict], topic: str) -> bool:
    """Create the week's form when it does not exist yet. False when Google
    refused: the reason is kept on the week and the candidates use the
    application's own page instead."""
    if week.form_id:
        return True
    program = class_group.program.name if class_group.program else class_group.name
    try:
        form = google_forms.create(
            f"{program} · semaine {week.week_number} · {class_group.name}",
            (f"Semaine {week.week_number} : {topic}.\n" if topic else "")
            + "Quelques questions sur ce qui a été vu en cours, puis trois clics pour nous dire comment s'est passée "
              "votre semaine. Ce n'est pas un examen : vos réponses aident votre formateur à adapter la suite.",
            _items(questions),
        )
    except google_forms.FormsError as exc:
        week.form_error = str(exc)[:500]
        db.commit()
        logger.warning("Google Form for class %s week %s: %s", class_group.id, week.week_number, exc)
        return False
    week.form_id, week.form_url, week.form_error = form["form_id"], form["url"], None
    week.form_map = json.dumps({"ids": form["ids"], "questions": questions, "seen": [], "unmatched": []}, ensure_ascii=False)
    db.commit()
    return True


def link_for(week: ClassWeek, email: str) -> str:
    """The form's address for one candidate, their email already written."""
    return google_forms.prefilled(week.form_url, _map(week).get("ids", {}).get("email"), email)


def questions_of(week: ClassWeek) -> list[dict]:
    return [item for item in _map(week).get("questions", []) if isinstance(item, dict)]


# ------------------------------------------------------------------
# Step 2: Google Form → Firebase
# ------------------------------------------------------------------

def _record(week: ClassWeek, class_group: ClassGroup | None, answer: dict) -> dict:
    """One answer the way it is written to Firebase."""
    mapping = _map(week)
    ids, values = mapping.get("ids", {}), answer["values"]
    said = lambda key: values.get(ids.get(key, ""), "")
    return {
        "form_id": week.form_id, "response_id": answer["id"],
        "class_id": week.class_group_id, "class_name": class_group.name if class_group else "",
        "week": week.week_number, "email": (said("email") or answer.get("email") or "").strip().lower(),
        "submitted_at": answer["at"] or "",
        "answers": {f"q{index}": said(f"q{index}") for index in range(len(mapping.get("questions", [])))},
        "mood": said("mood"), "pace": said("pace"), "confidence": said("confidence"),
        "hard": said("hard"), "message": said("message"), "source": "google_form",
    }


def collect(db: Session, now: datetime | None = None) -> int:
    """Write the new answers of every open form to Firebase. Returns how many."""
    now = now or datetime.now(timezone.utc)
    weeks = db.query(ClassWeek).filter(ClassWeek.form_id.isnot(None)).all()
    written = 0
    for week in weeks:
        sent = week.sent_at.replace(tzinfo=timezone.utc) if week.sent_at and week.sent_at.tzinfo is None else week.sent_at
        if sent is not None and now - sent > KEEP_COLLECTING:
            continue
        since = week.form_checked_at
        try:
            found = google_forms.answers(week.form_id, since=since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else None)
        except Exception as exc:
            week.form_error = f"The answers could not be read: {exc}"[:500]
            db.commit()
            continue
        class_group = db.get(ClassGroup, week.class_group_id)
        for answer in found:
            if not answer.get("id"):
                continue
            _put(f"{NODE}/{week.form_id}/{answer['id']}", _record(week, class_group, answer))
            written += 1
        # One minute of overlap: an answer is never missed, and one seen twice is recognised.
        week.form_checked_at = (now - timedelta(minutes=1)).replace(tzinfo=None)
        week.form_error = None
        db.commit()
    return written


# ------------------------------------------------------------------
# Step 3: Firebase → here
# ------------------------------------------------------------------

def _choice(text: str, options: list) -> int | None:
    wanted = " ".join(str(text or "").split()).lower()
    if not wanted:
        return None
    for index, option in enumerate(options):
        if " ".join(str(option).split()).lower() == wanted:
            return index
    return None


def import_record(db: Session, record: dict) -> str:
    """Bring one answer of Firebase into the application.

    Returns "imported", "known" (already here), "no_form" (not a form of this
    application) or "unknown" (the address is nobody's in the class).
    """
    from app.services import weekly_questions

    response_id = str(record.get("response_id") or "").strip()
    week = db.query(ClassWeek).filter(ClassWeek.form_id == str(record.get("form_id") or "")).first()
    if week is None or not response_id:
        return "no_form"
    mapping = _map(week)
    if response_id in mapping.get("seen", []):
        return "known"

    def remember(unmatched: dict | None = None) -> None:
        mapping.setdefault("seen", []).append(response_id)
        if unmatched:
            mapping.setdefault("unmatched", []).append(unmatched)
        week.form_map = json.dumps(mapping, ensure_ascii=False)
        db.commit()

    email = str(record.get("email") or "").strip().lower()
    registration = next((
        item for item in db.query(Registration).filter(
            Registration.class_group_id == week.class_group_id, Registration.status != RegistrationStatus.CANCELLED,
        ).all()
        if item.candidate is not None and (item.candidate.email or "").strip().lower() == email
    ), None)
    if registration is None:
        remember({"email": email or "(no address)", "at": str(record.get("submitted_at") or ""), "id": response_id})
        return "unknown"

    questions = questions_of(week)
    quiz = db.query(Quiz).filter(
        Quiz.kind == "weekly", Quiz.registration_id == registration.id,
        Quiz.class_group_id == week.class_group_id, Quiz.week_number == week.week_number,
    ).first()
    if quiz is not None and quiz.completed_at is not None:
        remember()
        return "known"
    if quiz is None:        # they answered a link someone passed on
        class_group = db.get(ClassGroup, week.class_group_id)
        program = class_group.program.name if class_group and class_group.program else (class_group.name if class_group else "Formation")
        quiz = Quiz(
            candidate_id=registration.candidate_id, registration_id=registration.id,
            course_name=f"{program} — semaine {week.week_number}", angle="weekly", level="week", kind="weekly",
            class_group_id=week.class_group_id, week_number=week.week_number, access_token=secrets.token_urlsafe(24),
            answers="[]", score=0.0, sent_at=datetime.utcnow(), prepared_at=datetime.utcnow(),
        )
        db.add(quiz)
    quiz.questions = json.dumps(questions, ensure_ascii=False)
    given = record.get("answers") if isinstance(record.get("answers"), dict) else {}
    answers = [_choice(given.get(f"q{index}"), question.get("options", [])) for index, question in enumerate(questions)]
    feedback = week_insights.clean_feedback({
        "mood": week_insights.from_words("mood", record.get("mood")),
        "pace": week_insights.from_words("pace", record.get("pace")),
        "confidence": week_insights.from_words("confidence", record.get("confidence")),
        "hard": record.get("hard"), "message": record.get("message"),
    })
    weekly_questions.grade(db, quiz, answers, feedback=feedback, source="google_form", external_id=response_id)
    remember()
    return "imported"


def import_pending(db: Session) -> int:
    """Import everything Firebase holds that is not here yet. Returns how many."""
    stored = _read(NODE)
    if not isinstance(stored, dict):
        return 0
    imported = 0
    for form_id, records in stored.items():
        if not isinstance(records, dict):
            continue
        for response_id, record in records.items():
            if not isinstance(record, dict):
                continue
            record = {"form_id": form_id, "response_id": response_id, **record}
            try:
                imported += import_record(db, record) == "imported"
            except Exception:
                db.rollback()
                logger.exception("Answer %s of form %s could not be imported.", response_id, form_id)
    return imported


def tick(db: Session) -> int:
    """The whole road, once. Returns how many answers arrived."""
    if not firebase_ready():
        return 0
    if google_forms.ready():
        collect(db)
    return import_pending(db)


def pick_for_form(pool: list, count: int, class_id: str, week_number: int, picker) -> list:
    """The questions of the week's form: the same for everyone, chosen once."""
    return picker(list(pool), count, random.Random(f"form-{class_id}-{week_number}"))

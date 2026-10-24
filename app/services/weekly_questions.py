"""Questions about what a class learned in a week.

How it works:

1. The system reads the week's course files and notes (see course_materials).
2. The local model writes multiple-choice questions, one passage of the
   course at a time, and must quote the sentence that gives each answer.
3. Every question is checked without trusting the model's word: the quoted
   sentence has to be in the course, and the model, reading the passage
   again without the answer, has to pick the same choice. A question that
   fails a check waits for the professor ("review") and is never sent.
4. Each candidate of the class gets a few of the ready questions by email,
   answers them on a private page, and the result says how well the week
   was understood.
"""
from __future__ import annotations

import json
import logging
import random
import re
import secrets
from datetime import datetime, timedelta, timezone

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.candidate import Candidate
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.classsession import ClassSession
from app.models.course_material import ClassMaterial, ClassWeek, WeekQuestion
from app.models.notification import Notification
from app.models.quiz import Quiz
from app.models.registration import Registration, RegistrationStatus
from app.services import course_materials, email_assistant, email_templates, week_insights
from app.services.schedule_utils import local_now

logger = logging.getLogger(__name__)

PASSAGE_CHARS = 1300            # what the model reads at once
MIN_PASSAGE_CHARS = 220
MAX_PASSAGES = 5                # per writing run
QUESTIONS_PER_PASSAGE = 2
MAX_QUESTIONS_PER_WEEK = 30
DEFAULT_PER_CANDIDATE = 5
MIN_TO_LEARN = 120              # characters of course needed before anything is written

STRONG, PARTIAL = 80, 50        # score, in percent, from which a week counts as understood

_SYSTEM = (
    "You are a teacher writing a short check of understanding for your own learners. "
    "You only use what the course passage says. You answer with one JSON object and nothing else."
)
_FRENCH = {"le", "la", "les", "des", "une", "est", "et", "pour", "dans", "que", "qui", "sur", "avec", "vous", "nous", "sont", "du", "au", "ce", "cette"}
_ENGLISH = {"the", "is", "are", "and", "for", "with", "that", "this", "you", "of", "to", "in", "on", "it", "as", "be", "can", "from", "by", "an"}
_ABOUT_THE_TEXT = re.compile(r"\b(passage|excerpt|extrait|the text above|ce texte|le texte|page \d+|slide \d+|according to the (document|passage))\b", re.IGNORECASE)
_LETTERS = re.compile(r"[^\W\d_]")
_NOT_AN_ANSWER = {"why", "quote", "answer", "explanation", "question", "...", "n/a", "none"}
_NEGATIONS = {"no", "not", "ne", "n", "pas", "non", "ni"}


# ------------------------------------------------------------------
# Levels of understanding
# ------------------------------------------------------------------

def level_of(score: float | None) -> dict:
    """How a result reads, for pages: {"key", "label", "tone"}."""
    if score is None:
        return {"key": "none", "label": "No answer yet", "tone": "muted"}
    if score >= STRONG:
        return {"key": "strong", "label": "Understood", "tone": "ok"}
    if score >= PARTIAL:
        return {"key": "partial", "label": "Partly understood", "tone": "warn"}
    return {"key": "weak", "label": "Needs support", "tone": "bad"}


# ------------------------------------------------------------------
# Reading the course
# ------------------------------------------------------------------

def language_of(text: str) -> str:
    words = set(re.findall(r"[a-zàâçéèêëîïôûùüÿœ']+", (text or "").lower()))
    return "French" if len(words & _FRENCH) > len(words & _ENGLISH) else "English"


def split_passages(text: str) -> list[str]:
    """Cut a course text into passages the model can read at once."""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text or "") if block.strip()]
    pieces: list[str] = []
    for block in blocks:
        while len(block) > PASSAGE_CHARS:
            cut = max(block.rfind(". ", 0, PASSAGE_CHARS), block.rfind("\n", 0, PASSAGE_CHARS))
            cut = cut + 1 if cut > PASSAGE_CHARS // 2 else PASSAGE_CHARS
            pieces.append(block[:cut].strip())
            block = block[cut:].strip()
        if block:
            pieces.append(block)
    passages: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > PASSAGE_CHARS:
            passages.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        passages.append(current)
    return [passage for passage in passages if _teaches(passage)]


def _teaches(passage: str) -> bool:
    """Whether a passage has sentences to learn from (not a cover page or a list of numbers)."""
    letters = len(_LETTERS.findall(passage))
    return len(passage) >= MIN_PASSAGE_CHARS and letters >= len(passage) * 0.55


def _spread(items: list, count: int) -> list:
    """`count` items taken evenly over the whole list."""
    if len(items) <= count:
        return list(items)
    step = len(items) / count
    return [items[int(index * step + step / 2)] for index in range(count)]


def plan_passages(sources: list[dict], done_keys: set[str], used_quotes: list[str] | None = None) -> list[dict]:
    """Passages to write questions from: every source not used yet gets its share.

    `used_quotes` (sentences earlier questions rely on) steers a new run
    towards parts of the course that have no question yet.
    """
    used = [" ".join(quote.split()).lower() for quote in (used_quotes or []) if quote]
    waiting = []
    for source in sources:
        if source["key"] in done_keys:
            continue
        passages = split_passages(source["text"])
        if not passages and len(source["text"].strip()) >= 60:
            passages = [source["text"].strip()[:PASSAGE_CHARS]]     # a short note still counts
        untouched = [
            passage for passage in passages
            if not any(quote in " ".join(passage.split()).lower() for quote in used)
        ]
        passages = untouched or passages
        if passages:
            waiting.append((source, passages))
    if not waiting:
        return []
    share = max(1, MAX_PASSAGES // len(waiting))
    planned = []
    for source, passages in waiting:
        for passage in _spread(passages, share):
            planned.append({"key": source["key"], "name": source["name"], "text": passage})
    return planned[:MAX_PASSAGES]


# ------------------------------------------------------------------
# Writing and checking with the local model
# ------------------------------------------------------------------

def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _answer_index(value, options: list[str]) -> int | None:
    """The model's "answer" as a position in the choices."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value < len(options) else None
    text = str(value or "").strip()
    if text.isdigit():
        return int(text) if int(text) < len(options) else None
    if len(text) == 1 and text.upper() in "ABCDEF"[: len(options)]:
        return "ABCDEF".index(text.upper())
    lowered = [option.lower() for option in options]
    return lowered.index(text.lower()) if text.lower() in lowered else None


def _for_model(passage: str) -> str:
    """The passage as the model reads it: straight quotes confuse its JSON answers."""
    return passage.replace('"', "'").replace("“", "'").replace("”", "'").replace("«", "'").replace("»", "'")


def _facts(passage: str, topic: str, language: str, count: int) -> list[dict]:
    """Questions with their short answer, as a teacher would ask them aloud."""
    prompt = (
        (f"Subject of the week: {topic}\n\n" if topic else "")
        + f"Course passage:\n<<<\n{_for_model(passage)}\n>>>\n\n"
        f"Write {count} questions, in {language}, that a teacher would ask to check that a learner understood "
        "this passage, each with its answer.\n"
        "- The answer is stated in the passage. It is short: a few words, one sentence at most.\n"
        "- Ask about an idea, a step, a tool, a reason or a difference. Never ask about the wording, a page or 'the passage'.\n"
        "- The questions are about different points of the passage.\n"
        "- 'quote' is the sentence of the passage that contains the answer, copied word for word.\n\n"
        'Answer with: {"items": [{"question": "...", "answer": "...", "quote": "..."}]}'
    )
    data = email_assistant.llm_json(_SYSTEM, prompt, max_tokens=150 * count + 60) or {}
    found = data.get("items") or data.get("questions")
    return [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []


def _wrong_answers(question: str, answer: str, passage: str, language: str) -> list[str]:
    """Believable wrong answers for a question whose right answer is known."""
    prompt = (
        f"Course passage:\n<<<\n{_for_model(passage)}\n>>>\n\n"
        f"Question: {question}\nCorrect answer: {answer}\n\n"
        f"Write 3 wrong answers, in {language}, for a multiple-choice quiz. Each one:\n"
        "- is false according to the passage,\n"
        f"- looks like the correct answer: same kind of thing, at most {max(4, len(answer.split()) + 3)} words,\n"
        "- is a mistake a learner who did not study could make,\n"
        "- is not the correct answer said differently.\n\n"
        'Answer with: {"wrong": ["...", "...", "..."]}'
    )
    data = email_assistant.llm_json(_SYSTEM, prompt, max_tokens=140) or {}
    found = data.get("wrong")
    if not isinstance(found, list):
        return []
    wrong = []
    for item in found:
        text = re.sub(r"^[A-Fa-f1-4][).:]\s+", "", _clean(item, 240))
        if len(text) < 1 or text.lower() in _NOT_AN_ANSWER:
            continue
        if _says_the_same(text, answer) or any(text.lower() == known.lower() for known in wrong):
            continue
        wrong.append(text)
    return wrong[:3]


def _asked_already(question: str, known: list[str]) -> bool:
    """Whether the same question, give or take its wording, is already in the bank.
    Two questions that only share their opening words are different questions."""
    return any(fuzz.token_sort_ratio(question.lower(), other.lower()) >= 92 for other in known)


def _key_words(text: str) -> set[str]:
    """The words that carry a short answer's meaning, negations included."""
    words = re.findall(r"[0-9a-zàâçéèêëîïôûùüÿœ]+", (text or "").lower())
    return {word for word in words if len(word) >= 3 or word in _NEGATIONS}


def _says_the_same(choice: str, answer: str) -> bool:
    """Whether a wrong choice is in fact the right answer in other words:
    nearly the same text, or a sentence that contains everything the answer says."""
    if fuzz.ratio(choice.lower(), answer.lower()) >= 90:
        return True
    needed = _key_words(answer)
    return bool(needed) and needed <= _key_words(choice)


def _tidy_choice(text: str) -> str:
    """Choices written alike, so the right one does not stand out by its form."""
    text = text.strip().rstrip(".")
    return text[:1].upper() + text[1:] if text else text


def _shape(raw: dict) -> dict | None:
    """A question and its answer as the model gave them, cleaned; None when unusable."""
    question = _clean(raw.get("question"), 400)
    answer = _clean(raw.get("answer"), 240)
    if len(question) < 12 or not answer or answer.lower() in _NOT_AN_ANSWER:
        return None
    if not question.endswith("?"):
        question = question.rstrip(".: ") + "?"
    return {"question": question, "answer_text": answer, "quote": _clean(raw.get("quote"), 500)}


def supported_by(quote: str, passage: str) -> bool:
    """Whether the sentence the model quoted really is in the course passage."""
    quote = " ".join((quote or "").split())
    if len(quote) < 15:
        return False
    return fuzz.partial_ratio(quote.lower(), " ".join(passage.split()).lower()) >= 88


def second_reading(passage: str, questions: list[dict]) -> list[int | None]:
    """The model answers the questions from the passage, without being told the answers."""
    if not questions:
        return []
    listed = []
    for number, item in enumerate(questions, 1):
        choices = "\n".join(f"   {index}. {option}" for index, option in enumerate(item["options"]))
        listed.append(f"Question {number}: {item['question']}\n{choices}")
    prompt = (
        f"Course passage:\n<<<\n{_for_model(passage)}\n>>>\n\n"
        + "\n\n".join(listed)
        + "\n\nFor each question, give the number of the choice the passage supports. "
        f'Answer with: {{"answers": [{", ".join("0" for _ in questions)}]}} (one number per question, in order).'
    )
    data = email_assistant.llm_json(_SYSTEM, prompt, max_tokens=40 + 12 * len(questions)) or {}
    answers = data.get("answers")
    if not isinstance(answers, list):
        return [None] * len(questions)
    picked = [_answer_index(value, item["options"]) for value, item in zip(answers, questions)]
    return picked + [None] * (len(questions) - len(picked))


def _judge(item: dict, passage: str, second: int | None) -> tuple[str, str | None]:
    """(status, reason) of a freshly written question."""
    if _ABOUT_THE_TEXT.search(item["question"]):
        return "review", "It asks about the document instead of the subject"
    if not supported_by(item["quote"], passage):
        return "review", "The sentence it relies on was not found in the course"
    if second is None:
        return "review", "It could not be checked a second time"
    if second != item["answer"]:
        return "review", "Reading the course again, the system chose another answer"
    return "ready", None


def write_for_passage(passage: dict, topic: str, existing: list[str], rng: random.Random) -> list[dict]:
    """Checked questions for one passage, ready to be stored.

    The right answer is known from the start (the model is asked for a
    question and its answer, then separately for wrong answers), so it cannot
    point at the wrong choice. What remains to check is that the answer is in
    the course and that the wrong choices are really wrong.
    """
    text = passage["text"]
    language = language_of(text)
    shaped = []
    for raw in _facts(text, topic, language, QUESTIONS_PER_PASSAGE):
        item = _shape(raw)
        if item is None:
            continue
        if _asked_already(item["question"], existing):
            continue
        wrong = _wrong_answers(item["question"], item["answer_text"], text, language)
        if len(wrong) < 2:
            continue
        right = _tidy_choice(item["answer_text"])
        options = [right, *(_tidy_choice(choice) for choice in wrong)]
        rng.shuffle(options)
        item["options"] = options
        item["answer"] = options.index(right)
        # What the candidate reads after answering: the course's own sentence.
        item["explanation"] = item["quote"] if supported_by(item["quote"], text) else ""
        existing.append(item["question"])
        shaped.append(item)
    for item, second in zip(shaped, second_reading(text, shaped)):
        item["status"], item["reason"] = _judge(item, text, second)
        item["source"] = passage
    return shaped


# ------------------------------------------------------------------
# The question bank of a week
# ------------------------------------------------------------------

def questions_of(db: Session, class_id: str, week_number: int) -> list[WeekQuestion]:
    return (
        db.query(WeekQuestion)
        .filter(WeekQuestion.class_group_id == class_id, WeekQuestion.week_number == week_number)
        .order_by(WeekQuestion.created_at.asc())
        .all()
    )


def ready_questions(db: Session, class_id: str, week_number: int) -> list[WeekQuestion]:
    return [item for item in questions_of(db, class_id, week_number) if item.status == "ready"]


def queue(db: Session, class_group: ClassGroup, week_number: int, mode: str = "new") -> ClassWeek:
    """Ask for questions to be written for a week.

    "new" covers the course files that have no question yet, "more" goes
    back to every file for other parts of it, "fresh" first removes the
    questions the system wrote (the professor's own are kept).
    """
    week = course_materials.get_week(db, class_group, week_number)
    if mode == "fresh":
        db.query(WeekQuestion).filter(
            WeekQuestion.class_group_id == class_group.id,
            WeekQuestion.week_number == week_number,
            WeekQuestion.author == "system",
        ).delete(synchronize_session=False)
    week.questions_status = "queued_more" if mode == "more" else "queued"
    week.questions_note = None
    week.questions_updated_at = datetime.now(timezone.utc)
    db.commit()
    return week


def write_week(db: Session, week: ClassWeek) -> int:
    """Write the questions of one queued week. Returns how many were kept."""
    class_id, number = week.class_group_id, week.week_number
    sources = course_materials.sources(db, class_id, number)
    learned = sum(len(source["text"]) for source in sources)
    existing = questions_of(db, class_id, number)

    def finish(status: str, note: str | None) -> None:
        week.questions_status = status
        week.questions_note = note
        week.questions_updated_at = datetime.now(timezone.utc)
        db.commit()

    if learned < MIN_TO_LEARN:
        finish("idle" if not existing else "ready",
               None if existing else "There is not enough to learn from yet. Add a course file, or write "
                                     "a few lines about what was covered this week.")
        return 0
    if len(existing) >= MAX_QUESTIONS_PER_WEEK:
        finish("ready", f"This week already has {len(existing)} questions.")
        return 0

    more = week.questions_status == "queued_more"
    done_keys = set() if more else {
        item.material_id for item in existing if item.author == "system" and item.material_id
    }
    planned = plan_passages(sources, done_keys, [item.source_excerpt for item in existing])
    if not planned:
        finish("ready" if existing else "idle",
               None if existing else "Nothing in the course files could be turned into questions. "
                                     "Write a few lines about what was covered this week.")
        return 0

    # Saved before the model is asked: writing takes minutes on this machine.
    week.questions_status = "writing"
    week.questions_note = None
    db.commit()

    topic = course_materials.week_topic(db, class_id, number)
    known = [item.question for item in existing]
    rng = random.Random(f"{class_id}-{number}-{len(existing)}")
    kept = answered = 0
    for passage in planned:
        written = write_for_passage(passage, topic, known, rng)
        answered += bool(written)
        for item in written:
            db.add(WeekQuestion(
                class_group_id=class_id, week_number=number,
                question=item["question"], options=json.dumps(item["options"], ensure_ascii=False),
                correct_answer=item["answer"], explanation=item["explanation"] or None,
                material_id=passage["key"], source_name=passage["name"],
                source_excerpt=item["quote"] or None,
                status=item["status"], review_reason=item["reason"], author="system",
            ))
            kept += 1
        db.commit()     # kept passage by passage, so a stop loses little

    if kept == 0:
        finish("failed" if not existing else "ready",
               "The assistant did not answer, so no question could be written. Ask again in a moment; "
               "if it keeps failing, Settings says how to start it." if not answered else
               "The model's questions were not usable this time. Ask again, or write your own.")
        return 0

    ready = len(ready_questions(db, class_id, number))
    finish("ready", None)
    class_group = db.get(ClassGroup, class_id)
    waiting = len([item for item in questions_of(db, class_id, number) if item.status == "review"])
    db.add(Notification(
        recipient_role="admin", category="WEEK_QUESTIONS",
        title=f"Week {number} questions written: {class_group.name if class_group else 'class'}",
        message=(
            f"{ready} question(s) are ready to send"
            + (f", {waiting} wait for your review" if waiting else "") + "."
        ),
        action_url=f"/classes/{class_id}/course?week={number}",
        program_id=class_group.program_id if class_group else None,
    ))
    db.commit()
    return kept


def release_interrupted(db: Session) -> int:
    """Weeks left "writing" by a server that stopped go back in the queue."""
    count = (
        db.query(ClassWeek)
        .filter(ClassWeek.questions_status == "writing")
        .update({"questions_status": "queued"}, synchronize_session=False)
    )
    if count:
        db.commit()
    return count


def work_one(db: Session) -> bool:
    """Write the questions of the week that has waited longest. False when none waits."""
    week = (
        db.query(ClassWeek)
        .filter(ClassWeek.questions_status.in_(("queued", "queued_more")))
        .order_by(ClassWeek.questions_updated_at.asc())
        .first()
    )
    if week is None:
        return False
    try:
        write_week(db, week)
    except Exception:
        db.rollback()
        logger.exception("Writing the questions of week %s failed.", week.id)
        week = db.get(ClassWeek, week.id)
        if week is not None:
            week.questions_status = "failed"
            week.questions_note = "Something went wrong while writing the questions. Ask again."
            db.commit()
    return True


# ------------------------------------------------------------------
# The professor's own changes
# ------------------------------------------------------------------

def read_question_form(form) -> dict:
    """A question typed on the page. Raises ValueError with a message for the professor."""
    question = _clean(form.get("question"), 400)
    options = [_clean(form.get(f"option_{index}"), 240) for index in range(5)]
    try:
        marked = int(form.get("correct", ""))
    except (TypeError, ValueError):
        raise ValueError("Choose which answer is the correct one.")
    if not 0 <= marked < len(options) or not options[marked]:
        raise ValueError("The answer marked as correct is empty.")
    kept = [(index, option) for index, option in enumerate(options) if option]
    if len(question) < 8:
        raise ValueError("Write the question.")
    if len(kept) < 2:
        raise ValueError("Give at least two possible answers.")
    if len({option.lower() for _, option in kept}) != len(kept):
        raise ValueError("Two answers are the same.")
    return {
        "question": question,
        "options": [option for _, option in kept],
        "answer": [index for index, _ in kept].index(marked),
        "explanation": _clean(form.get("explanation"), 500),
    }


def add_question(db: Session, class_group: ClassGroup, week_number: int, data: dict) -> WeekQuestion:
    course_materials.check_week(class_group, week_number)
    course_materials.get_week(db, class_group, week_number)
    row = WeekQuestion(
        class_group_id=class_group.id, week_number=week_number,
        question=data["question"], options=json.dumps(data["options"], ensure_ascii=False),
        correct_answer=data["answer"], explanation=data["explanation"] or None,
        status="ready", author="admin",
    )
    db.add(row)
    db.commit()
    return row


def save_question(db: Session, question: WeekQuestion, data: dict) -> None:
    """The professor corrected a question: it is theirs now, and ready."""
    question.question = data["question"]
    question.options = json.dumps(data["options"], ensure_ascii=False)
    question.correct_answer = data["answer"]
    question.explanation = data["explanation"] or None
    question.status = "ready"
    question.review_reason = None
    question.author = "admin"
    db.commit()


def set_status(db: Session, question: WeekQuestion, status: str) -> None:
    if status not in ("ready", "off"):
        raise ValueError("Unknown status.")
    question.status = status
    if status == "ready":
        question.review_reason = None
    db.commit()


# ------------------------------------------------------------------
# Sending a week's questions to the class
# ------------------------------------------------------------------

def members_of(db: Session, class_id: str) -> list[Registration]:
    return (
        db.query(Registration)
        .filter(Registration.class_group_id == class_id, Registration.status != RegistrationStatus.CANCELLED)
        .order_by(Registration.registration_date.asc())
        .all()
    )


def quizzes_of(db: Session, class_id: str, week_number: int | None = None) -> list[Quiz]:
    query = db.query(Quiz).filter(Quiz.kind == "weekly", Quiz.class_group_id == class_id)
    if week_number is not None:
        query = query.filter(Quiz.week_number == week_number)
    return query.order_by(Quiz.prepared_at.asc()).all()


def public_link(base_url: str, quiz: Quiz) -> str:
    return f"{base_url.rstrip('/')}/learn/{quiz.access_token}"


def is_local_address(base_url: str) -> bool:
    """Whether candidates cannot reach this address from their own device."""
    return bool(re.match(r"https?://(127\.|localhost|0\.0\.0\.0|\[::1\])", (base_url or "").strip(), re.IGNORECASE))


def _pick(pool: list[WeekQuestion], count: int, rng: random.Random) -> list[WeekQuestion]:
    """`count` questions for one candidate, from as many course files as possible."""
    by_source: dict[str, list[WeekQuestion]] = {}
    for question in pool:
        by_source.setdefault(question.material_id or question.id, []).append(question)
    groups = list(by_source.values())
    for group in groups:
        rng.shuffle(group)
    rng.shuffle(groups)
    picked = []
    while len(picked) < count and any(groups):
        for group in groups:
            if group and len(picked) < count:
                picked.append(group.pop())
    rng.shuffle(picked)
    return picked


def send_week(db: Session, class_group: ClassGroup, week_number: int, *, base_url: str,
              manual: bool = True) -> dict:
    """Email each candidate of the class their questions of the week.

    A candidate who already received them is skipped; one whose email failed
    earlier is tried again with the same questions. Returns
    {"sent", "failed", "already", "pool"} or {"error": message}.
    """
    pool = ready_questions(db, class_group.id, week_number)
    if not pool:
        return {"error": "No question is ready for this week yet."}
    members = members_of(db, class_group.id)
    if not members:
        return {"error": "This class has no candidate yet."}
    if not manual and not email_templates.is_on(db, "weekly_questions"):
        return {"error": "The “Weekly questions” email is switched off."}

    per_candidate = max(1, min(class_group.weekly_questions_count or DEFAULT_PER_CANDIDATE, len(pool)))
    program = class_group.program.name if class_group.program else class_group.name
    topic = course_materials.week_topic(db, class_group.id, week_number)
    existing = {quiz.registration_id: quiz for quiz in quizzes_of(db, class_group.id, week_number)}
    outcome = {"sent": 0, "failed": 0, "already": 0, "pool": len(pool), "form": False}

    # With Google Forms and Firebase connected, the week has one form for the
    # whole class and the answers come back through Firebase (see answer_sync).
    from app.services import answer_sync

    week_row = course_materials.get_week(db, class_group, week_number)
    form_questions = None
    if week_row.form_id or answer_sync.ready():
        if not week_row.form_id:
            chosen = answer_sync.pick_for_form(pool, per_candidate, class_group.id, week_number, _pick)
            answer_sync.ensure_form(db, class_group, week_row, [_as_asked(question) for question in chosen], topic)
        if week_row.form_id:
            form_questions = answer_sync.questions_of(week_row)
            outcome["form"] = True

    for registration in members:
        candidate = registration.candidate
        if candidate is None or not candidate.email:
            continue
        quiz = existing.get(registration.id)
        if quiz is not None and quiz.sent_at is not None:
            outcome["already"] += 1
            continue
        if quiz is None:
            rng = random.Random(f"{registration.id}-{week_number}-{len(pool)}")
            asked = form_questions or [_as_asked(question) for question in _pick(list(pool), per_candidate, rng)]
            quiz = Quiz(
                candidate_id=candidate.id, registration_id=registration.id,
                course_name=f"{program} — semaine {week_number}", angle="weekly", level="week",
                kind="weekly", class_group_id=class_group.id, week_number=week_number,
                access_token=secrets.token_urlsafe(24),
                questions=json.dumps(asked, ensure_ascii=False),
                answers="[]", score=0.0, sent_at=None, prepared_at=datetime.utcnow(),
                google_form_url=answer_sync.link_for(week_row, candidate.email) if form_questions else None,
            )
            db.add(quiz)
            db.commit()     # the link must exist before the email is written

        if _email_questions(db, registration, quiz, base_url=base_url, topic=topic, program=program, manual=manual):
            quiz.sent_at = datetime.utcnow()
            outcome["sent"] += 1
        else:
            outcome["failed"] += 1
        db.commit()

    if outcome["sent"]:
        week = course_materials.get_week(db, class_group, week_number)
        week.sent_at = week.sent_at or datetime.now(timezone.utc)
        db.commit()
    return outcome


def _as_asked(question: WeekQuestion) -> dict:
    """A question the way it is kept on a candidate's quiz."""
    return {
        "id": question.id, "question": question.question, "options": question.choices,
        "correct_answer": question.correct_answer, "explanation": question.explanation or "",
    }


def _email_questions(db: Session, registration: Registration, quiz: Quiz, *, base_url: str,
                     topic: str, program: str, manual: bool) -> bool:
    """Email one candidate the link to their questions. True when delivered."""
    message = email_templates.send(
        db, "weekly_questions", registration=registration, candidate=registration.candidate, manual=manual,
        values={
            "week_number": str(quiz.week_number),
            "week_topic": topic or program,
            "questions_count": str(len(questions_in(quiz))),
            "questions_link": quiz.google_form_url or public_link(base_url, quiz),
            "program_name": program,
        },
    )
    return email_templates.delivered(message)


def remind_week(db: Session, class_group: ClassGroup, week_number: int, *, base_url: str) -> dict:
    """Send their questions again to the candidates who received them and have not answered."""
    program = class_group.program.name if class_group.program else class_group.name
    topic = course_materials.week_topic(db, class_group.id, week_number)
    registrations = {item.id: item for item in members_of(db, class_group.id)}
    outcome = {"sent": 0, "failed": 0, "already": 0}
    for quiz in quizzes_of(db, class_group.id, week_number):
        registration = registrations.get(quiz.registration_id)
        if quiz.completed_at is not None or quiz.sent_at is None or registration is None:
            continue
        if not registration.candidate or not registration.candidate.email:
            continue
        delivered = _email_questions(db, registration, quiz, base_url=base_url, topic=topic, program=program, manual=True)
        outcome["sent" if delivered else "failed"] += 1
        db.commit()
    if not outcome["sent"] and not outcome["failed"]:
        return {"error": "Everyone who received this week's questions has answered them."}
    return outcome


def week_is_over(db: Session, class_group: ClassGroup, week_number: int, now: datetime) -> bool:
    """Whether the week's last session has ended (so its questions can go out)."""
    sessions = db.query(ClassSession).filter(
        ClassSession.class_group_id == class_group.id, ClassSession.week_number == week_number,
    ).all()
    if sessions:
        last = max(sessions, key=lambda item: item.session_date)
        end = datetime.combine(last.session_date, last.end_time or class_group.end_time or datetime.max.time())
        return now >= end
    if not class_group.start_date:
        return False
    return now >= class_group.start_date + timedelta(weeks=week_number - 1, days=1)


def send_due(db: Session, base_url: str) -> int:
    """Classes set to "automatic": send the questions of every finished week
    that has not gone out yet. Returns how many emails were sent."""
    now = local_now()
    sent = 0
    classes = db.query(ClassGroup).filter(
        ClassGroup.weekly_questions_mode == "auto", ClassGroup.status == ClassStatus.ONGOING,
    ).all()
    for class_group in classes:
        weeks = db.query(ClassWeek).filter(
            ClassWeek.class_group_id == class_group.id,
            ClassWeek.sent_at.is_(None),
            ClassWeek.questions_status == "ready",
        ).all()
        for week in weeks:
            if not week_is_over(db, class_group, week.week_number, now):
                continue
            outcome = send_week(db, class_group, week.week_number, base_url=base_url, manual=False)
            sent += outcome.get("sent", 0)
    return sent


# ------------------------------------------------------------------
# Answers and results
# ------------------------------------------------------------------

def quiz_by_token(db: Session, token: str) -> Quiz | None:
    if not token or len(token) < 16:
        return None
    return db.query(Quiz).filter(Quiz.kind.in_(("weekly", "session")), Quiz.access_token == token).first()


def questions_in(quiz: Quiz) -> list[dict]:
    try:
        loaded = json.loads(quiz.questions or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def answers_in(quiz: Quiz) -> list:
    try:
        loaded = json.loads(quiz.answers or "[]")
    except (TypeError, ValueError):
        return []
    return loaded if isinstance(loaded, list) else []


def grade(db: Session, quiz: Quiz, answers: list[int | None], feedback: dict | None = None,
          source: str = "page", external_id: str | None = None) -> dict:
    """Record a candidate's answers, and what they said about the week.

    Answers given once are final. `feedback` is the check-in (see
    week_insights); `source` says where the answers came from.
    """
    questions = questions_in(quiz)
    if quiz.completed_at is None:
        quiz.feedback = json.dumps(feedback, ensure_ascii=False) if week_insights.has_feedback(feedback) else None
        quiz.answer_source = source
        quiz.external_id = external_id
        answers = (list(answers) + [None] * len(questions))[: len(questions)]
        correct = sum(1 for question, answer in zip(questions, answers) if answer == question.get("correct_answer"))
        score = round(correct / len(questions) * 100, 1) if questions else 0.0
        quiz.answers = json.dumps(answers)
        quiz.score = score
        quiz.completed_at = datetime.utcnow()
        quiz.assessed_level = level_of(score)["key"]
        missed = [
            question.get("question", "") for question, answer in zip(questions, answers)
            if answer != question.get("correct_answer")
        ]
        quiz.improvement_areas = "\n".join(missed) or None
        db.add(Activity(
            candidate_id=quiz.candidate_id, event_type="WEEKLY_QUESTIONS_ANSWERED",
            details=f"{quiz.course_name}: {correct}/{len(questions)} ({score:.0f}%)",
        ))
        db.flush()
        # What the answers and the check-in say together (rules now, the model's reading later).
        topic = course_materials.week_topic(db, quiz.class_group_id, quiz.week_number) if quiz.class_group_id else ""
        week_insights.store(db, quiz, topic=topic or "")
        db.commit()
    return result_of(quiz)


def result_of(quiz: Quiz) -> dict:
    """A quiz with the candidate's answers set against the right ones."""
    questions, answers = questions_in(quiz), answers_in(quiz)
    rows = []
    for index, question in enumerate(questions):
        given = answers[index] if index < len(answers) else None
        rows.append({
            "question": question.get("question", ""),
            "options": question.get("options", []),
            "given": given,
            "correct": question.get("correct_answer"),
            "is_right": given is not None and given == question.get("correct_answer"),
            "explanation": question.get("explanation", ""),
            "id": question.get("id"),
        })
    done = quiz.completed_at is not None
    return {
        "rows": rows,
        "total": len(rows),
        "right": sum(1 for row in rows if row["is_right"]),
        "score": quiz.score if done else None,
        "level": level_of(quiz.score if done else None),
        "done": done,
    }


def week_results(db: Session, class_group: ClassGroup, week_number: int) -> dict:
    """How the class did on one week: who answered, the average, what was missed most."""
    quizzes = quizzes_of(db, class_group.id, week_number)
    candidates = {
        candidate.id: candidate for candidate in db.query(Candidate).filter(
            Candidate.id.in_([quiz.candidate_id for quiz in quizzes] or [""])
        ).all()
    }
    rows, missed = [], {}
    for quiz in quizzes:
        result = result_of(quiz)
        candidate = candidates.get(quiz.candidate_id)
        rows.append({
            "quiz": quiz,
            "candidate": candidate,
            "name": (candidate.full_name or candidate.email) if candidate else "Candidate",
            "sent": quiz.sent_at is not None,
            "done": result["done"],
            "right": result["right"], "total": result["total"],
            "score": result["score"], "level": result["level"],
        })
        if result["done"]:
            for row in result["rows"]:
                entry = missed.setdefault(row["id"] or row["question"], {"question": row["question"], "wrong": 0, "asked": 0})
                entry["asked"] += 1
                entry["wrong"] += not row["is_right"]
    answered = [row for row in rows if row["done"]]
    hardest = sorted(
        (entry for entry in missed.values() if entry["wrong"]),
        key=lambda entry: (-entry["wrong"] / entry["asked"], -entry["wrong"]),
    )
    average = round(sum(row["score"] for row in answered) / len(answered), 1) if answered else None
    return {
        "rows": rows,
        "sent": sum(1 for row in rows if row["sent"]),
        "not_delivered": sum(1 for row in rows if not row["sent"]),
        "answered": len(answered),
        "average": average,
        "level": level_of(average),
        "hardest": hardest[:3],
    }


def class_overview(db: Session, class_group: ClassGroup) -> dict:
    """A class's course in four numbers: files, questions, weeks sent, understanding."""
    statuses = [
        status for (status,) in db.query(WeekQuestion.status).filter(WeekQuestion.class_group_id == class_group.id).all()
    ]
    scores = [
        quiz.score for quiz in quizzes_of(db, class_group.id) if quiz.completed_at is not None
    ]
    average = round(sum(scores) / len(scores), 1) if scores else None
    return {
        "files": db.query(ClassMaterial).filter(ClassMaterial.class_group_id == class_group.id).count(),
        "ready": statuses.count("ready"),
        "review": statuses.count("review"),
        "sent_weeks": db.query(ClassWeek).filter(
            ClassWeek.class_group_id == class_group.id, ClassWeek.sent_at.isnot(None),
        ).count(),
        "answers": len(scores),
        "average": average,
        "level": level_of(average),
    }


def candidate_weeks(db: Session, candidate_id: str) -> dict:
    """A candidate's weekly results across classes: the list, the average and the trend."""
    quizzes = (
        db.query(Quiz)
        .filter(Quiz.kind == "weekly", Quiz.candidate_id == candidate_id)
        .order_by(Quiz.class_group_id.asc(), Quiz.week_number.asc())
        .all()
    )
    class_names = {
        row.id: row.name for row in db.query(ClassGroup).filter(
            ClassGroup.id.in_({quiz.class_group_id for quiz in quizzes} or {""})
        ).all()
    }
    weeks = []
    for quiz in quizzes:
        done = quiz.completed_at is not None
        weeks.append({
            "quiz": quiz, "class_id": quiz.class_group_id, "class_name": class_names.get(quiz.class_group_id, "Class"),
            "week": quiz.week_number, "sent": quiz.sent_at is not None, "done": done,
            "score": quiz.score if done else None, "level": level_of(quiz.score if done else None),
            "missed": (quiz.improvement_areas or "").splitlines() if done else [],
        })
    scores = [week["score"] for week in weeks if week["done"]]
    average = round(sum(scores) / len(scores), 1) if scores else None
    trend = None
    if len(scores) >= 2:
        change = scores[-1] - scores[-2]
        trend = "up" if change >= 10 else "down" if change <= -10 else "steady"
    waiting = [week for week in weeks if week["sent"] and not week["done"]]
    return {
        "weeks": weeks, "average": average, "level": level_of(average), "trend": trend,
        "answered": len(scores), "waiting": len(waiting),
    }

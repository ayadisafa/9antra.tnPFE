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


_VAGUE = re.compile(r"\b(main (topic|focus|idea|subject|purpose|goal)|sujet principal|idée principale|thème principal|objectif principal)\b",
                    re.IGNORECASE)


def _same_word(word: str, other: str) -> bool:
    return word == other or (len(word) >= 4 and len(other) >= 4 and (other.startswith(word) or word.startswith(other)))


def repeats_question(question: str, answer: str) -> bool:
    """Whether an answer only says again the question's own words ("What is the purpose of error handling?" → "Error
    handling"): every choice could then be defended."""
    asked = _key_words(question) - _FRENCH - _ENGLISH
    said = _key_words(answer) - _FRENCH - _ENGLISH
    return len(said) >= 2 and all(any(_same_word(word, other) for other in asked) for word in said)


def badly_written(question: dict) -> bool:
    """A question that cannot be fairly marked: its right answer repeats the question, it asks for "the main topic"
    (several choices fit), or its right answer is missing. Already sent, it counts as right for whoever answers it."""
    options = question.get("options") or []
    right = question.get("correct_answer")
    if not isinstance(right, int) or not 0 <= right < len(options):
        return True
    text = question.get("question") or ""
    return bool(_VAGUE.search(text)) or repeats_question(text, str(options[right]))


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
        if _VAGUE.search(item["question"]) or repeats_question(item["question"], item["answer_text"]):
            continue                         # could not be fairly marked
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


PLAN_NOTE = ("No course file yet: these questions come from the course plan, about what the class already studied. "
             "Add a file for questions about what was really said.")


_TERMS = re.compile(r"\b(?:[A-Z]{2,}[A-Za-z0-9]*|[A-Za-z]+[A-Z][A-Za-z0-9]*|[A-Za-z]+\d+[A-Za-z0-9]*)\b")


def _terms(text: str) -> set[str]:
    """The technical words of a text: ESP32, MQTT, WordPress, CapCut, DAX…"""
    return {term for term in _TERMS.findall(text or "") if 2 <= len(term) <= 24}


def plan_week_questions(db: Session, class_group: ClassGroup, week_number: int, used: set[str] | None = None) -> list[dict]:
    """Questions about what the class studied, in French, right by construction (no model): knowledge questions of the
    subject written by hand (level_bank) chosen for this week, a notion of the week among those of other weeks, a point
    that was not in the week, a point that was, the subject of an earlier week, and the week's own subject."""
    from app.services import chatbot_sources, level_bank
    from app.services.session_check import _objectives

    if class_group is None or class_group.program is None:
        return []
    weeks, _ = chatbot_sources._weeks_of(db, class_group.program)
    titled = [week for week in weeks if (week["title"] or "").strip()]
    rng = random.Random(f"plan-{class_group.id}-{week_number}")
    used = used or set()
    texts = {week["number"]: " ".join((week["title"] or "", week["objectives"] or "", week["site"] or "")) for week in weeks}

    def subject(week: dict, question: str) -> dict | None:
        right = week["title"].strip()
        others = list(dict.fromkeys(item["title"].strip() for item in titled if item["number"] != week["number"] and item["title"].strip() != right))
        if len(others) < 3:
            return None
        others.sort(key=lambda title: -abs(next(item["number"] for item in titled if item["title"].strip() == title) - week["number"]))
        options = [right] + rng.sample(others[:6], 3)
        rng.shuffle(options)
        return {"question": question, "options": options, "answer": options.index(right),
                "explanation": f"La semaine {week['number']} portait sur « {right} », selon le programme de la formation.",
                "quote": f"Programme, semaine {week['number']} : {right}"}

    found = []
    # 1. Knowledge of the subject, written by hand: the questions closest to this week, never one used already.
    bank = [item for item in level_bank.questions_for(class_group.program.name) if item["question"] not in used]
    if bank:
        here = _key_words(texts.get(week_number, ""))
        step = 1 if week_number <= max(1, len(weeks)) / 3 else 2 if week_number <= 2 * max(1, len(weeks)) / 3 else 3
        bank.sort(key=lambda item: (-len(_key_words(item["question"] + " " + " ".join(item["options"])) & here), abs(item["step"] - step), rng.random()))
        for item in bank[:2]:
            options = list(item["options"])
            right = options[0]
            rng.shuffle(options)
            found.append({"question": item["question"], "options": options, "answer": options.index(right),
                          "explanation": f"La bonne réponse est « {right} ».", "quote": "Banque de questions de 9antra", "kind": "bank"})
    this = next((week for week in titled if week["number"] == week_number), None)
    if this is not None:
        # 2. A notion seen this week, among the notions of other weeks.
        theirs_terms = set().union(*[_terms(text) for number, text in texts.items() if number != week_number]) if len(texts) > 1 else set()
        mine_terms = sorted(_terms(texts.get(week_number, "")) - theirs_terms)
        others = sorted(theirs_terms - _terms(texts.get(week_number, "")))
        if mine_terms and len(others) >= 3:
            right = rng.choice(mine_terms)
            options = [right] + rng.sample(others, 3)
            rng.shuffle(options)
            found.append({"question": f"Laquelle de ces notions avez-vous vue en semaine {week_number} ?", "options": options,
                          "answer": options.index(right), "explanation": f"« {right} » fait partie du contenu de la semaine {week_number}.",
                          "quote": f"Programme, semaine {week_number}"})
        mine = _objectives(this["objectives"])
        theirs = sorted({point for week in weeks if abs(week["number"] - week_number) >= 2 for point in _objectives(week["objectives"])} - set(mine))
        # 3. What was not in the week: three of its points and one from far away.
        if len(mine) >= 3 and theirs:
            right = rng.choice(theirs)
            options = rng.sample(mine, 3) + [right]
            rng.shuffle(options)
            found.append({"question": f"Lequel de ces points n'était PAS au programme de la semaine {week_number} ?", "options": options,
                          "answer": options.index(right), "explanation": f"« {right} » appartient à une autre semaine de la formation.",
                          "quote": f"Programme, semaine {week_number}"})
        # 4. A point that was in the week.
        if mine and len(theirs) >= 3:
            right = rng.choice(mine)
            options = [right] + rng.sample(theirs, 3)
            rng.shuffle(options)
            found.append({"question": f"Lequel de ces points faisait partie de la semaine {week_number} ?", "options": options,
                          "answer": options.index(right), "explanation": f"« {right} » fait partie des objectifs de la semaine {week_number}.",
                          "quote": f"Programme, semaine {week_number} : {right}"})
    # 5. What they studied before: one earlier week, to keep it fresh.
    earlier = [week for week in titled if week["number"] < week_number]
    for week in rng.sample(earlier, min(1, len(earlier))):
        item = subject(week, f"Vous l'avez déjà étudié : quel était le sujet de la semaine {week['number']} ?")
        if item:
            found.append(item)
    # 6. The week's own subject (the question after each session asks it too: last).
    if this is not None:
        item = subject(this, f"Quel était le sujet de la semaine {week_number} ?")
        if item:
            found.append(item)
    return found


def add_plan_questions(db: Session, class_id: str, number: int) -> int:
    """The course plan's questions for a week, kept ready to send. Returns how many were added."""
    group = db.get(ClassGroup, class_id)
    known = {item.question for item in questions_of(db, class_id, number)}
    used = {question for (question,) in db.query(WeekQuestion.question).filter(WeekQuestion.class_group_id == class_id).all()}
    added = 0
    for item in plan_week_questions(db, group, number, used=used):
        if item["question"] in known:
            continue
        bank = item.get("kind") == "bank"
        db.add(WeekQuestion(
            class_group_id=class_id, week_number=number, question=item["question"],
            options=json.dumps(item["options"], ensure_ascii=False), correct_answer=item["answer"], explanation=item["explanation"],
            material_id=f"bank:{number}" if bank else f"plan:{number}", source_name="9antra's question bank" if bank else "The course plan",
            source_excerpt=item["quote"], status="ready", review_reason=None, author="plan",
        ))
        added += 1
    db.commit()
    return added


def write_week(db: Session, week: ClassWeek) -> int:
    """Write the questions of one queued week. Returns how many were kept."""
    class_id, number = week.class_group_id, week.week_number
    everything = course_materials.sources(db, class_id, number)
    # The model only writes from what the professor gave (files, notes): from the course plan and the website alone its
    # questions were vague or wrong, so those weeks get the rules' questions, right by construction.
    sources = [source for source in everything if not str(source["key"]).startswith(("plan:", "site:"))]
    learned = sum(len(source["text"]) for source in sources)
    existing = questions_of(db, class_id, number)

    def finish(status: str, note: str | None) -> None:
        week.questions_status = status
        week.questions_note = note
        week.questions_updated_at = datetime.now(timezone.utc)
        db.commit()

    if learned < MIN_TO_LEARN:
        added = 0 if existing else add_plan_questions(db, class_id, number)
        if added:
            finish("ready", PLAN_NOTE)
            return added
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
        added = 0 if existing else add_plan_questions(db, class_id, number)
        if added:
            finish("ready", PLAN_NOTE)
            return added
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

    if kept == 0 and not existing:
        # The model could not write: the week still gets its questions, from what the class already studied.
        added = add_plan_questions(db, class_id, number)
        if added:
            finish("ready", "The assistant could not write from the course this time: questions from the course plan were written instead.")
            return added
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
              manual: bool = True, span: int = 1) -> dict:
    """Email each candidate of the class their questions of the week.

    A candidate who already received them is skipped; one whose email failed
    earlier is tried again with the same questions. Returns
    {"sent", "failed", "already", "pool"} or {"error": message}.
    """
    first = max(1, week_number - max(1, span) + 1)
    pool = [question for number in range(first, week_number + 1) for question in ready_questions(db, class_group.id, number)]
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
                course_name=f"{program} — semaine {week_number}" if first == week_number else f"{program} — semaines {first} à {week_number}",
                angle="weekly", level="week",
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


LETTERS = "ABCDEF"
_PAIR = re.compile(r"(?<!\d)(\d{1,2})\s*[-:.)=/]?\s*([a-fA-F])(?![a-zA-Z])")
_ONLY_LETTERS = re.compile(r"[\s,;.\-]*(?:[a-fA-F][\s,;.\-]*)+")
_QUOTE_START = re.compile(r"^\s*(>|le .{3,80} a [ée]crit|on .{3,80} wrote|-{2,}\s*(original|message))", re.IGNORECASE)


def written_out(quiz: Quiz) -> str:
    """The questions in the email itself, for candidates who cannot open a page of this computer: lettered choices,
    answered by replying."""
    lines, example = [], []
    for index, question in enumerate(questions_in(quiz), 1):
        options = question.get("options") or []
        lines.append(f"{index}. {question.get('question', '')}")
        lines.append("    ".join(f"{LETTERS[k]}) {option}" for k, option in enumerate(options[: len(LETTERS)])))
        lines.append("")
        example.append(f"{index}-{LETTERS[(index * 2 + 1) % max(1, min(len(options), len(LETTERS)))]}")
    lines.append(f"[box]Pour répondre, répondez simplement à cet email avec vos choix, par exemple : {', '.join(example[:3])}")
    return "\n".join(lines)


def _own_words(text: str) -> str:
    """A reply without the email it quotes."""
    kept = []
    for line in (text or "").splitlines():
        if _QUOTE_START.match(line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def answers_from_text(text: str, count: int) -> list[int | None] | None:
    """“1-B, 2-A, 3-D” (or “B A D”, in order) in a reply to the weekly questions; None when the reply is not that."""
    body = _own_words(text)
    if not body or count <= 0:
        return None
    answers: list[int | None] = [None] * count
    pairs = _PAIR.findall(body)
    if pairs:
        for number, letter in pairs:
            index = int(number) - 1
            if 0 <= index < count:
                answers[index] = LETTERS.index(letter.upper())
        return answers if sum(answer is not None for answer in answers) >= max(1, (count + 1) // 2) else None
    if _ONLY_LETTERS.fullmatch(body):
        letters = [char.upper() for char in body if char.upper() in LETTERS]
        if len(letters) == count:
            return [LETTERS.index(char) for char in letters]
    return None


_KEYWORD = re.compile(r"\b(r[ée]ponses?|response|answers?|choix|choice|jaweb|jawab|el jaweb)\b", re.IGNORECASE)
_UPPER = re.compile(r"(?<![A-Za-z])([A-F])(?![A-Za-z])")
_ANY_CASE = re.compile(r"(?<![A-Za-z])([A-Fa-f])(?![A-Za-z])")


def read_answers(text: str, count: int) -> tuple[dict[int, int], bool] | None:
    """What a reply answers: ({question index: choice}, positional). Pairs (“1-B, 2-A”) say which question; letters
    alone (“ma réponse est C”, “B A D”) fill the questions not answered yet, in order. None when it holds no answer."""
    body = _own_words(text)
    if not body or count <= 0:
        return None
    found = {}
    for number, letter in _PAIR.findall(body):
        index = int(number) - 1
        if 0 <= index < count:
            found[index] = LETTERS.index(letter.upper())
    if found:
        return found, False
    letters = []
    keyword = _KEYWORD.search(body)
    if keyword is not None:
        after = body[keyword.end():]
        letters = _UPPER.findall(after) or (_ANY_CASE.findall(after) if re.fullmatch(r"[\s:,;.\-]*(?:(?:est|sont|is|are)\s*)?(?:[a-fA-F][\s,;.\-]*)+", after) else [])
    elif _ONLY_LETTERS.fullmatch(body):
        letters = [char for char in body if char.upper() in LETTERS]
    elif count == 1:
        letters = _UPPER.findall(body)[:1]
    if not letters:
        return None
    return {index: LETTERS.index(letter.upper()) for index, letter in enumerate(letters[:count])}, True


def _partial_text(answers: list, questions: list) -> str:
    noted = ", ".join(f"{index} → {LETTERS[choice]}" for index, choice in enumerate(answers, 1) if choice is not None)
    missing = [str(index) for index, choice in enumerate(answers, 1) if choice is None]
    example = ", ".join(f"{number}-B" for number in missing[:2])
    return (f"Merci ! J'ai noté : {noted}. Il reste {'la question' if len(missing) == 1 else 'les questions'} {', '.join(missing)} : "
            f"répondez à cet email avec vos choix, par exemple « {example} ».")


def handle_reply(db: Session, candidate_id: str, subject: str, body: str) -> dict | None:
    """A candidate's reply to their weekly questions: the answers it holds are kept (replies add up), and graded once every
    question is answered. Returns {"text": what to answer, "done", "quiz"}, or None when the reply holds no answer."""
    waiting = (db.query(Quiz).filter(Quiz.candidate_id == candidate_id, Quiz.kind.in_(("weekly", "session")), Quiz.sent_at.isnot(None),
                                     Quiz.completed_at.is_(None)).order_by(Quiz.sent_at.desc()).all())
    if not waiting:
        return None
    # The questions after a session (“qu'avez-vous retenu de la séance du 24/10”), or those of a week (“semaine 3”).
    session = re.search(r"s[ée]ance\s*du\s*(\d{1,2}/\d{1,2})", subject or "", re.IGNORECASE)
    after_session = [item for item in waiting if item.kind == "session"]
    if session and after_session:
        quiz = next((item for item in after_session if session.group(1) in (item.course_name or "")), after_session[0])
    else:
        weekly = [item for item in waiting if item.kind == "weekly"] or waiting
        said = re.search(r"semaines?\s*(\d+)(?:\s*[àa-]\s*(\d+))?", subject or "", re.IGNORECASE)
        week = int(said.group(2) or said.group(1)) if said else None
        quiz = next((item for item in weekly if item.week_number == week), weekly[0])
    questions = questions_in(quiz)
    read = read_answers(body, len(questions))
    if read is None:
        return None
    given, positional = read
    answers = (list(answers_in(quiz)) + [None] * len(questions))[: len(questions)]
    if positional:
        empty = [index for index, choice in enumerate(answers) if choice is None]
        for slot, (_, choice) in zip(empty, sorted(given.items())):
            answers[slot] = choice
    else:
        for index, choice in given.items():
            answers[index] = choice
    if all(choice is not None for choice in answers):
        result = grade(db, quiz, answers, source="email")
        return {"text": result_text(result), "done": True, "quiz": quiz}
    quiz.answers = json.dumps(answers)
    db.commit()
    return {"text": _partial_text(answers, questions), "done": False, "quiz": quiz}


def reread_replies(db: Session) -> int:
    """Replies to the weekly questions that came to the admin before they could be read as answers: read again now."""
    from app.models.candidate_email import CandidateEmail
    from app.models.email_thread import EmailThread
    from app.services import email_threads

    done = 0
    rows = (db.query(CandidateEmail).filter(CandidateEmail.direction == "inbound", CandidateEmail.requires_admin.is_(True),
                                            CandidateEmail.replied_at.is_(None)).all())
    for row in rows:
        thread = db.get(EmailThread, row.thread_id) if row.thread_id else None
        subject = (thread.subject or "").lower() if thread is not None else ""
        if thread is None or not ("questions de la semaine" in subject or "de la séance du" in subject) or row.candidate_id is None:
            continue
        handled = handle_reply(db, row.candidate_id, thread.subject, row.body or "")
        if handled is None:
            continue
        candidate = db.get(Candidate, row.candidate_id)
        text = f"{email_assistant.greeting_for(candidate, 'fr')}\n\n{handled['text']}"
        plain, html_body = email_assistant.compose_reply(db, candidate, text, language="fr")
        email_threads.deliver(db, candidate, f"Re: {thread.subject}", plain, html_body, kind="reply", author="system", thread=thread,
                              language="fr", note="Answers to the weekly questions read from the reply.")
        row.status, row.requires_admin, row.replied_at = "replied", False, datetime.utcnow()
        thread.needs_admin, thread.status = False, "replied"
        db.commit()
        done += 1
    return done


def answers_view(db: Session) -> dict:
    """Every weekly questions sent, with each candidate's answers, like the level checks."""
    rows = []
    quizzes = (db.query(Quiz).filter(Quiz.kind == "weekly", Quiz.sent_at.isnot(None)).order_by(Quiz.sent_at.desc()).limit(400).all())
    classes = {group.id: group for group in db.query(ClassGroup).all()}
    for quiz in quizzes:
        candidate = db.get(Candidate, quiz.candidate_id) if quiz.candidate_id else None
        result = result_of(quiz)
        given = sum(1 for choice in answers_in(quiz) if choice is not None)
        state = "answered" if quiz.completed_at else "partial" if given else "waiting"
        rows.append({"quiz": quiz, "candidate": candidate, "class": classes.get(quiz.class_group_id), "result": result, "state": state,
                     "given": given, "level": level_of(quiz.score if quiz.completed_at else None),
                     "via": {"email": "By replying to the email", "form": "Google Form", "page": "On the page"}.get(quiz.answer_source or "", "")})
    answered = [row for row in rows if row["state"] == "answered"]
    levels = {key: sum(1 for row in answered if row["level"]["key"] == key) for key in ("strong", "partial", "weak")}
    return {
        "rows": rows, "levels": levels,
        "counts": {"sent": len(rows), "answered": len(answered), "partial": sum(1 for row in rows if row["state"] == "partial"),
                   "waiting": sum(1 for row in rows if row["state"] == "waiting")},
        "average": round(sum(row["quiz"].score or 0 for row in answered) / len(answered)) if answered else None,
        "classes": sorted({row["class"].name: row["class"] for row in rows if row["class"] is not None}.values(), key=lambda group: group.name),
    }


def waiting_quiz(db: Session, candidate_id: str) -> Quiz | None:
    """The candidate's latest weekly questions, sent and not answered yet."""
    return (db.query(Quiz).filter(Quiz.candidate_id == candidate_id, Quiz.kind == "weekly", Quiz.sent_at.isnot(None),
                                  Quiz.completed_at.is_(None))
            .order_by(Quiz.sent_at.desc()).first())


def result_text(result: dict) -> str:
    """The answer to a candidate's reply: their score, and the right answer for each one missed (in French)."""
    voided = [str(index) for index, row in enumerate(result["rows"], 1) if row.get("voided")]
    if voided and len(voided) == result["total"]:
        return ("Merci pour votre réponse ! Cette question était mal formulée de notre côté : elle est comptée juste."
                if len(voided) == 1 else
                "Merci pour vos réponses ! Ces questions étaient mal formulées de notre côté : elles sont comptées justes.") + \
            " Les prochaines questions porteront directement sur ce que vous avez étudié."
    lines = [f"Merci pour vos réponses ! Vous avez {result['right']} bonne(s) réponse(s) sur {result['total']}."]
    if voided:
        lines.append(f"La question {voided[0]} était mal formulée de notre côté : elle est comptée juste." if len(voided) == 1 else
                     f"Les questions {', '.join(voided)} étaient mal formulées de notre côté : elles sont comptées justes.")
    wrong = [(index, row) for index, row in enumerate(result["rows"], 1) if not row["is_right"]]
    if not wrong:
        lines.append("Bravo, tout est juste !")
    for index, row in wrong:
        options = row["options"] or []
        right = row["correct"] if isinstance(row["correct"], int) and 0 <= row["correct"] < len(options) else None
        said = f"{LETTERS[right]}) {options[right]}" if right is not None else ""
        lines.append(f"{index}. {row['question']} — la bonne réponse était {said}." + (f" {row['explanation']}" if row["explanation"] else ""))
    return "\n\n".join(lines)


def in_the_email(db: Session, quiz: Quiz, base_url: str) -> bool:
    """Whether the questions are written in the email itself (answered by replying), as chosen on the weekly page:
    always “in the email”, or automatic and this computer cannot be reached by candidates."""
    way = settings_of(db)["way"]
    return not quiz.google_form_url and (way == "email" or (way == "auto" and is_local_address(base_url)))


def _email_questions(db: Session, registration: Registration, quiz: Quiz, *, base_url: str,
                     topic: str, program: str, manual: bool) -> bool:
    """Email one candidate the link to their questions (or, when this computer cannot be reached from outside, the
    questions themselves, answered by replying). True when delivered."""
    overrides = None
    way = settings_of(db)["way"]
    if not quiz.google_form_url and (way == "email" or (way == "auto" and is_local_address(base_url))):
        template = email_templates.get(db, "weekly_questions")
        overrides = {"body": f"{template.body}\n\n{written_out(quiz)}", "cta_label": "", "cta_url": ""}
    message = email_templates.send(
        db, "weekly_questions", registration=registration, candidate=registration.candidate, manual=manual,
        values={
            "week_number": str(quiz.week_number),
            "week_topic": topic or program,
            "questions_count": str(len(questions_in(quiz))),
            "questions_link": quiz.google_form_url or public_link(base_url, quiz),
            "program_name": program,
        },
        overrides=overrides,
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


EVERY_CHOICES = (1, 2, 4, 0)
WAY_CHOICES = ("auto", "email", "link")


def settings_of(db: Session) -> dict:
    """How often the questions go (every 1, 2 or 4 weeks; 0: stopped) and how candidates answer them."""
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    every = getattr(settings, "weekly_every", None)
    every = 1 if every is None or int(every) not in EVERY_CHOICES else int(every)
    way = getattr(settings, "weekly_way", None)
    return {"every": every, "way": way if way in WAY_CHOICES else "auto"}


def save_settings(db: Session, *, every, way: str) -> dict:
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    try:
        every = int(every)
    except (TypeError, ValueError):
        every = 1
    settings.weekly_every = every if every in EVERY_CHOICES else 1
    settings.weekly_way = way if way in WAY_CHOICES else "auto"
    db.commit()
    return settings_of(db)


def replace_thin(db: Session) -> int:
    """Questions the model wrote from the course plan or the website alone (vague, sometimes wrong), and any of its
    questions that cannot be fairly marked, are removed from the weeks not sent yet; those weeks are written again.
    Returns how many weeks."""
    weeks = set()
    for row in db.query(WeekQuestion).filter(WeekQuestion.author == "system").all():
        thin = str(row.material_id or "").startswith(("plan:", "site:"))
        if not thin and not badly_written({"question": row.question, "options": row.choices, "correct_answer": row.correct_answer}):
            continue
        week = db.query(ClassWeek).filter(ClassWeek.class_group_id == row.class_group_id, ClassWeek.week_number == row.week_number).first()
        if week is not None and week.sent_at is not None:
            continue
        weeks.add((row.class_group_id, row.week_number))
        db.delete(row)
    db.commit()
    for class_id, number in weeks:
        group = db.get(ClassGroup, class_id)
        if group is not None:
            queue(db, group, number)
    return len(weeks)


def queue_due(db: Session, now: datetime | None = None) -> int:
    """Always on: the week a running class is in, and the one before, get their questions written, even with no course
    file (then from the course plan). Weeks further back are left as they were. Returns how many weeks were asked for."""
    now = now or datetime.now(timezone.utc)
    count = 0
    for class_group in db.query(ClassGroup).filter(ClassGroup.status == ClassStatus.ONGOING).all():
        if not members_of(db, class_group.id):
            continue
        current = max(1, class_group.current_week or 1)
        for number in range(max(1, current - 1), current + 1):
            week = course_materials.get_week(db, class_group, number)
            if week.sent_at is not None or ready_questions(db, class_group.id, number):
                continue
            if week.questions_status == "failed" and week.questions_updated_at is not None:
                tried = week.questions_updated_at if week.questions_updated_at.tzinfo else week.questions_updated_at.replace(tzinfo=timezone.utc)
                if now - tried < timedelta(hours=1):
                    continue                 # tried a moment ago: again later, not at every check
            if week.questions_status in ("idle", "failed"):
                queue(db, class_group, number)
                count += 1
    db.commit()
    return count


def send_due(db: Session, base_url: str) -> int:
    """Always on: every running class gets the questions of each week once its session is over (the week just finished,
    and the current one when it ends). Returns how many emails were sent."""
    now = local_now()
    sent = 0
    reread_replies(db)                       # replies that came before they could be read as answers
    plan = settings_of(db)
    if plan["every"] == 0:
        return 0                             # stopped by the admin
    db.query(ClassGroup).filter(ClassGroup.weekly_questions_mode != "auto").update({"weekly_questions_mode": "auto"}, synchronize_session=False)
    db.commit()
    replace_thin(db)
    queue_due(db)
    classes = db.query(ClassGroup).filter(ClassGroup.status == ClassStatus.ONGOING).all()
    for class_group in classes:
        current = max(1, class_group.current_week or 1)
        weeks = db.query(ClassWeek).filter(
            ClassWeek.class_group_id == class_group.id,
            ClassWeek.sent_at.is_(None),
            ClassWeek.questions_status == "ready",
            ClassWeek.week_number >= current - 1,
        ).all()
        for week in weeks:
            if not week_is_over(db, class_group, week.week_number, now):
                continue
            if plan["every"] > 1 and week.week_number % plan["every"]:
                continue                     # every 2 or 4 weeks: the questions of the weeks between wait for the review
            outcome = send_week(db, class_group, week.week_number, base_url=base_url, manual=False, span=plan["every"])
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
        correct = sum(1 for question, answer in zip(questions, answers) if _counts_right(question, answer))
        score = round(correct / len(questions) * 100, 1) if questions else 0.0
        quiz.answers = json.dumps(answers)
        quiz.score = score
        quiz.completed_at = datetime.utcnow()
        quiz.assessed_level = level_of(score)["key"]
        missed = [
            question.get("question", "") for question, answer in zip(questions, answers)
            if not _counts_right(question, answer)
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


def _counts_right(question: dict, answer) -> bool:
    """The right choice, or any choice to a question that was badly written on our side."""
    return answer is not None and (answer == question.get("correct_answer") or badly_written(question))


def result_of(quiz: Quiz) -> dict:
    """A quiz with the candidate's answers set against the right ones."""
    questions, answers = questions_in(quiz), answers_in(quiz)
    rows = []
    for index, question in enumerate(questions):
        given = answers[index] if index < len(answers) else None
        rows.append({
            "voided": badly_written(question),
            "question": question.get("question", ""),
            "options": question.get("options", []),
            "given": given,
            "correct": question.get("correct_answer"),
            "is_right": _counts_right(question, given),
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

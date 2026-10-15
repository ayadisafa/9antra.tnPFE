"""Proposals for programs whose details are missing.

A program created from a registration form often has only a name. The
system prepares the rest and asks the admin to confirm it:

* duration, hours and session time are the ones most of the other programs
  use (or the duration written in the name, "Bootcamp 6 months");
* the description and the week-by-week plan are written by the local model
  from the program's name, in the shape of the programs already in the
  catalogue;
* prices are never proposed.

Nothing is saved on the program until the admin accepts the proposal, and
accepting only fills what is still empty.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models.classgroup import ClassGroup
from app.models.notification import Notification
from app.models.program import Program, ProgramLevel, WeeklyCurriculum
from app.models.program_proposal import ProgramProposal
from app.services import email_assistant
from app.services.enrollment_service import seed_class_sessions

logger = logging.getLogger(__name__)

OPEN = ("queued", "writing", "ready", "failed")
MAX_LEVELS = 6
MAX_WEEKS_PER_LEVEL = 8

# What a program can miss, in the order it is shown.
FIELDS = {
    "description": "Description",
    "total_duration_days": "Duration",
    "hours_per_week": "Hours per week",
    "training_hours": "Training hours",
    "supervision_hours": "Supervision hours",
    "class_session_time": "Session time",
    "curriculum": "Week-by-week plan",
}
_NUMBERS = ("total_duration_days", "hours_per_week", "training_hours", "supervision_hours")

_DURATION_IN_NAME = re.compile(r"(\d{1,2})\s*(months?|mois|weeks?|semaines?)", re.IGNORECASE)
_NAME_NOISE = re.compile(r"\(.*?\)|[^a-z0-9\s]")

_SYSTEM = (
    "You write training catalogue entries for 9antra, a training centre for developers and "
    "digital professionals. You answer with one JSON object and nothing else. You write in "
    "English, concretely, naming real tools and techniques. You never mention prices, dates, "
    "trainers or certificates."
)


# ------------------------------------------------------------------
# What is missing
# ------------------------------------------------------------------

def has_curriculum(program: Program) -> bool:
    return any(level.weeks for level in program.levels)


def missing(program: Program) -> list[str]:
    """Keys of FIELDS this program does not have yet."""
    gaps = []
    if not (program.description or "").strip():
        gaps.append("description")
    for name in _NUMBERS:
        if not getattr(program, name):
            gaps.append(name)
    if not (program.class_session_time or "").strip():
        gaps.append("class_session_time")
    if not has_curriculum(program):
        gaps.append("curriculum")
    return gaps


def missing_labels(program: Program) -> list[str]:
    return [FIELDS[key] for key in missing(program)]


def open_proposal(db: Session, program_id: str) -> ProgramProposal | None:
    """The proposal still waiting on this program, if any."""
    return (
        db.query(ProgramProposal)
        .filter(ProgramProposal.program_id == program_id, ProgramProposal.status.in_(OPEN))
        .order_by(ProgramProposal.created_at.desc())
        .first()
    )


def open_proposals(db: Session) -> dict[str, ProgramProposal]:
    rows = (
        db.query(ProgramProposal)
        .filter(ProgramProposal.status.in_(OPEN))
        .order_by(ProgramProposal.created_at.asc())
        .all()
    )
    return {row.program_id: row for row in rows}


# ------------------------------------------------------------------
# What the other programs do
# ------------------------------------------------------------------

def _complete_programs(db: Session, exclude_id: str | None = None) -> list[Program]:
    return [
        program for program in db.query(Program).all()
        if program.id != exclude_id and not missing(program)
    ]


def house_values(db: Session, exclude_id: str | None = None) -> dict:
    """The value most complete programs share for each practical detail.

    Returns {field: {"value": ..., "count": programs using it, "of": programs looked at}}
    plus "levels" and "weeks_per_level" for the shape of the plan.
    """
    programs = _complete_programs(db, exclude_id)
    house: dict = {}
    for name in (*_NUMBERS, "class_session_time"):
        values = [getattr(program, name) for program in programs if getattr(program, name)]
        if values:
            value, count = Counter(values).most_common(1)[0]
            house[name] = {"value": value, "count": count, "of": len(programs)}
    shapes = [
        (len(program.levels), round(sum(len(level.weeks) for level in program.levels) / len(program.levels)))
        for program in programs if program.levels
    ]
    if shapes:
        (levels, weeks), _ = Counter(shapes).most_common(1)[0]
        house["levels"], house["weeks_per_level"] = levels, weeks
    return house


def duration_in_name(name: str | None) -> tuple[int, str] | None:
    """("Bootcamp 6 months") -> (180, "6 months")."""
    match = _DURATION_IN_NAME.search(name or "")
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2).lower()
    days = amount * (30 if unit.startswith("m") else 7)
    return (days, match.group(0)) if 7 <= days <= 730 else None


def _plain(name: str | None) -> str:
    return " ".join(_NAME_NOISE.sub(" ", (name or "").lower()).split())


def similar_program(db: Session, program: Program) -> tuple[Program, int] | None:
    """The complete program whose name is closest, when it is close enough to matter."""
    best, best_score = None, 0
    for other in _complete_programs(db, program.id):
        score = max(
            fuzz.token_set_ratio(_plain(program.name), _plain(other.name)),
            fuzz.partial_ratio(_plain(program.name), (other.name or "").lower()),
        )
        if score > best_score:
            best, best_score = other, score
    return (best, int(best_score)) if best is not None and best_score >= 70 else None


# ------------------------------------------------------------------
# Writing with the local model
# ------------------------------------------------------------------

def _clean(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _example(program: Program | None) -> str:
    """One catalogue entry, to show the model the expected shape and tone."""
    if program is None:
        return ""
    lines = [f'Program "{program.name}": {program.description}']
    for level in program.levels:
        lines.append(f"  {level.title} — {level.description}")
        for week in level.weeks:
            lines.append(f"    Week {week.week_number}: {week.topic_title}")
    return "\n".join(lines)


def _write_outline(name: str, levels: int, weeks: int, example: str) -> dict | None:
    prompt = (
        f'Write the catalogue entry of the training program "{name}".\n\n'
        f"It has {levels} level(s), each with {weeks} weeks, going from the basics to practice on a project.\n"
        + (f"\nAn existing entry of the same catalogue, for the shape and the tone only:\n{example}\n" if example else "")
        + "\nAnswer with this JSON object:\n"
        '{"description": "one sentence, 15 to 30 words, saying what the learner will be able to build or do",\n'
        ' "levels": [{"title": "Level 1: short title", "description": "one sentence on what this level covers",\n'
        f'   "weeks": ["topic of week 1", "topic of week 2"]}}]}}\n'
        f"Give exactly {levels} level(s) and exactly {weeks} week topics per level. "
        "Each week topic is 3 to 8 words and names what is studied that week."
    )
    return email_assistant.llm_json(_SYSTEM, prompt, max_tokens=180 + levels * (70 + weeks * 22))


def _points(value) -> str:
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(part).strip().rstrip(";.") for part in value if str(part).strip())
    return _clean(value, 600)


def _write_objectives(name: str, level_title: str, topics: list[str]) -> list[str]:
    """What is done and learned each week, one text per topic ("" when the model gave none)."""
    listed = "\n".join(f"week_{index}: {topic}" for index, topic in enumerate(topics, 1))
    keys = ", ".join(f'"week_{index}": "..."' for index in range(1, len(topics) + 1))
    prompt = (
        f'Training program "{name}", {level_title}.\n'
        f"The weekly topics are:\n{listed}\n\n"
        "For each week, write what the learner does and learns: 2 to 4 short points separated by "
        "semicolons, like \"Creating a Docker image and running it; Introduction to Docker Compose\".\n"
        f"Answer with this JSON object, one entry per week: {{{keys}}}"
    )
    data = email_assistant.llm_json(_SYSTEM, prompt, max_tokens=120 + len(topics) * 80) or {}
    if isinstance(data.get("objectives"), (list, dict)):    # some answers wrap the weeks
        wrapped = data["objectives"]
        data = wrapped if isinstance(wrapped, dict) else {f"week_{index}": item for index, item in enumerate(wrapped, 1)}
    objectives = [_points(data.get(f"week_{index}")) for index in range(1, len(topics) + 1)]

    # A week the model skipped is asked for on its own.
    for index, topic in enumerate(topics):
        if len(objectives[index]) >= 15:
            continue
        single = email_assistant.llm_json(
            _SYSTEM,
            f'Training program "{name}", {level_title}, week topic: "{topic}".\n'
            "Write what the learner does and learns that week: 2 to 4 short points separated by semicolons.\n"
            'Answer with this JSON object: {"points": "..."}',
            max_tokens=140,
        ) or {}
        objectives[index] = _points(single.get("points"))
    return objectives


def _valid_outline(data: dict | None) -> tuple[str, list[dict]]:
    """(description, levels) kept from the model's answer; levels may be empty."""
    if not isinstance(data, dict):
        return "", []
    description = _clean(data.get("description"), 400)
    if description and description[-1] not in ".!?":
        description += "."
    levels = []
    for index, raw in enumerate(data.get("levels") if isinstance(data.get("levels"), list) else [], 1):
        if not isinstance(raw, dict) or index > MAX_LEVELS:
            continue
        topics = []
        for topic in raw.get("weeks") if isinstance(raw.get("weeks"), list) else []:
            if isinstance(topic, dict):
                topic = topic.get("topic") or topic.get("topic_title") or topic.get("title")
            topic = re.sub(r"^(week|semaine)\s*\d+\s*[:.\-–]\s*", "", _clean(topic, 120), flags=re.IGNORECASE)
            if len(topic) >= 3 and topic.lower() not in (item.lower() for item in topics):
                topics.append(topic)
        if len(topics) < 2:
            continue
        title = _clean(raw.get("title"), 120) or f"Level {index}"
        if not re.match(r"(level|niveau)\s*\d", title, re.IGNORECASE):
            title = f"Level {index}: {title}"
        levels.append({
            "title": title,
            "description": _clean(raw.get("description"), 300),
            "topics": topics[:MAX_WEEKS_PER_LEVEL],
        })
    return description, levels


def write_text(name: str, *, want_description: bool, want_plan: bool,
               levels: int, weeks: int, example: str) -> dict | None:
    """Description and week-by-week plan from the model. None when it did not answer usefully."""
    description, outline = "", []
    for _ in range(2):      # a small model sometimes misses the shape; one more try
        description, outline = _valid_outline(_write_outline(name, levels, weeks, example))
        if (description or not want_description) and (outline or not want_plan):
            break
    if (want_description and not description) or (want_plan and not outline):
        return None

    written: dict = {}
    if want_description:
        written["description"] = description
    if want_plan:
        number = 0
        written["levels"] = []
        for level in outline:
            objectives = _write_objectives(name, level["title"], level["topics"])
            weeks_out = []
            for index, topic in enumerate(level["topics"]):
                number += 1
                weeks_out.append({
                    "week_number": number,
                    "topic_title": topic,
                    "learning_objectives": objectives[index] if index < len(objectives) else "",
                })
            written["levels"].append({"title": level["title"], "description": level["description"], "weeks": weeks_out})
    return written


# ------------------------------------------------------------------
# Preparing a proposal
# ------------------------------------------------------------------

def _how_many(entry: dict) -> str:
    return f"Used by {entry['count']} of your {entry['of']} complete programs"


def prepare(db: Session, proposal: ProgramProposal) -> None:
    """Fill a proposal for what its program misses now. Sets it ready or failed."""
    program = db.get(Program, proposal.program_id)
    if program is None:
        proposal.status = "dismissed"
        db.commit()
        return
    gaps = missing(program)
    if not gaps:
        proposal.status = "dismissed"
        proposal.decided_at = datetime.now(timezone.utc)
        db.commit()
        return

    # Saved before the model is asked: writing takes minutes on this machine.
    proposal.status = "writing"
    proposal.error = None
    db.commit()

    house = house_values(db, program.id)
    data: dict = {}
    basis: dict = {"missing": gaps}

    for name in (*_NUMBERS, "class_session_time"):
        if name in gaps and name in house:
            data[name] = house[name]["value"]
            basis[name] = _how_many(house[name])
    named = duration_in_name(program.name)
    if named and "total_duration_days" in gaps:
        data["total_duration_days"] = named[0]
        basis["total_duration_days"] = f"From the name (“{named[1]}”)"

    close = similar_program(db, program)
    if close:
        basis["similar"] = {"id": close[0].id, "name": close[0].name, "score": close[1]}

    want_description, want_plan = "description" in gaps, "curriculum" in gaps
    if want_description or want_plan:
        levels = house.get("levels", 2)
        weeks = house.get("weeks_per_level", 4)
        days = data.get("total_duration_days") or program.total_duration_days
        if named and days and house.get("total_duration_days"):
            # A longer program than the usual one gets proportionally more levels.
            usual = house["total_duration_days"]["value"]
            levels = max(1, min(MAX_LEVELS, round(levels * days / usual)))
        examples = _complete_programs(db, program.id)
        example = close[0] if close else (examples[0] if examples else None)
        written = write_text(
            program.name, want_description=want_description, want_plan=want_plan,
            levels=levels, weeks=weeks, example=_example(example),
        )
        if written is None:
            if not data:
                proposal.status = "failed"
                proposal.error = (
                    "The assistant did not answer, so nothing could be written. "
                    "Ask for a new proposal in a moment; if it keeps failing, Settings says how to start it."
                )
                db.commit()
                return
            basis["text_error"] = "The local model did not answer: the description and the plan are still to write."
        else:
            data.update(written)
            if want_description:
                basis["description"] = "Written by the local model from the program's name"
            if want_plan:
                basis["curriculum"] = (
                    f"Written by the local model, in the shape of “{example.name}”" if example
                    else "Written by the local model from the program's name"
                )

    proposal.data = json.dumps(data, ensure_ascii=False)
    proposal.basis = json.dumps(basis, ensure_ascii=False)
    proposal.status = "ready"
    db.add(Notification(
        recipient_role="admin",
        category="PROGRAM_PROPOSAL",
        title=f"Proposal ready: {program.name}",
        message=(
            f"{', '.join(FIELDS[key] for key in gaps)} were missing. "
            "Read the proposal, correct it if needed, then accept it."
        ),
        action_url=f"/programs/all?review={program.id}",
        program_id=program.id,
    ))
    db.commit()


def request(db: Session, program: Program) -> ProgramProposal | None:
    """Queue a (new) proposal for a program. None when nothing is missing."""
    if not missing(program):
        return None
    current = open_proposal(db, program.id)
    if current is not None:
        if current.status in ("queued", "writing"):
            return current
        current.status = "dismissed"
        current.decided_at = datetime.now(timezone.utc)
    proposal = ProgramProposal(program_id=program.id, status="queued")
    db.add(proposal)
    db.commit()
    return proposal


def queue_missing(db: Session) -> int:
    """Queue a proposal for every incomplete program that never had one."""
    known = {program_id for (program_id,) in db.query(ProgramProposal.program_id).distinct().all()}
    queued = 0
    for program in db.query(Program).order_by(Program.name.asc()).all():
        if program.id not in known and missing(program):
            db.add(ProgramProposal(program_id=program.id, status="queued"))
            queued += 1
    if queued:
        db.commit()
    return queued


def release_interrupted(db: Session) -> int:
    """Proposals left "writing" by a server that stopped go back in the queue."""
    count = (
        db.query(ProgramProposal)
        .filter(ProgramProposal.status == "writing")
        .update({"status": "queued"}, synchronize_session=False)
    )
    if count:
        db.commit()
    return count


def work_one(db: Session) -> bool:
    """Write the oldest queued proposal. False when the queue is empty."""
    proposal = (
        db.query(ProgramProposal)
        .filter(ProgramProposal.status == "queued")
        .order_by(ProgramProposal.created_at.asc())
        .first()
    )
    if proposal is None:
        return False
    try:
        prepare(db, proposal)
    except Exception:
        db.rollback()
        logger.exception("Preparing the proposal %s failed.", proposal.id)
        proposal = db.get(ProgramProposal, proposal.id)
        if proposal is not None:
            proposal.status = "failed"
            proposal.error = "Something went wrong while preparing this proposal. Ask for a new one."
            db.commit()
    return True


# ------------------------------------------------------------------
# The admin's decision
# ------------------------------------------------------------------

def _number(value, low: int, high: int) -> int | None:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def read_form(form) -> dict:
    """The proposal as the admin corrected it on the review form."""
    data: dict = {}
    description = _clean(form.get("description"), 1000)
    if description:
        data["description"] = description
    limits = {"total_duration_days": (1, 730), "hours_per_week": (1, 60), "training_hours": (1, 60), "supervision_hours": (1, 60)}
    for name, (low, high) in limits.items():
        number = _number(form.get(name), low, high)
        if number is not None:
            data[name] = number
    session_time = _clean(form.get("class_session_time"), 200)
    if session_time:
        data["class_session_time"] = session_time

    levels = []
    for level_index in range(MAX_LEVELS):
        title = _clean(form.get(f"level_{level_index}_title"), 150)
        weeks = []
        for week_index in range(MAX_WEEKS_PER_LEVEL):
            topic = _clean(form.get(f"week_{level_index}_{week_index}_topic"), 150)
            if topic:
                weeks.append({
                    "topic_title": topic,
                    "learning_objectives": _clean(form.get(f"week_{level_index}_{week_index}_objectives"), 1000),
                })
        if weeks:
            levels.append({
                "title": title or f"Level {len(levels) + 1}",
                "description": _clean(form.get(f"level_{level_index}_description"), 500),
                "weeks": weeks,
            })
    if levels:
        data["levels"] = levels
    return data


def accept(db: Session, proposal: ProgramProposal, data: dict | None = None) -> dict:
    """Write the (corrected) proposal on the program. Only fills what is still empty.

    Returns what was done: {"filled": [labels], "classes": classes that got their weekly plan}.
    """
    program = db.get(Program, proposal.program_id)
    if program is None:
        raise ValueError("This program no longer exists.")
    data = data if data is not None else proposal.details
    gaps = missing(program)
    filled = []

    if "description" in gaps and data.get("description"):
        program.description = data["description"]
        program.embedding = None    # written for the old text
        filled.append(FIELDS["description"])
    for name in (*_NUMBERS, "class_session_time"):
        if name in gaps and data.get(name):
            setattr(program, name, data[name])
            filled.append(FIELDS[name])

    classes = []
    if "curriculum" in gaps and data.get("levels"):
        for level in list(program.levels):      # levels left without any week
            db.delete(level)
        db.flush()
        number = 0
        for order, level in enumerate(data["levels"], 1):
            row = ProgramLevel(
                program_id=program.id, level_order=order,
                title=level["title"], description=level.get("description") or None,
                hours_per_week=program.hours_per_week or None,
                class_session_time=program.class_session_time or None,
            )
            db.add(row)
            db.flush()
            for week in level["weeks"]:
                number += 1
                db.add(WeeklyCurriculum(
                    level_id=row.id, week_number=number,
                    topic_title=week["topic_title"],
                    learning_objectives=week.get("learning_objectives") or None,
                ))
        db.flush()
        filled.append(FIELDS["curriculum"])
        # Classes of this program created before it had a plan get their sessions now.
        for class_group in db.query(ClassGroup).filter(ClassGroup.program_id == program.id).all():
            if class_group.start_date and not class_group.sessions:
                seed_class_sessions(db, class_group)
                classes.append(class_group.name)

    proposal.data = json.dumps(data, ensure_ascii=False)
    proposal.status = "accepted"
    proposal.decided_at = datetime.now(timezone.utc)
    db.commit()
    return {"filled": filled, "classes": classes}


def dismiss(db: Session, proposal: ProgramProposal) -> None:
    proposal.status = "dismissed"
    proposal.decided_at = datetime.now(timezone.utc)
    db.commit()

"""One agent per program, born with the program: "the Python agent", "the DevOps agent"…

Each one knows everything about its program: what it teaches week by week (its plan and its files), its price,
length and hours, its next session and its professors, the facts the admin told about it, and the answers taught or
given by its professors (all learnt from the inbox, the professors and the admin's corrections). When a program is
added, its agent exists at once; when it is removed, its agent goes with it. The orchestrator gives a program's agent
the questions that name its program (or come from a candidate of that program); Yasmine asks the other agents when it
does not know.
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.services.ui_words import t as _t

PREFIX = "program:"


def key_of(program) -> str:
    return PREFIX + program.id


def program_of(db: Session, key: str | None):
    from app.models import Program

    if not key or not key.startswith(PREFIX):
        return None
    return db.get(Program, key[len(PREFIX):])


def describe(db: Session, program) -> dict:
    """The agent of one program, as the agents page shows it."""
    from app.models.knowledge import KnowledgeEntry
    from app.models.course_material import ProgramFile

    weeks = sum(len(level.weeks) for level in program.levels) if getattr(program, "levels", None) else 0
    learnt = db.query(KnowledgeEntry).filter(KnowledgeEntry.program_id == program.id, KnowledgeEntry.is_active.is_(True)).count()
    files = db.query(ProgramFile).filter(ProgramFile.program_id == program.id).count()
    return {"key": key_of(program), "name": _t("{0} agent", program.name), "program": program, "icon": "bi-journal-code", "colour": "#7c3aed",
            "does": _t("Everything about {0}: what it teaches week by week, its price, hours, next session and professors, and what was learnt about it.", program.name),
            "reads": [_t("Its weekly plan ({0} weeks)", weeks), _t("Its files ({0})", files), _t("What was learnt about it ({0})", learnt)],
            "weeks": weeks, "files": files, "learnt": learnt}


def agents(db: Session) -> list[dict]:
    """Every program's agent, the programs' order."""
    from app.models import Program

    return [describe(db, program) for program in db.query(Program).order_by(Program.name).all()]


def find(db: Session, text: str, candidate=None):
    """The program a question is about: named in it, else the program the candidate follows."""
    from app.services import chatbot

    program = chatbot._program_in(db, chatbot._plain(text or ""))      # it reads the plain (lower-case) words
    if program is None and candidate is not None:
        rows = [row for row in getattr(candidate, "registrations", []) or [] if row.program is not None]
        rows.sort(key=lambda row: row.registration_date or datetime.min, reverse=True)
        program = rows[0].program if rows else None
    return program


def _words(text: str) -> set[str]:
    from app.services import knowledge_service

    return {token for token in knowledge_service.tokenize(text) if len(token) >= 3}


def answer(db: Session, program, text: str, *, lang: str | None = None) -> dict | None:
    """The program's agent's answer, or None when it has nothing sure to say (then another agent is asked)."""
    from app.services import chatbot_sources, enrollment_service, howto, knowledge_service, social_content, ui_words

    spoken = lang if lang in ("en", "fr", "ar", "tn") else howto.language_of(text)
    fr = spoken in ("fr", "tn", "ar")
    token = ui_words.use({"tn": "fr"}.get(spoken, spoken))
    try:
        found = chatbot_sources.program_content(db, text, program, fr)        # what it teaches, week by week
        if found and found.get("reply"):
            return {**found, "source": found.get("source") or "record"}
        lines, plain = [], (text or "").lower()
        asked = _words(text)
        if re.search(r"prix|price|co[uû]t|cost|combien|how much|tarif|9adech|قداش|سعر", plain):
            lines.append(_t("{0}: {1} DT online, {2} DT on site.", program.name, f"{program.price_online or 0:g}", f"{program.price_onsite or 0:g}"))
        if re.search(r"quand|when|commence|start|d[ée]but|date|session|wa9tech|متى", plain):
            upcoming = [item for item in social_content.next_sessions(db, most=8) if item["program_id"] == program.id]
            lines.append(_t("Its next session starts on {0}.", upcoming[0]["starts"].strftime("%d/%m/%Y")) if upcoming
                         else _t("No session of {0} is planned yet: the registration form keeps your place.", program.name))
        if re.search(r"prof|formateur|trainer|teacher|enseignant|أستاذ", plain):
            names = [professor.name for professor in enrollment_service.teachers_of(db, program)[:3]]
            if names:
                lines.append(_t("Its professors: {0}.", ", ".join(names)))
        if re.search(r"dur[ée]e|how long|combien de (semaines|temps)|length|weeks|semaines", plain):
            weeks = sum(len(level.weeks) for level in program.levels) if getattr(program, "levels", None) else 0
            if weeks:
                lines.append(_t("{0} lasts {1} weeks.", program.name, weeks))
        best, score = None, 0.0
        for fact in knowledge_service.facts(db, program.id):                  # what the admin told about it
            shared = len(asked & _words(f"{fact.title} {fact.content}"))
            if shared > score:
                best, score = fact, shared
        if best is not None and score >= 2:
            lines.append(best.content.strip())
        if not lines:
            return None
        return {"reply": "\n".join(lines), "links": [{"label": program.name, "url": f"/programs/{program.id}"}], "source": "record",
                "sources": [{"label": program.name, "url": f"/programs/{program.id}"}], "resources": []}
    finally:
        ui_words.current.reset(token)

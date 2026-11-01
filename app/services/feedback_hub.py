"""Every feedback in one place, wherever it was given: published on 9antra.tn (and Facebook reviews, once the page is
connected), the reviews candidates give in the application at the end of a program, and the weekly check-ins (how the
week went). One reading for all: how many are happy, the stars, what people talk about, what to improve, and who is
waiting for an answer. Shown as one section on the dashboard; read by rules only (feedback.THEMES, the stored tones).
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

SOURCES = {
    "website": {"label": "9antra.tn", "icon": "bi-globe2", "colour": "#0b8ec4", "url": "/website#feedback"},
    "facebook": {"label": "Facebook", "icon": "bi-facebook", "colour": "#1877f2", "url": "/website#feedback"},
    "review": {"label": "End-of-program reviews", "icon": "bi-star-fill", "colour": "#d97706", "url": "/programs/all"},
    "weekly": {"label": "Weekly check-ins", "icon": "bi-calendar-week", "colour": "#7c3aed", "url": "/quiz/weekly"},
}


def _themes_of(text: str) -> list[str]:
    from app.services import feedback

    return feedback.analyse(text)["themes"]


def _naive(moment) -> datetime | None:
    if moment is None:
        return None
    return moment.replace(tzinfo=None) if getattr(moment, "tzinfo", None) else moment


def items(db: Session) -> list[dict]:
    """Every feedback as one kind of row: {"source", "name", "text", "improve", "stars", "sentiment", "themes", "program",
    "when", "url", "waiting"}."""
    from app.models import Candidate, Quiz
    from app.models.feedback import CandidateFeedback
    from app.services import feedback, program_feedback

    found = []
    for row in feedback._rows(db):
        reading = row["reading"] or {}
        found.append({
            "source": "facebook" if reading.get("source") == "facebook" else "website",
            "name": row["name"] or "", "text": row["text"], "improve": "", "stars": row["stars"],
            "sentiment": reading.get("sentiment"), "themes": list(reading.get("themes") or []), "program": None,
            "when": _naive(row["item"].first_seen_at), "url": "/website#feedback", "waiting": row["waiting"],
            "praise": list(reading.get("positive") or []),
        })
    for view in program_feedback.summary(db.query(CandidateFeedback).filter(CandidateFeedback.source == program_feedback.SOURCE).all())["rows"]:
        if not view["answered"]:
            continue
        row = view["row"]
        found.append({
            "source": "review", "name": view["name"], "text": view["words"], "improve": view["improve"], "stars": view["stars"],
            "sentiment": row.sentiment, "themes": [key for key, theme in feedback.THEMES.items() if theme in view["themes"]],
            "improve_themes": [key for key, theme in feedback.THEMES.items() if theme in view["improve_themes"]],
            "program": view["program"], "when": _naive(view["when"]),
            "url": f"/candidate/{row.candidate_id}#reviews" if row.candidate_id else "/programs/all", "waiting": False,
            "recommend": view["recommend"], "praise": [], "public": view["may_publish"],
        })
    names = {}
    for quiz in db.query(Quiz).filter(Quiz.kind == "weekly", Quiz.feedback.isnot(None)).all():
        try:
            said, read = json.loads(quiz.feedback or "{}"), json.loads(quiz.insight or "{}")
        except ValueError:
            continue
        words = " ".join(part for part in (said.get("message"), said.get("hard")) if part and str(part).strip().lower() not in ("rien", "nothing", "non", "no", "-"))
        if quiz.candidate_id not in names:
            person = db.get(Candidate, quiz.candidate_id)
            names[quiz.candidate_id] = (person.full_name or person.email) if person else ""
        mood = said.get("mood")
        found.append({
            "source": "weekly", "name": names[quiz.candidate_id], "text": words.strip(), "improve": "",
            "stars": None, "mood": mood if isinstance(mood, int) else None,
            "sentiment": read.get("sentiment") or ({5: "positive", 4: "positive", 3: "mixed"}.get(mood) if isinstance(mood, int) else None)
            or ("negative" if isinstance(mood, int) else None),
            "themes": _themes_of(words) if words else [], "program": f"{quiz.course_name} · week {quiz.week_number}" if quiz.week_number else quiz.course_name,
            "when": _naive(quiz.completed_at), "url": f"/classes/{quiz.class_group_id}/course" if quiz.class_group_id else "/quiz/weekly",
            "waiting": False, "praise": [],
        })
    return found


def overview(db: Session, source: str | None = None) -> dict:
    """One reading of everything (or of one source): the figures, the subjects, what to improve, the rows."""
    from app.services import feedback

    every = items(db)
    rows = [row for row in every if source in (None, "", "all") or row["source"] == source or (source == "answer" and row["sentiment"] in ("negative", "mixed"))]
    judged = [row for row in rows if row["sentiment"] in ("positive", "mixed", "negative")]
    counts = {key: sum(1 for row in judged if row["sentiment"] == key) for key in ("positive", "mixed", "negative")}
    stars = [row["stars"] for row in rows if row.get("stars")]
    recommend = [row["recommend"] for row in rows if row.get("recommend")]
    themes: dict[str, dict] = {}
    for row in rows:
        for key in row["themes"]:
            entry = themes.setdefault(key, {"count": 0, "happy": 0, "sources": set()})
            entry["count"] += 1
            entry["happy"] += row["sentiment"] == "positive"
            entry["sources"].add(row["source"])
    talked = sum(1 for row in rows if row["themes"]) or 1
    subjects = sorted(({**feedback.THEMES[key], "key": key, "count": value["count"], "happy": value["happy"],
                        "share": round(100 * value["count"] / talked), "sources": sorted(value["sources"])}
                       for key, value in themes.items() if key in feedback.THEMES), key=lambda item: -item["count"])
    improve: dict[str, int] = {}
    for row in rows:
        for key in row.get("improve_themes") or []:
            improve[key] = improve.get(key, 0) + 1
        if row["sentiment"] in ("negative", "mixed"):
            for key in row["themes"]:
                improve[key] = improve.get(key, 0) + 1
    praise: dict[str, int] = {}
    for row in rows:
        for word in row.get("praise") or []:
            praise[word] = praise.get(word, 0) + 1
    order = {"negative": 0, "mixed": 1}
    ordered = sorted(rows, key=lambda row: (order.get(row["sentiment"], 2), -(row["when"].timestamp() if row["when"] else 0)))
    for row in ordered:
        row["view"] = feedback.view(row["sentiment"])
        row["theme_views"] = [feedback.THEMES[key] for key in row["themes"][:3] if key in feedback.THEMES]
        row["source_view"] = SOURCES[row["source"]]
    return {
        "total": len(every), "shown": len(rows), "source": source or "all",
        "sources": [{"key": key, **value, "count": sum(1 for row in every if row["source"] == key)} for key, value in SOURCES.items()
                    if key != "facebook" or any(row["source"] == "facebook" for row in every)],
        "counts": counts, "judged": len(judged), "happy_share": round(100 * counts["positive"] / len(judged)) if judged else None,
        "stars": round(sum(stars) / len(stars), 1) if stars else None, "rated": len(stars),
        "recommend_pct": round(100 * sum(1 for value in recommend if value == "yes") / len(recommend)) if recommend else None,
        "subjects": subjects[:6],
        "improve": [{**feedback.THEMES[key], "key": key, "count": count} for key, count in sorted(improve.items(), key=lambda pair: -pair[1])
                    if key in feedback.THEMES][:4],
        "praise": sorted(praise.items(), key=lambda pair: -pair[1])[:8],
        "to_answer": sum(1 for row in rows if row["sentiment"] in ("negative", "mixed")),
        "waiting": sum(1 for row in rows if row["waiting"]),
        "rows": ordered,
    }

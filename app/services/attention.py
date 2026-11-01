"""How a questions page was answered (learn.html): the time on each question, the answers changed, and how often and how
long the candidate left the page. The candidate is told on the page that it is noted.

The page measures it; what it sends is checked here (numbers only, within limits) and kept on the quiz (quizzes.attention).
reading() says it in a few plain lines for the professor and the admin, with the score: it never accuses anyone, it says
what the times are, and points at what is worth a word (a page left during a question that was then right, answers
given in a couple of seconds).
"""
from __future__ import annotations

import json

FAST = 4            # seconds: an answer this quick was hardly read
LONG_AWAY = 20      # seconds away from the page that are worth saying
MOST_SECONDS = 4 * 3600


def _number(value, most: int = MOST_SECONDS) -> int:
    try:
        return max(0, min(most, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def clean(raw: str | None, questions: int) -> dict | None:
    """What the page sent, kept only when it is what the page writes."""
    try:
        data = json.loads(raw or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    per = data.get("per_question") if isinstance(data.get("per_question"), list) else []
    rows = []
    for index in range(questions):
        item = per[index] if index < len(per) and isinstance(per[index], dict) else {}
        rows.append({"s": _number(item.get("s")), "changes": _number(item.get("changes"), 50),
                     "away_s": _number(item.get("away_s")), "first_s": _number(item.get("first_s"))})
    return {"total_s": _number(data.get("total_s")), "left": _number(data.get("left"), 500), "away_s": _number(data.get("away_s")),
            "blurs": _number(data.get("blurs"), 500), "per_question": rows}


def store(quiz, raw: str | None, questions: int) -> None:
    found = clean(raw, questions)
    if found is not None:
        quiz.attention = json.dumps(found)


def of(quiz) -> dict | None:
    try:
        found = json.loads(getattr(quiz, "attention", None) or "")
    except (TypeError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def duration(seconds: int) -> str:
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest:02d}" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d}"


def reading(quiz, rows: list[dict] | None = None) -> dict | None:
    """{"lines": [...], "tone": ok | watch, "total", "left", "away", "per": [...]} for the professor and the admin."""
    from app.services.ui_words import t as _t

    found = of(quiz)
    if not found:
        return None
    per = found.get("per_question") or []
    lines = [_t("Answered in {0}.", duration(found.get("total_s", 0)))]
    tone = "ok"
    if found.get("left"):
        lines.append(_t("Left the page {0} time(s), {1} away in all.", found["left"], duration(found.get("away_s", 0))))
        if found.get("away_s", 0) >= LONG_AWAY:
            tone = "watch"
    else:
        lines.append(_t("Stayed on the page the whole time."))
    flagged = []
    for index, item in enumerate(per):
        right = rows[index]["is_right"] if rows and index < len(rows) else None
        if item.get("away_s", 0) >= LONG_AWAY and right:
            flagged.append(_t("question {0}: right after {1} away from the page", index + 1, duration(item["away_s"])))
        elif 0 < item.get("s", 0) <= FAST and not item.get("away_s"):
            flagged.append(_t("question {0}: answered in {1}", index + 1, duration(item["s"])))
    if flagged:
        tone = "watch"
        lines.append(_t("Worth a word: {0}.", "; ".join(flagged[:3])))
    changed = sum(item.get("changes", 0) for item in per)
    if changed:
        lines.append(_t("Changed an answer {0} time(s).", changed))
    return {"lines": lines, "tone": tone, "total": duration(found.get("total_s", 0)), "left": found.get("left", 0),
            "away": duration(found.get("away_s", 0)),
            "per": [{"n": index + 1, "time": duration(item.get("s", 0)), "away": item.get("away_s", 0)} for index, item in enumerate(per)]}

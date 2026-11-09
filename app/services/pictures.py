"""The moving picture that comes with each of Yasmine's answers.

News and trends come with a real photo from the journal (it moves slowly in the window); everything else with the
animated picture of its subject (_ui_art.html: payments, sessions, candidates…), drawn here so the window only places it.
Small talk has none: she is just talking.
"""
from __future__ import annotations

import re
import threading

from sqlalchemy.orm import Session

# Which picture says each agent's subject (an agent's key, else the subject the question was read as).
SCENES = {
    "programs": "programs", "program": "programs", "planning": "sessions", "payment": "payments", "registration": "candidates",
    "technical": "settings", "complaint": "feelings", "trends": "news", "system": "settings", "marketing": "posts",
    "inbox": "inbox", "set_aside": "inbox", "review": "inbox", "professor": "classes", "guide": "assistant",
    "summary": "data", "knowledge": "website", "keep": "feelings",
}
# For the agents whose answers speak of anything (the centre's figures, what it knows): what the answer speaks of.
_SPOKEN = [
    ("payments", r"\b(pa(?:id|y|yé|yée?s?|iement\w*)|owe\w*|doi\w+|dt|reste)\b"),
    ("sessions", r"\b(s[ée]ances?|sessions?|planning|schedule|horaires?)\b"),
    ("classes", r"\b(class(?:e|es)?|groupes?)\b"),
    ("candidates", r"\b(candidat\w*|inscri\w*|registr\w*)\b"),
    ("inbox", r"\b(e-?mails?|messages?|whatsapp)\b"),
    ("programs", r"\b(programs?|programmes?|formations?|courses?|cours)\b"),
    ("feelings", r"\b(avis|reviews?|[ée]toiles?|stars?)\b|★"),
]
_ENV = None
_LOCK = threading.Lock()


def scene(name: str) -> str:
    """The animated picture's markup (an SVG of the application's own)."""
    global _ENV
    with _LOCK:
        if _ENV is None:
            from jinja2 import Environment, FileSystemLoader

            _ENV = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    return str(_ENV.get_template("_ui_art.html").module.scene(name)).strip()


def _photo(db: Session, reply: str) -> dict | None:
    """A photo of the journal: one whose article the answer speaks of, else the latest with a photo."""
    from app.models.news_item import NewsItem

    rows = db.query(NewsItem).filter(NewsItem.image_url.isnot(None)).order_by(NewsItem.published_at.desc()).limit(40).all()
    if not rows:
        return None
    said = set(re.findall(r"[^\W\d_]{4,}", (reply or "").lower()))
    best = max(rows, key=lambda row: len(said & set(re.findall(r"[^\W\d_]{4,}", (row.title or "").lower()))))
    return {"photo": best.image_url, "caption": best.title, "url": best.url}


def for_answer(db: Session, found: dict, agent: str | None, category: str | None) -> dict | None:
    """{"svg", "scene"} or {"photo", "caption", "url"}; None for small talk."""
    if agent == "talk" or not found.get("reply"):
        return None
    if agent in ("trends", "marketing") or category == "trends":
        photo = _photo(db, found["reply"])
        if photo:
            return photo
    name = found.get("scene") or SCENES.get(agent or "") or SCENES.get(category or "")
    if not name or name == "assistant":
        text = (found.get("reply") or "").lower()
        name = next((scene_name for scene_name, words in _SPOKEN if re.search(words, text)), None) or name or "assistant"
    try:
        return {"svg": scene(name), "scene": name}
    except Exception:
        return None

"""The interface in French and Arabic.

The pages are written in English. Every piece of text they hold is listed here
(read from the templates and from the texts the services write), translated
once, and kept in the database: the most common words by hand, the rest by the
local model, a few at a time, starting with what was actually seen on a page.
A page then swaps its texts in the browser. Names, emails and anything typed
by people are never sent to translation: only the application's own words are.

A translation is kept as long as its English text exists; a text that changes
is a new line and is translated again.
"""
from __future__ import annotations

import ast
import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.ui_translation import UiTranslation
from app.services.translation_seed import SEED

logger = logging.getLogger(__name__)

LANGUAGES = {"en": "English", "fr": "Français", "ar": "العربية"}
TARGETS = {"fr": "French", "ar": "Arabic"}
BATCH = 10                      # texts translated for one language in one pass of the job
TEMPLATES = Path("app/templates")
# Pages written for candidates, already in French, and the emails: not part of the interface.
_NOT_INTERFACE = ("level_check", "join", "opinion", "learn", "login", "setup", "email", "quiz_take", "public")
# Services whose sentences appear on the pages.
_SPEAKING = ("guides", "system_status", "advisor", "notification_fixes", "backups")

_JINJA = re.compile(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", re.S)
_BLOCKS = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_TEXT = re.compile(r">([^<>]+)<")
_ATTRIBUTE = re.compile(r'\b(?:placeholder|title|aria-label|data-confirm|alt)="([^"]+)"')
_LETTERS = re.compile(r"[A-Za-z]{2}")
_ARABIC = re.compile(r"[؀-ۿ]")
_BRACES = re.compile(r"\{[a-z_]+\}")
_cache: list[str] | None = None


def clean_language(value) -> str:
    return value if value in LANGUAGES else "en"


def digest(source: str) -> str:
    return hashlib.sha1(source.encode("utf-8")).hexdigest()


def _worth(text: str) -> bool:
    return (2 <= len(text) <= 220 and _LETTERS.search(text) is not None and "@" not in text and "://" not in text
            and not _ARABIC.search(text) and not text.startswith(("/", "#", "bi-", "data-")) and "_" not in text.split(" ")[0])


def _pieces(markup: str) -> list[str]:
    """The texts a template shows: what stands between tags and in the attributes people read,
    cut where a value is inserted."""
    markup = _BLOCKS.sub(" ", markup)
    found = []
    for raw in _TEXT.findall(markup) + _ATTRIBUTE.findall(markup):
        for part in _JINJA.split(raw):
            text = " ".join(part.replace("&nbsp;", " ").replace("&amp;", "&").split()).strip(" ·:,;()[]–-|")
            # An example of an email (“Bonjour {first_name}, …”) is shown as it will be sent, not translated.
            if _worth(text) and not _BRACES.search(text) and "&#10;" not in text and not text.startswith(("Bonjour", "Bienvenue", "Votre ", "Vous ")):
                found.append(text)
    return found


def _spoken(path: Path) -> list[str]:
    """The sentences a service writes: its text constants, and the fixed parts of the ones it fills in."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = " ".join(node.value.split()).strip(" ·:,;()[]–-|")
            if (" " in text and _worth(text) and not text.startswith(("select ", "SELECT ", "PRAGMA", "Bonjour"))
                    and "{first_name}" not in text and "**" not in text and "[box" not in text):
                found.append(text)
    return found


def sources(refresh: bool = False) -> list[str]:
    """Every text of the interface, each once. Read from the files once per start."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    seen: dict[str, None] = {key: None for key in SEED}
    if TEMPLATES.is_dir():
        for path in sorted(TEMPLATES.glob("*.html")):
            if path.stem.startswith(_NOT_INTERFACE):
                continue
            try:
                for text in _pieces(path.read_text(encoding="utf-8")):
                    seen.setdefault(text, None)
            except OSError:
                continue
    for name in _SPEAKING:
        for text in _spoken(Path("app/services") / f"{name}.py"):
            seen.setdefault(text, None)
    # Labels that reach the pages as values: who a reminder goes to, the ideas, the looks of a PDF.
    from app.services import program_pdf, reminders

    extra = list(reminders.AUDIENCES.values()) + [idea["name"] for idea in reminders.IDEAS]
    extra += [value for style in program_pdf.STYLES.values() for value in (style["name"], style["what"])]
    for text in extra:
        if _worth(text):
            seen.setdefault(text, None)
    _cache = list(seen)
    return _cache


def ensure(db: Session) -> int:
    """A line for every text in every language; the hand-made translations filled in. Returns how many lines were added."""
    added = 0
    texts = sources()
    for lang, position in (("fr", 0), ("ar", 1)):
        known = {row.source_hash: row for row in db.query(UiTranslation).filter(UiTranslation.lang == lang).all()}
        for source in texts:
            key = digest(source)
            row = known.get(key)
            seed = SEED.get(source)
            if row is None:
                db.add(UiTranslation(lang=lang, source_hash=key, source=source, text=seed[position] if seed else None,
                                     origin="seed" if seed else None, updated_at=datetime.utcnow() if seed else None))
                added += 1
            elif seed and row.origin in (None, "seed") and row.text != seed[position]:
                row.text, row.origin, row.updated_at = seed[position], "seed", datetime.utcnow()
    db.commit()
    return added


def dictionary(db: Session, lang: str) -> dict:
    """What a page needs: {"t": {English: translation}, "p": [texts not translated yet]}."""
    current = set(sources())
    done, waiting = {}, []
    for row in db.query(UiTranslation).filter(UiTranslation.lang == lang).all():
        if row.source not in current:
            continue
        if row.text:
            done[row.source] = row.text
        else:
            waiting.append(row.source)
    return {"t": done, "p": waiting}


def version(db: Session, lang: str) -> str:
    """Changes whenever a translation is added, so browsers take the new ones."""
    done = db.query(UiTranslation).filter(UiTranslation.lang == lang, UiTranslation.text.isnot(None)).count()
    return f"{lang}-{done}-{len(sources())}"


def progress(db: Session) -> dict:
    total = len(sources())
    current = set(sources())
    found = {}
    for lang in TARGETS:
        rows = db.query(UiTranslation).filter(UiTranslation.lang == lang, UiTranslation.text.isnot(None)).all()
        found[lang] = {"done": sum(1 for row in rows if row.source in current), "total": total, "name": LANGUAGES[lang],
                       "used": db.query(UiTranslation.id).filter(UiTranslation.lang == lang, UiTranslation.seen_at.isnot(None)).first() is not None}
    return found


def mark_seen(db: Session, lang: str, texts: list[str]) -> int:
    """Texts a page showed without a translation: they are translated first. Only the interface's own texts count."""
    if lang not in TARGETS or not texts:
        return 0
    keys = [digest(text) for text in texts[:300] if isinstance(text, str)]
    rows = db.query(UiTranslation).filter(UiTranslation.lang == lang, UiTranslation.source_hash.in_(keys), UiTranslation.text.is_(None)).all()
    now = datetime.utcnow()
    for row in rows:
        row.seen_at = now
    db.commit()
    return len(rows)


def acceptable(source: str, text, lang: str) -> str | None:
    """A translation the model gave, kept only when it can be one."""
    if not isinstance(text, str):
        return None
    text = " ".join(text.split()).strip().strip('"«»“”').strip()
    if not text or len(text) > len(source) * 3 + 14 or "\n" in text:
        return None
    if sorted(_BRACES.findall(source)) != sorted(_BRACES.findall(text)):
        return None
    if sorted(re.findall(r"\d+", source)) != sorted(re.findall(r"\d+", text)):
        return None
    if lang == "ar" and not _ARABIC.search(text):
        return None
    if lang == "fr" and _ARABIC.search(text):
        return None
    if re.match(r"(?i)^(translation|traduction|here is|voici)\b", text):
        return None
    return text


def translate_batch(db: Session, lang: str, limit: int = BATCH) -> int:
    """Ask the local model for a few translations, the texts seen on a page first. Returns how many were kept.
    One text at a time, answered in plain words: asked for several at once, the small model garbles Arabic."""
    from app.services import email_assistant

    rows = (db.query(UiTranslation).filter(UiTranslation.lang == lang, UiTranslation.text.is_(None), UiTranslation.tries < 2)
            .order_by(UiTranslation.seen_at.is_(None), UiTranslation.seen_at.desc(), UiTranslation.tries.asc()).limit(limit * 4).all())
    current = set(sources())
    rows = [row for row in rows if row.source in current][:limit]
    if not rows:
        return 0
    into = "Arabic (Modern Standard Arabic)" if lang == "ar" else TARGETS[lang]
    system = (f"Translate this label of a web application for a training centre from English into {into}. "
              "Answer with the translation only, nothing else. Keep numbers, symbols and words inside {braces} exactly as they are.")
    kept = 0
    for row in rows:
        row.tries = (row.tries or 0) + 1
        answer = email_assistant._chat(system, row.source, as_json=False, max_tokens=max(60, len(row.source)))
        text = acceptable(row.source, (answer or "").split("\n")[0], lang)
        if text:
            row.text, row.origin, row.updated_at = text, "model", datetime.utcnow()
            kept += 1
        db.commit()
    return kept


def work(db: Session) -> int:
    """One pass of the job: the languages somebody chose, what was seen first. Returns how many translations were kept."""
    from app.services import email_assistant

    languages = [lang for lang, state in progress(db).items() if state["used"] and state["done"] < state["total"]]
    if not languages or not email_assistant.model_available():
        return 0
    return sum(translate_batch(db, lang) for lang in languages)


def listing(db: Session, lang: str, search: str = "", only: str = "", limit: int = 80) -> list[UiTranslation]:
    """Translations to read and correct: those the model made first, then the rest."""
    current = set(sources())
    query = db.query(UiTranslation).filter(UiTranslation.lang == lang)
    if search.strip():
        like = f"%{search.strip()}%"
        query = query.filter(UiTranslation.source.ilike(like) | UiTranslation.text.ilike(like))
    if only in ("model", "seed", "admin"):
        query = query.filter(UiTranslation.origin == only)
    elif only == "waiting":
        query = query.filter(UiTranslation.text.is_(None))
    rows = query.order_by(UiTranslation.origin != "model", UiTranslation.updated_at.desc()).limit(limit * 3).all()
    return [row for row in rows if row.source in current][:limit]


def correct(db: Session, lang: str, source: str, text: str) -> bool:
    """The admin's own wording for one text: it replaces the translation and is never overwritten."""
    row = db.query(UiTranslation).filter(UiTranslation.lang == lang, UiTranslation.source_hash == digest(source)).first()
    if row is None or not text.strip():
        return False
    row.text, row.origin, row.updated_at = " ".join(text.split()), "admin", datetime.utcnow()
    db.commit()
    return True

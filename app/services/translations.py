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
from app.services.translation_seed import MORE, SEED

logger = logging.getLogger(__name__)

LANGUAGES = {"en": "English", "fr": "Français", "ar": "العربية"}
TARGETS = {"fr": "French", "ar": "Arabic"}
BATCH = 10                      # texts translated for one language in one pass of the job
TEMPLATES = Path("app/templates")
# Pages written for candidates, already in French, and the emails: not part of the interface.
_NOT_INTERFACE = ("level_check", "join", "opinion", "learn", "login", "setup", "email", "quiz_take", "public")
# Services whose sentences do not appear on the pages: what candidates receive (written in French), what the model is
# asked, what the assistant answers in the language it is asked in. Every other service's sentences are collected.
_SILENT = ("answer_sync", "background", "brand", "catalogue_reader", "chatbot", "chatbot_actions", "chatbot_data", "chatbot_send",
           "chatbot_sources", "class_emails", "course_questions", "derja", "document_pdf", "email_account", "email_assistant",
           "email_composer", "email_files", "email_layout", "email_service", "email_templates", "email_threads", "embedding_service",
           "firebase_service", "google_forms", "jobs", "knowledge_service", "level_bank", "mailbox_service", "page_pdf_links",
           "program_assistant", "program_documents", "program_names", "program_pdf", "realtime", "reply_writer", "report_pdf",
           "translation_seed", "translation_more", "translations", "tunisian_banks", "website_sync", "weekly_questions", "welcome",
           "emotions", "finder", "derja", "howto_tn", "howto_examples", "meaning", "prospects")       # (the last ones: lists of words to recognise, not words to show)
# What the routes say back on the page (“Saved.”, “The class is full.”): the words given as these.
_SAID = {"notice", "error", "detail", "message", "label", "title", "reply"}
RETRY_TRIES = 4                 # a text the model could not translate is tried again, with an example, up to this

_JINJA = re.compile(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", re.S)
_BLOCKS = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_TEXT = re.compile(r">([^<>]+)<")
_ATTRIBUTE = re.compile(r'\b(?:placeholder|title|aria-label|data-confirm|alt)="([^"]+)"')
_LETTERS = re.compile(r"[A-Za-z]{2}")
_ARABIC = re.compile(r"[؀-ۿ]")
_BRACES = re.compile(r"\{[a-z_]+\}")
_SCRIPTS = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)
_QUOTED = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'|`([^`]*)`')
_CODE = re.compile(r"[=<>{};\[\]\\#]|\.[a-z]|^[a-z]+[A-Z]|^[a-z-]+$|^[A-Z0-9_]+$|^\w+\(|[a-z]\(")
# Search patterns, template code and lists of class names: not words.
_PATTERN = re.compile(r"[|\\]|\(\?|\)\?|\{%|%\}|\{\{|\}\}|^[a-z0-9]+(?:-[a-z0-9]+)+$|^(?:\S*-\S*\s+){1,}\S*-\S*$"
                      r"|\s\?[\w/]|\w\+\w|\[[^\]]*\]|<\w+[\s>]|\"\"\"")
_FRENCH = re.compile(r"[éèêàçùûôîœ]|\b(le|la|les|des|du|une|vous|votre|est|sont|pour|avec|dans|sur|nous|pas|qui|que|aux|ces|cette"
                     r"|trop|peu|oui|nos|tout|bien|aucun|rien|quelques|informations|tarifs|rejoindre|merci)\b", re.I)
_ENTITY = re.compile(r"&[a-z]+;?|&#\d+;?")
_cache: list[str] | None = None


def clean_language(value) -> str:
    return value if value in LANGUAGES else "en"


def digest(source: str) -> str:
    return hashlib.sha1(source.encode("utf-8")).hexdigest()


def _worth(text: str) -> bool:
    return (2 <= len(text) <= 600 and _LETTERS.search(text) is not None and "@" not in text and "://" not in text
            and not _ARABIC.search(text) and not text.startswith(("/", "#", "bi-", "bi ", "data-")) and "_" not in text.split(" ")[0])


def _pieces(markup: str) -> list[str]:
    """The texts a template shows: what stands between tags and in the attributes people read,
    cut where a value is inserted."""
    found = []
    # Sentences written inside the template's code: {{ 'Nothing to answer' if … else 'Open the inbox' }}.
    for code in _JINJA.findall(_BLOCKS.sub(" ", markup)):
        for match in _QUOTED.finditer(code):
            text = " ".join((next(group for group in match.groups() if group is not None)).split()).strip(" ·:,;()[]–-|")
            if " " in text and (text[:1].isupper() or re.fullmatch(r"[a-z][a-z',]*(?: [a-z',]+)+[.!?…]?", text)) \
                    and _worth(text) and not _CODE.search(text) and not _PATTERN.search(text) \
                    and not _FRENCH.search(text) and not _BRACES.search(text):
                found.append(text)
    # The template's own code goes first: a “>” inside {% if a > b %} is not the end of a tag.
    markup = _JINJA.sub("\x00", _BLOCKS.sub(" ", markup))
    for raw in _TEXT.findall(markup) + _ATTRIBUTE.findall(markup):
        for part in raw.split("\x00"):
            text = " ".join(_ENTITY.sub(" ", part.replace("&amp;", "&")).split()).strip(" ·:,;()[]–-|")
            # An example of an email (“Bonjour {first_name}, …”) is shown as it will be sent, not translated.
            if _worth(text) and not _BRACES.search(text) and not _FRENCH.search(text):
                found.append(text)
    return found


def _scripted(markup: str) -> list[str]:
    """The texts a page's own script writes on it (“Free day”, “Saving…”): its quoted words that read as words, not code."""
    found = []
    for script in _SCRIPTS.findall(markup):
        for match in _QUOTED.finditer(_JINJA.sub("\x00", script)):
            raw = next(group for group in match.groups() if group is not None)
            for part in re.split(r"\$\{[^}]*\}|\x00", raw):
                text = " ".join(part.replace("\\n", " ").split()).strip(" ·:,;()[]–-|")
                if not _worth(text) or _CODE.search(text) or _FRENCH.search(text) or _PATTERN.search(text):
                    continue
                if " " in text or re.fullmatch(r"[A-Z][a-z]{2,}[.!…]?", text):
                    found.append(text)
    return found


def _said(text: str) -> bool:
    return (" " in text and _worth(text) and not text.startswith(("select ", "SELECT ", "PRAGMA", "Bonjour"))
            and "{first_name}" not in text and "**" not in text and "[box" not in text and not _FRENCH.search(text)
            and not _PATTERN.search(text) and not re.search(r"\{\d", text))      # “{0} owes {1}”: translated where it is written (ui_words)


def _spoken(path: Path, only_said: bool = False) -> list[str]:
    """The sentences a service writes: its text constants, and the fixed parts of the ones it fills in. Not its
    explanations (docstrings) nor what it writes in the log. For a route, only the words it says back on the page."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    skip: set[int] = set()
    said: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in ("logger", "logging", "log"):
            skip |= {id(inner) for inner in ast.walk(node)}
        if isinstance(node, ast.Call):
            said += [keyword.value for keyword in node.keywords if keyword.arg in _SAID]
        if isinstance(node, ast.Dict):
            said += [value for key, value in zip(node.keys, node.values) if isinstance(key, ast.Constant) and key.value in _SAID]
        # A list of words to recognise (“j'ai compris”, “not clear”, “globe with meridians”): all small letters.
        for group in ((node.elts,) if isinstance(node, (ast.Tuple, ast.List, ast.Set)) else
                      (node.keys, node.values) if isinstance(node, ast.Dict) else ()):
            words = [item for item in group if isinstance(item, ast.Constant) and isinstance(item.value, str)]
            if len(words) >= 4 and len(words) == len(group) and all(item.value == item.value.lower() for item in words):
                skip |= {id(item) for item in words}
    roots = said if only_said else [tree]
    found, seen = [], set()
    for root in roots:
        for node in ast.walk(root):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip and id(node) not in seen:
                seen.add(id(node))
                text = " ".join(node.value.split()).strip(" ·:,;()[]–-|")
                if _said(text):
                    found.append(text)
    return found


def sources(refresh: bool = False) -> list[str]:
    """Every text of the interface, each once. Read from the files once per start."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    # Every text translated by hand counts: some reach the pages built from pieces the files do not show whole.
    seen: dict[str, None] = {key: None for key in SEED}
    seen.update({key: None for key in MORE})
    if TEMPLATES.is_dir():
        for path in sorted(TEMPLATES.glob("*.html")):
            if path.stem.startswith(_NOT_INTERFACE):
                continue
            try:
                markup = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for text in _pieces(markup) + _scripted(markup):
                seen.setdefault(text, None)
    for path in sorted(Path("app/services").glob("*.py")):
        if path.stem not in _SILENT:
            for text in _spoken(path):
                seen.setdefault(text, None)
    for path in sorted(Path("app/api").glob("*.py")):
        for text in _spoken(path, only_said=True):
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
            seed = SEED.get(source) or MORE.get(source)
            if row is None:
                db.add(UiTranslation(lang=lang, source_hash=key, source=source, text=seed[position] if seed else None,
                                     origin="seed" if seed else None, updated_at=datetime.utcnow() if seed else None))
                added += 1
            elif seed and row.origin in (None, "seed", "model") and row.text != seed[position]:     # by hand beats the model; the admin's own wording stays
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
    if re.match(r"(?i)^(translation|traduction|here is|here's|voici|i can|i'll|i will|sorry|je ne peux)\b", text) or re.search(r"(?i)\b(i can't|i cannot|as an ai)\b", text):
        return None
    if text.startswith("{") and not source.startswith("{") or text.endswith("}") and not source.endswith("}"):
        return None                     # the small model wraps some answers in braces
    if text.lower() in ("english", "french", "arabic", "français", "anglais") and source.lower() not in ("english", "french", "arabic"):
        return None
    if lang == "ar":
        # Arabic with words of other languages in it (German, Vietnamese...): only the names the source has may stay.
        latin = set(re.findall(r"[A-Za-zÀ-ɏḀ-ỿ]{3,}", text)) - set(re.findall(r"[A-Za-z]{3,}", source))
        if latin or re.search(r"[À-ɏḀ-ỿ]", text):
            return None
    if lang == "fr" and len(source) > 12 and text.lower() == source.lower() and re.search(r"(?i)\b(the|your|you|and|with)\b", source):
        return None                     # left in English
    return text


def translate_batch(db: Session, lang: str, limit: int = BATCH) -> int:
    """Ask the local model for a few translations, the texts seen on a page first. Returns how many were kept.
    One text at a time, answered in plain words: asked for several at once, the small model garbles Arabic."""
    from app.services import email_assistant

    # Only what a page actually showed: every text known today is translated by hand (translation_more.json), so
    # what is left for the model is a new text that appeared on a page, never a piece of code that merely looks like one.
    rows = (db.query(UiTranslation).filter(UiTranslation.lang == lang, UiTranslation.text.is_(None), UiTranslation.tries < RETRY_TRIES,
                                           UiTranslation.seen_at.isnot(None))
            .order_by(UiTranslation.seen_at.is_(None), UiTranslation.seen_at.desc(), UiTranslation.tries.asc()).limit(limit * 4).all())
    current = set(sources())
    rows = [row for row in rows if row.source in current][:limit]
    if not rows:
        return 0
    into = "Arabic (Modern Standard Arabic)" if lang == "ar" else TARGETS[lang]
    system = (f"Translate this label of a web application for a training centre from English into {into}. "
              "Answer with the translation only, nothing else. Keep numbers, symbols and words inside {braces} exactly as they are.")
    example = ("Example: “Send me a test” → “Envoyez-moi un essai”." if lang == "fr"
               else "Example: “Send me a test” → “أرسل لي تجربة”.")
    kept = 0
    for row in rows:
        row.tries = (row.tries or 0) + 1
        # Tried twice already: once more with an example, which the small model follows better.
        asked = system if row.tries <= 2 else f"{system} {example}"
        answer = email_assistant._chat(asked, row.source, as_json=False, max_tokens=max(60, len(row.source)))
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

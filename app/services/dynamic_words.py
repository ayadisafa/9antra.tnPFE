"""What the application writes as it goes (its notifications above all), shown in the language of the page.

A notification is kept as it was written, with the names in it. It is shown in English, French or Arabic by, in turn:
1. the interface's own translations, when it is one of its fixed texts;
2. a sentence written by hand in the three languages with a place for each value (dynamic_phrases.json: “Payment proof
   from {0}: {1}”); a value that is itself a fixed text of the interface is translated too, names stay as they are;
3. else the local model, once, in the background; kept from then on (TextTranslation). Until then, the text as written.

Where a notification leads (`target_of`): its own link, else the page of what it is about, never nowhere.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_PHRASES = Path(__file__).with_name("dynamic_phrases.json")
_compiled: list[tuple[str, re.Pattern, dict]] | None = None
_wanted: dict[tuple[str, str], str] = {}          # (lang, text) -> text: asked for on a page, not translated yet
_lock = threading.Lock()
MOST_WANTED = 400
RETRY_TRIES = 3
_ARABIC = re.compile(r"[؀-ۿ]")
_FRENCH = re.compile(r"[éèêàçùûôîœ]|\b(le|la|les|des|du|une|est|sont|pour|avec|dans|sur|nous|vous|pas|qui|que|aux|cette|été|à)\b", re.I)
_ENGLISH = re.compile(r"\b(the|is|are|and|for|with|was|were|has|have|of|to|your|you|not|new|from|by|been|its|their)\b", re.I)


def language_of(text: str) -> str:
    """en | fr | ar: the language a text is written in, roughly."""
    letters = re.findall(r"[^\W\d_]", text or "")
    if letters and sum(1 for char in letters if _ARABIC.match(char)) / len(letters) > 0.3:
        return "ar"
    return "fr" if len(_FRENCH.findall(text or "")) > len(_ENGLISH.findall(text or "")) else "en"


def _regex(pattern: str) -> re.Pattern:
    parts, seen = [], set()
    for piece in re.split(r"(\{\d+\})", pattern):
        found = re.fullmatch(r"\{(\d+)\}", piece)
        if found:
            name = f"v{found.group(1)}"
            parts.append(f"(?P={name})" if name in seen else f"(?P<{name}>.+?)")
            seen.add(name)
        else:
            parts.append(re.escape(piece))
    return re.compile("^" + "".join(parts) + "$", re.S)


def phrases() -> list[tuple[str, re.Pattern, dict]]:
    """(source language, its pattern, the sentence in every language), longest first."""
    global _compiled
    if _compiled is None:
        rows = json.loads(_PHRASES.read_text(encoding="utf-8")) if _PHRASES.exists() else []
        found = []
        for row in rows:
            for lang in ("en", "fr", "ar"):
                if row.get(lang):
                    found.append((lang, _regex(row[lang]), row))
        found.sort(key=lambda item: -len(item[1].pattern))
        _compiled = found
    return _compiled


def _value(text: str, words: dict, lang: str = "fr") -> str:
    """A value put in a sentence: translated when it is a fixed text of the interface (or a list of them)."""
    clean = text.strip()
    if clean in words:
        return words[clean]
    parts = [part.strip() for part in clean.split(",")]
    if len(parts) > 1 and all(part in words for part in parts):
        return ("، " if lang == "ar" else ", ").join(words[part] for part in parts)
    return text


_words_kept: dict[str, tuple[str, dict]] = {}


def words_for(db: Session, lang: str) -> dict:
    """The interface's translations in `lang`, kept in memory until one is added (translations.version)."""
    from app.services import translations

    if lang not in translations.TARGETS:
        return {}
    version = translations.version(db, lang)
    kept = _words_kept.get(lang)
    if kept is None or kept[0] != version:
        from app.services.translation_seed import MORE, SEED

        position = 0 if lang == "fr" else 1
        hand = {text: pair[position] for text, pair in {**SEED, **MORE}.items() if pair and pair[position]}
        kept = (version, {**hand, **translations.dictionary(db, lang)["t"]})
        _words_kept[lang] = kept
    return kept[1]


def shower(db: Session, lang: str):
    """`shown` for one page: a function of a text only, for its templates."""
    words = words_for(db, lang)
    cache: dict[str, str] = {}

    def shown_here(text) -> str:
        if not text or lang == "en" and language_of(str(text)) == "en":
            return text or ""
        text = str(text)
        if text not in cache:
            cache[text] = shown(db, text, lang, words, stored={})
        return cache[text]
    return shown_here


def _digest(lang: str, text: str) -> str:
    return hashlib.sha1(f"{lang}\n{text}".encode("utf-8")).hexdigest()


def shown(db: Session, text: str | None, lang: str, words: dict | None = None, stored: dict | None = None) -> str:
    """`text` in `lang` (en, fr or ar), as well as it can be said now."""
    from app.services import translations

    if not text or lang not in translations.LANGUAGES:
        return text or ""
    words = words if words is not None else (translations.dictionary(db, lang)["t"] if lang in translations.TARGETS else {})
    if text in words:
        return words[text]
    for source, pattern, row in phrases():
        found = pattern.match(text)
        if found is None:
            continue
        if source == lang:
            return text
        values = found.groupdict()
        return re.sub(r"\{(\d+)\}", lambda match: _value(values.get("v" + match.group(1), ""), words, lang), row[lang])
    if language_of(text) == lang:
        return text
    if stored is None:
        stored = _stored(db, [text], lang)
    known = stored.get(text)
    if known:
        return known
    with _lock:
        if len(_wanted) < MOST_WANTED:
            _wanted.setdefault((lang, text), text)
    return text


def _stored(db: Session, texts: list[str], lang: str) -> dict:
    from app.models.ui_translation import TextTranslation

    keys = {_digest(lang, text): text for text in texts if text}
    if not keys:
        return {}
    rows = db.query(TextTranslation).filter(TextTranslation.source_hash.in_(list(keys))).all()
    return {keys[row.source_hash]: row.text for row in rows if row.text and row.source_hash in keys}


# ------------------------------------------------------------------
# Notifications
# ------------------------------------------------------------------

_BY_CATEGORY = {
    "EMAIL_CONFIG": "/settings#email", "EMAIL_FAILED": "/inbox", "EMAIL_QUESTION": "/inbox", "EMAIL_ANSWERED": "/inbox",
    "CANDIDATE_EMAIL": "/inbox", "WEEK_QUESTIONS": "/quiz/weekly", "LEVEL": "/quiz/all", "PROGRAM_APPROVAL": "/programs/all",
    "PROGRAM_PROPOSAL": "/programs/all", "PROGRAM_MATCH": "/programs/all", "WEBSITE": "/website", "TEAM_MESSAGE": "/team",
    "PAYMENT": "/payments", "PAYMENT_PROOF": "/payments#proofs", "REMINDER": "/reminders", "CLASS": "/classes/",
    "ASSIGNMENT": "/classes/", "ASSIGNMENT_PENDING": "/classes/", "FEEDBACK": "/quiz/all", "COURSE_FILE": "/classes/",
    "PROGRAM_FILE": "/programs/expertise",
}


def target_of(notification) -> str:
    """Where a notification leads: its own link, else the page of the candidate, the program or the kind of news."""
    url = (notification.action_url or "").strip()
    if url.startswith("/") and not url.startswith("//"):
        return url
    if notification.candidate_id:
        return f"/candidate/{notification.candidate_id}"
    if getattr(notification, "program_id", None):
        return f"/programs/{notification.program_id}"
    return _BY_CATEGORY.get(notification.category or "", "/")


def localize(db: Session, notifications: list, lang: str) -> list:
    """Each notification gets `title_shown`, `message_shown` (in `lang`) and `target` (where it leads)."""
    from app.services import translations

    words = words_for(db, lang)
    texts = [text for item in notifications for text in (item.title, item.message) if text]
    stored = _stored(db, texts, lang)
    for item in notifications:
        item.title_shown = shown(db, item.title, lang, words, stored)
        item.message_shown = shown(db, item.message, lang, words, stored)
        item.target = target_of(item)
    return notifications


# ------------------------------------------------------------------
# The background job: the local model, for what no sentence covers
# ------------------------------------------------------------------

_INTO = {"fr": "French", "ar": "Arabic (Modern Standard Arabic)", "en": "English"}


def work(db: Session, limit: int = 6) -> int:
    """Translate a few texts asked for on a page. Returns how many were kept."""
    from app.models.ui_translation import TextTranslation
    from app.services import email_assistant, translations

    with _lock:
        waiting = list(_wanted.items())[:limit]
    if not waiting or not email_assistant.model_available():
        return 0
    kept = 0
    for (lang, text), _ in waiting:
        with _lock:
            _wanted.pop((lang, text), None)
        key = _digest(lang, text)
        row = db.query(TextTranslation).filter(TextTranslation.source_hash == key).first()
        if row is not None and (row.text or (row.tries or 0) >= RETRY_TRIES):
            continue
        if row is None:
            row = TextTranslation(lang=lang, source_hash=key, source=text[:4000], tries=0)
            db.add(row)
        row.tries = (row.tries or 0) + 1
        system = (f"Translate this notification of a training centre's management application into {_INTO[lang]}. "
                  "Keep the names of people, programs, classes, email addresses, numbers and dates exactly as they are written. "
                  "Answer with the translation only, nothing else.")
        answer = email_assistant._chat(system, text, as_json=False, max_tokens=max(80, len(text) * 2))
        first = (answer or "").strip().split("\n")[0]
        translated = _english(text, first) if lang == "en" else translations.acceptable(text, first, lang)
        if translated:
            row.text, row.origin, row.updated_at = translated, "model", datetime.utcnow()
            kept += 1
        db.commit()
    return kept


def _english(source: str, answer) -> str | None:
    """A translation into English, kept when it can be one (the numbers kept, no Arabic left)."""
    text = " ".join(str(answer or "").split()).strip().strip('"«»“”')
    if not text or _ARABIC.search(text) or len(text) > len(source) * 3 + 14:
        return None
    if sorted(re.findall(r"\d+", source)) != sorted(re.findall(r"\d+", text)):
        return None
    return text

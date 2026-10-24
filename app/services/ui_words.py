"""Sentences a service writes for a page (the advice on the dashboard…), in the page's language.

A service writes _t("Send {0} a payment reminder", name): the sentence in English with its values apart. The language of
the page being drawn (its cookie, set for the request by the gate) picks the French or the Arabic of that sentence,
written by hand in ui_phrases.json. A sentence nobody translated yet stays in English. Outside a page (the scheduler,
emails to the admin) it is English.
"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from pathlib import Path

logger = logging.getLogger(__name__)

current: ContextVar[str] = ContextVar("ui_lang", default="en")
_phrases: dict | None = None

# The words that are counted: (singular, plural) in French and in Arabic.
_COUNTED = {
    "email": (("email", "emails"), ("رسالة", "رسائل")),
    "candidate": (("candidat", "candidats"), ("مترشح", "مترشحين")),
    "paid candidate": (("candidat ayant payé", "candidats ayant payé"), ("مترشح دفع", "مترشحين دفعوا")),
    "program": (("formation", "formations"), ("برنامج", "برامج")),
    "question": (("question", "questions"), ("سؤال", "أسئلة")),
    "reminder": (("rappel", "rappels"), ("تذكير", "تذكيرات")),
    "session": (("séance", "séances"), ("حصة", "حصص")),
    "class": (("classe", "classes"), ("قسم", "أقسام")),
    "day": (("jour", "jours"), ("يوم", "أيام")),
    "week": (("semaine", "semaines"), ("أسبوع", "أسابيع")),
}


def phrases() -> dict:
    global _phrases
    if _phrases is None:
        try:
            _phrases = json.loads(Path(__file__).with_name("ui_phrases.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _phrases = {}
    return _phrases


def use(lang: str | None):
    """The language of the page being drawn (en, fr or ar)."""
    return current.set(lang if lang in ("fr", "ar") else "en")


def language() -> str:
    return current.get()


def t(template: str, *values) -> str:
    """The sentence in the page's language, with its values."""
    lang = current.get()
    text = template
    if lang != "en":
        pair = phrases().get(template)
        if pair:
            text = pair[0 if lang == "fr" else 1]
    try:
        return text.format(*values)
    except (IndexError, KeyError, ValueError):
        logger.warning("The %s sentence for %r does not take its values; English is used.", lang, template[:60])
        return template.format(*values)


def plural(count: int, word: str) -> str:
    """“3 emails”, “3 emails” in French, “3 رسائل” in Arabic."""
    lang = current.get()
    forms = _COUNTED.get(word)
    if lang == "en" or forms is None:
        return f"{count} {word}{'' if count == 1 else 's'}"
    singular, many = forms[0 if lang == "fr" else 1]
    return f"{count} {singular if count == 1 else many}"

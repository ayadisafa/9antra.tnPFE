"""Rewording, on every page where something is written: a few words typed in a hurry (Tunisian, French or English, mixed)
come back as a proper message, in French or English. Nothing is sent from here: the text goes back into its box, to read
and change; "Put my words back" undoes it.

Two kinds of box:
- "message": words for a person (an answer, a note to a professor, a team message): a greeting with their first name, the
  message, a short polite end, no signature (it is added when it goes);
- "text": the same text said better (an email template, a reminder, a note, the words of a post): no greeting added, the
  {variables} of the emails kept.

The cloud writer writes it when it is on, otherwise the model of this computer. What comes back is checked by the
application, not trusted: a figure, a link or a {variable} added or lost is said in a warning.

The inbox's answers (`for_reply`) also read the candidate's question, and say whether the result may go by itself
("Reword and send at once"): only when it is well reworded — nothing the checks above found, no name the words did not
give, and written in the language chosen. Otherwise it waits in the box, with why.
"""
from __future__ import annotations

import re

from app.services import derja, email_assistant, reply_writer
from app.services.ui_words import t as _t

MAX = 2000
KINDS = ("message", "text")
_VARIABLE = re.compile(r"\{\w+\}")

_CLOUD = (
    "You rewrite what a member of 9antra, a training centre in Tunisia, typed in a hurry, so that it reads well before they "
    "send it. Rules:\n"
    "- Same meaning, nothing added: never a figure, a date, an hour, a place, a price, a link, a name or a promise that is not "
    "in their words. Keep every one of theirs exactly, and every negation.\n"
    "- Their words may be Tunisian (Latin letters where 3 7 9 5 8 2 stand for letters, or Arabic letters), French or English, "
    "mixed: understand them, then write only in the language asked.\n"
    "- Keep every mark like [link 1] and every word in curly brackets like {first_name} exactly as it is, once each.\n"
    "- KIND message: start with \"Bonjour <first name>,\" (\"Hello <first name>,\" in English; without a name \"Bonjour,\"), unless "
    "their words already greet; then 1 to 4 short, warm and clear sentences; no signature.\n"
    "- KIND text: the same text, corrected and clearer, about as long; no greeting and no signature that their words do not have.")
_LOCAL_TEXT = (
    "You correct and improve a short text for 9antra, a training centre. You keep its meaning exactly: you never add facts, "
    "figures, dates, names or promises. You answer with one JSON object and nothing else.")


def _language(language: str | None, words: str) -> str:
    if language in ("fr", "en"):
        return language
    return "en" if email_assistant._detect_language(words) == "en" else "fr"


_FIGURE_WORD = re.compile(r"\d+(?:[.,:/]\d+)*(?:h\d{0,2}|dt|tnd|min|am|pm|e|er|eme|ème)?")


def figures(text: str) -> list[str]:
    """The figures of a text: numbers, dates, hours (“9h”, “10h30”); not the digits that stand for letters in Tunisian
    (“m3a”, “9oul”, “a7na”)."""
    found = []
    for token in re.findall(r"[^\W_]+(?:[.,:/][^\W_]+)*", text or ""):
        if _FIGURE_WORD.fullmatch(token.lower()):
            # “09:00” and “9h” are the same hour: zero minutes are not a figure of their own.
            found += [number.lstrip("0") for number in re.findall(r"\d+", token) if number.lstrip("0")]
    return found


def _numbers(text: str) -> list[str]:
    return sorted(figures(text))


def _problems(words: str, written: str) -> list[str]:
    """What the rewording changed that it must not: said to the person, who reads before sending."""
    found = []
    added = sorted(set(_numbers(written)) - set(_numbers(words)))
    lost = sorted(set(_numbers(words)) - set(_numbers(written)))
    if added:
        found.append(_t("it wrote {0}, which is not in your words", ", ".join(added)))
    if lost:
        found.append(_t("it lost {0}", ", ".join(lost)))
    missing = sorted(set(_VARIABLE.findall(words)) - set(_VARIABLE.findall(written)))
    extra = sorted(set(_VARIABLE.findall(written)) - set(_VARIABLE.findall(words)))
    if missing:
        found.append(_t("it lost {0}", ", ".join(missing)))
    if extra:
        found.append(_t("it added {0}", ", ".join(extra)))
    return found


def _cloud(words: str, kind: str, first_name: str, language: str, question: str = "") -> str | None:
    from app.services import cloud_model

    if not cloud_model.available():
        return None
    hints = derja.hints(words, "English" if language == "en" else "French")
    asked = " ".join((question or "").split())[:600]
    prompt = (f"KIND: {kind}\nLanguage: {'English' if language == 'en' else 'French'}\n"
              + (f"First name of the person: {first_name}\n" if kind == "message" and first_name else "")
              + (f"They wrote (their words are answered, nothing of it is added): {asked}\n" if asked else "")
              + (f"{hints}\n" if hints else "") + f"\nTHEIR WORDS:\n{words}")
    found = cloud_model.json_of(cloud_model.generate([{"role": "user", "parts": [{"text": prompt}]}], system=_CLOUD,
                                                     schema={"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]},
                                                     temperature=0.4))
    text = str((found or {}).get("text") or "").strip()
    return text[:4000] or None


def _local_text(words: str, language: str) -> str | None:
    """A text said better by the model of this computer (no greeting added)."""
    hints = derja.hints(words, "English" if language == "en" else "French")
    prompt = (f"Correct and improve this text, in {'English' if language == 'en' else 'French'}. Keep every figure, date, name, "
              "every mark like [link 1] and every word in curly brackets like {first_name} exactly as written. Do not add a greeting "
              "or a signature that the text does not have.\n"
              + (f"{hints}\n" if hints else "") + f'Text: "{words}"\n\nAnswer with: {{"text": "..."}}')
    data = email_assistant.llm_json(_LOCAL_TEXT, prompt, max_tokens=200 + len(words))
    text = str((data or {}).get("text") or "").replace("\\n", "\n").strip() if isinstance(data, dict) else ""
    return text or None


def reword(words: str, *, kind: str = "message", first_name: str = "", language: str | None = None, question: str = "") -> dict | None:
    """{"reply", "warning" (None when nothing changed that must not), "by": "cloud" | "local"}; None when no writer answered."""
    words = (words or "").strip()[:MAX]
    if len(words) < 2:
        return None
    kind = kind if kind in KINDS else "message"
    first_name = " ".join((first_name or "").split()[:1])[:40]
    language = _language(language, words)
    # Links travel as marks ([link 1]): a long address comes back garbled, a mark is kept or visibly lost.
    marked, links = reply_writer.protect_links(words, "link")
    written, by = _cloud(marked, kind, first_name, language, question), "cloud"
    if written is not None:
        restored = reply_writer.restore_links(written, links) if links else written
        if restored is None:
            return {"reply": words, "warning": _t("The rewording lost a web link, so your own words are kept."), "by": by}
        written = restored
    elif kind == "message" and not _VARIABLE.search(words):
        found = reply_writer.reformulate(words, first_name=first_name, question=question, language=language)
        if found is None:
            return None
        written, by = found["reply"], "local"
        if found.get("warning"):
            return {"reply": written, "warning": found["warning"], "by": by}
    else:
        local = _local_text(marked, language)
        if local is None:
            return None
        restored = reply_writer.restore_links(local, links) if links else local
        if restored is None:
            return {"reply": words, "warning": _t("The rewording lost a web link, so your own words are kept."), "by": "local"}
        written, by = restored, "local"
    problems = _problems(words, written)
    return {"reply": written, "by": by,
            "warning": (_t("Check before sending: {0}.", "; ".join(problems)) if problems else None)}


# Words a reply may write with a capital that the admin's words need not have given (days, months, the greeting).
_NAMED_ANYWAY = ("Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May June July August September "
                 "October November December Lundi Mardi Mercredi Jeudi Vendredi Samedi Dimanche Hello Dear Bonjour Bonsoir Cher Chère DT")


def _people_named(db, note: str, first_name: str, reply: str) -> list[str]:
    """People of the centre (candidates, professors, team) the reply names and the admin's words do not: wherever they
    stand, also first in a sentence (where any word has a capital)."""
    from app.models import Candidate, Professor, User

    said = {word.lower() for word in re.findall(r"[^\W\d_]+", f"{note} {first_name}")}
    names = set()
    for (name,) in db.query(Candidate.full_name).all() + db.query(Professor.name).all() + db.query(User.name).all():
        names.update(part.lower() for part in re.findall(r"[^\W\d_]{3,}", name or ""))
    found = []
    for word in re.findall(r"\b[A-ZÀ-Ý][^\W\d_]{2,}", reply or ""):
        if word.lower() in names and word.lower() not in said and word.lower() not in _NAMED_ANYWAY.lower().split() and word not in found:
            found.append(word)
    return found


def for_reply(note: str, *, first_name: str = "", question: str = "", language: str | None = None, db=None, recipient: str = "") -> dict | None:
    """The admin's few words as the answer to a candidate: {"reply", "warning", "ready", "why", "by"}; None when no writer
    answered. "ready": well reworded, it may go by itself; otherwise "why" says what to look at, and it waits."""
    written = reword(note, kind="message", first_name=first_name, language=language, question=question)
    if written is None:
        return None
    language = _language(language, note)
    reply = (written.get("reply") or "").strip()
    why = []
    if written.get("warning"):
        why.append(written["warning"].rstrip("."))
    if not reply:
        why.append(_t("nothing came back"))
    added = reply_writer.added_names(f"{note} {first_name} {recipient} {_NAMED_ANYWAY}", reply)
    if db is not None:
        added += [word for word in _people_named(db, note, f"{first_name} {recipient}", reply) if word not in added]
    if added:
        why.append(_t("it wrote {0}, which is not in your words", ", ".join(added[:3])))
    if len(reply.split()) >= 6 and email_assistant._detect_language(reply) != language:
        why.append(_t("it is not written in the language chosen"))
    return {**written, "reply": reply, "ready": not why, "why": why,
            "warning": written.get("warning") or (_t("Waiting for your eye: {0}.", "; ".join(why)) if why else None)}

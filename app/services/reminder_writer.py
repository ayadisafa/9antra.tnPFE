"""A reminder (or a professor's message to their class) written from its topic: the admin says what it is about ("the exam
on Saturday, bring your laptop") and the cloud model writes the subject and the message, ready to read and change before
it goes.

Only the topic goes to the model, with who it is for in general words (a class, a program, everyone): never a person's
name or details. The application checks what comes back: no figure, date or link the topic did not give (a missing
date is written as "[écrivez la date]"), only the {variables} the emails know. Without the cloud model, the closest of
the reminder ideas is offered instead.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.services.ui_words import t as _t

ALLOWED = ("first_name", "program_name", "class_name", "start_date", "start_time", "schedule", "professor")
SESSION = ("session_date", "session_time", "session_topic")
WHO = {"candidate": "one candidate", "class": "the candidates of one class", "program": "everyone who follows one program",
       "unpaid": "the candidates who still owe something (do not talk about money: the payment reminder does)",
       "everyone": "every candidate", "professor": "one professor", "professors": "every professor"}
SYSTEM = (
    "You write short messages that 9antra, a training centre in Tunisia, sends by email or WhatsApp. Write the message from the "
    "TOPIC only. Rules:\n"
    "- Never invent a date, an hour, a place, a price, a link, a name or a promise the topic does not give. Where one is needed and "
    "not given, write a placeholder in square brackets, in the message's language: [écrivez la date], [write the time].\n"
    "- Write in French, unless the topic is written in English or Arabic: then in that language.\n"
    "- Start with \"Bonjour {first_name},\" (or \"Hello {first_name},\" / \"مرحبا {first_name}،\"). Warm, clear and short: 3 to 6 "
    "sentences. You may put the key facts in one paragraph that starts with [box] (it is drawn as a coloured square) and use "
    "**bold** for a few key words.\n"
    "- Variables you may use, written exactly like this: {variables}. No other word in curly brackets.\n"
    "- No signature at the end: it is added.\n"
    "- subject: at most 70 letters. title: a short title for the top of the email (or the subject again).")
SCHEMA = {"type": "OBJECT", "properties": {"subject": {"type": "STRING"}, "title": {"type": "STRING"}, "body": {"type": "STRING"}},
          "required": ["subject", "body"]}
_LINK = re.compile(r"https?://\S+|www\.\S+")


def _numbers(text: str) -> set[str]:
    from app.services.cloud_answer import _numbers

    return set(_numbers(text or ""))


def _check(found: dict, topic: str, allowed: tuple) -> str | None:
    """What is wrong with the text written, or None."""
    from app.services import email_templates

    written = "\n".join(str(found.get(key) or "") for key in ("subject", "title", "body"))
    unsupported = [number for number in _numbers(written) if number not in _numbers(topic)]
    if unsupported:
        return "a figure or a date you did not give"
    links = [link for link in _LINK.findall(written) if link.rstrip(".,)") not in (topic or "")]
    if links:
        return "a link you did not give"
    unknown = [name for name in email_templates.unknown_variables(written, extra=[(name, "", "") for name in SESSION])
               if name not in allowed]
    unknown += [name for name in re.findall(r"\{(\w+)\}", written) if name not in allowed]
    if unknown:
        return "a variable the emails do not know"
    return None


REWORD = (
    "You write the message the admin of 9antra, a training centre in Tunisia, asks to send to someone. The ADMIN'S WORDS say what "
    "to say, or what the message is for (\"rappelle-lui la séance de demain\", \"to thank them for coming\"). Turn them into a "
    "short, warm and clear message, as the centre writes. Rules:\n"
    "- Start with \"Bonjour <first name>,\" (\"Hello <first name>,\" in English) when a first name is given, else \"Bonjour,\". "
    "No signature: it is added.\n"
    "- Never add a figure, a date, an hour, a place, a price, a link, a name or a promise the admin's words do not give. If a "
    "detail is missing, write around it; never invent it.\n"
    "- Keep every figure, date, hour, link and negation of the admin's words exactly.\n"
    "- 2 to 5 sentences. The language asked.")


def reword(words: str, first_name: str = "", language: str = "fr") -> dict | None:
    """The message written from the admin's words: {"reply"}, or {"reply", "warning"} when it changed a figure or a
    link (then nothing is sent), or None without the cloud model (the old rewording does it)."""
    from app.services import cloud_model

    words = (words or "").strip()
    if not words or not cloud_model.available():
        return None
    prompt = (f"ADMIN'S WORDS: {words}\n\nFirst name of the person: {first_name or '(none)'}\n"
              f"Language: {'English' if language == 'en' else 'French'}.")
    problem = None
    for _ in range(2):
        asked = prompt if problem is None else prompt + f"\n\nThe last text had {problem}: write it again without it."
        found = cloud_model.json_of(cloud_model.generate([{"role": "user", "parts": [{"text": asked}]}], system=REWORD,
                                                         schema={"type": "OBJECT", "properties": {"message": {"type": "STRING"}}, "required": ["message"]},
                                                         temperature=0.5))
        text = str((found or {}).get("message") or "").strip()
        if not text:
            return None
        lost = [number for number in _numbers(words) if number not in _numbers(text)]
        added = [number for number in _numbers(text) if number not in _numbers(words)]
        links = [link for link in _LINK.findall(text) if link.rstrip(".,)") not in words]
        problem = ("a figure that is not in your words" if added else "a figure of your words missing" if lost
                   else "a link that is not in your words" if links else None)
        if problem is None:
            return {"reply": text[:3000], "by": "cloud"}
    return {"reply": text[:3000], "warning": _t("What was written had {0}: add it to the topic, or write that part yourself.", _t(problem)), "by": "cloud"}


def _idea(topic: str) -> dict | None:
    """Without the cloud model: the reminder idea that shares the most words with the topic."""
    from app.services import reminders

    said = set(re.findall(r"[^\W\d_]{4,}", (topic or "").lower()))
    best, score = None, 0
    for idea in reminders.IDEAS:
        words = set(re.findall(r"[^\W\d_]{4,}", f"{idea['name']} {idea['subject']} {idea['body']}".lower()))
        if len(said & words) > score:
            best, score = idea, len(said & words)
    return best


def write(db: Session, topic: str, *, audience: str = "class", repeat: str = "once", author: str | None = None) -> dict:
    """{"subject", "title", "body", "how": "cloud" | "idea"} or {"error"}."""
    from app.services import cloud_model

    topic = " ".join((topic or "").split())[:600]
    if len(topic) < 3:
        return {"error": _t("Say in a few words what it is about.")}
    allowed = ALLOWED + (SESSION if repeat == "session" else ())
    if not cloud_model.available():
        idea = _idea(topic)
        if idea is None:
            return {"error": _t("The online writer is not available just now: start from an idea, or write it.")}
        return {"subject": idea["subject"], "title": idea["title"], "body": idea["body"], "how": "idea", "style": idea["style"]}
    sender = f"the professor {author}, to the candidates of their class" if author else "the centre"
    prompt = (f"TOPIC: {topic}\n\nIt goes to {WHO.get(audience, 'candidates')}, from {sender}"
              + (", before each of their sessions (the session's day, hour and topic are in {session_date}, {session_time}, {session_topic})"
                 if repeat == "session" else "") + ".")
    system = SYSTEM.replace("{variables}", ", ".join("{" + name + "}" for name in allowed))
    problem = None
    for attempt in range(2):
        asked = prompt if problem is None else prompt + f"\n\nThe last text had {problem}: write it again without it (use a placeholder)."
        content = cloud_model.generate([{"role": "user", "parts": [{"text": asked}]}], system=system, schema=SCHEMA, temperature=0.6)
        found = cloud_model.json_of(content)
        if not found or not str(found.get("body") or "").strip():
            return {"error": _t("The online writer did not answer just now: try again in a moment, or start from an idea.")}
        problem = _check(found, topic, allowed)
        if problem is None:
            subject = " ".join(str(found.get("subject") or "").split())[:200]
            return {"subject": subject, "title": " ".join(str(found.get("title") or subject).split())[:120],
                    "body": str(found["body"]).strip()[:4000], "how": "cloud"}
    return {"error": _t("What was written had {0}: add it to the topic, or write that part yourself.", _t(problem))}

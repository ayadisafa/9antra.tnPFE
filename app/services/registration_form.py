"""The registration form: the link given to whoever asks how to register.

Whoever asks it (a candidate, or somebody who is not one yet) by email, WhatsApp or SMS gets the form's link in a fixed
sentence, in French or English. The question is recognised by its words, in French, English, Tunisian and Arabic: no
language model decides. The link is the admin's (Settings, The centre's numbers); until they write one, it is the form
9antra uses today.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from email.utils import make_msgid

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://docs.google.com/forms/d/e/1FAIpQLSe_mb7BtCDgVVtHDhNMywq59VmyFRTh2AdteYWig4QlB4yLOg/viewform"

# “How do I register?”, “I want to register”, “the registration form”, in French, English and Tunisian in Latin letters.
_HOW = re.compile(
    # “Comment s'inscrire ?”, “how can I register?”, “kifech nsajel?” (not “how much is the registration?”)
    r"\b(comment|how(?! much| many| long| often)|kifech|kifeh|kifach)\b.{0,40}"
    r"\b(m inscrire|s inscrire|s inscrit|s inscrivent|inscrire|inscription|register|registers|registering|registration|sign ?up|"
    r"enrol+|enrol+ing|nsaj+el|tsaj+el|tasjil)\b"
    # “Où s'inscrire ?”, “where do I sign up?”: the verb only (not “where is my registration?”)
    r"|\b(ou|where|win)\b.{0,20}\b(m inscrire|s inscrire|inscrire|register|sign ?up|enrol+|nsaj+el)\b"
    r"|\b(je|j)\b.{0,12}\b(veux|voudrais|souhaite|souhaiterais|aimerais|desire)\b.{0,20}\b(m inscrire|inscrire|inscription)\b"
    r"|\bi\b.{0,12}\b(want|would like|wish|d like)\b.{0,12}\b(to )?(register|sign ?up|enrol+|join)\b"
    r"|\b(formulaire|fiche|lien|link|form)\b.{0,15}\b(d )?(inscription|registration|register|sign ?up)\b"
    r"|\b(inscription|registration|sign ?up)\b.{0,6}\b(form|link|formulaire|lien)\b"
    r"|\b(n7eb|nheb|nhib|n7ib|bech|bch|bich|nejem|najem)\b.{0,15}\b(nsaj+el|n9ayed|nkayed|nsaj+l)\b",
)
_PROCESS = re.compile(r"\b(comment|how)\b.{0,25}\b(se passe|passe|fonctionne|marche|deroule|work|works|happens?)\b")
# Leaving or getting money back is not a wish to register.
_NOT = re.compile(r"\b(annul\w*|cancel\w*|rembours\w*|refund\w*|desinscri\w*|unsubscribe\w*|unregister\w*)")
_HOW_ARABIC = re.compile(r"(كيف|كيفاش|وين|شنوة|أين|اين|متى|نحب|أريد|اريد|باش).{0,25}(التسجيل|تسجيل|نسجل|أسجل|اسجل|نتسجل|يتسجل|يسجل)"
                         r"|(رابط|استمارة|إستمارة)\s*(ال)?تسجيل")


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded).split())


def asks_how(*texts: str) -> bool:
    """Does the message ask how to register (or for the registration form)?"""
    text = " ".join(part or "" for part in texts)
    plain = _plain(text)
    if _NOT.search(plain) or re.search(r"إلغاء|الغاء|استرجاع", text):
        return False
    # “Comment se passe l'inscription ?”, “how does registration work?”: the process, which the knowledge answers.
    if _PROCESS.search(plain) and not re.search(r"\b(m inscrire|s inscrire|inscrire|register|sign ?up|enrol+|nsaj+el)\b", plain):
        return False
    return _HOW.search(plain) is not None or _HOW_ARABIC.search(text) is not None


def url(db: Session) -> str:
    from app.services.settings_service import get_settings

    return (getattr(get_settings(db), "registration_form_url", None) or "").strip() or DEFAULT_URL


def reply(db: Session, language: str = "fr") -> str:
    """The fixed sentence that carries the link."""
    link = url(db)
    if language == "en":
        return (f"To register, simply fill in our registration form:\n{link}\n\n"
                "As soon as it is sent, your registration reaches us and we come back to you with what follows "
                "(payment, class and start date).")
    return (f"Pour vous inscrire, il suffit de remplir notre formulaire d'inscription :\n{link}\n\n"
            "Dès qu'il est envoyé, votre inscription nous parvient et nous revenons vers vous avec la suite "
            "(paiement, classe et date de début).")


def knowledge_chunk(db: Session) -> dict:
    """The same, for what the assistant knows."""
    link = url(db)
    return {"source_key": "system:register-form", "kind": "process", "program_id": None,
            "title": "Comment s'inscrire : le formulaire d'inscription (how to register, registration form)",
            "content": (f"Pour s'inscrire à une formation 9antra (how to register, sign up, s'inscrire, inscription), il faut "
                        f"remplir le formulaire d'inscription : {link} . L'inscription arrive ensuite dans l'application ; "
                        "le candidat reçoit le message de bienvenue, puis la suite (paiement, classe, date de début).")}


def language_of(text: str) -> str:
    from app.services import email_assistant

    return "en" if email_assistant._detect_language(text or "") == "en" else "fr"


# ------------------------------------------------------------------
# Somebody who is not a candidate yet
# ------------------------------------------------------------------

def _may_answer_alone(db: Session, channel: str) -> bool:
    """Only when the admin lets the assistant answer alone on that way (the fixed sentence is still an answer)."""
    from app.services import email_assistant
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    mode = email_assistant.whatsapp_mode_of(settings) if channel in ("whatsapp", "sms") else email_assistant.mode_of(settings)
    return mode == "auto"


def answer_stranger(db: Session, *, channel: str, to: str, text: str, subject: str = "") -> dict | None:
    """Somebody who is not a candidate asks how to register: they get the form's link by the way they wrote, when the
    assistant answers alone there. None when the message does not ask it. Else {"sent": bool, "problem", "text"}."""
    if not asks_how(subject, text):
        return None
    language = language_of(f"{subject} {text}")
    words = reply(db, language)
    greeting = "Hello," if language == "en" else "Bonjour,"
    body = f"{greeting}\n\n{words}"
    if not _may_answer_alone(db, channel):
        return {"sent": False, "problem": "the assistant does not answer alone", "text": body}
    problem = None
    try:
        if channel == "whatsapp":
            from app.services import whatsapp

            problem = whatsapp.send(to, body, free=True) if whatsapp.configured() else "no WhatsApp Business number"
        elif channel == "sms":
            from app.services import sms

            problem = sms.send(to, body) if sms.configured() else "no Android phone connected"
        else:
            from app.services import email_assistant, email_threads

            if not email_threads.smtp_configured():
                problem = "email is not set up"
            else:
                plain, html_body = email_assistant.compose_reply(db, None, body, language=language)
                title = (subject or "").strip()
                title = title if title.lower().startswith("re:") else f"Re: {title}" if title else (
                    "Your registration at 9antra" if language == "en" else "Votre inscription chez 9antra")
                problem, _ = email_threads._attempt(to, title, plain, html_body, make_msgid(domain="9antra.tn"), None, None)
    except Exception as error:          # never stops the message from being kept
        logger.exception("The registration form could not be sent to %s.", to)
        problem = str(error) or type(error).__name__
    return {"sent": problem is None, "problem": problem, "text": body}


def tell_admin(db: Session, *, channel: str, who: str, text: str, answered: dict, number: str | None = None) -> None:
    """The admin sees it in the bell: sent, or ready to send with one button (WhatsApp, SMS or email, already written)."""
    from urllib.parse import quote

    from app.models.notification import Notification

    way = {"whatsapp": "WhatsApp", "sms": "SMS"}.get(channel, "email")
    words = " ".join((text or "").split())
    said = f"{who}: “{words[:200]}{'…' if len(words) > 200 else ''}”"
    if answered["sent"]:
        title, message = f"Asked how to register: the form went by {way}", f"{said} The registration form's link was sent back."
        if channel == "whatsapp":                # the conversation, to go on with them
            from app.services import whatsapp

            action = whatsapp.link(number or who) or "/inbox"
        elif channel == "sms":
            action = f"sms:{number or who}"
        else:
            action = f"mailto:{who}"
    else:
        title = f"Asked how to register ({way})"
        message = f"{said} Answer with the registration form's link: it is written for you ({answered['problem']})."
        if channel == "whatsapp":
            from app.services import whatsapp

            action = whatsapp.link(number or who, answered["text"]) or "/inbox"
        elif channel == "sms":
            action = f"sms:{number or who}?body={quote(answered['text'])}"
        else:
            action = f"mailto:{who}?subject={quote('Inscription 9antra')}&body={quote(answered['text'])}"
    db.add(Notification(recipient_role="admin", category="CANDIDATE_EMAIL", title=title[:200], message=message[:500], action_url=action))
    db.commit()

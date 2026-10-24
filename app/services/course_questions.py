"""Questions about the learning itself (an exercise, a lesson, an error, "I did not understand"), asked by email,
WhatsApp or SMS.

Only a candidate who paid for the program gets an answer to those: their question goes to the professor of their
class (in the application, by email and on WhatsApp), the professor answers in the application, and the answer goes
back to the candidate by the way they wrote. Someone who has not paid is told, with a fixed text, that the
registration must be paid first. Questions about prices, dates or what a program covers are not these: the assistant
answers them for everyone, as before.

A professor who has not answered after a day is reminded once (application, email, WhatsApp); after two days the
admin is told.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

REMIND_AFTER = timedelta(hours=24)
TELL_ADMIN_AFTER = timedelta(hours=48)

_LEARNING = re.compile(
    r"(exercice|exercise|\btp\b|\btd\b|devoir|homework|assignment|projet|project|le cours d|la s[ée]ance d|the lesson|the session|"
    r"lesson|le chapitre|chapter|support de cours|slides|diapo|enregistrement|recording|replay|"
    r"je n['’]?ai pas compris|pas compris|didn['’]?t understand|don['’]?t understand|mafhemtch|mafhamtech|"
    r"explique|expliquer|explain|comment (on )?(fait|faire|installer|utiliser|configurer|corriger)|how (do|can) (i|we) (install|use|fix|configure|run|solve)|"
    r"erreur|error|bug|exception|ne (marche|fonctionne) pas|doesn['’]?t work|not working|install(er|ation)?\b|"
    r"\bcode\b|fonction|function|variable|requ[êe]te|query)",
    re.I,
)
# Words that make it an information question, not a learning one (a program's name alone says nothing).
_INFO = re.compile(r"(prix|price|tarif|combien|9adech|co[uû]t|payer|paiement|inscri|register|registration|date|quand|when|"
                   r"horaire|schedule|commence|start|certificat|certificate|o[uù] (se trouve|est)|adresse|address|location|"
                   r"brochure|pdf|catalogue|documentation du programme|programme d[ée]taill[ée])", re.I)


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def is_learning_question(text: str) -> bool:
    """A question about the learning itself, not about prices, dates or what a program covers."""
    text = text or ""
    learning = len(_LEARNING.findall(text))
    if not learning:
        return False
    return learning >= 2 or not _INFO.search(text)


def class_of(db: Session, candidate, text: str = ""):
    """(registration, class) the question is about: the paid registration with a class, the one named first.
    (None, None) when the candidate has paid for no program with a class."""
    from app.models.classgroup import ClassStatus
    from app.models.registration import PaymentStatus, RegistrationStatus

    paid = [item for item in candidate.registrations if item.payment_status == PaymentStatus.PAID
            and item.status not in (RegistrationStatus.CANCELLED,) and item.class_group is not None]
    if not paid:
        return None, None
    said = _plain(text)
    named = [item for item in paid if item.program_name and _plain(item.program_name) in said]
    running = [item for item in paid if item.class_group.status == ClassStatus.ONGOING]
    chosen = (named or running or paid)[0]
    return chosen, chosen.class_group


def unpaid_registration(candidate, text: str = ""):
    """The unpaid registration the question is about (the one named, or the only one)."""
    from app.models.registration import RegistrationStatus

    owing = [item for item in candidate.registrations if item.status != RegistrationStatus.CANCELLED and (item.remaining_amount or 0) > 0]
    said = _plain(text)
    named = [item for item in owing if item.program_name and _plain(item.program_name) in said]
    return (named or owing or [None])[0]


def professor_of(db: Session, class_group):
    from app.models import Professor

    if class_group is None:
        return None
    if class_group.professor_id:
        return db.get(Professor, class_group.professor_id)
    if class_group.professor_name:
        return db.query(Professor).filter(Professor.name == class_group.professor_name).first()
    return None


def professor_line(db: Session, candidate, language: str = "fr") -> str:
    """"You can also ask your professor, X (email)": for a paid candidate whose class has a professor."""
    _, class_group = class_of(db, candidate)
    professor = professor_of(db, class_group)
    if professor is None:
        return ""
    contact = f" ({professor.email})" if professor.email else ""
    if language == "en":
        return f"You can also ask your questions to your professor, {professor.name}{contact}."
    return f"Vous pouvez aussi poser vos questions à votre formateur, {professor.name}{contact}."


def pay_first_text(registration, language: str = "fr") -> str:
    if registration is None:
        return ("To ask questions about a course's content, you first need to register and pay for it."
                if language == "en" else
                "Pour poser des questions sur le contenu d'un cours, il faut d'abord vous inscrire à la formation et la régler.")
    remaining = f"{registration.remaining_amount:g} DT"
    if language == "en":
        return (f"Questions about the content of “{registration.program_name}” are answered once the registration is paid "
                f"(remaining: {remaining}). As soon as we receive your payment, you can ask your professor directly.")
    return (f"Les questions sur le contenu de la formation « {registration.program_name} » sont traitées une fois l'inscription "
            f"réglée (reste à payer : {remaining}). Dès réception de votre paiement, vous pourrez poser vos questions à votre formateur.")


def forwarded_text(professor, language: str = "fr") -> str:
    contact = f" Vous pouvez aussi lui écrire directement : {professor.email}." if professor.email else ""
    if language == "en":
        contact = f" You can also write to them directly: {professor.email}." if professor.email else ""
        return f"Your question was sent to your professor, {professor.name}, who will answer you here soon.{contact}"
    return f"Votre question a été transmise à votre formateur, {professor.name}, qui vous répondra ici rapidement.{contact}"


def forward(db: Session, *, candidate, message, registration, class_group, professor) -> object:
    """Send the candidate's question to their professor (application, email, WhatsApp). Returns the team message."""
    from app.services import team_messages

    key = f"professor:{professor.id}"
    question = (message.body or "").strip()
    text = (f"Question de {candidate.full_name or candidate.email} ({class_group.name}, {registration.program_name}) :\n\n"
            f"{question}\n\nRépondez dans l'application (Messages) : votre réponse lui est envoyée.")
    kept = team_messages.send(db, key, text, ["app", "email", "whatsapp"], author="The assistant")
    first = kept[0] if kept else None
    if first is not None:
        first.question_message_id = message.id
        first.question_candidate_id = candidate.id
        db.flush()
    return first


def waiting_for(db: Session, professor_key: str) -> list:
    """The candidates' questions this professor has not answered yet."""
    from app.models.team_message import TeamMessage

    return (db.query(TeamMessage).filter(TeamMessage.person == professor_key, TeamMessage.question_message_id.isnot(None),
                                         TeamMessage.answered_at.is_(None))
            .order_by(TeamMessage.created_at.asc()).all())


def answer(db: Session, question_row, text: str, *, professor_name: str):
    """The professor's answer, sent to the candidate in their own discussion (email, WhatsApp or SMS)."""
    from app.models import Candidate
    from app.models.candidate_email import CandidateEmail
    from app.models.email_thread import EmailThread
    from app.services import email_assistant, email_threads
    from app.services.notification_service import create_notification

    asked = db.get(CandidateEmail, question_row.question_message_id)
    candidate = db.get(Candidate, question_row.question_candidate_id)
    if asked is None or candidate is None:
        raise ValueError("This question is not linked to a candidate any more.")
    thread = db.get(EmailThread, asked.thread_id)
    language = "en" if (asked.language or "") == "en" else "fr"
    signed = f"{text.strip()}\n\n— {professor_name}"
    plain, html_body = email_assistant.compose_reply(db, candidate, signed, language=language, quote=asked.body)
    sent = email_threads.deliver(db, candidate, f"Re: {thread.subject}" if thread else "Votre question", plain, html_body,
                                 kind="reply", author=f"professor:{professor_name}", thread=thread, language=language,
                                 note=f"Answered by the professor, {professor_name}.")
    if sent is None or sent.status == "failed":
        raise ValueError("The answer could not be delivered to the candidate. The centre was told.")
    question_row.answered_at = datetime.utcnow()
    asked.status, asked.requires_admin, asked.replied_at = "replied", False, datetime.utcnow()
    if thread is not None:
        thread.needs_admin = False
    create_notification(db, title=f"{professor_name} answered {candidate.full_name or candidate.email}", message=text[:300],
                        category="EMAIL_ANSWERED", action_url=f"/inbox?thread={asked.thread_id}", candidate_id=candidate.id)
    db.commit()
    return sent


def remind_due(db: Session, now: datetime | None = None) -> int:
    """Once per question: the professor after a day (application, email, WhatsApp), the admin after two."""
    from app.models.team_message import TeamMessage
    from app.services import team_messages
    from app.services.notification_service import create_notification

    now = now or datetime.utcnow()
    done = 0
    rows = (db.query(TeamMessage).filter(TeamMessage.question_message_id.isnot(None), TeamMessage.answered_at.is_(None))
            .order_by(TeamMessage.created_at.asc()).limit(50).all())
    for row in rows:
        if row.reminded_at is None and row.created_at <= now - REMIND_AFTER:
            row.reminded_at = now
            db.commit()
            team_messages.send(db, row.person, "Rappel : une question d'un candidat attend votre réponse dans l'application (Messages).",
                               ["app", "email", "whatsapp"], author="The assistant")
            done += 1
        elif row.reminded_at is not None and row.admin_told_at is None and row.created_at <= now - TELL_ADMIN_AFTER:
            row.admin_told_at = now
            found = team_messages.person(db, row.person)
            create_notification(db, title=f"{found['name'] if found else 'A professor'} has not answered a candidate for two days",
                                message=(row.body or "")[:300], category="TEAM_MESSAGE", action_url=f"/inbox/team?person={row.person}")
            done += 1
    return done

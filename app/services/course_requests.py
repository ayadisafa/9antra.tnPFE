"""A candidate asks for the course itself: the files of a week (“envoyez-moi le support de la semaine 3”, “ab3athli
el cours”, “the slides please”).

The files the professor added for that week of the candidate's class are sent at once, attached to the answer. When the
week has none yet, the request goes to the professor teaching the class (in the application, by email and WhatsApp) and
the admin is told; the candidate is told it was passed on, and the files go to them by themselves as soon as the
professor adds them. Only for a candidate who paid and is in a class; a week not taught yet is said so.
"""
from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import Session

from app.database import Base

logger = logging.getLogger(__name__)
LIMIT_BYTES = 18 * 1024 * 1024        # what one email can carry; beyond it the files are linked to, not attached


class CourseRequest(Base):
    """A week's course asked for before it was there: sent by itself once the professor adds it."""
    __tablename__ = "course_requests"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id = Column(String, nullable=False, index=True)
    class_group_id = Column(String, nullable=False, index=True)
    week_number = Column(Integer, nullable=False)
    message_id = Column(String, nullable=True)          # the candidate's message that asked
    thread_id = Column(String, nullable=True)
    status = Column(String, default="waiting", nullable=False)   # waiting | sent
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_at = Column(DateTime, nullable=True)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", text)


# What is asked for: the course's files, not the program's brochure (program_documents answers that).
_FILES = (r"supports?|slides?|diapo\w*|polycop\w*|fichiers?|files?|course materials?|materials?|pdf|enregistrements?|recordings?|"
          r"replay|presentations? du cours|notes du cours|lecture notes")
_COURSE = r"cours|course|lessons?|lecons?|exercices?|exercises?"
_WHEN = r"semaine|week|seance|session|hier|yesterday|derniere|last|aujourd hui|today|jem3a|jom3a|lbare7|el bare7"
_SEND = (r"envo\w*|send|donn\w*|give|partag\w*|share|recevoir|receive|get|obten\w*|telecharg\w*|download|"
         r"ou (est|sont|trouver)|where (is|are|can)|(can|could|may) i have|puis je avoir|je peux avoir|ab3ath\w*|ab3eth\w*|ab3th\w*|3tini|a3tini|mafamech|famech")
_WANT = r"veux|voudrais|aimerais|want|need|besoin|nheb|n7eb|bghit|possible|svp|please"
_ARABIC = re.compile(r"(أرسل|ارسل|ابعث|أبعث|عطيني|أعطني|أريد|اريد|نحب|ممكن).{0,30}(الدرس|الدروس|المحاضرة|ملفات|ملف|الشرائح|التمارين|التسجيل)")
_WEEK = re.compile(r"(?:semaine|week|seance|session|jem3a|jom3a)\s*(?:n\s*)?(\d{1,2})|(\d{1,2})\s*(?:e|eme|th|st|nd|rd)?\s*(?:semaine|week|seance|session)")
_ARABIC_WEEK = re.compile(r"(?:الأسبوع|الاسبوع|أسبوع|اسبوع|الحصة)\s*(\d{1,2})")


def asks_for_material(text: str) -> bool:
    """Whether a message asks for the course's files (a week's slides, notes, exercises, recording)."""
    if _ARABIC.search(text or ""):
        return True
    plain = _plain(text)
    if re.search(r"\b(programme|program|brochure|catalogue|formations?)\b", plain) and not re.search(r"\b(semaine|week|seance|session|supports?|slides?)\b", plain):
        return False                       # the program's PDF: program_documents answers it
    sends, wants = bool(re.search(rf"\b({_SEND})\b", plain)), bool(re.search(rf"\b({_WANT})\b", plain))
    if re.search(rf"\b({_FILES})\b", plain):
        return sends or wants
    # “le cours” alone is not a file: only the course of a week or a session, asked to be sent.
    return bool(re.search(rf"\b({_COURSE})\b", plain)) and bool(re.search(rf"\b({_WHEN})\b", plain)) and sends


def week_asked(text: str) -> int | None:
    found = _ARABIC_WEEK.search(text or "")
    if found:
        return int(found.group(1))
    found = _WEEK.search(_plain(text))
    if found:
        return int(found.group(1) or found.group(2))
    return None


def _current_week(class_group) -> int:
    return max(1, int(class_group.current_week or 1))


def other_program_asked(db: Session, candidate, text: str):
    """The program a candidate asks the course of when they are not registered in it (none of the programs they name is
    theirs); None otherwise. They are told to register first: the admin cannot give it."""
    from app.models.registration import RegistrationStatus
    from app.services import program_documents

    if candidate is None or not asks_for_material(text):
        return None
    named = program_documents.named_programs(db, text)
    if not named:
        return None
    theirs = {item.program_id for item in candidate.registrations if item.status != RegistrationStatus.CANCELLED and item.program_id}
    if any(program.id in theirs for program in named):
        return None
    return named[0]


def register_first_text(db: Session, program, language: str) -> str:
    from app.services import registration_form

    link = registration_form.url(db)
    if language == "en":
        return (f"The course of “{program.name}” is for the candidates registered in that program. To receive it, register first: {link}. "
                "Once you are registered and have paid, its course comes to you every week.")
    return (f"Le cours de « {program.name} » est réservé aux candidats inscrits à cette formation. Pour le recevoir, inscrivez-vous d'abord : "
            f"{link}. Une fois inscrit(e) et le paiement fait, ses cours vous arrivent chaque semaine.")


def files_of(db: Session, class_group, week: int) -> list:
    """The files of a week of the class that are really on disk."""
    from app.models.course_material import ClassMaterial
    from app.services import course_materials

    rows = (db.query(ClassMaterial).filter(ClassMaterial.class_group_id == class_group.id, ClassMaterial.week_number == week,
                                           course_materials.in_use())
            .order_by(ClassMaterial.created_at.asc()).all())
    return [row for row in rows if course_materials.path_of(row).is_file()]


def lookup(db: Session, candidate, text: str) -> dict | None:
    """What a candidate's request for the course finds: their class, the week, its files. None when they have no class
    (the rest of the assistant answers: pay first, or the admin)."""
    from app.services import course_questions

    registration, class_group = course_questions.class_of(db, candidate, text)
    if registration is None or class_group is None:
        return None
    current = _current_week(class_group)
    asked = week_asked(text)
    if asked is not None and asked > current and getattr(class_group.status, "value", class_group.status) != "completed":
        return {"registration": registration, "class": class_group, "week": asked, "files": [], "state": "later", "current": current}
    if asked is None:
        # No week said: the latest week taught that has files, else the current one.
        week = next((number for number in range(current, 0, -1) if files_of(db, class_group, number)), current)
    else:
        week = asked
    files = files_of(db, class_group, week)
    return {"registration": registration, "class": class_group, "week": week, "files": files,
            "state": "ready" if files else "missing", "current": current}


def attach(db: Session, files: list) -> tuple[list[str], list[str]]:
    """The week's files as email attachments (up to what an email carries); the names of those left out."""
    from app.services import course_materials, email_files

    kept, left, total = [], [], 0
    for row in files:
        data = course_materials.path_of(row).read_bytes()
        if total + len(data) > LIMIT_BYTES:
            left.append(row.filename)
            continue
        total += len(data)
        kept.append(email_files.save_upload(db, filename=row.filename, data=data, purpose="attachment").id)
    return kept, left


def reply_text(found: dict, language: str, left: list[str]) -> str:
    names = ", ".join(row.filename for row in found["files"] if row.filename not in left)
    week, group = found["week"], found["class"].name
    if language == "en":
        text = f"Here is the course of week {week} ({group}), attached: {names}."
        if left:
            text += f" Too big for one email, so not attached: {', '.join(left)}; your professor can share it with you."
        return text + " Good work!"
    text = f"Voici le cours de la semaine {week} ({group}), en pièce jointe : {names}."
    if left:
        text += f" Trop lourd pour un email, donc non joint : {', '.join(left)} ; votre formateur peut vous le partager."
    return text + " Bon travail !"


def answer(db: Session, candidate, analysis: dict, message) -> dict | None:
    """For the assistant's answer: the week's files attached (method "material"), when they are there."""
    text = f"{analysis.get('subject') or ''}\n{analysis.get('derja') or message.body or ''}"
    if not asks_for_material(text):
        return None
    found = lookup(db, candidate, text)
    if found is None or found["state"] != "ready":
        return None
    language = "en" if analysis.get("language") == "en" else "fr"
    kept, left = attach(db, found["files"])
    if not kept:
        return None
    return {"method": "material", "answer": reply_text(found, language, left), "evidence": 1.0, "files": kept,
            "programs": [found["class"].program_id] if found["class"].program_id else [], "blocker": None}


def passed_on_text(found: dict, professor, language: str) -> str:
    week = found["week"]
    if found["state"] == "later":
        return (f"Week {week} has not been taught yet (your class is in week {found['current']}). Its course comes after its session."
                if language == "en" else
                f"La semaine {week} n'a pas encore été vue (votre classe est en semaine {found['current']}). Son cours viendra après sa séance.")
    who = professor.name if professor is not None else ("the team" if language == "en" else "l'équipe")
    if language == "en":
        return (f"The course of week {week} has not been added yet. Your request was passed on to {who} and to the 9antra team: "
                "you will receive it by itself as soon as it is added.")
    return (f"Le cours de la semaine {week} n'a pas encore été ajouté. Votre demande a été transmise à {who} et à l'équipe 9antra : "
            "vous le recevrez automatiquement dès qu'il sera ajouté.")


def keep_waiting(db: Session, candidate, found: dict, message, thread) -> CourseRequest:
    known = (db.query(CourseRequest).filter(CourseRequest.candidate_id == candidate.id, CourseRequest.class_group_id == found["class"].id,
                                            CourseRequest.week_number == found["week"], CourseRequest.status == "waiting").first())
    if known is not None:
        return known
    row = CourseRequest(candidate_id=candidate.id, class_group_id=found["class"].id, week_number=found["week"],
                        message_id=getattr(message, "id", None), thread_id=getattr(thread, "id", None))
    db.add(row)
    db.flush()
    return row


def deliver_waiting_task(class_id: str, week: int) -> None:
    """In the background, with its own session: the course of this week to whoever asked for it."""
    from app.database import SessionLocal
    from app.models import ClassGroup

    db = SessionLocal()
    try:
        group = db.get(ClassGroup, class_id)
        if group is not None:
            deliver_waiting(db, group, week)
    except Exception:
        db.rollback()
        logger.exception("Sending the course of week %s to those who asked failed.", week)
    finally:
        db.close()


def deliver_waiting(db: Session, class_group, week: int) -> int:
    """The professor just added files for this week: everyone who asked for them receives them now. Returns how many."""
    from app.models import Candidate
    from app.models.email_thread import EmailThread
    from app.services import email_assistant, email_threads, email_files

    waiting = (db.query(CourseRequest).filter(CourseRequest.class_group_id == class_group.id, CourseRequest.week_number == week,
                                              CourseRequest.status == "waiting").all())
    files = files_of(db, class_group, week)
    if not waiting or not files:
        return 0
    sent = 0
    for row in waiting:
        candidate = db.get(Candidate, row.candidate_id)
        if candidate is None:
            continue
        thread = db.get(EmailThread, row.thread_id) if row.thread_id else None
        found = {"week": week, "class": class_group, "files": files}
        kept, left = attach(db, files)
        body = f"{email_assistant.greeting_for(candidate, 'fr')}\n\n{reply_text(found, 'fr', left)}"
        text, html_body = email_assistant.compose_reply(db, candidate, body, language="fr")
        message = email_threads.deliver(db, candidate, f"Re: {thread.subject}" if thread else f"Le cours de la semaine {week}", text, html_body,
                                        kind="reply", author="system", thread=thread, language="fr",
                                        note=f"The course of week {week} that {candidate.full_name or candidate.email} asked for, sent once added.",
                                        files=email_files.load(db, kept))
        if message is not None and message.status != "failed":
            row.status, row.sent_at = "sent", datetime.utcnow()
            sent += 1
    db.commit()
    return sent

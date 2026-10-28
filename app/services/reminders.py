"""Reminders the admin writes: to one candidate, a class, a program, everyone, or the professors; now or at a chosen
time, once, every week, every month, or each time a candidate is marked absent.

They are separate from the automatic ones (payment, first session, level
check), which keep their own rules. A reminder written here goes once, at its
time, to the people it names at that moment. It is an email in the centre's
design; the text may hold coloured squares (see email_layout.text_to_html).

`IDEAS` are texts to start from. `suggestions` looks at the classes and
proposes a reminder when one would help; nothing is sent from a suggestion
until the admin has read it and chosen a time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.admin_reminder import AdminReminder
from app.models.candidate import Candidate
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.program import Program
from app.models.registration import Registration, RegistrationStatus
from app.models.user import Attendance
from app.services import schedule_utils
from app.services.email_layout import STYLES

logger = logging.getLogger(__name__)

AUDIENCES = {
    "candidate": "One candidate",
    "class": "A class",
    "program": "Everyone of a program",
    "unpaid": "Those who still owe something",
    "everyone": "Every candidate",
    "professor": "One professor",
    "professors": "Every professor",
}
FOR_PROFESSORS = ("professor", "professors")
REPEATS = {
    "once": "Once",
    "weekly": "Every week",
    "monthly": "Every month",
    "absence": "Each time a candidate is marked absent",
}
MOST_AT_ONCE = 3        # reminders sent in one pass of the job

# Texts to start from. {first_name} and the other variables are filled for each candidate.
IDEAS = [
    {"key": "bring", "name": "What to bring", "icon": "bi-laptop", "style": "brand",
     "subject": "À préparer pour votre prochaine séance", "title": "Pour votre prochaine séance",
     "body": "Bonjour {first_name},\n\nVotre prochaine séance de **{program_name}** approche. Pour en profiter pleinement :\n\n"
             "[box]\n**À apporter**\n- votre ordinateur chargé\n- vos identifiants de connexion\n- vos questions de la semaine\n\n"
             "À très bientôt !"},
    {"key": "date", "name": "A date not to miss", "icon": "bi-calendar-event", "style": "announcement",
     "subject": "Une date à retenir", "title": "Une date à retenir",
     "body": "Bonjour {first_name},\n\nNous vous rappelons un rendez-vous important.\n\n"
             "[box gold]\n**Quand :** [écrivez la date et l'heure]\n**Où :** [en ligne ou l'adresse]\n\n"
             "Merci de confirmer votre présence en répondant à cet email."},
    {"key": "project", "name": "A project to hand in", "icon": "bi-flag", "style": "brand",
     "subject": "Votre projet : la date de remise approche", "title": "Votre projet",
     "body": "Bonjour {first_name},\n\nLa remise de votre projet approche.\n\n"
             "[box]\n**Date limite :** [écrivez la date]\n**À envoyer :** le lien de votre dépôt et une courte présentation\n\n"
             "Si vous êtes bloqué(e) sur un point, répondez à cet email : nous vous aidons avant la date."},
    {"key": "no_session", "name": "No session this week", "icon": "bi-calendar-x", "style": "letter",
     "subject": "Pas de séance cette semaine", "title": "Pas de séance cette semaine",
     "body": "Bonjour {first_name},\n\nIl n'y aura pas de séance cette semaine.\n\n"
             "[box grey]\n**Reprise :** [écrivez la date de reprise]\n\n"
             "Profitez-en pour revoir les dernières notions. À très vite !"},
    {"key": "documents", "name": "Documents to send", "icon": "bi-folder-check", "style": "letter",
     "subject": "Documents à nous envoyer", "title": "Il nous manque un document",
     "body": "Bonjour {first_name},\n\nPour compléter votre dossier, il nous manque :\n\n"
             "[box gold]\n- [document 1]\n- [document 2]\n\n"
             "Vous pouvez les envoyer en réponse à cet email. Merci !"},
    {"key": "missed", "name": "We missed you", "icon": "bi-emoji-smile", "style": "letter",
     "subject": "Vous nous avez manqué", "title": "Tout va bien ?",
     "body": "Bonjour {first_name},\n\nNous ne vous avons pas vu(e) aux dernières séances de **{program_name}** et nous espérons que tout va bien.\n\n"
             "[box green]\nLes enregistrements et les supports sont disponibles : dites-nous ce qu'il vous manque et nous vous aidons à rattraper.\n\n"
             "Répondez simplement à cet email."},
    {"key": "last", "name": "The end of the program", "icon": "bi-award", "style": "announcement",
     "subject": "Votre formation se termine bientôt", "title": "Dernière ligne droite",
     "body": "Bonjour {first_name},\n\nVotre formation **{program_name}** se termine bientôt. Bravo pour le chemin parcouru !\n\n"
             "[box]\n**Pour bien finir**\n- terminez votre projet\n- préparez vos dernières questions\n- dites-nous ce que vous avez pensé de la formation\n\n"
             "À très bientôt pour la dernière séance."},
]
_IDEAS = {idea["key"]: idea for idea in IDEAS}


def idea(key: str | None) -> dict | None:
    return _IDEAS.get(key or "")


def to_utc(local: datetime) -> datetime:
    """A time the admin chose on the centre's clock, as the stored UTC time."""
    return local.replace(tzinfo=schedule_utils._tz()).astimezone(timezone.utc).replace(tzinfo=None)


def _active(db: Session):
    return db.query(Registration).filter(Registration.status != RegistrationStatus.CANCELLED)


def recipients(db: Session, audience: str, audience_id: str | None) -> list[tuple[Candidate, str | None]]:
    """(candidate, the registration the reminder is about) for everyone it goes to, each person once; for the professors,
    (professor, None)."""
    if audience in FOR_PROFESSORS:
        from app.models import Professor

        if audience == "professor":
            person = db.get(Professor, audience_id or "")
            return [(person, None)] if person is not None and person.email else []
        return [(person, None) for person in db.query(Professor).order_by(Professor.name).all()
                if person.email and not (person.name or "").startswith("À nommer")]
    if audience == "candidate":
        person = db.get(Candidate, audience_id or "")
        return [(person, None)] if person is not None and person.email else []
    if audience in ("class", "program") and not audience_id:
        return []
    query = _active(db)
    if audience == "class":
        query = query.filter(Registration.class_group_id == audience_id)
    elif audience == "program":
        query = query.filter(Registration.program_id == audience_id)
    elif audience not in ("unpaid", "everyone"):
        return []
    found, seen = [], set()
    for registration in query.all():
        if audience == "unpaid" and not registration.remaining_amount > 0:
            continue
        person = registration.candidate
        if person is None or not person.email or person.id in seen:
            continue
        seen.add(person.id)
        found.append((person, registration.id))
    return sorted(found, key=lambda pair: (pair[0].full_name or pair[0].email).lower())


def label_of(db: Session, audience: str, audience_id: str | None) -> str:
    if audience == "professor":
        from app.models import Professor

        person = db.get(Professor, audience_id or "")
        return person.name if person is not None else "A professor who is no longer here"
    if audience == "candidate":
        person = db.get(Candidate, audience_id or "")
        return (person.full_name or person.email) if person is not None else "Somebody who is no longer here"
    if audience == "class":
        group = db.get(ClassGroup, audience_id or "")
        return f"Class {group.name}" if group is not None else "A class that no longer exists"
    if audience == "program":
        program = db.get(Program, audience_id or "")
        return f"Everyone of {program.name}" if program is not None else "A program that no longer exists"
    return AUDIENCES.get(audience, audience)


def create(db: Session, *, audience: str, audience_id: str | None, subject: str, body: str, title: str | None = None,
           style: str = "brand", send_at: datetime | None = None, by: str | None = None, channel: str = "email",
           repeat: str = "once", until: datetime | None = None) -> AdminReminder:
    """Keep a reminder. `send_at` is UTC; None means at the next pass, within a minute. `repeat`: once, weekly, monthly,
    or absence (it then waits, and goes to each candidate of its choice the moment they are marked absent)."""
    if audience not in AUDIENCES:
        raise ValueError("Choose who receives the reminder.")
    if audience in ("candidate", "class", "program", "professor") and not audience_id:
        raise ValueError("Choose who receives the reminder.")
    if not subject.strip() or not body.strip():
        raise ValueError("Write a subject and a message.")
    repeat = repeat if repeat in REPEATS else "once"
    if repeat == "absence" and audience in FOR_PROFESSORS:
        raise ValueError("“Each time a candidate is marked absent” goes to that candidate: choose candidates (a class, a program, everyone).")
    if repeat != "absence" and not recipients(db, audience, audience_id):
        raise ValueError("Nobody would receive this reminder: there is nobody with an email address in that choice.")
    reminder = AdminReminder(
        audience=audience, audience_id=audience_id or None, audience_label=label_of(db, audience, audience_id),
        subject=" ".join(subject.split())[:200], title=" ".join((title or "").split())[:120] or None, body=body.strip(),
        style=style if style in STYLES else "brand", send_at=send_at or datetime.utcnow(), created_by=by,
        channel=channel if channel in ("email", "both", "whatsapp") else "email", repeat=repeat, until=until, times=0,
        status="watching" if repeat == "absence" else "scheduled",
    )
    db.add(reminder)
    db.commit()
    return reminder


def _next_time(when: datetime, repeat: str) -> datetime | None:
    """The next time a repeated reminder goes: a week later, or the same day of the next month (its last day when shorter)."""
    import calendar

    if repeat == "weekly":
        return when + timedelta(days=7)
    if repeat == "monthly":
        year, month = (when.year + 1, 1) if when.month == 12 else (when.year, when.month + 1)
        return when.replace(year=year, month=month, day=min(when.day, calendar.monthrange(year, month)[1]))
    return None


def _to_professor(db: Session, reminder: AdminReminder, person) -> bool:
    """A reminder to a professor: their name in it, by email (and WhatsApp when chosen and possible)."""
    import re

    from app.services import email_composer, whatsapp
    from app.services.email_service import send_email

    first = ((person.name or "").replace("Pr ", "").replace("Dr ", "").split() or [""])[0]
    names = {"first_name": first, "full_name": person.name or "", "name": person.name or ""}

    def filled(text: str) -> str:
        return re.sub(r"\{(\w+)\}", lambda found: names.get(found.group(1), ""), text or "")

    rendered = email_composer.render(db, subject=filled(reminder.subject), title=filled(reminder.title or ""), body=filled(reminder.body),
                                     style=reminder.style)
    delivered = False
    if reminder.channel != "whatsapp" or whatsapp.normalise(getattr(person, "phone", None)) is None:
        try:
            delivered = send_email(to_email=person.email, subject=rendered["subject"], body=rendered["html"], is_html=True,
                                   text_body=rendered["text"], journal=False)
        except Exception:
            logger.exception("A reminder to a professor could not be sent.")
    if reminder.channel in ("both", "whatsapp") and whatsapp.normalise(getattr(person, "phone", None)) is not None:
        try:
            # send() says what went wrong, or nothing when it went.
            delivered = (whatsapp.configured() and whatsapp.send(person.phone, rendered["text"]) is None) or delivered
        except Exception:
            logger.exception("A WhatsApp reminder to a professor could not be sent.")
    return delivered


def cancel(db: Session, reminder: AdminReminder) -> bool:
    if reminder.status not in ("scheduled", "watching"):
        return False
    reminder.status = "cancelled"
    db.commit()
    return True


def send(db: Session, reminder: AdminReminder) -> tuple[int, int]:
    """Send one reminder to everyone it names now. Returns (sent, not delivered)."""
    from app.services import email_composer       # here: it imports the mail services

    if reminder.status not in ("scheduled", "sending"):
        return 0, 0
    reminder.status = "sending"
    db.commit()
    sent = failed = 0
    from app.services import whatsapp, whatsapp_inbox

    channel = reminder.channel if reminder.channel in ("email", "both", "whatsapp") else "email"
    for person, registration_id in recipients(db, reminder.audience, reminder.audience_id):
        if reminder.audience in FOR_PROFESSORS:
            delivered = _to_professor(db, reminder, person)
            sent, failed = sent + (1 if delivered else 0), failed + (0 if delivered else 1)
            continue
        try:
            fields = dict(subject=reminder.subject, title=reminder.title or "", body=reminder.body, style=reminder.style,
                          registration_id=registration_id)
            texted = channel in ("both", "whatsapp") and whatsapp.normalise(person.phone) is not None
            delivered = False
            if channel != "whatsapp" or not texted:
                delivered = email_composer.send_one(db, person, **fields)
            if texted:
                from app.services.email_templates import whatsapp_text

                rendered = email_composer.render(db, candidate=person, **fields)
                message = whatsapp_inbox.send(db, person, whatsapp_text(rendered), kind="reminder", author="admin")
                delivered = delivered or bool(message is not None and message.status in ("sent", "to_send"))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Reminder %s to candidate %s failed.", reminder.id, person.id)
            delivered = False
        sent, failed = sent + (1 if delivered else 0), failed + (0 if delivered else 1)
    reminder.status, reminder.sent_at, reminder.sent_count, reminder.failed_count = "sent", datetime.utcnow(), sent, failed
    reminder.times = (reminder.times or 0) + 1
    coming = _next_time(reminder.send_at, reminder.repeat or "once")
    if coming is not None:
        while coming <= datetime.utcnow():            # the server was off for a while: the next one still to come
            coming = _next_time(coming, reminder.repeat)
        if reminder.until is None or coming <= reminder.until:
            reminder.status, reminder.send_at = "scheduled", coming
    db.commit()
    return sent, failed


def answer_absences(db: Session, limit: int = 20) -> int:
    """The reminders that go each time a candidate is marked absent: to every candidate of their choice marked absent
    since the reminder was written, once per absence. Returns how many went."""
    import json

    from app.models.classgroup import ClassGroup as Group
    from app.services import email_composer

    went = 0
    for reminder in db.query(AdminReminder).filter(AdminReminder.status == "watching").all():
        if reminder.until is not None and reminder.until < datetime.utcnow():
            reminder.status = "sent"
            db.commit()
            continue
        try:
            done = set(json.loads(reminder.done_keys or "[]"))
        except ValueError:
            done = set()
        query = db.query(Attendance).filter(Attendance.status == "absent", Attendance.marked_at >= reminder.created_at)
        if reminder.audience == "class":
            query = query.filter(Attendance.class_group_id == reminder.audience_id)
        elif reminder.audience == "candidate":
            query = query.filter(Attendance.candidate_id == reminder.audience_id)
        elif reminder.audience == "program":
            ids = [group.id for group in db.query(Group.id).filter(Group.program_id == reminder.audience_id).all()]
            query = query.filter(Attendance.class_group_id.in_(ids or [""]))
        for mark in query.order_by(Attendance.marked_at.asc()).limit(limit).all():
            if mark.id in done:
                continue
            person = db.get(Candidate, mark.candidate_id)
            done.add(mark.id)
            if person is None or not person.email:
                continue
            if reminder.audience == "unpaid":
                registration = db.get(Registration, mark.registration_id)
                if registration is None or not (registration.remaining_amount or 0) > 0:
                    continue
            try:
                if email_composer.send_one(db, person, subject=reminder.subject, title=reminder.title or "", body=reminder.body,
                                           style=reminder.style, registration_id=mark.registration_id):
                    reminder.sent_count = (reminder.sent_count or 0) + 1
                    reminder.times = (reminder.times or 0) + 1
                    reminder.sent_at = datetime.utcnow()
                    went += 1
            except Exception:
                logger.exception("The absence reminder %s could not go to candidate %s.", reminder.id, person.id)
        reminder.done_keys = json.dumps(sorted(done)[-2000:])
        db.commit()
    return went


def send_due(db: Session, now: datetime | None = None) -> int:
    """Send the reminders whose time has come. Returns how many reminders went."""
    now = now or datetime.utcnow()
    due = (db.query(AdminReminder).filter(AdminReminder.status == "scheduled", AdminReminder.send_at <= now)
           .order_by(AdminReminder.send_at.asc()).limit(MOST_AT_ONCE).all())
    for reminder in due:
        send(db, reminder)
    answer_absences(db)
    return len(due)


def upcoming(db: Session) -> list[AdminReminder]:
    return (db.query(AdminReminder).filter(AdminReminder.status.in_(("scheduled", "sending", "watching")))
            .order_by(AdminReminder.status != "watching", AdminReminder.send_at.asc()).all())


def history(db: Session, limit: int = 12) -> list[AdminReminder]:
    return (db.query(AdminReminder).filter(AdminReminder.status.in_(("sent", "cancelled")))
            .order_by(AdminReminder.send_at.desc()).limit(limit).all())


def _planned_for(db: Session, audience: str, audience_id: str) -> bool:
    return db.query(AdminReminder.id).filter(
        AdminReminder.audience == audience, AdminReminder.audience_id == audience_id,
        AdminReminder.status.in_(("scheduled", "sending"))).first() is not None


def suggestions(db: Session, now: datetime | None = None) -> list[dict]:
    """Reminders worth writing, from how the classes stand: {"text", "why", "href", "icon"}. Nothing is sent from here."""
    now = now or schedule_utils.local_now()
    found = []
    for group in db.query(ClassGroup).filter(ClassGroup.status != ClassStatus.COMPLETED).all():
        members = recipients(db, "class", group.id)
        if not members or _planned_for(db, "class", group.id):
            continue
        start, end = schedule_utils.class_start_dt(group), schedule_utils.class_end_dt(group)
        if group.status == ClassStatus.NOT_STARTED and start is not None and timedelta(0) <= start - now <= timedelta(days=3):
            found.append({"icon": "bi-laptop", "text": f"{group.name} starts {schedule_utils.humanize_until(start, now)}",
                          "why": f"Tell its {len(members)} candidate{'s' if len(members) != 1 else ''} what to bring and prepare.",
                          "href": f"/reminders?audience=class&to={group.id}&idea=bring#new"})
        elif group.status == ClassStatus.ONGOING and end is not None and timedelta(0) <= end - now <= timedelta(days=7):
            found.append({"icon": "bi-award", "text": f"{group.name} ends {schedule_utils.humanize_until(end, now)}",
                          "why": "Remind them of the project, the last session and what comes next.",
                          "href": f"/reminders?audience=class&to={group.id}&idea=last#new"})
    # Absent at their last two sessions.
    last_two: dict[str, list[str]] = {}
    for row in db.query(Attendance).order_by(Attendance.marked_at.desc()).limit(600).all():
        marks = last_two.setdefault(row.candidate_id, [])
        if len(marks) < 2:
            marks.append(row.status)
    for candidate_id, marks in last_two.items():
        if marks == ["absent", "absent"] and not _planned_for(db, "candidate", candidate_id):
            person = db.get(Candidate, candidate_id)
            if person is not None and person.email:
                found.append({"icon": "bi-emoji-smile", "text": f"{person.full_name or person.email} missed their last two sessions",
                              "why": "A short personal note often brings a candidate back.",
                              "href": f"/reminders?audience=candidate&to={person.id}&idea=missed#new"})
    return found[:6]

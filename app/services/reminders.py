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

import json
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
    "daily": "Every day",
    "weekly": "Every week",
    "monthly": "Every month",
    "session": "Before each of their sessions",
    "absence": "Each time a candidate is marked absent",
}
WATCHING = ("absence", "session")       # these wait for something to happen, then go to each candidate it concerns
HOURS_BEFORE = 24                        # before each session: by default, the day before
# What a reminder before each session can say of that session.
SESSION_VARIABLES = (("session_date", "Day of the session", "samedi 14 novembre"), ("session_time", "Hour of the session", "18:00"),
                     ("session_topic", "What the session is about", "Conteneurs avec Docker"))
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


def design_of(reminder) -> dict:
    from app.services import email_layout

    return email_layout.clean_design(getattr(reminder, "design", None))


def create(db: Session, *, audience: str, audience_id: str | None, subject: str, body: str, title: str | None = None,
           style: str = "brand", send_at: datetime | None = None, by: str | None = None, channel: str = "email",
           repeat: str = "once", until: datetime | None = None, design=None, hours_before: int | None = None) -> AdminReminder:
    """Keep a reminder. `send_at` is UTC; None means at the next pass, within a minute. `repeat`: once, daily, weekly,
    monthly; absence (it then waits, and goes to each candidate of its choice the moment they are marked absent); or
    session (it goes to each candidate of its choice `hours_before` hours before each of their sessions)."""
    if audience not in AUDIENCES:
        raise ValueError("Choose who receives the reminder.")
    if audience in ("candidate", "class", "program", "professor") and not audience_id:
        raise ValueError("Choose who receives the reminder.")
    if not subject.strip() or not body.strip():
        raise ValueError("Write a subject and a message.")
    repeat = repeat if repeat in REPEATS else "once"
    if repeat == "absence" and audience in FOR_PROFESSORS:
        raise ValueError("“Each time a candidate is marked absent” goes to that candidate: choose candidates (a class, a program, everyone).")
    if repeat == "session" and audience in FOR_PROFESSORS:
        raise ValueError("“Before each of their sessions” goes to the candidates of each session: choose candidates (a class, a program, everyone).")
    if repeat == "session":
        hours_before = HOURS_BEFORE if hours_before is None else int(hours_before)
        if not 1 <= hours_before <= 168:
            raise ValueError("Before each session: from 1 to 168 hours before.")
    if repeat not in WATCHING and not recipients(db, audience, audience_id):
        raise ValueError("Nobody would receive this reminder: there is nobody with an email address in that choice.")
    reminder = AdminReminder(
        audience=audience, audience_id=audience_id or None, audience_label=label_of(db, audience, audience_id),
        subject=" ".join(subject.split())[:200], title=" ".join((title or "").split())[:120] or None, body=body.strip(),
        style=style if style in STYLES else "brand", send_at=send_at or datetime.utcnow(), created_by=by,
        channel=channel if channel in ("email", "both", "whatsapp") else "email", repeat=repeat, until=until, times=0,
        status="watching" if repeat in WATCHING else "scheduled", hours_before=hours_before if repeat == "session" else None,
    )
    from app.services import email_layout, email_templates

    design = email_layout.clean_design(design)
    email_templates.check_design(db, design)
    reminder.design = json.dumps(design) if design else None
    db.add(reminder)
    db.commit()
    return reminder


def _next_time(when: datetime, repeat: str) -> datetime | None:
    """The next time a repeated reminder goes: a week later, or the same day of the next month (its last day when shorter)."""
    import calendar

    if repeat == "daily":
        return when + timedelta(days=1)
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
                                     style=reminder.style, design=design_of(reminder))
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


def reset(db: Session, reminder: AdminReminder) -> AdminReminder:
    """Starts a reminder again, as if just written: what it counted is cleared, a stopped or finished one is on again.
    It goes next at its own hour: once, the next time that hour comes; repeated, its next turn from now; before each
    session or at each absence, it waits again from now (what came before is not answered)."""
    now = datetime.utcnow()
    reminder.times, reminder.sent_count, reminder.failed_count, reminder.sent_at, reminder.done_keys = 0, 0, 0, None, None
    if reminder.until is not None and reminder.until < now:
        reminder.until = None                       # its last day had passed: it would stop at once
    if (reminder.repeat or "once") in WATCHING:
        reminder.status, reminder.created_at = "watching", now     # absences counted from now on
    else:
        coming = reminder.send_at or now
        if (reminder.repeat or "once") == "once":
            while coming <= now:
                coming += timedelta(days=1)
        else:
            while coming <= now:
                coming = _next_time(coming, reminder.repeat)
        reminder.status, reminder.send_at = "scheduled", coming
    db.commit()
    return reminder


def _session_start(session, group) -> datetime | None:
    """When a session starts, in local time."""
    hour = session.start_time or group.start_time
    return datetime.combine(session.session_date, hour) if session.session_date and hour else None


def answer_sessions(db: Session, now: datetime | None = None, limit: int = 60) -> int:
    """The reminders that go before each session: to each candidate of their choice whose session starts within its
    hours, once per session. Returns how many went."""
    import json

    from app.models.classgroup import ClassGroup as Group, ClassStatus
    from app.models.classsession import ClassSession
    from app.services import email_composer, whatsapp, whatsapp_inbox
    from app.services.schedule_utils import format_date_fr, local_now

    now_local = now or local_now()
    went = 0
    for reminder in db.query(AdminReminder).filter(AdminReminder.status == "watching", AdminReminder.repeat == "session").all():
        if reminder.until is not None and reminder.until < datetime.utcnow():
            reminder.status = "sent"
            db.commit()
            continue
        try:
            done = set(json.loads(reminder.done_keys or "[]"))
        except ValueError:
            done = set()
        ahead = timedelta(hours=reminder.hours_before or HOURS_BEFORE)
        groups = db.query(Group).filter(Group.status != ClassStatus.COMPLETED)
        if reminder.audience == "class":
            groups = groups.filter(Group.id == reminder.audience_id)
        elif reminder.audience == "program":
            groups = groups.filter(Group.program_id == reminder.audience_id)
        wanted = {person.id for person, _ in recipients(db, reminder.audience, reminder.audience_id)} if reminder.audience in ("candidate", "unpaid") else None
        for group in groups.all():
            for session in (db.query(ClassSession).filter(ClassSession.class_group_id == group.id,
                                                          ClassSession.session_date >= now_local.date(),
                                                          ClassSession.session_date <= (now_local + ahead).date()).all()):
                starts = _session_start(session, group)
                if starts is None or not (now_local < starts <= now_local + ahead):
                    continue
                values = {"session_date": format_date_fr(starts), "session_time": f"{starts:%H:%M}", "session_topic": session.topic or ""}
                for registration in _active(db).filter(Registration.class_group_id == group.id).all():
                    person = registration.candidate
                    key = f"{session.id}:{registration.id}"
                    if key in done or person is None or (wanted is not None and person.id not in wanted):
                        continue
                    done.add(key)
                    fields = dict(subject=reminder.subject, title=reminder.title or "", body=reminder.body, style=reminder.style,
                                  registration_id=registration.id, design=design_of(reminder), values=values)
                    try:
                        texted = reminder.channel in ("both", "whatsapp") and whatsapp.normalise(person.phone) is not None
                        delivered = False
                        if (reminder.channel != "whatsapp" or not texted) and person.email:
                            delivered = email_composer.send_one(db, person, **fields)
                        if texted:
                            from app.services.email_templates import whatsapp_text

                            rendered = email_composer.render(db, candidate=person, **fields)
                            message = whatsapp_inbox.send(db, person, whatsapp_text(rendered), kind="reminder", author="admin")
                            delivered = delivered or bool(message is not None and message.status in ("sent", "to_send"))
                    except Exception:
                        db.rollback()
                        logger.exception("The session reminder %s could not go to candidate %s.", reminder.id, person.id)
                        delivered = False
                    if delivered:
                        reminder.sent_count = (reminder.sent_count or 0) + 1
                        reminder.times = (reminder.times or 0) + 1
                        reminder.sent_at = datetime.utcnow()
                        went += 1
                    else:
                        reminder.failed_count = (reminder.failed_count or 0) + 1
                    if went >= limit:
                        break
        reminder.done_keys = json.dumps(sorted(done)[-4000:])
        db.commit()
    return went


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
                          registration_id=registration_id, design=design_of(reminder))
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
    for reminder in db.query(AdminReminder).filter(AdminReminder.status == "watching", AdminReminder.repeat == "absence").all():
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
                                           style=reminder.style, registration_id=mark.registration_id, design=design_of(reminder)):
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
    answer_sessions(db)
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
    """Reminders worth writing, from how the classes stand: {"text", "why", "href", "icon"}. Nothing is sent from here.
    (What is smart for each candidate, their absences among it, is in services/smart_reminders.py.)"""
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
    return found[:6]

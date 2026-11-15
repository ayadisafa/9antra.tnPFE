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
import threading
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
    "every": "Again and again",
    "session": "Before each of their sessions",
    "absence": "After each absence",
    "start": "Before their class starts",
    "after_start": "After their class started",
    "end": "Before their class ends",
    "after_end": "After their class ended",
    "unanswered": "Weekly questions not answered",
    "registered": "After they registered",
}
OLD_REPEATS = {"daily": "1d", "weekly": "1w", "monthly": "1mo"}      # kept for the reminders written before "every"
# These wait for something to happen, then go to each candidate it concerns (once per thing, `offset` before or after).
WATCHING = ("absence", "session", "start", "after_start", "end", "after_end", "unanswered", "registered")
EVENTS = ("start", "after_start", "end", "after_end", "unanswered", "registered")
OFFSET_DEFAULTS = {"session": "24h", "absence": "0min", "start": "2d", "after_start": "3w", "end": "1w", "after_end": "3d",
                   "unanswered": "2d", "registered": "2d"}
LATE_BY = timedelta(days=7)              # a moment passed longer ago than this is not sent any more (after a long stop)
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


def offset_of(reminder: AdminReminder):
    """How long before / after what it waits for (a reminder written in hours before each session keeps its hours)."""
    from app.services import durations

    found = durations.parse(reminder.offset or "", "hours")
    if found is not None:
        return found
    if reminder.repeat == "session":
        return durations.Duration(reminder.hours_before or HOURS_BEFORE, "hours")
    return durations.parse(OFFSET_DEFAULTS.get(reminder.repeat or "", "0min"), "hours")


def every_of(reminder: AdminReminder):
    from app.services import durations

    if reminder.repeat == "every":
        return durations.parse(reminder.every or "", "days")
    code = OLD_REPEATS.get(reminder.repeat or "")
    return durations.parse(code, "days") if code else None


def create(db: Session, *, audience: str, audience_id: str | None, subject: str, body: str, title: str | None = None,
           style: str = "brand", send_at: datetime | None = None, by: str | None = None, channel: str = "email",
           repeat: str = "once", until: datetime | None = None, design=None, hours_before: int | None = None,
           every=None, offset=None, kind: str | None = None) -> AdminReminder:
    """Keep a reminder. `send_at` is UTC; None means at the next pass, within a minute. `repeat`: once, daily, weekly,
    monthly; absence (it then waits, and goes to each candidate of its choice the moment they are marked absent); or
    session (it goes to each candidate of its choice `hours_before` hours before each of their sessions)."""
    if audience not in AUDIENCES:
        raise ValueError("Choose who receives the reminder.")
    if audience in ("candidate", "class", "program", "professor") and not audience_id:
        raise ValueError("Choose who receives the reminder.")
    if not subject.strip() or not body.strip():
        raise ValueError("Write a subject and a message.")
    from app.services import durations

    repeat = repeat if (repeat in REPEATS or repeat in OLD_REPEATS) else "once"
    if repeat == "every":
        every = durations.parse(every or "", "days")
        if every is None or every.value <= 0:
            raise ValueError("Again and again: write how often (a number and its unit).")
        durations.checked(every, least=durations.Duration(1, "minutes"), most=durations.Duration(12, "months"))
    if repeat in WATCHING:
        offset = durations.parse(offset if offset is not None else (f"{hours_before}h" if repeat == "session" and hours_before else
                                                                     OFFSET_DEFAULTS[repeat]), "hours")
        if offset is None:
            raise ValueError("Write the time with its unit.")
        durations.checked(offset, most=durations.Duration(6, "months"), zero=repeat == "absence")
        if repeat == "session":
            durations.checked(offset, least=durations.Duration(1, "minutes"), most=durations.Duration(1, "months"))
            hours_before = max(1, -(-offset.seconds // 3600))
    if repeat in EVENTS and audience in FOR_PROFESSORS:
        raise ValueError("This one goes to candidates: choose candidates (one, a class, a program, everyone).")
    if repeat == "absence" and audience in FOR_PROFESSORS:
        raise ValueError("“Each time a candidate is marked absent” goes to that candidate: choose candidates (a class, a program, everyone).")
    if repeat == "session" and audience in FOR_PROFESSORS:
        raise ValueError("“Before each of their sessions” goes to the candidates of each session: choose candidates (a class, a program, everyone).")
    if repeat not in WATCHING and not recipients(db, audience, audience_id):
        raise ValueError("Nobody would receive this reminder: there is nobody with an email address in that choice.")
    reminder = AdminReminder(
        audience=audience, audience_id=audience_id or None, audience_label=label_of(db, audience, audience_id),
        subject=" ".join(subject.split())[:200], title=" ".join((title or "").split())[:120] or None, body=body.strip(),
        style=style if style in STYLES else "brand", send_at=send_at or datetime.utcnow(), created_by=by,
        channel=channel if channel in ("email", "both", "whatsapp") else "email", repeat=repeat, until=until, times=0,
        status="watching" if repeat in WATCHING else "scheduled", hours_before=hours_before if repeat == "session" else None,
        kind=kind or None, every=every.code if repeat == "every" else None, offset=offset.code if repeat in WATCHING else None,
    )
    from app.services import email_layout, email_templates

    design = email_layout.clean_design(design)
    email_templates.check_design(db, design)
    reminder.design = json.dumps(design) if design else None
    db.add(reminder)
    db.commit()
    return reminder


def _next_time(when: datetime, repeat: str, every: str | None = None) -> datetime | None:
    """The next time a repeated reminder goes: `every` later (a month: the same day of the next month, or its last day)."""
    import calendar

    if repeat == "every":
        from app.services import durations

        step = durations.parse(every or "", "days")
        return step.after(when) if step is not None and step.value > 0 else None
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
            while coming is not None and coming <= now:
                coming = _next_time(coming, reminder.repeat, reminder.every)
        reminder.status, reminder.send_at = "scheduled", coming or now
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
        ahead = offset_of(reminder).delta
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
    if reminder.status not in ("scheduled", "watching", "paused"):
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
    coming = _next_time(reminder.send_at, reminder.repeat or "once", reminder.every)
    if coming is not None:
        while coming <= datetime.utcnow():            # the server was off for a while: the next one still to come
            coming = _next_time(coming, reminder.repeat, reminder.every)
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
        wait = offset_of(reminder).delta          # how long after the absence it goes (0: at once)
        query = db.query(Attendance).filter(Attendance.status == "absent", Attendance.marked_at >= reminder.created_at)
        if wait:
            query = query.filter(Attendance.marked_at <= datetime.utcnow() - wait)
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


def _deliver(db: Session, reminder: AdminReminder, person, registration_id: str | None, values: dict | None = None) -> bool:
    """One candidate, by the reminder's way (email, WhatsApp or both)."""
    from app.services import email_composer, whatsapp, whatsapp_inbox

    fields = dict(subject=reminder.subject, title=reminder.title or "", body=reminder.body, style=reminder.style,
                  registration_id=registration_id, design=design_of(reminder), values=values or {})
    texted = reminder.channel in ("both", "whatsapp") and whatsapp.normalise(person.phone) is not None
    delivered = False
    if (reminder.channel != "whatsapp" or not texted) and person.email:
        delivered = email_composer.send_one(db, person, **fields)
    if texted:
        from app.services.email_templates import whatsapp_text

        rendered = email_composer.render(db, candidate=person, **fields)
        message = whatsapp_inbox.send(db, person, whatsapp_text(rendered), kind="reminder", author="admin")
        delivered = delivered or bool(message is not None and message.status in ("sent", "to_send"))
    return delivered


def _events(db: Session, reminder: AdminReminder, now_local: datetime, now_utc: datetime) -> list[tuple]:
    """(key, registration, values) of what this watching reminder must answer now (each thing once)."""
    from app.models.classgroup import ClassGroup as Group
    from app.models.quiz import Quiz
    from app.services.schedule_utils import class_end_dt, class_start_dt, format_date_fr

    offset = offset_of(reminder)
    scope = _active(db)
    if reminder.audience == "class":
        scope = scope.filter(Registration.class_group_id == reminder.audience_id)
    elif reminder.audience == "program":
        scope = scope.filter(Registration.program_id == reminder.audience_id)
    elif reminder.audience == "candidate":
        scope = scope.filter(Registration.candidate_id == reminder.audience_id)
    found = []
    for registration in scope.all():
        if reminder.audience == "unpaid" and not (registration.remaining_amount or 0) > 0:
            continue
        group = registration.class_group
        if reminder.repeat == "registered":
            moment = registration.registration_date
            if moment is None:
                continue
            if moment.tzinfo is not None:
                from datetime import timezone

                moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
            due = offset.after(moment)
            if moment >= reminder.created_at - LATE_BY and due <= now_utc <= due + LATE_BY:
                found.append((f"reg:{registration.id}", registration, {}))
            continue
        if reminder.repeat == "unanswered":
            for quiz in (db.query(Quiz).filter(Quiz.registration_id == registration.id, Quiz.kind == "weekly", Quiz.sent_at.isnot(None),
                                               Quiz.completed_at.is_(None)).all()):
                due = offset.after(quiz.sent_at)
                if due <= now_utc <= due + LATE_BY:
                    found.append((f"quiz:{quiz.id}", registration, {}))
            continue
        if group is None:
            continue
        start, end = class_start_dt(group), class_end_dt(group)
        values = {"start_date": format_date_fr(start) if start else "", "start_time": f"{start:%H:%M}" if start else ""}
        if reminder.repeat == "start" and start is not None and offset.before(start) <= now_local < start:
            found.append((f"start:{registration.id}:{group.id}", registration, values))
        elif reminder.repeat == "end" and end is not None and offset.before(end) <= now_local < end:
            found.append((f"end:{registration.id}:{group.id}", registration, values))
        elif reminder.repeat == "after_start" and start is not None and offset.after(start) <= now_local <= offset.after(start) + LATE_BY \
                and (end is None or now_local < end):
            found.append((f"after_start:{registration.id}:{group.id}", registration, values))
        elif reminder.repeat == "after_end" and end is not None and offset.after(end) <= now_local <= offset.after(end) + LATE_BY:
            found.append((f"after_end:{registration.id}:{group.id}", registration, values))
    return found


def answer_events(db: Session, limit: int = 60) -> int:
    """The watching reminders of the new triggers: each candidate it concerns, once per thing. Returns how many went."""
    import json

    from app.services.schedule_utils import local_now

    now_local, now_utc = local_now(), datetime.utcnow()
    went = 0
    for reminder in db.query(AdminReminder).filter(AdminReminder.status == "watching", AdminReminder.repeat.in_(EVENTS)).all():
        if reminder.until is not None and reminder.until < now_utc:
            reminder.status = "sent"
            db.commit()
            continue
        try:
            done = set(json.loads(reminder.done_keys or "[]"))
        except ValueError:
            done = set()
        for key, registration, values in _events(db, reminder, now_local, now_utc):
            person = registration.candidate
            if key in done or person is None:
                continue
            done.add(key)
            try:
                delivered = _deliver(db, reminder, person, registration.id, values)
            except Exception:
                db.rollback()
                logger.exception("The reminder %s could not go to candidate %s.", reminder.id, person.id)
                delivered = False
            if delivered:
                reminder.sent_count, reminder.times, reminder.sent_at = (reminder.sent_count or 0) + 1, (reminder.times or 0) + 1, now_utc
                went += 1
            else:
                reminder.failed_count = (reminder.failed_count or 0) + 1
            if went >= limit:
                break
        reminder.done_keys = json.dumps(sorted(done)[-4000:])
        db.commit()
    return went


_SENDING = threading.Lock()      # the 10-second pass and the minute pass never send the same reminder twice


def send_scheduled(db: Session, now: datetime | None = None) -> int:
    """Only the reminders at a time (also every 10 seconds: a timer in seconds goes on time)."""
    if not _SENDING.acquire(blocking=False):
        return 0                                 # the other pass is sending them right now
    try:
        now = now or datetime.utcnow()
        due = (db.query(AdminReminder).filter(AdminReminder.status == "scheduled", AdminReminder.send_at <= now)
               .order_by(AdminReminder.send_at.asc()).limit(MOST_AT_ONCE).all())
        for reminder in due:
            send(db, reminder)
        return len(due)
    finally:
        _SENDING.release()


def send_due(db: Session, now: datetime | None = None) -> int:
    """Send the reminders whose time has come. Returns how many reminders went."""
    count = send_scheduled(db, now)
    answer_absences(db)
    answer_sessions(db)
    answer_events(db)
    return count


def pause(db: Session, reminder: AdminReminder, on: bool) -> str:
    """Switch an automatic reminder off (it waits, nothing is sent) or on again."""
    if on and reminder.status == "paused":
        reminder.status = "watching" if reminder.repeat in WATCHING else "scheduled"
        if reminder.status == "scheduled" and reminder.send_at <= datetime.utcnow():
            step = every_of(reminder)
            while step is not None and reminder.send_at <= datetime.utcnow():
                reminder.send_at = step.after(reminder.send_at)
    elif not on and reminder.status in ("watching", "scheduled"):
        reminder.status = "paused"
    db.commit()
    return reminder.status


def set_offset(db: Session, reminder: AdminReminder, duration) -> None:
    """Its timer: how often (every) or how long before / after what it waits for."""
    from app.services import durations

    if reminder.repeat == "every" or reminder.repeat in OLD_REPEATS:
        durations.checked(duration, least=durations.Duration(1, "minutes"), most=durations.Duration(12, "months"))
        reminder.repeat, reminder.every = "every", duration.code
    elif reminder.repeat in WATCHING:
        durations.checked(duration, most=durations.Duration(6, "months"), zero=reminder.repeat == "absence")
        reminder.offset = duration.code
        if reminder.repeat == "session":
            durations.checked(duration, least=durations.Duration(1, "minutes"))
            reminder.hours_before = max(1, -(-duration.seconds // 3600))
    else:
        raise ValueError("This reminder goes once: change its day instead.")
    db.commit()


def rules(db: Session) -> list[AdminReminder]:
    """The admin's reminders that go by themselves again and again: shown with the automatic ones."""
    return (db.query(AdminReminder).filter(AdminReminder.status.in_(("watching", "scheduled", "sending", "paused")),
                                           AdminReminder.repeat != "once").order_by(AdminReminder.created_at.asc()).all())


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

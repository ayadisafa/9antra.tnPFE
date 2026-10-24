"""Send again something the application sends by itself once (the admin “resets” it), now or at the time they choose:
the welcome message, the payment reminder, the level check, the questions after a session, the week's questions, the
session links, the professor's announcement of a class, the review request, the progress report.

A reset is a line in `planned_resends`: what, for whom (a candidate, a class, or everyone it concerns), when. The
reminders job sends the lines whose time has come (run_due); “now” is sent at once. Each line keeps what happened,
and the admin is told. The assistant takes it in words too: “renvoie le message de bienvenue à Fares demain à 9h”,
“3awed ab3ath el rappel mta3 el khlas l Fares ghodwa”; without a time, it asks when.
"""
from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.orm import Session

from app.database import Base

logger = logging.getLogger(__name__)


class PlannedResend(Base):
    __tablename__ = "planned_resends"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    kind = Column(String, nullable=False)
    target_kind = Column(String, nullable=False)          # candidate | class | all
    target_id = Column(String, nullable=True)
    target_name = Column(String, nullable=True)
    due_at = Column(DateTime, nullable=False, index=True)  # local time
    status = Column(String, nullable=False, default="planned")    # planned | done | cancelled
    outcome = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    done_at = Column(DateTime, nullable=True)


# kind: (name, for whom: candidate / class / all, the words that name it)
KINDS = {
    "welcome": ("Welcome message", "candidate", r"bienvenue|welcome|accueil|marhba"),
    "payment": ("Payment reminder", "candidate", r"rappel (de |du )?(paiement|paye\w*)|payment reminder|relance|khlas|paiement|payment"),
    "level": ("Level check", "candidate", r"test de niveau|level check|niveau|level|mostawa"),
    "session": ("Questions after the session", "class", r"apres la seance|after the session|questions? de la seance|session questions?|seance|session"),
    "weekly": ("Weekly questions", "class", r"questions? de la semaine|weekly questions?|semaine|week"),
    "links": ("Session links", "class", r"liens?|links?|lien de la seance|meet"),
    "announcement": ("Professor's announcement", "class", r"annonce|announcement|formateur|professeur|prof"),
    "reviews": ("Review request", "candidate", r"avis|review|opinion|note de fin"),
    "report": ("Progress report", "all", r"rapport|report"),
}


def name_of(kind: str) -> str:
    return KINDS.get(kind, (kind,))[0]


# ------------------------------------------------------------------
# Planning, cancelling, the list
# ------------------------------------------------------------------

def plan(db: Session, kind: str, *, target_id: str | None, when: datetime, created_by: str = "Admin") -> PlannedResend:
    """Plan `kind` again for this candidate or class (or everyone, for the report) at `when` (local time)."""
    from app.models import Candidate, ClassGroup

    if kind not in KINDS:
        raise ValueError("Unknown thing to send again.")
    for_whom = KINDS[kind][1]
    name = None
    if for_whom == "candidate":
        person = db.get(Candidate, target_id or "")
        if person is None:
            raise ValueError("Choose the candidate to send it to again.")
        name = person.full_name or person.email
    elif for_whom == "class":
        group = db.get(ClassGroup, target_id or "")
        if group is None:
            raise ValueError("Choose the class to send it to again.")
        name = group.name
    row = PlannedResend(kind=kind, target_kind=for_whom, target_id=target_id if for_whom != "all" else None, target_name=name,
                        due_at=when.replace(second=0, microsecond=0), created_by=created_by)
    db.add(row)
    db.commit()
    return row


def cancel(db: Session, resend_id: str) -> bool:
    row = db.get(PlannedResend, resend_id)
    if row is None or row.status != "planned":
        return False
    row.status, row.done_at = "cancelled", datetime.utcnow()
    db.commit()
    return True


def planned(db: Session) -> list[PlannedResend]:
    return db.query(PlannedResend).filter(PlannedResend.status == "planned").order_by(PlannedResend.due_at).all()


def recent(db: Session, limit: int = 6) -> list[PlannedResend]:
    return (db.query(PlannedResend).filter(PlannedResend.status == "done").order_by(PlannedResend.done_at.desc()).limit(limit).all())


def said(row: PlannedResend, now: datetime | None = None, fr: bool = False) -> str:
    """“The welcome message will go again to Fares on Saturday 25 October at 09:00.”"""
    from app.services.schedule_utils import format_date_en, format_date_fr, local_now

    now = now or local_now()
    who = f" {'à' if fr else 'to'} {row.target_name}" if row.target_name else ""
    if row.due_at <= now + timedelta(minutes=1):
        return (f"« {name_of_fr(row.kind)} » repart{who} maintenant." if fr else f"“{name_of(row.kind)}” goes again{who} now.")
    day = format_date_fr(row.due_at) if fr else format_date_en(row.due_at)
    return (f"C'est réinitialisé : « {name_of_fr(row.kind)} » repartira{who} le {day} à {row.due_at:%H:%M}." if fr else
            f"Reset: “{name_of(row.kind)}” will go again{who} on {day} at {row.due_at:%H:%M}.")


_FRENCH_NAMES = {"welcome": "Message de bienvenue", "payment": "Rappel de paiement", "level": "Test de niveau", "session": "Questions après la séance",
                 "weekly": "Questions de la semaine", "links": "Liens de la séance", "announcement": "Annonce au formateur",
                 "reviews": "Demande d'avis", "report": "Rapport d'avancement"}


def name_of_fr(kind: str) -> str:
    return _FRENCH_NAMES.get(kind, kind)


# ------------------------------------------------------------------
# Sending
# ------------------------------------------------------------------

def _active(candidate) -> list:
    from app.models.registration import RegistrationStatus

    return [item for item in candidate.registrations if item.status != RegistrationStatus.CANCELLED]


def _send(db: Session, row: PlannedResend, base_url: str) -> str:
    """Send it again now; what happened, in a line."""
    from app.models import Candidate, ClassGroup
    from app.services import email_templates

    if row.kind == "report":
        from app.services import report_email

        outcome = report_email.send(db)
        return f"Sent to {', '.join(outcome.get('to') or [])}." if outcome.get("ok") else (outcome.get("problem") or "Not sent.")
    if row.target_kind == "candidate":
        candidate = db.get(Candidate, row.target_id or "")
        if candidate is None:
            return "The candidate no longer exists."
        registrations = _active(candidate)
        if row.kind == "welcome":
            if not registrations:
                return "They have no registration."
            message = email_templates.send(db, "welcome", registration=registrations[0], candidate=candidate, manual=True)
            return "Sent." if email_templates.delivered(message) else "Not delivered (see the inbox)."
        if row.kind == "payment":
            from app.services import payment_reminders

            message = payment_reminders.send(db, candidate, manual=True)
            if message is None:
                return "They owe nothing: no reminder was needed."
            return "Sent." if email_templates.delivered(message) else "Not delivered (see the inbox)."
        if row.kind == "level":
            from app.services import level_check

            sent = 0
            for registration in registrations:
                quiz = level_check.current(db, registration.id) or (level_check.prepare(db, registration) if level_check.can_have_one(registration) else None)
                if quiz is not None and quiz.completed_at is None and level_check.send(db, quiz, base_url, manual=True) is not None:
                    sent += 1
            return f"Sent ({sent})." if sent else "No level check to send (none, or already answered)."
        if row.kind == "reviews":
            from app.services import program_feedback

            asked = sum(1 for registration in registrations if registration.class_group_id and program_feedback.ask(db, registration, manual=True))
            return f"Asked ({asked})." if asked else "No finished class to ask about."
    group = db.get(ClassGroup, row.target_id or "")
    if group is None:
        return "The class no longer exists."
    if row.kind == "session":
        from app.services import session_check

        return session_check.send_again(db, group, base_url)
    if row.kind == "weekly":
        from app.models.course_material import ClassWeek
        from app.services import weekly_questions

        week = (db.query(ClassWeek).filter(ClassWeek.class_group_id == group.id, ClassWeek.sent_at.isnot(None))
                .order_by(ClassWeek.week_number.desc()).first())
        if week is None:
            return "No week's questions were sent yet in this class."
        outcome = weekly_questions.remind_week(db, group, week.week_number, base_url=base_url)
        return outcome.get("error") or f"Week {week.week_number}: sent again to {outcome['sent']} who had not answered."
    if row.kind == "links":
        from app.models.classsession import ClassSession
        from app.services import meetings
        from app.services.schedule_utils import local_now

        session = (db.query(ClassSession).filter(ClassSession.class_group_id == group.id, ClassSession.session_date >= local_now().date())
                   .order_by(ClassSession.session_date).first())
        if session is None:
            return "No session to come in this class."
        outcome = meetings.send_links(db, group, session, base_url, manual=True, again=True)
        return f"Session of {session.session_date:%d/%m}: {outcome.get('sent', 0)} link(s) sent."
    if row.kind == "announcement":
        from app.services import professor_announcements

        if not group.professor_id:
            return "The class has no professor."
        found = (db.query(professor_announcements.ProfessorAnnouncement)
                 .filter_by(class_group_id=group.id, professor_id=group.professor_id).first())
        if found is None:
            found = professor_announcements.ProfessorAnnouncement(class_group_id=group.id, professor_id=group.professor_id)
            db.add(found)
        found.sent_at = None
        db.commit()
        return f"Sent to {group.professor_name}." if professor_announcements.send_due(db, base_url) else "Not sent (the professor cannot be reached)."
    if row.kind == "reviews":
        from app.services import program_feedback

        outcome = program_feedback.ask_class(db, group)
        return f"Asked: {outcome['sent']}; already answered: {outcome['answered']}; could not be reached: {outcome['failed']}."
    return "Nothing to send."


def run(db: Session, row: PlannedResend, base_url: str) -> str:
    from app.models.notification import Notification

    try:
        outcome = _send(db, row, base_url)
    except Exception:
        db.rollback()
        logger.exception("Sending %s again failed.", row.kind)
        outcome = "Something went wrong: nothing was sent."
    row = db.get(PlannedResend, row.id)
    row.status, row.outcome, row.done_at = "done", outcome, datetime.utcnow()
    db.add(Notification(recipient_role="admin", category="RESEND", title=f"Sent again: {name_of(row.kind)}"
                        + (f" · {row.target_name}" if row.target_name else ""), message=outcome, action_url="/system#automatic"))
    db.commit()
    return outcome


def run_due(db: Session, base_url: str, now: datetime | None = None) -> int:
    """Send the resets whose time has come. Returns how many."""
    from app.services.schedule_utils import local_now

    now = now or local_now()
    done = 0
    for row in db.query(PlannedResend).filter(PlannedResend.status == "planned", PlannedResend.due_at <= now).order_by(PlannedResend.due_at).all():
        run(db, row, base_url)
        done += 1
    return done


# ------------------------------------------------------------------
# When, said in words (French, English, Tunisian)
# ------------------------------------------------------------------

def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9:/ ]+", " ", text)


def when_from(text: str, now: datetime) -> datetime | None:
    """The moment a message names: “now”, “tawa”, “demain à 9h”, “ghodwa 14:30”, “à 18h”, “le 26/10 à 9h”, “dans 2 heures”,
    “ba3d sa3tin”. None when it names none."""
    plain = _plain(text)
    if re.search(r"\b(maintenant|tout de suite|now|right away|tawa|taw|tawwa|immediatement)\b", plain):
        return now
    found = re.search(r"\b(?:dans|in|ba3d|baad)\s+(\d{1,2}|une|un|one|a|sa3a)\s*(heures?|hours?|h|sa3at|swaye3|minutes?|min)\b", plain)
    if found:
        amount = 1 if found.group(1) in ("une", "un", "one", "a", "sa3a") else int(found.group(1))
        unit = found.group(2)
        return now + (timedelta(minutes=amount) if unit.startswith("min") else timedelta(hours=amount))
    if re.search(r"\b(ba3d|baad) (sa3tin|saatin)\b", plain):
        return now + timedelta(hours=2)
    day = None
    if re.search(r"\b(apres demain|apres-demain|after tomorrow|ba3d ghodwa)\b", plain):
        day = now.date() + timedelta(days=2)
    elif re.search(r"\b(demain|tomorrow|ghodwa|ghudwa)\b", plain):
        day = now.date() + timedelta(days=1)
    elif re.search(r"\b(aujourd hui|today|lyoum|el youm|elyoum|ce soir|tonight|lila)\b", plain):
        day = now.date()
    dated = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", plain)
    if dated:
        year = int(dated.group(3)) if dated.group(3) else now.year
        year = year + 2000 if year < 100 else year
        try:
            day = datetime(year, int(dated.group(2)), int(dated.group(1))).date()
        except ValueError:
            pass
    clock = re.search(r"\b(?:a|at|3la|vers|around)?\s*(\d{1,2})(?:[h:](\d{2})?|\s*(?:heures?|h))\b", plain)
    if clock is None and day is None:
        return None
    hour = int(clock.group(1)) if clock else 9
    minute = int(clock.group(2) or 0) if clock else 0
    if hour > 23 or minute > 59:
        return None
    if day is None:
        day = now.date()
        if (hour, minute) <= (now.hour, now.minute):
            day += timedelta(days=1)            # “à 9h” said at 10:00: tomorrow
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)


# ------------------------------------------------------------------
# Said to the assistant
# ------------------------------------------------------------------

_AGAIN = re.compile(r"\b(renvoie\w*|renvoyer|reenvoie\w*|re envoie\w*|resend\w*|send (?:it |them )?again|again to|a nouveau|de nouveau|"
                    r"reset\w*|reinitialise\w*|3awed|3aoued|aawed|3awdou)\b")
ASK_WHEN = ("Quand voulez-vous qu'il reparte ? Par exemple : maintenant, demain à 9h, le 26/10 à 14h, dans 2 heures.",
            "When should it go again? For example: now, tomorrow at 9, on 26/10 at 14:00, in 2 hours.")


def _who(db: Session, kind: str, plain: str) -> tuple[str | None, str | None]:
    from app.models import Candidate, ClassGroup, ClassStatus

    if KINDS[kind][1] == "candidate":
        matches = []
        for person in db.query(Candidate).all():
            name = _plain(person.full_name or "")
            words = [word for word in name.split() if len(word) >= 3]
            if name and (f" {name} " in f" {plain} " or (words and all(f" {word} " in f" {plain} " for word in words[:1]))):
                matches.append(person)
        full = [person for person in matches if f" {_plain(person.full_name or '')} " in f" {plain} "]
        chosen = full if len(full) == 1 else matches
        return (chosen[0].id, chosen[0].full_name) if len(chosen) == 1 else (None, None)
    if KINDS[kind][1] == "class":
        groups = db.query(ClassGroup).filter(ClassGroup.status != ClassStatus.COMPLETED).all()
        named = [group for group in groups if _plain(group.name) and _plain(group.name) in plain]
        if not named:
            named = [group for group in groups if group.program and _plain(group.program.name) and _plain(group.program.name) in plain]
        return (named[0].id, named[0].name) if len(named) == 1 else (None, None)
    return None, None


def from_words(db: Session, message: str) -> dict | None:
    """What a message asks to send again: {"kind", "target_id", "target_name", "when"} (any may be None), or None when
    it does not ask to send something again."""
    from app.services import derja
    from app.services.schedule_utils import local_now

    plain = _plain(f"{message} {derja.to_french(message)}")
    if not _AGAIN.search(plain):
        return None
    kind = next((key for key, (_, _, words) in KINDS.items() if re.search(r"\b(" + words + r")\b", plain)), None)
    if kind is None:
        return {"kind": None, "target_id": None, "target_name": None, "when": None}
    target_id, target_name = _who(db, kind, plain)
    return {"kind": kind, "target_id": target_id, "target_name": target_name, "when": when_from(message, local_now())}


_ENGLISH = re.compile(r"\b(resend|send (?:it |them )?again|reset|tomorrow|now|today|in \d+ hours?)\b")


def in_french(message: str) -> bool:
    """A reset said in French or Tunisian is answered in French; in English, in English."""
    return not _ENGLISH.search(_plain(message))


def answer(db: Session, asked: dict, fr: bool, *, author: str = "Admin", base_url: str = "") -> dict:
    """The assistant's reply: asks what, whom or when when it is missing; plans it (and sends it if it is now)."""
    from app.services.schedule_utils import local_now

    if asked["kind"] is None:
        names = ", ".join((name_of_fr(key) if fr else name_of(key)).lower() for key in KINDS)
        return {"reply": (f"Que faut-il renvoyer ? Par exemple : {names}." if fr else f"What should go again? For example: {names}."),
                "links": [{"label": "Send something again", "url": "/system#resend"}], "source": "action"}
    for_whom = KINDS[asked["kind"]][1]
    if for_whom != "all" and not asked["target_id"]:
        whom = ("Pour quel candidat ? Écrivez son nom complet." if for_whom == "candidate" else "Pour quelle classe ? Écrivez son nom ou sa formation.") if fr \
            else ("For which candidate? Write their full name." if for_whom == "candidate" else "For which class? Write its name or its program.")
        return {"reply": whom, "links": [{"label": "Send something again", "url": "/system#resend"}], "source": "action"}
    if asked["when"] is None:
        return {"reply": ASK_WHEN[0 if fr else 1], "links": [], "source": "action", "asks": "when"}
    now = local_now()
    row = plan(db, asked["kind"], target_id=asked["target_id"], when=max(asked["when"], now), created_by=author)
    reply = said(row, now, fr)
    if row.due_at <= now + timedelta(minutes=1):
        outcome = run(db, row, base_url)
        reply += ("\n" + ("Ce qui s'est passé : " if fr else "What happened: ") + outcome)
    return {"reply": reply, "links": [{"label": "Planned to go again" if row.status == "planned" else "Sent again", "url": "/system#resend"}],
            "source": "action", "sources": [{"label": "What the application sends by itself, sent again", "url": "/system#resend"}]}

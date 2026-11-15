"""A professor away for a day or a period: they ask, and the admin accepts or refuses when nobody can replace them.

A professor asks from their space (/teacher/space#absences): from a day to a day, the whole day or some hours of
each day, and why. Each of their sessions in that period is looked at: can someone replace them? Someone can when they
teach the session's program, are available that date, are not away themselves and have no class of their own at those
hours (services/replacements.busy_then). Then, with Settings › Classes › "A professor asks to be away" on its default
("nobody"):

  every session has someone free (or there is no session)  -> accepted at once: each session asks its replacement
                                                               (one professor at a time, they confirm), the admin is told
  a session has nobody free                                  -> the request waits for the admin (/professors?tab=absences)

With "always", every request waits for the admin. The admin accepts (the sessions look for a replacement; those with
nobody follow Settings › Classes › "nobody can replace them": the admin chooses another professor, another day or a
week later, or the session goes a week later by itself) or refuses, with a word for the professor. Refused: the
professor gets a message (application, email, WhatsApp) with a private link (/absence/{token}) to answer "I will be
there" or "I still cannot come, because…"; the second puts the request back before the admin ("insists").

The admin can also note an absence themselves: it is accepted at once. Everything is counted from the records, never
by a model.
"""
from __future__ import annotations

import logging
import secrets
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models import ClassGroup, ClassStatus, Professor
from app.models.classsession import ClassSession
from app.models.notification import Notification
from app.models.professor_absence import ProfessorAbsence
from app.services.ui_words import t as _t

logger = logging.getLogger(__name__)

WAITING = ("requested", "insists")           # the admin decides
AWAY = ("accepted", "requested", "insists")  # counted as away when someone is chosen to teach or replace
LONGEST = 92                                 # days: a longer absence is a change of availability (Edit the professor)
DECISIONS = ("nobody", "always")


def state(absence: ProfessorAbsence) -> str:
    return absence.status or "accepted"


def covers(absence: ProfessorAbsence, day: date, start: time | None = None, end: time | None = None) -> bool:
    """Whether this absence takes that day (and those hours: an absence or a session without hours takes the whole day)."""
    if day is None or not (absence.absence_date <= day <= absence.last_day):
        return False
    if not (absence.start_time and absence.end_time and start and end):
        return True
    return max(start, absence.start_time) < min(end, absence.end_time)


def period_label(absence: ProfessorAbsence, *, french: bool = False) -> str:
    """"Mon 17/11/2026" or "17/11 to 21/11/2026", with the hours when there are some."""
    from app.services.schedule_utils import format_date_fr

    first, last = absence.absence_date, absence.last_day
    if french:
        text = format_date_fr(first) if first == last else f"du {format_date_fr(first)} au {format_date_fr(last)}"
        if absence.start_time and absence.end_time:
            text += f", de {absence.start_time:%H:%M} à {absence.end_time:%H:%M}"
        return text
    text = f"{first:%d/%m/%Y}" if first == last else f"{first:%d/%m} → {last:%d/%m/%Y}"
    if absence.start_time and absence.end_time:
        text += f" · {absence.start_time:%H:%M}–{absence.end_time:%H:%M}"
    return text


def days_count(absence: ProfessorAbsence) -> int:
    return (absence.last_day - absence.absence_date).days + 1


# ----------------------------------------------------------------------
# Their sessions in the period, and who could replace them
# ----------------------------------------------------------------------

def sessions_in(db: Session, professor_id: str, first: date, last: date, start: time | None = None,
                end: time | None = None) -> list[tuple[ClassSession, ClassGroup, bool]]:
    """[(session, class, replacing)]: the sessions this professor teaches in the period at those hours: their own
    classes' (not given to someone else that day) and the ones where they replace a colleague."""
    from app.services.replacements import _crosses, _hours

    found = []
    for group in db.query(ClassGroup).filter(ClassGroup.professor_id == professor_id, ClassGroup.status != ClassStatus.COMPLETED).all():
        for session in (db.query(ClassSession).filter(ClassSession.class_group_id == group.id, ClassSession.session_date >= first,
                                                      ClassSession.session_date <= last).all()):
            if session.substitute_professor_id and session.substitute_professor_id != professor_id:
                continue
            if _crosses(*_hours(session, group), start, end):
                found.append((session, group, False))
    for session in (db.query(ClassSession).filter(ClassSession.substitute_professor_id == professor_id, ClassSession.session_date >= first,
                                                  ClassSession.session_date <= last).all()):
        group = db.get(ClassGroup, session.class_group_id)
        if group is not None and group.professor_id != professor_id and _crosses(*_hours(session, group), start, end):
            found.append((session, group, True))
    found.sort(key=lambda item: (item[0].session_date, item[0].start_time or item[1].start_time or time(0)))
    return found


def _free_for(db: Session, session: ClassSession, group: ClassGroup, absent_id: str) -> list[Professor]:
    """Who could take that session: the same rule as the replacement search (services/replacements.candidates_for)."""
    from app.services.replacements import SessionReplacement, candidates_for

    probe = SessionReplacement(session_id=session.id, class_group_id=group.id, absent_professor_id=absent_id, declined="[]")
    return candidates_for(db, probe)


def check(db: Session, professor: Professor, first: date, last: date, start: time | None = None, end: time | None = None) -> dict:
    """What an absence in that period would mean: each session, and who could replace them for it.
    {"sessions": [{"session", "class", "replacing", "choices", "nobody", "taken"}], "count", "covered", "nobody"}."""
    from app.services.replacements import SessionReplacement

    rows = []
    for session, group, replacing in sessions_in(db, professor.id, first, last, start, end):
        taken = (db.query(SessionReplacement).filter(SessionReplacement.session_id == session.id,
                                                     SessionReplacement.status.in_(("asked", "accepted"))).first())
        choices = [] if taken is not None else _free_for(db, session, group, professor.id)
        rows.append({"session": session, "class": group, "replacing": replacing, "choices": choices, "taken": taken,
                     "nobody": taken is None and not choices})
    return {"sessions": rows, "count": len(rows), "nobody": sum(1 for row in rows if row["nobody"]),
            "covered": sum(1 for row in rows if not row["nobody"])}


def of_absence(db: Session, absence: ProfessorAbsence) -> dict:
    return check(db, absence.professor, absence.absence_date, absence.last_day, absence.start_time, absence.end_time)


# ----------------------------------------------------------------------
# Asking, accepting, refusing, answering
# ----------------------------------------------------------------------

def read_period(first: str, last: str, start: str = "", end: str = "", *, today: date | None = None):
    """(first, last, start, end) from a form, or raises ValueError with what is wrong."""
    from app.services.schedule_utils import local_now

    today = today or local_now().date()
    try:
        begin = date.fromisoformat(first)
    except ValueError:
        raise ValueError(_t("Choose the first day away.")) from None
    try:
        finish = date.fromisoformat(last) if last else begin
    except ValueError:
        raise ValueError(_t("Choose the last day away.")) from None
    try:
        hours = (time.fromisoformat(start) if start else None, time.fromisoformat(end) if end else None)
    except ValueError:
        raise ValueError(_t("The hours are not valid.")) from None
    if begin < today:
        raise ValueError(_t("Choose a day to come."))
    if finish < begin:
        raise ValueError(_t("The last day is before the first."))
    if (finish - begin).days + 1 > LONGEST:
        raise ValueError(_t("An absence of more than {0} days: change their availability instead (Edit the professor).", LONGEST))
    if bool(hours[0]) != bool(hours[1]):
        raise ValueError(_t("Give both hours, or none for whole days."))
    if hours[0] and hours[1] and hours[1] <= hours[0]:
        raise ValueError(_t("The end must be after the start."))
    return begin, finish, hours[0], hours[1]


def _same(db: Session, professor_id: str, first: date, last: date, start, end) -> ProfessorAbsence | None:
    for absence in db.query(ProfessorAbsence).filter(ProfessorAbsence.professor_id == professor_id,
                                                     ProfessorAbsence.absence_date == first).all():
        if absence.last_day == last and absence.start_time == start and absence.end_time == end and state(absence) in AWAY:
            return absence
    return None


def _note(db: Session, absence: ProfessorAbsence, *, waits: bool, message: str) -> None:
    """The admin's notification about this absence: one per absence, that waits while the admin decides."""
    url = f"/professors?tab=absences&absence={absence.id}"
    note = db.query(Notification).filter(Notification.action_url == url).first()
    title = (f"{absence.professor.name} asks to be away" if waits else f"{absence.professor.name} is away")
    if note is None:
        note = Notification(recipient_role="admin", category="ABSENCE_REQUEST" if waits else "ABSENCE", title=title,
                            message=message[:500], action_url=url)
        db.add(note)
    note.category, note.title, note.message = ("ABSENCE_REQUEST" if waits else "ABSENCE"), title, message[:500]
    if waits:
        note.is_read, note.status, note.read_at = False, "unread", None
    else:
        note.is_read, note.status, note.read_at = True, "settled", datetime.utcnow()
    db.flush()


def _first_name(professor: Professor) -> str:
    return ((professor.name or "").replace("Pr ", "").replace("Dr ", "").split() or [""])[0]


def _sessions_rows(rows: list[dict]) -> str:
    return ", ".join(f"{row['class'].name} ({row['session'].session_date:%d/%m})" for row in rows[:8]) + (" …" if len(rows) > 8 else "")


def _tell_professor(db: Session, absence: ProfessorAbsence, text: str, subject: str, *, rows=None, cta=None, ways=("app", "email")) -> None:
    from app.services import team_messages

    try:
        team_messages.send(db, f"professor:{absence.professor_id}", text, list(ways), author="The assistant", subject=subject,
                           rows=rows, cta=cta)
    except Exception:
        db.rollback()
        logger.exception("Absence %s: the professor could not be told.", absence.id)


def _open_replacements(db: Session, absence: ProfessorAbsence, base_url: str) -> list:
    from app.services import replacements

    return replacements.open_for_absence(db, absence, base_url)


def request(db: Session, professor: Professor, first: date, last: date, start: time | None, end: time | None, reason: str,
            base_url: str, *, by_admin: bool = False, by: str | None = None) -> tuple[ProfessorAbsence, dict]:
    """Note an absence. From the admin: accepted at once. From the professor: accepted at once when every session in the
    period has someone free to replace them (Settings: "nobody"), else it waits for the admin.
    Returns (absence, {"outcome": accepted | waiting, "opened": [replacements], "check": check()})."""
    from app.services.settings_service import get_settings

    same = _same(db, professor.id, first, last, start, end)
    seen = check(db, professor, first, last, start, end)
    if same is not None:
        return same, {"outcome": "waiting" if state(same) in WAITING else "accepted", "opened": [], "check": seen, "again": True}
    absence = ProfessorAbsence(professor_id=professor.id, absence_date=first, end_date=last if last != first else None,
                               start_time=start, end_time=end, reason=(reason or "").strip()[:200] or None,
                               requested_by="admin" if by_admin else "professor", created_at=datetime.utcnow())
    decision = (get_settings(db).absence_decision or "nobody")
    waits = not by_admin and (decision == "always" or seen["nobody"] > 0)
    absence.status = "requested" if waits else "accepted"
    if not waits:
        absence.decided_by, absence.decided_at = (by or "Admin") if by_admin else "the application", datetime.utcnow()
    db.add(absence)
    db.flush()
    db.refresh(absence)
    period = period_label(absence)
    if waits:
        why = (_t("nobody is free to replace them for {0} of their {1} sessions", seen["nobody"], seen["count"]) if seen["nobody"]
               else _t("every request waits for you (Settings › Classes)"))
        _note(db, absence, waits=True, message=f"{professor.name}: {period}" + (f" ({absence.reason})" if absence.reason else "")
              + f" — {why}. " + _t("Accept or refuse it."))
        db.commit()
        return absence, {"outcome": "waiting", "opened": [], "check": seen}
    db.commit()
    opened = _open_replacements(db, absence, base_url)
    if not by_admin:
        _note(db, absence, waits=False, message=_t("{0}: {1}. Accepted by itself: someone can replace them for each of their {2} sessions; they are being asked.",
                                                   professor.name, period, seen["count"]) if seen["count"]
              else _t("{0}: {1}. No session in that period: nothing to replace.", professor.name, period))
        db.commit()
    return absence, {"outcome": "accepted", "opened": opened, "check": seen}


def accept(db: Session, absence: ProfessorAbsence, base_url: str, *, by: str = "Admin", note: str = "") -> list:
    """The admin says yes: the professor is told, and each session in the period looks for a replacement."""
    if state(absence) not in WAITING:
        return []
    absence.status, absence.decided_by, absence.decided_at = "accepted", by, datetime.utcnow()
    absence.decision_note = (note or "").strip()[:600] or None
    absence.token = None
    seen = of_absence(db, absence)
    _note(db, absence, waits=False, message=_t("{0}: {1}. Accepted by {2}.", absence.professor.name, period_label(absence), by))
    db.commit()
    opened = _open_replacements(db, absence, base_url)
    first = _first_name(absence.professor)
    lines = [f"Bonjour {first},", "",
             f"Votre absence {period_label(absence, french=True)} est acceptée."]
    if seen["count"]:
        lines.append("Vos séances de cette période sont confiées à un collègue, ou déplacées : vous n'avez rien à faire.")
    if absence.decision_note:
        lines += ["", absence.decision_note]
    lines += ["", "Bon courage !"]
    rows = [("Période", period_label(absence, french=True)), ("Séances concernées", str(seen["count"]) if seen["count"] else "aucune")]
    _tell_professor(db, absence, "\n".join(lines), "Votre absence est acceptée", rows=rows,
                    cta=("Voir mon espace", f"{base_url.rstrip('/')}/teacher/space#absences"))
    return opened


def refuse(db: Session, absence: ProfessorAbsence, base_url: str, *, by: str = "Admin", note: str = "") -> str | None:
    """The admin says no, with a word for the professor: they are told everywhere they can be reached, with their link
    to answer. Returns what is wrong, or None."""
    if state(absence) not in WAITING:
        return _t("This request is already decided.")
    words = (note or "").strip()
    absence.status, absence.decided_by, absence.decided_at = "refused", by, datetime.utcnow()
    absence.decision_note = words[:600] or None
    absence.token = secrets.token_urlsafe(18)
    absence.response, absence.responded_at = None, None
    seen = of_absence(db, absence)
    _note(db, absence, waits=False, message=_t("{0}: {1}. Refused by {2}; they were told and can answer.", absence.professor.name,
                                               period_label(absence), by))
    db.commit()
    first = _first_name(absence.professor)
    nobody = [row for row in seen["sessions"] if row["nobody"]]
    lines = [f"Bonjour {first},", "",
             f"Nous ne pouvons pas accepter votre absence {period_label(absence, french=True)} : "
             + ("aucun collègue n'est libre pour vous remplacer." if nobody else "vos séances de cette période ne peuvent pas être déplacées.")]
    if words:
        lines += ["", f"[box] {words}"]
    lines += ["", "Merci de nous dire en un clic si vous serez là, ou pourquoi vous ne pourrez vraiment pas venir : nous chercherons une solution ensemble."]
    rows = [("Période", period_label(absence, french=True))]
    if nobody:
        rows.append(("Séances sans remplaçant", _sessions_rows(nobody)))
    _tell_professor(db, absence, "\n".join(lines), "Votre demande d'absence", rows=rows,
                    cta=("Répondre", f"{base_url.rstrip('/')}/absence/{absence.token}"), ways=("app", "email", "whatsapp"))
    return None


def by_token(db: Session, token: str) -> ProfessorAbsence | None:
    if not token or len(token) < 16:
        return None
    return db.query(ProfessorAbsence).filter(ProfessorAbsence.token == token).first()


def respond(db: Session, absence: ProfessorAbsence, comes: bool, words: str = "") -> None:
    """The professor answered the refusal: they will be there (closed), or they still cannot come (back to the admin)."""
    absence.response = ("Je serai là." if comes else ("Je ne peux toujours pas venir. " + (words or "").strip()).strip())[:1000]
    absence.responded_at = datetime.utcnow()
    absence.token = None
    if comes:
        absence.status = "refused"
        _note(db, absence, waits=False, message=_t("{0} will be there ({1}): they answered the refusal.", absence.professor.name,
                                                   period_label(absence)))
    else:
        absence.status = "insists"
        _note(db, absence, waits=True, message=_t("{0} still cannot come ({1}): “{2}”. Accept or refuse it.", absence.professor.name,
                                                  period_label(absence), (words or "").strip()[:200] or "—"))
    db.commit()


def cancel(db: Session, absence: ProfessorAbsence, *, by: str = "Admin") -> None:
    """Taken back (by the professor while it waits, or removed by the admin): replacements still being asked stop.
    A colleague who already said yes keeps the session; the admin sees it on the class."""
    from app.services.replacements import SessionReplacement, _notify_admin

    was = state(absence)
    absence.status, absence.token = "cancelled", None
    absence.decided_by = absence.decided_by or by
    if was == "accepted":
        days = [absence.absence_date + timedelta(days=offset) for offset in range(days_count(absence))]
        rows = (db.query(SessionReplacement).join(ClassSession, ClassSession.id == SessionReplacement.session_id)
                .filter(SessionReplacement.absent_professor_id == absence.professor_id, ClassSession.session_date.in_(days),
                        SessionReplacement.status.in_(("searching", "asked", "none"))).all())
        for row in rows:
            row.status, row.token = "cancelled", None
            _notify_admin(db, row, _t("The absence was taken back: no replacement needed."), done=True)
    url = f"/professors?tab=absences&absence={absence.id}"
    for note in db.query(Notification).filter(Notification.action_url == url).all():
        note.is_read, note.status, note.read_at = True, "settled", datetime.utcnow()
    db.commit()


# ----------------------------------------------------------------------
# What the pages show
# ----------------------------------------------------------------------

def _view(db: Session, absence: ProfessorAbsence, *, with_check: bool = True) -> dict:
    from app.services.replacements import SessionReplacement

    seen = of_absence(db, absence) if with_check else None
    replaced = []
    if state(absence) == "accepted":
        days = [absence.absence_date + timedelta(days=offset) for offset in range(days_count(absence))]
        for row in (db.query(SessionReplacement).join(ClassSession, ClassSession.id == SessionReplacement.session_id)
                    .filter(SessionReplacement.absent_professor_id == absence.professor_id, ClassSession.session_date.in_(days)).all()):
            session, group = db.get(ClassSession, row.session_id), db.get(ClassGroup, row.class_group_id)
            if session is None or group is None or row.status in ("cancelled", "dropped"):
                continue                # a search an earlier absence opened and that was stopped: not this one's
            replaced.append({"row": row, "session": session, "class": group,
                             "asked": db.get(Professor, row.asked_professor_id) if row.asked_professor_id else None})
        replaced.sort(key=lambda item: item["session"].session_date)
    return {"absence": absence, "professor": absence.professor, "state": state(absence), "period": period_label(absence),
            "days": days_count(absence), "check": seen, "replaced": replaced}


def waiting(db: Session) -> list[dict]:
    """The requests the admin decides, the oldest first."""
    rows = db.query(ProfessorAbsence).filter(ProfessorAbsence.status.in_(WAITING)).order_by(ProfessorAbsence.absence_date).all()
    return [_view(db, absence) for absence in rows]


def coming(db: Session, today: date | None = None) -> list[dict]:
    """Accepted absences not over yet, the nearest first, with how each session is replaced."""
    from app.services.schedule_utils import local_now

    today = today or local_now().date()
    rows = [absence for absence in db.query(ProfessorAbsence).all() if state(absence) == "accepted" and absence.last_day >= today]
    rows.sort(key=lambda absence: (absence.absence_date, absence.professor.name or ""))
    return [_view(db, absence, with_check=False) for absence in rows]


def refused(db: Session, days: int = 45) -> list[dict]:
    """Refused requests of the last weeks, with the professor's answer."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(ProfessorAbsence).filter(ProfessorAbsence.status == "refused", ProfessorAbsence.decided_at >= since)
            .order_by(ProfessorAbsence.decided_at.desc()).all())
    return [_view(db, absence, with_check=False) for absence in rows]


def of_professor(db: Session, professor: Professor, today: date | None = None) -> list[dict]:
    """A professor's absences still to come (and refusals of the last weeks): for their space."""
    from app.services.schedule_utils import local_now

    today = today or local_now().date()
    since = datetime.utcnow() - timedelta(days=45)
    found = []
    for absence in professor.absences:
        current = state(absence)
        if current == "cancelled" or absence.last_day < today:
            continue
        if current == "refused" and (absence.decided_at or datetime.utcnow()) < since:
            continue
        found.append(_view(db, absence, with_check=current in WAITING))
    found.sort(key=lambda item: item["absence"].absence_date)
    return found


def counts(db: Session) -> dict:
    return {"waiting": db.query(ProfessorAbsence.id).filter(ProfessorAbsence.status.in_(WAITING)).count()}

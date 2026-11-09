"""The online meeting of a class session.

Each session has one meeting room: a link the professor pastes (Google Meet,
Zoom, Teams…) or a private room created here. Candidates never receive that
address. Each gets a link of their own, which works only for them, only
around the session's hours, and notes when they arrived. That is what makes
"only those who were given the link" true, and it is the one thing about a
meeting this application can observe by itself: who came, and when.

What happens inside the meeting (speaking, camera) is not visible from here;
the professor notes it on the presence sheet (see attendance).

A class is fully online, fully on site, or both (mixed): only its online
candidates get links. Three days before an online or mixed class starts, its
professor is asked for the private meeting link (in the application, by email,
WhatsApp and SMS); once given, every session uses it and each online candidate
gets their own link at once.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models.classgroup import ClassGroup, ClassStatus
from app.models.classsession import ClassSession
from app.models.registration import Registration
from app.models.session_join import SessionJoin
from app.models.user import Attendance
from app.services import attendance, email_templates
from app.services.schedule_utils import local_now

OPENS_BEFORE = timedelta(minutes=30)        # the door opens this long before the session
STAYS_AFTER = timedelta(minutes=30)         # and closes this long after its end
LATE_AFTER = 10                             # minutes after the start from which an arrival is "late"
SEND_BEFORE = timedelta(minutes=60)         # links are emailed this long before the session
ROOMS = "https://meet.jit.si/"
MODES = {"online": "Fully online", "onsite": "Fully on site", "mixed": "Online and on site"}
LINK_ASK_DAYS = 3                           # the professor is asked for the meeting link this many days before the start
LINK_LATE = timedelta(days=1)               # still none this close to the start: the admin is told


def mode_of(db: Session, class_group: ClassGroup) -> str:
    """online | onsite | mixed: as set on the class, else from its candidates' choices (online when nobody chose yet)."""
    if class_group.delivery_mode in MODES:
        return class_group.delivery_mode
    chosen = {("onsite" if (registration.delivery_mode or "online") == "onsite" else "online")
              for registration in attendance.members(db, class_group.id)}
    if chosen == {"onsite"}:
        return "onsite"
    return "mixed" if len(chosen) > 1 else "online"


def follows_online(db: Session, class_group: ClassGroup, registration: Registration, mode: str | None = None) -> bool:
    """Whether this candidate follows the class online (and so gets its links)."""
    mode = mode or mode_of(db, class_group)
    if mode == "onsite":
        return False
    return mode == "online" or (registration.delivery_mode or "online") != "onsite"


def clean_url(value: str | None) -> str | None:
    """A meeting address typed by the professor, or None. Raises ValueError when it is not one."""
    value = (value or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = "https://" + value
    if not re.match(r"^https://[^\s/]+\.[^\s/]+(/\S*)?$", value):
        raise ValueError("Paste the meeting's address, starting with https://")
    return value[:400]


def new_room(class_group: ClassGroup) -> str:
    """A private room: its name cannot be guessed."""
    name = re.sub(r"[^A-Za-z0-9]+", "", class_group.name or "Class")[:24] or "Class"
    return f"{ROOMS}{name}-{secrets.token_urlsafe(12)}"


def room_of(db: Session, session: ClassSession, class_group: ClassGroup, *, create: bool = True) -> str | None:
    """The session's meeting room: the class's link from its professor, else one created now if it has none."""
    if not session.meeting_url and (class_group.meeting_url or create):
        session.meeting_url = class_group.meeting_url or new_room(class_group)
        db.commit()
    return session.meeting_url


def links_for(db: Session, class_group: ClassGroup, session: ClassSession) -> dict[str, SessionJoin]:
    """Each online candidate's own link to this session, created for those who have none yet."""
    existing = {row.registration_id: row for row in db.query(SessionJoin).filter(SessionJoin.session_id == session.id).all()}
    made = False
    mode = mode_of(db, class_group)
    for registration in attendance.members(db, class_group.id):
        if registration.id not in existing and follows_online(db, class_group, registration, mode):
            row = SessionJoin(
                session_id=session.id, registration_id=registration.id, candidate_id=registration.candidate_id,
                token=secrets.token_urlsafe(18),
            )
            db.add(row)
            existing[registration.id] = row
            made = True
    if made:
        db.commit()
    return existing


def by_token(db: Session, token: str) -> SessionJoin | None:
    if not token or len(token) < 16:
        return None
    return db.query(SessionJoin).filter(SessionJoin.token == token).first()


def hours_of(session: ClassSession, class_group: ClassGroup | None) -> tuple[datetime, datetime]:
    """When the session starts and ends (local time). Without hours: the whole day."""
    start_time = session.start_time or (class_group.start_time if class_group else None)
    end_time = session.end_time or (class_group.end_time if class_group else None)
    start = datetime.combine(session.session_date, start_time or time(0, 0))
    end = datetime.combine(session.session_date, end_time) if end_time else (
        start + timedelta(hours=3) if start_time else datetime.combine(session.session_date, time(23, 59)))
    return start, end


def door(session: ClassSession, class_group: ClassGroup | None, now: datetime | None = None) -> str:
    """"early", "open" or "closed": whether a candidate's link lets them in right now."""
    now = now or local_now()
    start, end = hours_of(session, class_group)
    if now < start - OPENS_BEFORE:
        return "early"
    return "open" if now <= end + STAYS_AFTER else "closed"


def enter(db: Session, join: SessionJoin, now: datetime | None = None) -> dict:
    """A candidate uses their link. Returns {"state", "url", "session", "class_group"}.

    state: "open" (go in: `url`), "early", "closed", "no_room", or "gone" (no
    longer in the class). An entry is noted, and marks the candidate present
    (or late) when the professor has not marked them yet.
    """
    now = now or local_now()
    session = db.get(ClassSession, join.session_id)
    registration = db.get(Registration, join.registration_id)
    class_group = db.get(ClassGroup, session.class_group_id) if session else None
    found = {"state": "gone", "url": None, "session": session, "class_group": class_group, "registration": registration}
    if session is None or registration is None or class_group is None or registration.class_group_id != class_group.id:
        return found
    state = door(session, class_group, now)
    if state != "open":
        return {**found, "state": state}
    if not session.meeting_url:
        return {**found, "state": "no_room"}

    start, _ = hours_of(session, class_group)
    if join.first_joined_at is None:
        join.first_joined_at = now
        join.minutes_late = max(0, int((now - start).total_seconds() // 60)) if (session.start_time or class_group.start_time) else 0
    join.last_joined_at = now
    join.joins = (join.joins or 0) + 1
    mark = db.query(Attendance).filter(Attendance.session_id == session.id, Attendance.registration_id == registration.id).first()
    arrived = "late" if (join.minutes_late or 0) > LATE_AFTER else "present"
    if mark is None:
        db.add(Attendance(
            class_group_id=class_group.id, session_id=session.id, registration_id=registration.id,
            candidate_id=registration.candidate_id, status=arrived, marked_by="their link",
        ))
    elif mark.status == "absent" and mark.marked_by == "their link":
        mark.status = arrived
    db.commit()
    return {**found, "state": "open", "url": session.meeting_url}


def send_links(db: Session, class_group: ClassGroup, session: ClassSession, base_url: str, *,
               manual: bool = True, again: bool = False) -> dict:
    """Email each candidate of the class their own link to this session.
    Returns {"sent", "failed", "skipped"}; a link already sent is not sent again unless `again`."""
    room_of(db, session, class_group)
    start, end = hours_of(session, class_group)
    hours = f"{start:%H:%M}" + (f" – {end:%H:%M}" if (session.end_time or class_group.end_time) else "") if (session.start_time or class_group.start_time) else ""
    outcome = {"sent": 0, "failed": 0, "skipped": 0}
    for registration_id, join in links_for(db, class_group, session).items():
        registration = db.get(Registration, registration_id)
        candidate = registration.candidate if registration else None
        if candidate is None or not candidate.email or (join.sent_at is not None and not again):
            outcome["skipped"] += 1
            continue
        message = email_templates.send(
            db, "session_link", candidate=candidate, registration=registration, manual=manual,
            values={
                "join_link": f"{base_url.rstrip('/')}/join/{join.token}",
                "session_date": _french_date(session.session_date),
                "session_hours": hours or "à l'heure habituelle",
                "session_topic": session.topic or "Séance",
            },
        )
        if message is None:                 # the email is switched off
            outcome["skipped"] += 1
            continue
        join.sent_at = datetime.utcnow()    # once, whether or not it could be delivered
        db.commit()
        outcome["sent" if email_templates.delivered(message) else "failed"] += 1
    return outcome


def send_due(db: Session, base_url: str, now: datetime | None = None) -> int:
    """Before each session of a class that is running (or about to start): each
    candidate gets their own link, once. Returns how many emails went out."""
    from app.services import weekly_questions      # here: it imports a lot

    if not email_templates.is_on(db, "session_link") or weekly_questions.is_local_address(base_url):
        return 0
    now = now or local_now()
    sent = 0
    sessions = db.query(ClassSession).filter(ClassSession.session_date == now.date()).all()
    for session in sessions:
        class_group = db.get(ClassGroup, session.class_group_id)
        if class_group is None or class_group.status == ClassStatus.COMPLETED:
            continue
        if not (session.start_time or class_group.start_time):
            continue                        # no hour: nothing to be on time for
        if mode_of(db, class_group) == "onsite":
            continue                        # in the room: no link
        start, end = hours_of(session, class_group)
        if not (start - SEND_BEFORE <= now <= end):
            continue
        from app.services.enrollment_service import hold_reasons

        if class_group.status == ClassStatus.NOT_STARTED and hold_reasons(class_group, len(attendance.members(db, class_group.id))):
            continue                        # a class on hold has no session to join
        sent += send_links(db, class_group, session, base_url, manual=False)["sent"]
    return sent


def set_class_link(db: Session, class_group: ClassGroup, address: str, base_url: str) -> int:
    """The class's meeting link (from its professor or the admin): every session to come uses it, and each online
    candidate gets their own link to the next session at once. Returns how many were sent. Raises ValueError for an
    address that is not one."""
    from app.services import weekly_questions

    url = clean_url(address)
    if url is None:
        raise ValueError("Paste the meeting's address, starting with https://")
    class_group.meeting_url = url
    today = local_now().date()
    coming = (db.query(ClassSession).filter(ClassSession.class_group_id == class_group.id, ClassSession.session_date >= today)
              .order_by(ClassSession.session_date.asc()).all())
    for session in coming:
        if not session.meeting_url or session.meeting_url.startswith(ROOMS):
            session.meeting_url = url
    db.commit()
    if not coming or weekly_questions.is_local_address(base_url):
        return 0
    return send_links(db, class_group, coming[0], base_url)["sent"]


def ask_links_due(db: Session, base_url: str, now: datetime | None = None) -> dict:
    """Three days before an online or mixed class starts, its professor is asked for the private meeting link, once;
    a day before, still none: the admin is told, once. Returns {"asked", "late"}."""
    from app.models.notification import Notification
    from app.models.professor import Professor
    from app.services import team_messages
    from app.services.schedule_utils import class_start_dt, describe_schedule_fr, format_date_fr

    now = now or local_now()
    done = {"asked": 0, "late": 0}
    for class_group in db.query(ClassGroup).filter(ClassGroup.status == ClassStatus.NOT_STARTED, ClassGroup.meeting_url.is_(None)).all():
        start = class_start_dt(class_group)
        if start is None or start <= now or start - now > timedelta(days=LINK_ASK_DAYS):
            continue
        if not attendance.members(db, class_group.id) or mode_of(db, class_group) == "onsite":
            continue
        professor = db.get(Professor, class_group.professor_id) if class_group.professor_id else None
        if class_group.link_asked_at is None and professor is not None:
            first = ((professor.name or "").replace("Pr ", "").replace("Dr ", "").split() or [""])[0]
            text = (f"Bonjour {first}, votre classe {class_group.name} commence le {format_date_fr(start)} à {start:%H:%M} "
                    f"({describe_schedule_fr(class_group)}). Collez ici le lien privé de la réunion (Google Meet, Zoom, Teams…) : "
                    f"{base_url.rstrip('/')}/teacher/classes/{class_group.id}/link · il sera envoyé à vos candidats en ligne, "
                    "chacun avec son propre lien.")
            class_group.link_asked_at = datetime.utcnow()
            db.commit()                     # kept first: never asked twice
            try:
                team_messages.send(db, f"professor:{professor.id}", text, ["app", "email", "whatsapp", "sms"], author="The assistant")
            except Exception:
                db.rollback()
            done["asked"] += 1
        if start - now <= LINK_LATE and not db.query(Notification.id).filter(Notification.category == "MEETING_LINK",
                                                                             Notification.action_url == f"/classes/{class_group.id}").first():
            db.add(Notification(recipient_role="admin", category="MEETING_LINK", title="No meeting link yet",
                                message=(f"{class_group.name} starts {format_date_fr(start)} at {start:%H:%M} online, and "
                                         f"{professor.name if professor else 'its professor'} has not given the meeting link yet. "
                                         "Paste it in the class's parameters, or a private room is made at the start."),
                                action_url=f"/classes/{class_group.id}"))
            db.commit()
            done["late"] += 1
    return done


SHEET_TEXT = (
    "Bonjour {professor},\n\n"
    "La séance « {topic} » de {class_name} est terminée. {seen}\n"
    "Vérifiez la feuille de présence et confirmez-la en un geste (et notez qui a participé) :\n{link}\n\nMerci !"
)


def confirmed(marks: dict) -> bool:
    """Whether a person (the professor, the admin) marked the sheet, not only the candidates' own links."""
    return any((row.marked_by or "") not in ("", "their link") for row in marks.values())


def after_sessions(db: Session, base_url: str, now: datetime | None = None) -> dict:
    """Once a session is over (its door closed): with the links sent, whoever never came in is marked absent (by
    "their link", the professor can still change it); and its professor is asked, once, to confirm the sheet in one
    tap. Returns {"absent": marked, "asked": professors asked}."""
    from app.models.professor import Professor
    from app.services import program_feedback, team_messages

    now = now or local_now()
    done = {"absent": 0, "asked": 0}
    sessions = (db.query(ClassSession).filter(ClassSession.session_date >= now.date() - timedelta(days=1),
                                              ClassSession.session_date <= now.date()).all())
    for session in sessions:
        class_group = db.get(ClassGroup, session.class_group_id)
        if class_group is None or class_group.status not in (ClassStatus.IN_PROGRESS, ClassStatus.COMPLETED):
            continue
        if not (session.start_time or class_group.start_time) or door(session, class_group, now) != "closed":
            continue
        members = attendance.members(db, class_group.id)
        marks = attendance.marks_of(db, session.id)
        joins = {row.registration_id: row for row in db.query(SessionJoin).filter(SessionJoin.session_id == session.id).all()}
        if session.meeting_url and any(row.sent_at for row in joins.values()):
            for registration in members:
                if registration.id not in marks and registration.id in joins and joins[registration.id].first_joined_at is None:
                    db.add(Attendance(class_group_id=class_group.id, session_id=session.id, registration_id=registration.id,
                                      candidate_id=registration.candidate_id, status="absent", marked_by="their link"))
                    done["absent"] += 1
            db.commit()
            marks = attendance.marks_of(db, session.id)
        if members and program_feedback.is_last_session(db, session):
            # The last session is over: its candidates are asked what they thought now, while it is fresh (the QR code
            # of the class's page can be shown in the room too); asked once, see program_feedback.queue_class.
            program_feedback.queue_class(db, class_group, members)
            db.commit()
        if session.sheet_asked_at is not None or confirmed(marks) or not members or not class_group.professor_id:
            continue
        professor = db.get(Professor, class_group.professor_id)
        if professor is None:
            continue
        came = [row for row in joins.values() if row.first_joined_at is not None]
        late = sum(1 for row in came if (row.minutes_late or 0) > LATE_AFTER)
        seen = (f"Par leur lien : {len(came)} entré(s){f', dont {late} en retard' if late else ''}, "
                f"{len(members) - len(came)} pas venu(s)." if joins and any(row.sent_at for row in joins.values())
                else f"{len(members)} candidat(s) dans la classe.")
        text = SHEET_TEXT.format(professor=(professor.name or "").split()[0] if professor.name else "", topic=session.topic or "Séance",
                                 class_name=class_group.name, seen=seen,
                                 link=f"{base_url.rstrip('/')}/classes/{class_group.id}/attendance?session={session.id}")
        session.sheet_asked_at = datetime.utcnow()
        db.commit()            # kept first: the professor is never asked twice, whatever happens next
        team_messages.send(db, f"professor:{professor.id}", text, ["app", "email", "whatsapp"], author="The assistant")
        done["asked"] += 1
    return done


_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _french_date(day) -> str:
    return f"{_DAYS[day.weekday()]} {day.day} {_MONTHS[day.month - 1]}"


def sheet(db: Session, class_group: ClassGroup, session: ClassSession) -> dict[str, dict]:
    """registration id -> what is known of their arrival at this session's meeting."""
    found = {}
    for row in db.query(SessionJoin).filter(SessionJoin.session_id == session.id).all():
        found[row.registration_id] = {
            "token": row.token, "sent": row.sent_at is not None, "joined": row.first_joined_at,
            "late": (row.minutes_late or 0) > LATE_AFTER, "minutes_late": row.minutes_late or 0, "joins": row.joins or 0,
        }
    return found


def arrivals(db: Session, candidate_id: str) -> dict:
    """A candidate's arrivals over all their sessions: {"joined", "on_time", "late", "average_late"}."""
    rows = db.query(SessionJoin).filter(SessionJoin.candidate_id == candidate_id, SessionJoin.first_joined_at.isnot(None)).all()
    late = [row for row in rows if (row.minutes_late or 0) > LATE_AFTER]
    return {
        "joined": len(rows), "late": len(late), "on_time": len(rows) - len(late),
        "average_late": round(sum(row.minutes_late or 0 for row in late) / len(late)) if late else 0,
    }

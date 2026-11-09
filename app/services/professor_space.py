"""Each professor's own space: everything about one professor, in one place and apart from the rest: who they are, their
classes and next sessions, what candidates thought of them, the messages, and their problems (an absence without a
replacement, presence sheets not filled, questions waiting for them, a meeting link missing, low reviews…), each with
the place to solve it.

The admin opens it for any professor (/professors/{id}); a professor opens their own (/teacher/space), with the same
parts, minus what only the admin decides. Everything is counted from the records, never by a model.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import ClassGroup, ClassStatus, Professor
from app.models.classsession import ClassSession
from app.services.ui_words import t as _t

SHEETS_DAYS = 30          # presence sheets looked at: the sessions of the last 30 days
LINK_DAYS = 7             # a meeting link missing for a class starting within a week is a problem
WAITING_HOURS = 24        # a candidate's question waiting longer than this is a problem


def _key(professor: Professor) -> str:
    return f"professor:{professor.id}"


def _classes(db: Session, professor: Professor) -> list[ClassGroup]:
    order = {ClassStatus.ONGOING: 0, ClassStatus.NOT_STARTED: 1, ClassStatus.COMPLETED: 2}
    groups = db.query(ClassGroup).filter(ClassGroup.professor_id == professor.id).all()
    return sorted(groups, key=lambda group: (order.get(group.status, 3), group.start_date or datetime.max, group.name))


def _marked(db: Session, session_id: str) -> bool:
    """A person marked this session's presence (not only the candidates' own links)."""
    from app.services import attendance

    return any((mark.marked_by or "") not in ("", "their link") for mark in attendance.marks_of(db, session_id).values())


def problems(db: Session, professor: Professor, *, own: bool = False) -> list[dict]:
    """[{"icon", "tone": bad | warn | info, "text", "url", "do"}]: what is wrong or waits for this professor, the most
    pressing first. `own`: as the professor sees it (their own pages)."""
    from app.services import course_questions, enrollment_service, meetings, program_feedback
    from app.services.replacements import SessionReplacement
    from app.services.schedule_utils import local_now

    now = local_now()
    today = now.date()
    found: list[dict] = []
    groups = _classes(db, professor)
    open_groups = [group for group in groups if group.status != ClassStatus.COMPLETED]
    # An absence coming: is someone replacing them?
    for absence in sorted((item for item in professor.absences if item.absence_date >= today), key=lambda item: item.absence_date):
        rows = (db.query(SessionReplacement).filter(SessionReplacement.absent_professor_id == professor.id)
                .join(ClassSession, ClassSession.id == SessionReplacement.session_id)
                .filter(ClassSession.session_date == absence.absence_date).all())
        day = absence.absence_date.strftime("%d/%m")
        for row in rows:
            session = db.get(ClassSession, row.session_id)
            group = db.get(ClassGroup, row.class_group_id)
            if group is None or session is None:
                continue
            url = f"/classes/{group.id}?replacement={row.id}#follow"
            if row.status == "none":
                found.append({"icon": "bi-person-x-fill", "tone": "bad", "text": _t("Away on {0}: nobody replaces them for {1} yet.", day, group.name),
                              "url": url, "do": _t("Choose: another professor, another day, or a week later")})
            elif row.status == "asked":
                found.append({"icon": "bi-hourglass-split", "tone": "warn", "text": _t("Away on {0}: a replacement was asked for {1}, waiting for the answer.", day, group.name),
                              "url": url, "do": _t("See who was asked")})
            elif row.status in ("moved", "postponed"):
                found.append({"icon": "bi-calendar2-check", "tone": "info",
                              "text": _t("Away on {0}: the session of {1} was moved to another day.", day, group.name) if row.status == "moved"
                              else _t("Away on {0}: the session of {1} was put a week later.", day, group.name), "url": url, "do": _t("See it")})
    # Presence sheets of the sessions that ended, not filled by a person.
    since = today - timedelta(days=SHEETS_DAYS)
    missing = []
    for group in open_groups:
        for session in (db.query(ClassSession).filter(ClassSession.class_group_id == group.id, ClassSession.session_date >= since,
                                                      ClassSession.session_date <= today).order_by(ClassSession.session_date).all()):
            if session.substitute_professor_id and session.substitute_professor_id != professor.id:
                continue
            if meetings.door(session, group, now) == "closed" and not _marked(db, session.id):
                missing.append((session, group))
    if missing:
        session, group = missing[0]
        found.append({"icon": "bi-clipboard-x", "tone": "warn",
                      "text": _t("Presence sheets not filled: {0} (the first: {1}, {2}).", len(missing), group.name, f"{session.session_date:%d/%m}"),
                      "url": f"/classes/{group.id}/attendance?session={session.id}", "do": _t("Fill it")})
    # Candidates' questions waiting for them.
    waiting = course_questions.waiting_for(db, _key(professor))
    late = [row for row in waiting if row.created_at and datetime.utcnow() - row.created_at >= timedelta(hours=WAITING_HOURS)]
    if waiting:
        found.append({"icon": "bi-patch-question-fill", "tone": "bad" if late else "warn",
                      "text": _t("Candidates' questions waiting for an answer: {0}, {1} of them for more than a day.", len(waiting), len(late)) if late
                      else _t("Candidates' questions waiting for an answer: {0}.", len(waiting)),
                      "url": "/account/messages" if own else f"/inbox/team?person={_key(professor)}", "do": _t("Answer")})
    # A meeting link missing for an online class that starts soon (or runs).
    for group in open_groups:
        start = group.start_date
        soon = group.status == ClassStatus.ONGOING or (start is not None and start.date() <= today + timedelta(days=LINK_DAYS))
        if soon and not group.meeting_url and meetings.mode_of(db, group) in ("online", "mixed"):
            found.append({"icon": "bi-link-45deg", "tone": "warn", "text": _t("No meeting link for {0} yet.", group.name),
                          "url": f"/teacher/classes/{group.id}/link" if own else f"/classes/{group.id}#follow", "do": _t("Give the link")})
    # A class whose program is not among their specialities.
    for group in open_groups:
        if group.program is not None and not enrollment_service.teaches(professor, group.program):
            found.append({"icon": "bi-mortarboard", "tone": "info", "text": _t("{0} is not among the specialities written, yet {1} is taught.", group.program.name, group.name),
                          "url": "/teacher/profile" if own else "/classes/?tab=professors", "do": _t("Add the speciality")})
    # Low reviews about them.
    reviews = program_feedback.of_professor(db, professor.name)
    low = [item for item in reviews["rows"] if item["answered"] and item["prof_stars"] and item["prof_stars"] <= 2]
    if low:
        found.append({"icon": "bi-emoji-frown-fill", "tone": "warn", "text": _t("Reviews with 2 stars or less for the professor: {0}.", len(low)),
                      "url": "#feedback", "do": _t("Read them")})
    if not own:
        from app.models.user import User

        if not professor.email and not professor.phone:
            found.append({"icon": "bi-telephone-x", "tone": "bad", "text": _t("No email and no phone: they cannot be told anything."),
                          "url": "/classes/?tab=professors", "do": _t("Add their email or phone")})
        if db.query(User.id).filter(User.professor_id == professor.id).first() is None:
            found.append({"icon": "bi-person-lock", "tone": "info", "text": _t("No account: they cannot fill presence or answer questions in the application."),
                          "url": "/classes/?tab=professors", "do": _t("Give them an account")})
    order = {"bad": 0, "warn": 1, "info": 2}
    return sorted(found, key=lambda item: order[item["tone"]])


def feedback(db: Session, professor: Professor) -> dict:
    """What candidates said of them at the end of their programs: their stars, the words about them, what to improve."""
    from app.services import program_feedback

    summary = program_feedback.of_professor(db, professor.name)
    answered = [item for item in summary["rows"] if item["answered"]]
    stars = [item["prof_stars"] for item in answered if item["prof_stars"]]
    return {"stars": round(sum(stars) / len(stars), 1) if stars else None, "rated": len(stars), "answered": len(answered),
            "program_stars": summary["stars"], "recommend_pct": summary["recommend_pct"], "to_improve": summary["to_improve"],
            "reviews": [{"name": item["name"], "stars": item["prof_stars"], "program_stars": item["stars"], "words": item["prof_words"],
                         "program": item["program"], "when": item["when"], "low": bool(item["prof_stars"] and item["prof_stars"] <= 2)}
                        for item in sorted(answered, key=lambda item: item["when"] or datetime.min, reverse=True)][:20]}


def classes(db: Session, professor: Professor) -> list[dict]:
    """Their classes, the running ones first: program, hours, candidates, presence."""
    from app.services import attendance, class_looks
    from app.services.class_views import class_summary
    from app.services.schedule_utils import local_now

    now = local_now()
    found = []
    for group in _classes(db, professor):
        found.append({"class": group, "summary": class_summary(group, now), "look": class_looks.look(group),
                      "presence": attendance.class_summary(db, group)})
    return found


def next_sessions(db: Session, professor: Professor, days: int = 14) -> list[dict]:
    """Their sessions of the next two weeks, the ones they replace someone for too."""
    from app.services.schedule_utils import local_now

    today = local_now().date()
    own = [group.id for group in _classes(db, professor) if group.status != ClassStatus.COMPLETED]
    rows = (db.query(ClassSession).filter(ClassSession.session_date >= today, ClassSession.session_date <= today + timedelta(days=days))
            .order_by(ClassSession.session_date, ClassSession.start_time).all())
    found = []
    for session in rows:
        mine = session.class_group_id in own and session.substitute_professor_id in (None, professor.id)
        replacing = session.substitute_professor_id == professor.id and session.class_group_id not in own
        if mine or replacing:
            found.append({"session": session, "class": db.get(ClassGroup, session.class_group_id), "replacing": replacing,
                          "is_today": session.session_date == today})
    return found[:10]


def messages(db: Session, professor: Professor, most: int = 5) -> dict:
    """The conversation with them (the team chat): the latest messages, and how many they have not read."""
    from app.services import team_messages

    lines = team_messages.conversation(db, _key(professor))
    return {"latest": lines[-most:], "count": len(lines), "url": f"/inbox/team?person={_key(professor)}"}


def figures(db: Session, professor: Professor, groups: list[dict], said: dict) -> dict:
    """The few figures at the top of the space."""
    from app.models.team_message import TeamMessage
    from app.services.enrollment_service import class_members

    running = [card for card in groups if card["class"].status == ClassStatus.ONGOING]
    people = {member.candidate_id for card in groups if card["class"].status != ClassStatus.COMPLETED
              for member in class_members(db, card["class"].id)}
    rates = [card["presence"]["rate"] for card in groups if card["presence"].get("rate") is not None]
    asked = db.query(TeamMessage).filter(TeamMessage.person == _key(professor), TeamMessage.question_message_id.isnot(None)).all()
    answered = sorted((row.answered_at - row.created_at).total_seconds() / 3600 for row in asked if row.answered_at and row.created_at)
    return {"running": len(running), "classes": len([card for card in groups if card["class"].status != ClassStatus.COMPLETED]),
            "candidates": len(people), "presence": round(sum(rates) / len(rates)) if rates else None, "stars": said["stars"],
            "rated": said["rated"], "questions": len(asked), "answered": len(answered),
            "answer_hours": round(answered[len(answered) // 2], 1) if answered else None}


def space(db: Session, professor: Professor, *, own: bool = False) -> dict:
    """Everything of one professor's space."""
    groups = classes(db, professor)
    said = feedback(db, professor)
    return {"professor": professor, "own": own, "classes": groups, "sessions": next_sessions(db, professor),
            "feedback": said, "problems": problems(db, professor, own=own), "messages": messages(db, professor),
            "figures": figures(db, professor, groups, said),
            "specialties": [item.strip() for item in (professor.specialties or "").split(",") if item.strip()],
            "absences": sorted((item for item in professor.absences if item.absence_date >= _today()), key=lambda item: item.absence_date)}


def _today():
    from app.services.schedule_utils import local_now

    return local_now().date()


def every(db: Session) -> list[dict]:
    """Every professor, for the professors' page: their figures and how many problems wait."""
    found = []
    for professor in db.query(Professor).order_by(Professor.name).all():
        if (professor.name or "").startswith("À nommer"):
            continue
        groups = classes(db, professor)
        said = feedback(db, professor)
        issues = problems(db, professor)
        found.append({"professor": professor, "figures": figures(db, professor, groups, said), "problems": issues,
                      "bad": sum(1 for item in issues if item["tone"] == "bad"),
                      "specialties": [item.strip() for item in (professor.specialties or "").split(",") if item.strip()][:4]})
    found.sort(key=lambda item: (-item["bad"], -len(item["problems"]), item["professor"].name))
    return found

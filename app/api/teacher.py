"""A professor's own space (their classes and profile) and the presence sheet
of a class, which the professor of the class and the admin both use."""
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from pydantic import BaseModel

from app.database import get_db
from app.models import ClassGroup, ClassStatus, Professor
from app.models.classsession import ClassSession
from app.models.user import User
from app.services import attendance, auth, class_looks, weekly_questions
from app.services.class_views import class_summary
from app.services.schedule_utils import local_now
from app.template_context import notification_context
from app.services.ui_words import t as _t

router = APIRouter(tags=["Professor space"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def _my_professor(request: Request, db: Session) -> Professor | None:
    user = auth.current(request)
    return db.get(Professor, user.professor_id) if user is not None and user.professor_id else None


# ------------------------------------------------------------------
# My classes
# ------------------------------------------------------------------

@router.get("/teacher", response_class=HTMLResponse)
def my_space(request: Request, db: Session = Depends(get_db)):
    professor = _my_professor(request, db)
    if professor is None:
        # The admin has no classes of their own: the professors page is theirs.
        return RedirectResponse("/classes/?tab=professors", status_code=303)
    now = local_now()
    classes = (
        db.query(ClassGroup)
        .options(joinedload(ClassGroup.registrations), joinedload(ClassGroup.sessions), joinedload(ClassGroup.program))
        .filter(ClassGroup.professor_id == professor.id)
        .all()
    )
    order = {ClassStatus.ONGOING: 0, ClassStatus.NOT_STARTED: 1, ClassStatus.COMPLETED: 2}
    classes.sort(key=lambda item: (order.get(item.status, 3), item.start_date or datetime.max, item.name))

    cards, coming = [], []
    today, horizon = now.date(), now.date() + timedelta(days=14)
    for class_group in classes:
        presence = attendance.class_summary(db, class_group)
        cards.append({
            "class": class_group,
            "summary": class_summary(class_group, now),
            "look": class_looks.look(class_group),
            "course": weekly_questions.class_overview(db, class_group),
            "presence": presence,
        })
        if class_group.status == ClassStatus.COMPLETED:
            continue
        for session in class_group.sessions:
            if today <= session.session_date <= horizon:
                coming.append({"session": session, "class": class_group, "look": cards[-1]["look"],
                               "is_today": session.session_date == today})
    coming.sort(key=lambda item: (item["session"].session_date, str(item["session"].start_time or "")))

    active = [card for card in cards if card["class"].status != ClassStatus.COMPLETED]
    return templates.TemplateResponse(
        request=request,
        name="teacher.html",
        context={
            "active_page": "teacher",
            "professor": professor,
            "cards": cards,
            "coming": coming[:6],
            "now": now,
            "totals": {
                "classes": len(active),
                "candidates": sum(card["summary"]["enrolled"] for card in active),
                "today": sum(1 for item in coming if item["is_today"]),
            },
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/teacher/space", response_class=HTMLResponse)
def my_whole_space(request: Request, db: Session = Depends(get_db)):
    """Everything about them in one place: their classes, what candidates thought of them, the messages, what waits."""
    from app.services import professor_space

    professor = _my_professor(request, db)
    if professor is None:
        return RedirectResponse("/professors", status_code=303)
    return templates.TemplateResponse(request=request, name="professor_space.html", context={
        "active_page": "teacher-space", **professor_space.space(db, professor, own=True),
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


@router.post("/teacher/absence")
def i_will_be_away(request: Request, absence_date: str = Form(""), start_time: str = Form(""), end_time: str = Form(""),
                   reason: str = Form(""), db: Session = Depends(get_db)):
    """They say they will be away: their sessions that day look for a replacement (the admin is told)."""
    from datetime import date, time

    from app.models.professor_absence import ProfessorAbsence
    from app.services import replacements
    from app.services.settings_service import public_base_url

    professor = _my_professor(request, db)
    if professor is None:
        raise HTTPException(status_code=404, detail="No professor profile")
    try:
        day = date.fromisoformat(absence_date)
        begins = time.fromisoformat(start_time) if start_time else None
        ends = time.fromisoformat(end_time) if end_time else None
    except ValueError:
        return RedirectResponse(f"/teacher/space?{urlencode({'error': _t('Choose the day you will be away.')})}#absences", status_code=303)
    if day < local_now().date():
        return RedirectResponse(f"/teacher/space?{urlencode({'error': _t('Choose a day to come.')})}#absences", status_code=303)
    absence = ProfessorAbsence(professor_id=professor.id, absence_date=day, start_time=begins, end_time=ends, reason=reason.strip()[:200] or None)
    db.add(absence)
    db.commit()
    opened = replacements.open_for_absence(db, absence, public_base_url(db, request))
    notice = _t("Noted. The admin is told; {0} of your sessions that day look for a replacement.", len(opened)) if opened else _t("Noted. The admin is told.")
    return RedirectResponse(f"/teacher/space?{urlencode({'notice': notice})}#absences", status_code=303)


def _writer_of(request: Request, db: Session, class_id: str) -> tuple[ClassGroup, str]:
    """The class, when the one asking teaches it (or is the admin), and the name it is signed with."""
    user = auth.current(request)
    group = db.get(ClassGroup, class_id)
    if group is None or user is None:
        raise HTTPException(status_code=404, detail="Class not found")
    if user.is_admin:
        return group, user.name or "9antra"
    professor = _my_professor(request, db)
    if professor is None or group.professor_id != professor.id:
        raise HTTPException(status_code=403, detail="Only the professor of this class writes to it.")
    return group, professor.name


@router.post("/teacher/classes/{class_id}/message")
def write_to_my_class(class_id: str, request: Request, subject: str = Form(""), body: str = Form(""), channel: str = Form("email"),
                      back: str = Form(""), db: Session = Depends(get_db)):
    """A professor writes to every candidate of their class (email, or WhatsApp too), signed with their name."""
    from app.services import reminders

    group, name = _writer_of(request, db, class_id)
    back = "/teacher/space" if not auth.current(request).is_admin else (back if back.startswith("/professors/") else f"/classes/{class_id}")
    text = body.strip()
    if text and name and name not in text[-120:]:
        text += f"\n\n{name}"
    try:
        reminder = reminders.create(db, audience="class", audience_id=group.id, subject=subject, body=text, by=name,
                                    channel=channel if channel in ("email", "both", "whatsapp") else "email", send_at=None)
    except ValueError as problem:
        return RedirectResponse(f"{back}?{urlencode({'error': str(problem)})}#classes", status_code=303)
    sent, failed = reminders.send(db, reminder)
    notice = _t("Sent to the candidates of {0}: {1}; not reached: {2}.", group.name, sent, failed) if failed else _t("Sent to the candidates of {0}: {1}.", group.name, sent)
    return RedirectResponse(f"{back}?{urlencode({'notice': notice})}#classes", status_code=303)


class _Topic(BaseModel):
    topic: str = ""


@router.post("/teacher/write")
def write_it_for_me(payload: _Topic, request: Request, db: Session = Depends(get_db)):
    """“Write it for me”, for a professor's message to their class: only the topic goes to the online writer."""
    from app.services import reminder_writer

    professor = _my_professor(request, db)
    return reminder_writer.write(db, payload.topic, audience="class", author=professor.name if professor else None)


@router.get("/teacher/profile", response_class=HTMLResponse)
def my_profile(request: Request, db: Session = Depends(get_db)):
    professor = _my_professor(request, db)
    if professor is None:
        return RedirectResponse("/classes/?tab=professors", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="teacher_profile.html",
        context={
            "active_page": "teacher-profile",
            "professor": professor,
            "specialties": [item.strip() for item in (professor.specialties or "").split(",") if item.strip()],
            "class_count": db.query(ClassGroup).filter(ClassGroup.professor_id == professor.id).count(),
            "absences": sorted(
                (item for item in professor.absences if item.absence_date >= local_now().date()),
                key=lambda item: item.absence_date,
            ),
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/teacher/profile")
def save_my_profile(
    request: Request,
    name: str = Form(""),
    phone: str = Form(""),
    specialties: str = Form(""),
    db: Session = Depends(get_db),
):
    professor = _my_professor(request, db)
    if professor is None:
        raise HTTPException(status_code=404, detail="No professor profile")
    name = " ".join(name.split())
    if not name:
        return RedirectResponse(f"/teacher/profile?{urlencode({'error': 'Enter your name.'})}", status_code=303)
    professor.name = name[:120]
    professor.phone = " ".join(phone.split())[:40] or None
    professor.specialties = ", ".join(
        dict.fromkeys(item.strip() for item in specialties.split(",") if item.strip())
    )[:500] or None
    # The name shown on their classes and on their account follows.
    for class_group in db.query(ClassGroup).filter(ClassGroup.professor_id == professor.id).all():
        class_group.professor_name = professor.name
    account = db.query(User).filter(User.professor_id == professor.id).first()
    if account is not None:
        account.name = professor.name
    db.commit()
    return RedirectResponse(f"/teacher/profile?{urlencode({'notice': 'Profile saved.'})}", status_code=303)


# ------------------------------------------------------------------
# Presence of candidates
# ------------------------------------------------------------------

def _class_or_404(db: Session, class_id: str) -> ClassGroup:
    class_group = db.get(ClassGroup, class_id)
    if class_group is None:
        raise HTTPException(status_code=404, detail="Class not found")
    return class_group


def _back(class_id: str, session_id: str | None = None, notice: str | None = None, error: str | None = None):
    query = urlencode({
        key: value for key, value in (("session", session_id), ("notice", notice), ("error", error)) if value
    })
    return RedirectResponse(f"/classes/{class_id}/attendance{'?' + query if query else ''}", status_code=303)


def _guess(mark, join: dict | None) -> str:
    """The presence a row starts with: as marked, else as their own link saw it (came, late, never came), else present."""
    if mark is not None:
        return mark.status
    if join and join.get("joined"):
        return "late" if join.get("late") else "present"
    if join and join.get("sent"):
        return "absent"
    return "present"


@router.get("/classes/{class_id}/attendance", response_class=HTMLResponse)
def attendance_page(class_id: str, request: Request, db: Session = Depends(get_db)):
    class_group = _class_or_404(db, class_id)
    now = local_now()
    all_sessions = attendance.sessions(db, class_group.id)
    wanted = request.query_params.get("session")
    selected = next((item for item in all_sessions if item.id == wanted), None) or attendance.default_session(all_sessions)
    marks = attendance.marks_of(db, selected.id) if selected else {}
    people = attendance.members(db, class_group.id)
    summary = attendance.class_summary(db, class_group)
    rates = {item["registration"].id: item for item in summary["people"]}
    from app.services import behaviour, email_threads, meetings, weekly_questions      # here: only this page needs them
    from app.services.settings_service import public_base_url

    if selected:
        meetings.links_for(db, class_group, selected)       # each candidate's own link to the meeting
    joins = meetings.sheet(db, class_group, selected) if selected else {}
    scores = behaviour.scores(db, [registration.candidate for registration in people])
    from app.services import session_check

    checks = session_check.evidence(db, class_group.id) if selected else {}
    check_plan = session_check.settings_of(db)
    base_url = public_base_url(db, request)

    return templates.TemplateResponse(
        request=request,
        name="attendance.html",
        context={
            "active_page": "teacher" if not auth.current(request) or auth.current(request).is_professor else "classes",
            "cls": class_group,
            "look": class_looks.look(class_group),
            "class_info": class_summary(class_group, now),
            "sessions": [
                {
                    "session": item,
                    "is_selected": selected is not None and item.id == selected.id,
                    "is_future": item.session_date > now.date(),
                    "is_today": item.session_date == now.date(),
                    "counts": summary["by_session"].get(item.id),
                }
                for item in all_sessions
            ],
            "selected": selected,
            "selected_is_future": bool(selected and selected.session_date > now.date()),
            "sheet": [
                {
                    "registration": registration,
                    "name": (registration.candidate.full_name or registration.candidate.email) if registration.candidate else "Candidate",
                    "candidate": registration.candidate,
                    "status": marks[registration.id].status if registration.id in marks else None,
                    # What the sheet shows before anyone confirms it: what their own link saw, else present.
                    "guess": _guess(marks.get(registration.id), joins.get(registration.id)),
                    "note": marks[registration.id].note if registration.id in marks else "",
                    "participation": marks[registration.id].participation if registration.id in marks else None,
                    "questions": marks[registration.id].questions if registration.id in marks else None,
                    "join": joins.get(registration.id),
                    "check": checks.get((selected.id, registration.id)) if selected else None,
                    "behaviour": scores.get(registration.candidate_id),
                    "rate": rates.get(registration.id, {}).get("rate"),
                    "absent_in_a_row": rates.get(registration.id, {}).get("absent_in_a_row", 0),
                }
                for registration in people
            ],
            "already_marked": bool(marks),
            # Marked by a person (not only by the candidates' links): the sheet is confirmed.
            "confirmed": meetings.confirmed(marks),
            "from_links": any(item["sent"] for item in joins.values()),
            "check_summary": session_check.session_summary(db, selected.id) if selected else None,
            "check_plan": check_plan,
            "summary": summary,
            "labels": attendance.LABELS,
            "participation_labels": behaviour.PARTICIPATION,
            "meeting": {
                "url": selected.meeting_url if selected else None,
                "door": meetings.door(selected, class_group, now) if selected else None,
                "sent": sum(1 for item in joins.values() if item["sent"]),
                "joined": sum(1 for item in joins.values() if item["joined"]),
                "base_url": base_url,
                "local": weekly_questions.is_local_address(base_url),
                "email_ready": email_threads.smtp_configured(),
                "late_after": meetings.LATE_AFTER,
            },
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/classes/{class_id}/attendance/today")
def add_today(class_id: str, db: Session = Depends(get_db)):
    """A session for today, when the class has no weekly plan to choose from."""
    class_group = _class_or_404(db, class_id)
    session = attendance.add_session_today(db, class_group)
    return _back(class_id, session.id, notice="Today's session is ready: mark who is here.")


@router.post("/classes/{class_id}/attendance/{session_id}")
async def save_attendance(class_id: str, session_id: str, request: Request, db: Session = Depends(get_db)):
    class_group = _class_or_404(db, class_id)
    session = db.get(ClassSession, session_id)
    if session is None or session.class_group_id != class_id:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.session_date > local_now().date():
        return _back(class_id, session_id, error="This session has not taken place yet.")
    form = await request.form()
    marks = {key[len("status_"):]: value for key, value in form.items() if key.startswith("status_")}
    notes = {key[len("note_"):]: value for key, value in form.items() if key.startswith("note_")}
    observations = {}
    for registration_id in marks:
        took_part, asked = str(form.get(f"participation_{registration_id}") or ""), str(form.get(f"questions_{registration_id}") or "")
        observations[registration_id] = {
            "participation": int(took_part) if took_part in ("1", "2", "3") else None,
            "questions": min(int(asked), 50) if asked.isdigit() else None,
        }
    if not marks:
        return _back(class_id, session_id, error="Mark at least one candidate.")
    user = auth.current(request)
    saved = attendance.save(db, class_group, session, marks, notes, marked_by=(user.name if user else "") or "Admin", observations=observations)
    if not saved["marked"]:
        return _back(class_id, session_id, error="None of these candidates is in this class.")
    notice = f"Presence saved for {saved['marked']} candidate{'s' if saved['marked'] != 1 else ''}"
    notice += f": {saved['absent']} absent." if saved["absent"] else ": everyone was there."
    return _back(class_id, session_id, notice=notice)


# ------------------------------------------------------------------
# The meeting link of a class, asked three days before it starts (services/meetings.ask_links_due)
# ------------------------------------------------------------------

def _my_class(request: Request, db: Session, class_id: str) -> tuple[Professor | None, ClassGroup]:
    professor = _my_professor(request, db)
    class_group = db.get(ClassGroup, class_id)
    user = auth.current(request)
    if class_group is None or (professor is None and not getattr(user, "is_admin", False)) or \
            (professor is not None and class_group.professor_id != professor.id):
        raise HTTPException(status_code=404, detail="Class not found")
    return professor, class_group


@router.get("/teacher/classes/{class_id}/link", response_class=HTMLResponse)
def class_link_page(class_id: str, request: Request, db: Session = Depends(get_db)):
    """Where the professor pastes the private meeting link of their class."""
    from app.services import meetings
    from app.services.schedule_utils import class_start_dt

    professor, class_group = _my_class(request, db, class_id)
    members = attendance.members(db, class_group.id)
    mode = meetings.mode_of(db, class_group)
    return templates.TemplateResponse(request=request, name="teacher_link.html", context={
        "active_page": "teacher", "class_group": class_group, "start": class_start_dt(class_group), "mode": mode, "modes": meetings.MODES,
        "online": sum(1 for item in members if meetings.follows_online(db, class_group, item, mode)), "members": len(members),
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error"),
    })


@router.post("/teacher/classes/{class_id}/link")
def save_class_link(class_id: str, request: Request, meeting_url: str = Form(""), db: Session = Depends(get_db)):
    """The professor's link is kept for every session, and each online candidate gets their own link at once."""
    from app.services import meetings
    from app.services.settings_service import public_base_url

    _, class_group = _my_class(request, db, class_id)
    try:
        sent = meetings.set_class_link(db, class_group, meeting_url, public_base_url(db, request))
    except ValueError as problem:
        db.rollback()
        return RedirectResponse(f"/teacher/classes/{class_id}/link?" + urlencode({"error": str(problem)}), status_code=303)
    notice = (f"Saved. {sent} online candidate(s) received their own link." if sent
              else "Saved. Each online candidate gets their own link before each session.")
    return RedirectResponse(f"/teacher/classes/{class_id}/link?" + urlencode({"notice": notice}), status_code=303)

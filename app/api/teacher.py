"""A professor's own space (their classes and profile) and the presence sheet
of a class, which the professor of the class and the admin both use."""
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import ClassGroup, ClassStatus, Professor
from app.models.classsession import ClassSession
from app.models.user import User
from app.services import attendance, auth, class_looks, weekly_questions
from app.services.class_views import class_summary
from app.services.schedule_utils import local_now
from app.template_context import notification_context

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
    saved = attendance.save(db, class_group, session, marks, notes, marked_by=user.name if user else "", observations=observations)
    if not saved["marked"]:
        return _back(class_id, session_id, error="None of these candidates is in this class.")
    notice = f"Presence saved for {saved['marked']} candidate{'s' if saved['marked'] != 1 else ''}"
    notice += f": {saved['absent']} absent." if saved["absent"] else ": everyone was there."
    return _back(class_id, session_id, notice=notice)

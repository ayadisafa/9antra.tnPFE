from datetime import datetime, date, time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Form, Request
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import ClassGroup, ClassStatus, Program, ClassSession
from app.models import Registration, Professor
from app.models.professor_absence import ProfessorAbsence
from app.models.user import User
from app.models.registration import RegistrationStatus, PaymentStatus
from app.services import attendance, auth, class_emails, class_looks, course_materials, level_check, program_feedback, weekly_questions
from app.services.class_views import (
    class_summary,
    move_options,
    reminders_by_registration,
)
from app.services.class_service import get_session_status, get_candidate_session_calendar
from app.services.enrollment_service import (
    START_AFTER_DAYS,
    START_FIXED_DATE,
    START_MIN_ENROLLMENT,
    able_to_take,
    assign_waiting_professors,
    class_members,
    complete_class,
    create_class,
    delete_class,
    enroll_paid_registration,
    move_registration,
    refresh_class_status,
    reopen_class,
    reschedule_sessions,
    send_reminder_now,
    start_class_now,
    sync_class_lifecycle,
    sync_members,
    teachers_of,
    teaches,
    tell_class_of_professor,
    unassign_registration,
    update_class_parameters,
)
from app.services.schedule_utils import (
    DAY_KEYS,
    DAY_SHORT_EN,
    class_start_dt,
    local_now,
    parse_days,
    parse_time,
    utc_to_local,
)
from app.services.settings_service import get_settings, parse_reminder_days

router = APIRouter(prefix="/classes", tags=["Class Groups"])
from app.template_context import notification_context

templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


class CalendarMove(BaseModel):
    start: str
    end: str | None = None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _redirect(url: str, notice: str | None = None, error: str | None = None) -> RedirectResponse:
    """303 redirect carrying a one-line message for the next page."""
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key not in ("notice", "error")]
    if notice:
        query.append(("notice", notice))
    if error:
        query.append(("error", error))
    return RedirectResponse(urlunsplit(parts._replace(query=urlencode(query))), status_code=303)


def _return_url(request: Request, fallback: str) -> str:
    """Path of the page the form was submitted from, or `fallback`."""
    referer = request.headers.get("referer")
    if not referer:
        return fallback
    parts = urlsplit(referer)
    return urlunsplit(("", "", parts.path or fallback, parts.query, ""))


def _get_class(db: Session, class_id: str) -> ClassGroup:
    class_group = db.get(ClassGroup, class_id)
    if not class_group:
        raise HTTPException(status_code=404, detail="Class Group not found")
    return class_group


def _parameters_context(db: Session, class_group: ClassGroup, now: datetime) -> dict:
    """Everything the parameters form of a class shows."""
    return {
        "cls": class_group,
        "summary": class_summary(class_group, now),
        "look": class_looks.look(class_group),
        "symbols": class_looks.SYMBOLS,
        "colours": class_looks.COLOURS,
        "professors": _professor_choices(db, class_group),
        "reminder_offsets": parse_reminder_days(class_group.reminder_days),
        "day_options": list(zip(DAY_KEYS, DAY_SHORT_EN)),
        "selected_days": [DAY_KEYS[index] for index in parse_days(class_group.days_of_week)],
        "created_local": utc_to_local(class_group.created_at),
        "now": now,
        "modes": {"after_days": START_AFTER_DAYS, "min_enrollment": START_MIN_ENROLLMENT, "fixed": START_FIXED_DATE},
    }


def _professor_choices(db: Session, class_group: ClassGroup) -> list[Professor]:
    """Who can take this class: the professors of its program who are available and free at its hours (able_to_take).
    The one teaching it today stays first, marked when that is no longer so."""
    choices = able_to_take(db, class_group, exclude_id=class_group.professor_id)
    current = class_group.professor
    if current is not None:
        choices = [current] + choices
    for professor in choices:
        professor.fits = teaches(professor, class_group.program)
    return choices


def _waiting_for_a_class(db: Session, program_id: str | None = None) -> list[Registration]:
    """Paid registrations that have no class yet, oldest first."""
    query = db.query(Registration).filter(
        Registration.payment_status == PaymentStatus.PAID,
        Registration.class_group_id.is_(None),
        Registration.program_id.is_not(None),
        Registration.status == RegistrationStatus.PENDING,
    )
    if program_id:
        query = query.filter(Registration.program_id == program_id)
    return query.order_by(Registration.registration_date.asc()).all()


def _get_professor(db: Session, professor_id: str) -> Professor:
    professor = db.get(Professor, professor_id)
    if not professor:
        raise HTTPException(status_code=404, detail="Professor not found")
    return professor


def _clean_specialties(value: str, chosen: list[str] | None = None) -> str | None:
    """What a professor teaches: the programs ticked in the form, then what was typed."""
    items = [item.strip() for item in (chosen or [])] + [item.strip() for item in (value or "").split(",")]
    kept: dict[str, str] = {}
    for item in items:
        if item:
            kept.setdefault(item.lower(), item)
    return ", ".join(kept.values())[:500] or None


# ------------------------------------------------------------------
# Timetable drag & drop
# ------------------------------------------------------------------

@router.post("/{class_id}/calendar-move")
def move_class_from_calendar(class_id: str, payload: CalendarMove, db: Session = Depends(get_db)):
    class_group = _get_class(db, class_id)
    try:
        start = datetime.fromisoformat(payload.start.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid calendar date") from exc
    class_group.start_mode = START_FIXED_DATE
    class_group.start_date = start
    class_group.start_time = start.time()
    if payload.end:
        try:
            parsed_end = datetime.fromisoformat(payload.end.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid calendar end date") from exc
        class_group.end_time = parsed_end.time()
    reschedule_sessions(db, class_group)
    sync_members(db, class_group)
    refresh_class_status(db, class_group)
    db.commit()
    return {"status": "updated", "class_id": class_id, "start": class_group.start_date.isoformat()}


# ------------------------------------------------------------------
# Create
# ------------------------------------------------------------------

@router.post("/create")
async def create_class_group(
    request: Request,
    program_id: str = Form(...),
    professor_id: str = Form(""),
    max_capacity: int = Form(None),
    total_weeks: int = Form(None),
    start_date: str = Form(None),  # YYYY-MM-DD; empty = use the default start rule
    days_of_week: str = Form(""),
    start_time: str = Form(""),
    end_time: str = Form(""),
    db: Session = Depends(get_db)
):
    program = db.get(Program, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    back = "/classes/?tab=classes"
    professor = db.get(Professor, professor_id) if professor_id else None
    if professor is not None and not professor.is_available:
        return _redirect(back, error=f"{professor.name} is marked unavailable. Choose another professor.")

    # A class is made for somebody: the paid candidates of this program who have none.
    waiting = _waiting_for_a_class(db, program.id)
    if not waiting:
        return _redirect(back, error=(
            f"Nobody is waiting for a {program.name} class, so it would be empty. "
            "A class is created by itself as soon as a candidate pays."
        ))
    try:
        new_class = create_class(
            db, program,
            professor=professor,
            start_date=date.fromisoformat(start_date) if start_date else None,
            capacity=max_capacity,
            total_weeks=total_weeks,
            days_of_week=days_of_week,
            start_time=parse_time(start_time),
            end_time=parse_time(end_time),
        )
    except ValueError as exc:
        db.rollback()
        return _redirect(back, error=str(exc))
    db.commit()

    placed = 0
    for registration in waiting:
        try:
            move_registration(db, registration.id, new_class.id)
            placed += 1
        except ValueError:
            db.rollback()      # the class is full: the others keep waiting
            break
    return _redirect(
        f"/classes/{new_class.id}",
        notice=f"{new_class.name} created with {placed} candidate{'s' if placed != 1 else ''} who {'were' if placed != 1 else 'was'} waiting.",
    )


@router.post("/professors/create")
def create_professor(
    name: str = Form(...),
    specialties: str = Form(""),
    programs: list[str] = Form(default=[]),
    email: str = Form(""),
    phone: str = Form(""),
    available_from: str = Form(""),
    available_until: str = Form(""),
    db: Session = Depends(get_db),
):
    clean_name = " ".join(name.split())
    if not clean_name:
        return _redirect("/classes/?tab=professors", error="Enter the professor's name.")
    db.add(Professor(
        name=clean_name,
        specialties=_clean_specialties(specialties, programs),
        email=email.strip().lower() or None,
        phone=" ".join(phone.split()) or None,
        available_from=date.fromisoformat(available_from) if available_from else None,
        available_until=date.fromisoformat(available_until) if available_until else None,
    ))
    db.commit()
    sync_class_lifecycle(db)          # a class waiting for a professor of this program takes them at once
    return _redirect("/classes/?tab=professors", notice=f"{clean_name} added.")


@router.post("/professors/{professor_id}/edit")
def edit_professor(
    professor_id: str,
    name: str = Form(...),
    specialties: str = Form(""),
    programs: list[str] = Form(default=[]),
    email: str = Form(""),
    phone: str = Form(""),
    available_from: str = Form(""),
    available_until: str = Form(""),
    is_available: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Everything about a professor, from one form."""
    professor = _get_professor(db, professor_id)
    clean_name = " ".join(name.split())
    if not clean_name:
        return _redirect("/classes/?tab=professors", error="Enter the professor's name.")
    try:
        start = date.fromisoformat(available_from) if available_from else None
        end = date.fromisoformat(available_until) if available_until else None
    except ValueError:
        return _redirect("/classes/?tab=professors", error="The availability dates are not valid.")
    if start and end and end < start:
        return _redirect("/classes/?tab=professors", error="“Available until” is before “available from”.")
    professor.name = clean_name[:120]
    professor.specialties = _clean_specialties(specialties, programs)
    professor.email = email.strip().lower() or None
    professor.phone = " ".join(phone.split())[:40] or None
    professor.available_from, professor.available_until = start, end
    professor.is_available = is_available
    # Their classes and their account carry the same name.
    for class_group in db.query(ClassGroup).filter(ClassGroup.professor_id == professor.id).all():
        class_group.professor_name = professor.name
    account = db.query(User).filter(User.professor_id == professor.id).first()
    if account is not None:
        account.name = professor.name
    db.commit()
    sync_class_lifecycle(db)          # available again, or a new speciality: a waiting class takes them at once
    return _redirect("/classes/?tab=professors", notice=f"{professor.name} saved.")


TO_NAME = "À nommer"          # a place for a professor still to find (made before 22 October 2026): never given a class


def is_place_to_name(professor: Professor) -> bool:
    return (professor.name or "").startswith(TO_NAME)


def professors_coverage(db: Session, programs: list, professors: list) -> list[dict]:
    """Each program and who teaches it (as many professors as the admin likes); the programs nobody teaches yet first."""
    found = []
    for program in programs:
        mine = [professor for professor in professors if teaches(professor, program)]
        real = [professor for professor in mine if not is_place_to_name(professor)]
        found.append({"program": program, "real": real, "places": [professor for professor in mine if is_place_to_name(professor)],
                      "nobody": not real,
                      "others": [professor for professor in professors if professor not in mine and not is_place_to_name(professor)]})
    found.sort(key=lambda item: (not item["nobody"], len(item["real"]), item["program"].name))
    return found


@router.post("/professors/resource-reminders")
def save_resource_reminders(on: str = Form(""), days: str = Form("2"), text: str = Form(""), send_now: str = Form(""), db: Session = Depends(get_db)):
    """The reminders to professors to add their resources: on or off, how many days before, and their words."""
    from app.services import resource_reminders
    from app.services.settings_service import public_base_url

    plan = resource_reminders.save_settings(db, on=bool(on), days=days, text=text)
    sent = resource_reminders.send_due(db, base_url=public_base_url(db)) if send_now and plan["on"] else 0
    notice = (f"Professors are reminded {plan['days']} day{'s' if plan['days'] != 1 else ''} before a week that has no resource yet."
              if plan["on"] else "The resource reminders are stopped.")
    if send_now:
        notice += f" {sent} reminder{'s' if sent != 1 else ''} sent now."
    return _redirect("/classes/?tab=professors#resources", notice=notice)


@router.post("/professors/{professor_id}/specialty")
def add_professor_specialty(
    request: Request,
    professor_id: str,
    program_id: str = Form(...),
    db: Session = Depends(get_db),
):
    """One click from a class: its program joins what this professor teaches."""
    professor = _get_professor(db, professor_id)
    program = db.get(Program, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    if not teaches(professor, program):
        professor.specialties = _clean_specialties(professor.specialties or "", [program.name])
        db.commit()
    sync_class_lifecycle(db)       # a class that was waiting for this can start
    return _redirect(_return_url(request, "/classes/?tab=professors"),
                     notice=f"{program.name} is now one of {professor.name}'s specialities.")


@router.post("/professors/{professor_id}/access")
def give_professor_access(
    professor_id: str,
    email: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    """Let a professor sign in (or give them a new password)."""
    professor = _get_professor(db, professor_id)
    back = "/classes/?tab=professors"
    account = db.query(User).filter(User.professor_id == professor.id).first()
    address = auth.clean_email(email)
    if address is None:
        return _redirect(back, error="Enter the email address the professor will sign in with.")
    problem = auth.password_problem(password)
    if problem:
        return _redirect(back, error=problem)
    if account is None:
        try:
            auth.create_user(db, email=address, name=professor.name, password=password,
                             role=auth.PROFESSOR, professor_id=professor.id)
        except ValueError as exc:
            db.rollback()
            return _redirect(back, error=str(exc))
        notice = f"{professor.name} can now sign in with {address} and the password you chose."
    else:
        taken = db.query(User.id).filter(User.email == address, User.id != account.id).first()
        if taken is not None:
            return _redirect(back, error="This email address already has an account.")
        account.email, account.is_active = address, True
        auth.set_password(db, account, password)
        notice = f"New password saved for {professor.name}. They are signed out everywhere and sign in with {address}."
    if not professor.email:
        professor.email = address
        db.commit()
    return _redirect(back, notice=notice + " Give it to them yourself: no email is sent.")


@router.post("/professors/{professor_id}/access/remove")
def remove_professor_access(professor_id: str, db: Session = Depends(get_db)):
    professor = _get_professor(db, professor_id)
    account = db.query(User).filter(User.professor_id == professor.id).first()
    if account is not None:
        db.delete(account)
        db.commit()
    return _redirect("/classes/?tab=professors", notice=f"{professor.name} can no longer sign in.")


@router.get("/professors/{professor_id}/delete", response_class=HTMLResponse)
def professor_delete_form(professor_id: str, request: Request, db: Session = Depends(get_db)):
    """What deleting this professor will do, for the dialog: each class with who could take it, their access, their questions."""
    from app.services import course_questions, professor_removal

    professor = _get_professor(db, professor_id)
    rows = [{"class": group, "successors": professor_removal.successors(db, professor, group)}
            for group in professor_removal.classes_of(db, professor)]
    return templates.TemplateResponse(request=request, name="_professor_delete.html", context={
        "professor": professor, "rows": rows, "auto": get_settings(db).auto_assign_professor,
        "account": db.query(User).filter(User.professor_id == professor.id).first(),
        "questions": len(course_questions.waiting_for(db, f"professor:{professor.id}")),
    })


@router.post("/professors/{professor_id}/delete")
async def delete_professor(professor_id: str, request: Request, db: Session = Depends(get_db)):
    """A professor removed for good: each of their classes goes to the professor chosen for it, or waits for one."""
    from app.services import professor_removal

    professor = _get_professor(db, professor_id)
    form = await request.form()
    handover = {key[len("give_"):]: value for key, value in form.items() if key.startswith("give_")}
    user = auth.current(request)
    try:
        done = professor_removal.remove(db, professor, handover, who=user.name if user else None)
    except ValueError as exc:
        return _redirect("/classes/?tab=professors", error=str(exc))
    for group, taker in assign_waiting_professors(db):        # left waiting: the first professor who can takes it
        done["waiting"] = [name for name in done["waiting"] if name != group.name]
        done["given"].append({"id": group.id, "class": group.name, "to": taker.name})
    db.commit()
    tell_class_of_professor(db, [item["id"] for item in done["given"]])
    sync_class_lifecycle(db)          # a class still without a professor is held until it has one
    return _redirect("/classes/?tab=professors", notice=professor_removal.notice(done))


@router.post("/professors/{professor_id}/absences")
def add_professor_absence(
    professor_id: str,
    absence_date: str = Form(...),
    start_time: str = Form(""),
    end_time: str = Form(""),
    reason: str = Form(""),
    db: Session = Depends(get_db),
):
    professor = db.get(Professor, professor_id)
    if not professor:
        raise HTTPException(status_code=404, detail="Professor not found")
    db.add(ProfessorAbsence(
        professor_id=professor_id,
        absence_date=date.fromisoformat(absence_date),
        start_time=time.fromisoformat(start_time) if start_time else None,
        end_time=time.fromisoformat(end_time) if end_time else None,
        reason=reason.strip() or None,
    ))
    db.commit()
    return _redirect("/classes/?tab=professors", notice=f"Absence saved for {professor.name}.")


# ------------------------------------------------------------------
# Class parameters and status
# ------------------------------------------------------------------

@router.post("/{class_id}/parameters")
def save_class_parameters(
    class_id: str,
    name: str = Form(""),
    professor_id: str = Form(""),
    capacity: int = Form(...),
    total_weeks: int = Form(...),
    start_mode: str = Form(...),
    start_delay_days: int = Form(10),
    min_students_to_start: int = Form(5),
    min_start_notice_days: int = Form(3),
    fixed_start_date: str = Form(""),
    days: list[str] = Form(default=[]),
    start_time: str = Form(""),
    end_time: str = Form(""),
    room: str = Form(""),
    reminder_days: str = Form("7,3,1"),
    notify_members: bool = Form(False),
    symbol: str = Form(""),
    color: str = Form(""),
    back: str = Form(""),
    db: Session = Depends(get_db),
):
    """The class's parameters: symbol, start rule, schedule, capacity, professor, reminders."""
    class_group = _get_class(db, class_id)
    # Saved from the list of classes, the admin returns to the list.
    target = "/classes/?tab=classes" if back == "list" else f"/classes/{class_id}"
    try:
        result = update_class_parameters(
            db, class_group,
            name=name,
            professor_id=professor_id or None,
            capacity=capacity,
            total_weeks=total_weeks,
            start_mode=start_mode,
            start_delay_days=start_delay_days,
            min_students_to_start=min_students_to_start,
            min_start_notice_days=min_start_notice_days,
            fixed_start_date=date.fromisoformat(fixed_start_date) if fixed_start_date else None,
            days_of_week=days,
            start_time=parse_time(start_time),
            end_time=parse_time(end_time),
            room=room,
            reminder_days=reminder_days,
            notify_members=notify_members,
        )
    except ValueError as exc:
        db.rollback()
        return _redirect(target, error=f"{class_group.name}: {exc}")

    class_group.symbol = class_looks.clean_symbol(symbol)
    class_group.color = class_looks.clean_colour(color)
    db.commit()

    notice = f"{class_group.name}: parameters saved."
    if result["emails"]:
        notice += f" {result['emails']} candidate(s) are being emailed the new schedule."
    return _redirect(target, notice=notice)


@router.get("/{class_id}/parameters-form", response_class=HTMLResponse)
def class_parameters_form(class_id: str, request: Request, db: Session = Depends(get_db)):
    """The parameters form of a class, on its own: shown in a dialog on the list of classes."""
    class_group = _get_class(db, class_id)
    return templates.TemplateResponse(
        request=request,
        name="_class_parameters.html",
        context={**_parameters_context(db, class_group, local_now()), "back": "list"},
    )


@router.post("/{class_id}/start-now")
def start_now(class_id: str, db: Session = Depends(get_db)):
    class_group = _get_class(db, class_id)
    try:
        start_class_now(db, class_group)
    except ValueError as exc:
        db.rollback()
        return _redirect(f"/classes/{class_id}", error=str(exc))
    return _redirect(f"/classes/{class_id}", notice="Class started.")


@router.post("/{class_id}/complete")
def mark_completed(class_id: str, db: Session = Depends(get_db)):
    class_group = _get_class(db, class_id)
    if class_group.status != ClassStatus.ONGOING:
        return _redirect(f"/classes/{class_id}", error="Only an ongoing class can be marked completed.")
    complete_class(db, class_group)
    return _redirect(f"/classes/{class_id}", notice="Class marked completed.")


@router.post("/{class_id}/reopen")
def reopen(class_id: str, db: Session = Depends(get_db)):
    class_group = _get_class(db, class_id)
    reopen_class(db, class_group)
    return _redirect(f"/classes/{class_id}", notice="Class reopened; its status follows its dates again.")


@router.post("/{class_id}/update-status")
async def update_class_status(
    class_id: str,
    status: ClassStatus = Form(...),
    db: Session = Depends(get_db)
):
    """Kept for older forms. Status is derived from dates, so this adjusts the dates."""
    class_group = _get_class(db, class_id)
    if status == ClassStatus.ONGOING and class_group.status == ClassStatus.NOT_STARTED:
        try:
            start_class_now(db, class_group)
        except ValueError as exc:
            db.rollback()
            return _redirect(f"/classes/{class_id}", error=str(exc))
    elif status == ClassStatus.ONGOING:
        reopen_class(db, class_group)
    elif status == ClassStatus.COMPLETED and class_group.status == ClassStatus.ONGOING:
        complete_class(db, class_group)
    return _redirect(f"/classes/{class_id}")


@router.post("/{class_id}/delete")
def remove_class(class_id: str, db: Session = Depends(get_db)):
    class_group = _get_class(db, class_id)
    name = class_group.name
    try:
        delete_class(db, class_group)
    except ValueError as exc:
        db.rollback()
        return _redirect(f"/classes/{class_id}", error=str(exc))
    course_materials.forget_class(db, class_id)
    return _redirect("/classes/?tab=classes", notice=f"{name} deleted.")


# ------------------------------------------------------------------
# Registrations: open, move, decide
# ------------------------------------------------------------------

@router.get("/registration/{registration_id}")
def open_registration_class(registration_id: str, db: Session = Depends(get_db)):
    """Where a candidate's paid program leads: their class, with their enrollment in focus."""
    registration = db.get(Registration, registration_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Registration not found")
    if registration.class_group_id:
        return RedirectResponse(f"/classes/{registration.class_group_id}?registration={registration.id}", status_code=303)
    if registration.payment_status == PaymentStatus.PAID:
        return RedirectResponse(f"/classes/?tab=decisions#decision-{registration.id}", status_code=303)
    return RedirectResponse(f"/candidate/{registration.candidate_id}#payment-pending", status_code=303)


@router.post("/registration/{registration_id}/move")
def change_registration_class(
    request: Request,
    registration_id: str,
    class_group_id: str = Form(...),
    notify_candidate: bool = Form(False),
    stay: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Change a candidate's class (or assign one for the first time)."""
    try:
        registration = move_registration(db, registration_id, class_group_id, notify_candidate=notify_candidate)
    except ValueError as exc:
        db.rollback()
        return _redirect(_return_url(request, "/classes/"), error=str(exc))
    target = registration.class_group
    notice = f"{registration.candidate.full_name or registration.candidate.email} is now in {target.name}."
    if stay:
        return _redirect(_return_url(request, f"/classes/{target.id}"), notice=notice)
    return _redirect(f"/classes/{target.id}?registration={registration.id}", notice=notice)


@router.post("/registration/{registration_id}/decision")
def decide_registration_assignment(
    registration_id: str,
    decision: str = Form(...),
    class_group_id: str = Form(""),
    professor_id: str = Form(""),
    max_capacity: int = Form(None),
    total_weeks: int = Form(None),
    start_date: str = Form(None),
    days_of_week: str = Form(""),
    start_time: str = Form(""),
    end_time: str = Form(""),
    db: Session = Depends(get_db),
):
    registration = db.query(Registration).filter(Registration.id == registration_id).first()
    if not registration:
        raise HTTPException(status_code=404, detail="Registration not found")
    if registration.payment_status != PaymentStatus.PAID:
        raise HTTPException(status_code=400, detail="Registration must be fully paid first")

    back = "/classes/?tab=decisions"
    try:
        if decision == "auto":
            result = enroll_paid_registration(db, registration)
            if not result.class_group:
                reasons = {
                    "no_program": "This registration is not linked to a program yet.",
                    "no_open_class": "No open class exists and automatic class creation is off. Create a new class instead.",
                    "busy": "They already follow another class: one program at a time. They start this program in a class "
                            "some days after it ends (Settings, Classes). To start them now anyway, choose a class or create a new one.",
                }
                raise ValueError(reasons.get(result.reason, "The registration could not be assigned."))
        elif decision == "assign_existing":
            if not class_group_id:
                raise ValueError("Select the class to assign.")
            move_registration(db, registration_id, class_group_id)
        elif decision == "create_new":
            program = db.get(Program, registration.program_id) if registration.program_id else None
            if not program:
                raise ValueError("This registration is not linked to a program yet.")
            new_class = create_class(
                db, program,
                professor=db.get(Professor, professor_id) if professor_id else None,
                start_date=date.fromisoformat(start_date) if start_date else None,
                capacity=max_capacity,
                total_weeks=total_weeks,
                days_of_week=days_of_week,
                start_time=parse_time(start_time),
                end_time=parse_time(end_time),
            )
            db.commit()
            move_registration(db, registration_id, new_class.id)
        elif decision == "leave_pending":
            return _redirect(back, notice="Left unassigned for now.")
        else:
            raise ValueError("Unknown assignment decision.")
    except ValueError as exc:
        db.rollback()
        return _redirect(back, error=str(exc))

    db.refresh(registration)
    return _redirect(
        f"/classes/{registration.class_group_id}?registration={registration.id}",
        notice=f"Assigned to {registration.class_group.name}.",
    )


# ------------------------------------------------------------------
# Pages
# ------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def list_classes(request: Request, db: Session = Depends(get_db)):
    sync_class_lifecycle(db)
    now = local_now()
    classes = (
        db.query(ClassGroup)
        .options(joinedload(ClassGroup.program), joinedload(ClassGroup.registrations), joinedload(ClassGroup.sessions))
        .all()
    )
    status_order = {ClassStatus.NOT_STARTED: 0, ClassStatus.ONGOING: 1, ClassStatus.COMPLETED: 2}
    classes.sort(key=lambda item: (status_order.get(item.status, 3), class_start_dt(item) or datetime.max, item.name))
    summaries = {item.id: class_summary(item, now) for item in classes}

    programs = db.query(Program).order_by(Program.name.asc()).all()
    used_program_ids = {
        program_id for (program_id,) in db.query(Registration.program_id).filter(
            Registration.program_id.is_not(None)
        ).distinct().all()
    }
    used_program_ids.update(item.program_id for item in classes if item.program_id)
    used_programs = [program for program in programs if program.id in used_program_ids]
    specialty_suggestions = sorted({
        program.name.strip()
        for program in used_programs
        if program.name and program.name.strip()
    })
    professors = db.query(Professor).filter(Professor.is_available.is_(True)).order_by(Professor.name.asc()).all()
    all_professors = db.query(Professor).order_by(Professor.name.asc()).all()
    pending_registrations = (
        db.query(Registration)
        .options(joinedload(Registration.candidate), joinedload(Registration.program))
        .filter(
            Registration.payment_status == PaymentStatus.PAID,
            Registration.class_group_id.is_(None),
            Registration.status != RegistrationStatus.CANCELLED,
        )
        .order_by(Registration.registration_date.asc())
        .all()
    )
    session_calendar = get_candidate_session_calendar(db)
    settings = get_settings(db)
    # A class is created for the candidates who wait, with a professor of its speciality.
    waiting_counts: dict[str, int] = {}
    for item in pending_registrations:
        if item.program_id and item.status == RegistrationStatus.PENDING:
            waiting_counts[item.program_id] = waiting_counts.get(item.program_id, 0) + 1
    teachers = {
        program.id: [professor.id for professor in professors if teaches(professor, program)]
        for program in programs
    }

    accounts ={account.professor_id: account for account in db.query(User).filter(User.professor_id.isnot(None)).all()}
    professor_cards = []
    for professor in all_professors:
        mine = [item for item in classes if item.professor_id == professor.id and item.status != ClassStatus.COMPLETED]
        hours = 0.0
        for item in mine:
            if item.start_time and item.end_time:
                length = (datetime.combine(date.min, item.end_time) - datetime.combine(date.min, item.start_time)).seconds / 3600
                hours += length * max(1, len(parse_days(item.days_of_week)))
        professor_cards.append({
            "professor": professor,
            "specialties": [item.strip() for item in (professor.specialties or "").split(",") if item.strip()],
            "classes": mine,
            "running": sum(1 for item in mine if item.status == ClassStatus.ONGOING),
            "candidates": sum(summaries[item.id]["enrolled"] for item in mine),
            "hours": round(hours, 1),
            "account": accounts.get(professor.id),
            "reviews": __import__("app.services.program_feedback", fromlist=["of_professor"]).of_professor(db, professor.name),
            "absences": sorted(
                (item for item in professor.absences if item.absence_date >= now.date()), key=lambda item: item.absence_date,
            )[:3],
        })

    return templates.TemplateResponse(
        request=request,
        name="classes_dashboard.html",
        context={
            "classes": classes,
            "summaries": summaries,
            "looks": {item.id: class_looks.look(item) for item in classes},
            "professor_cards": professor_cards,
            "min_password": auth.MIN_PASSWORD,
            "programs": programs,
            "active_page": "classes",
            "pending_registrations": pending_registrations,
            "professors": professors,
            "all_professors": all_professors,
            "waiting_counts": waiting_counts,
            "teachers": teachers,
            "coverage": professors_coverage(db, programs, all_professors),
            "resource_plan": __import__("app.services.resource_reminders", fromlist=["settings_of"]).settings_of(db),
            "resource_due": __import__("app.services.resource_reminders", fromlist=["due"]).due(db),
            "resource_recent": __import__("app.services.resource_reminders", fromlist=["recent"]).recent(db),

            "today": date.today(),
            "specialty_suggestions": specialty_suggestions,
            "used_programs": used_programs,
            "session_calendar": session_calendar,
            "settings": settings,
            "counts": {
                "not_started": sum(1 for item in classes if item.status == ClassStatus.NOT_STARTED),
                "ongoing": sum(1 for item in classes if item.status == ClassStatus.ONGOING),
                "completed": sum(1 for item in classes if item.status == ClassStatus.COMPLETED),
                "unstaffed": sum(1 for item in classes if not item.professor_name and item.status != ClassStatus.COMPLETED),
                "on_hold": sum(1 for item in classes if summaries[item.id]["on_hold"]),
            },
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        }
    )


@router.get("/{class_id}", response_class=HTMLResponse)
def class_detail(class_id: str, request: Request, db: Session = Depends(get_db)):
    """One class: its parameters, candidates, reminders and weekly plan."""
    sync_class_lifecycle(db)
    class_group = _get_class(db, class_id)
    now = local_now()
    summary = class_summary(class_group, now)

    registrations = class_members(db, class_group.id)
    reminder_plan = reminders_by_registration(db, [item.id for item in registrations], now)

    members = [
        {
            "registration": item,
            "candidate": item.candidate,
            "name": (item.candidate.full_name or item.candidate.email) if item.candidate else "Candidate",
            "reminders": reminder_plan.get(item.id, []),
            "last_payment": max((payment.payment_date for payment in item.payments if payment.payment_date), default=None),
        }
        for item in registrations
    ]
    focus_id = request.query_params.get("registration")
    focus = next((member for member in members if member["registration"].id == focus_id), None)

    other_classes = move_options(db, class_group.program_id, class_group.id, now)

    professors = _professor_choices(db, class_group)
    sessions = sorted(class_group.sessions, key=lambda item: (item.session_date, item.week_number or 0))
    for session in sessions:
        session.session_status = get_session_status(session)

    offsets = parse_reminder_days(class_group.reminder_days)
    # What the class needs before it can start (or, once running, what is not in order).
    needs = []
    program_name = class_group.program.name if class_group.program else "its program"
    if class_group.status != ClassStatus.COMPLETED:
        if not summary["enrolled"]:
            needs.append({
                "icon": "bi-people-fill", "title": "Nobody is in this class",
                "text": "A class runs only with candidates in it, so this one does not start. Move a paid candidate "
                        "into it, or remove it: an empty class can be removed at any time.",
                "remove": True,
            })
        professor = class_group.professor
        if professor is None and not class_group.professor_name:
            needs.append({
                "icon": "bi-person-exclamation", "title": "No professor yet",
                "text": f"Choose one in the parameters. Only professors who have {program_name} among their specialities are listed"
                        + ("." if len(professors) else ": today nobody has it, so add it to a professor first."),
                "link": {"label": "Choose a professor", "url": "#parameters"} if professors else
                        {"label": "Open the professors", "url": "/classes/?tab=professors"},
            })
        elif professor is not None and class_group.program is not None and not teaches(professor, class_group.program):
            needs.append({
                "icon": "bi-mortarboard-fill", "title": f"{program_name} is not one of {professor.name}'s specialities",
                "text": "Only a professor of the speciality teaches a class. If they do teach it, add it to their profile; "
                        "otherwise choose another professor in the parameters.",
                "add": {"professor_id": professor.id, "program_id": class_group.program_id,
                        "label": f"Yes, {professor.name} teaches it"},
            })
    attention = []
    if class_group.status != ClassStatus.COMPLETED:
        if not class_group.start_time:
            attention.append("No session time is set, so emails and reminders cannot tell candidates what time to attend.")
        if class_group.start_mode == START_MIN_ENROLLMENT and not summary["start"]:
            attention.append(
                f"Waiting for {max(0, class_group.min_students_to_start - summary['enrolled'])} more candidate(s) "
                f"before a start date is set ({summary['enrolled']}/{class_group.min_students_to_start})."
            )
        if not class_emails.smtp_configured():
            attention.append("Email is not configured: candidates are not receiving class emails or reminders. See Settings.")

    created_local = utc_to_local(class_group.created_at)
    return templates.TemplateResponse(
        request=request,
        name="class_detail.html",
        context={
            "active_page": "classes",
            "cls": class_group,
            "summary": summary,
            "members": members,
            "focus": focus,
            "other_classes": other_classes,
            "professors": professors,
            "sessions": sessions,
            "reminder_offsets": offsets,
            "attention": attention,
            "needs": needs,
            "levels": level_check.class_overview(db, class_group),
            "opinions": program_feedback.of_class(db, class_group.id),
            "day_options": list(zip(DAY_KEYS, DAY_SHORT_EN)),
            "selected_days": [DAY_KEYS[index] for index in parse_days(class_group.days_of_week)],
            "course": weekly_questions.class_overview(db, class_group),
            "presence": attendance.class_summary(db, class_group),
            "dynamics": __import__("app.services.class_dynamics", fromlist=["of_class"]).of_class(db, class_group),
            "look": class_looks.look(class_group),
            "symbols": class_looks.SYMBOLS,
            "colours": class_looks.COLOURS,
            "back": "",
            "created_local": created_local,
            "now": now,
            "modes": {"after_days": START_AFTER_DAYS, "min_enrollment": START_MIN_ENROLLMENT, "fixed": START_FIXED_DATE},
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        }
    )


# ------------------------------------------------------------------
# Weekly plan
# ------------------------------------------------------------------

@router.post("/{class_id}/sessions")
def add_class_session(
    class_id: str,
    week_number: int = Form(...),
    session_date: str = Form(...),
    topic: str = Form(...),
    start_time: str = Form(""),
    end_time: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    class_group = _get_class(db, class_id)
    session = ClassSession(
        class_group_id=class_id,
        week_number=week_number,
        session_date=date.fromisoformat(session_date),
        topic=topic.strip(),
        start_time=parse_time(start_time) or class_group.start_time,
        end_time=parse_time(end_time) or class_group.end_time,
        notes=notes.strip() or None,
    )
    db.add(session)
    db.commit()
    return _redirect(f"/classes/{class_id}#plan", notice="Session added.")


@router.post("/{class_id}/sessions/{session_id}/edit")
def edit_class_session(
    class_id: str,
    session_id: str,
    session_date: str = Form(...),
    week_number: int = Form(...),
    topic: str = Form(...),
    start_time: str = Form(""),
    end_time: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    session = db.query(ClassSession).filter(
        ClassSession.id == session_id,
        ClassSession.class_group_id == class_id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.week_number = week_number
    session.session_date = date.fromisoformat(session_date)
    session.topic = topic.strip()
    session.start_time = parse_time(start_time)
    session.end_time = parse_time(end_time)
    session.notes = notes.strip() or None
    db.commit()
    return _redirect(f"/classes/{class_id}#plan", notice="Session saved.")


@router.post("/{class_id}/sessions/{session_id}/delete")
def delete_class_session(class_id: str, session_id: str, db: Session = Depends(get_db)):
    session = db.query(ClassSession).filter(
        ClassSession.id == session_id,
        ClassSession.class_group_id == class_id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(session)
    db.commit()
    return _redirect(f"/classes/{class_id}#plan", notice="Session deleted.")


# ------------------------------------------------------------------
# Members
# ------------------------------------------------------------------

@router.post("/{class_id}/members/{registration_id}/remove")
def remove_class_member(class_id: str, registration_id: str, db: Session = Depends(get_db)):
    registration = db.query(Registration).filter(
        Registration.id == registration_id,
        Registration.class_group_id == class_id,
    ).first()
    if not registration:
        raise HTTPException(status_code=404, detail="Class member not found")
    name = registration.candidate.full_name or registration.candidate.email
    unassign_registration(db, registration)
    return _redirect(f"/classes/{class_id}", notice=f"{name} removed from the class.")


@router.post("/{class_id}/members/{registration_id}/remind")
def remind_class_member(class_id: str, registration_id: str, db: Session = Depends(get_db)):
    """Send this candidate the session reminder right now."""
    registration = db.query(Registration).filter(
        Registration.id == registration_id,
        Registration.class_group_id == class_id,
    ).first()
    if not registration:
        raise HTTPException(status_code=404, detail="Class member not found")
    if not class_start_dt(registration.class_group):
        return _redirect(f"/classes/{class_id}", error="This class has no start date yet, so there is nothing to remind about.")
    name = registration.candidate.full_name or registration.candidate.email
    if send_reminder_now(db, registration):
        return _redirect(f"/classes/{class_id}", notice=f"Reminder sent to {name}.")
    return _redirect(f"/classes/{class_id}", error=f"The reminder to {name} could not be delivered. Check the email settings.")

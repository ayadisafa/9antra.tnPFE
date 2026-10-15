from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.models.activity import Activity
from app.models.candidate import Candidate
from app.models.program import Program
from app.models.registration import Registration, RegistrationStatus, PaymentStatus
from app.models.classgroup import ClassGroup
from app.models.payment import Payment
from app.models.notification import Notification
from app.models.quiz import Quiz
from app.models.candidate_reminder import CandidateReminder
from app.models.candidate_email import CandidateEmail
from app.services import (
    advisor, attendance, behaviour, email_templates, email_threads, level_check, payment_reminders, program_icons, week_insights,
    weekly_questions, whatsapp,
)
from app.services.email_service import send_email
from app.services.jobs import (
    settle_registration_payment,
    purge_candidate_registrations,
)
from app.services.background import run_in_background
from app.services.class_views import class_summary, move_options, reminders_by_registration, upcoming_sessions
from app.services.enrollment_service import enroll_paid_registration, move_registration
from app.services.schedule_utils import class_start_dt, local_now, utc_to_local

router = APIRouter(prefix="/candidate", tags=["Candidates"])
from app.template_context import notification_context

templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _program_price(program, delivery_mode):
    mode = (delivery_mode or "online").lower()
    if mode == "onsite":
        return float(program.price_onsite or 0)
    return float(program.price_online or 0)


def _candidate_metrics(candidate, quizzes):
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    def as_utc(value):
        if not value:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    registrations = sorted(
        candidate.registrations or [],
        key=lambda registration: as_utc(registration.registration_date) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    latest = registrations[0] if registrations else None
    latest_date = as_utc(latest.registration_date) if latest else None
    new_registration = bool(latest_date and latest_date >= week_ago)
    ongoing_registration = next(
        (
            registration for registration in registrations
            if registration.class_group_id
            or _enum_value(registration.status) in {"ongoing", "assigned"}
            or (
                as_utc(registration.training_start_date)
                and as_utc(registration.training_start_date) <= now
                and (
                    not registration.training_end_date
                    or as_utc(registration.training_end_date) >= now
                )
            )
        ),
        None,
    )

    total_due = sum(
        (registration.total_amount or registration.total_price or 0) for registration in registrations
    )
    total_paid = sum(registration.paid_amount or 0 for registration in registrations)
    candidate_quizzes = quizzes.get(candidate.id, [])
    completed_quizzes = [quiz for quiz in candidate_quizzes if quiz.completed_at is not None]
    quiz_scores = [quiz.score for quiz in completed_quizzes if quiz.score is not None]
    progress = candidate.program_progress
    active_registrations = [
        registration for registration in registrations
        if _enum_value(registration.status) != "cancelled"
    ]
    completed_registrations = [
        registration for registration in active_registrations
        if _enum_value(registration.status) == "completed"
    ]
    paid_registrations = [
        registration for registration in active_registrations
        if _enum_value(registration.payment_status) == "paid"
    ]
    loyalty_score = min(
        100,
        round(
            len(active_registrations) * 12
            + len(completed_registrations) * 18
            + len(paid_registrations) * 10
            + (progress.engagement_score or 0) * 4 if progress else
            len(active_registrations) * 12
            + len(completed_registrations) * 18
            + len(paid_registrations) * 10,
            1,
        ),
    )
    loyalty_label = (
        "Highly loyal" if loyalty_score >= 75
        else "Loyal" if loyalty_score >= 50
        else "Growing relationship" if loyalty_score >= 25
        else "New relationship"
    )
    feedback_count = sum(
        1 for activity in (candidate.activities or [])
        if "FEEDBACK" in (activity.event_type or "").upper()
    )
    return {
        "latest_registration": latest,
        "new_registration": new_registration,
        "ongoing_registration": ongoing_registration,
        "status_label": "Ongoing" if ongoing_registration else (
            "New registration" if new_registration else (
                "Completed" if latest and _enum_value(latest.status) == "completed" else "Pending"
            )
        ),
        "total_due": total_due,
        "total_paid": total_paid,
        "remaining_due": max(0, total_due - total_paid),
        "payment_rate": round((total_paid / total_due) * 100, 1) if total_due else 0,
        "quiz_count": len(candidate_quizzes),
        "completed_quiz_count": len(completed_quizzes),
        "quiz_average": round(sum(quiz_scores) / len(quiz_scores), 1) if quiz_scores else None,
        "feedback_count": feedback_count,
        "analysis_available": bool(progress),
        "engagement_score": round(progress.engagement_score, 1) if progress and progress.engagement_score is not None else None,
        "engagement_level": _enum_value(progress.engagement_level) if progress else None,
        "completion_percentage": round(progress.completion_percentage or 0, 1) if progress else 0,
        "registration_count": len(active_registrations),
        "completed_program_count": len(completed_registrations),
        "loyalty_score": loyalty_score,
        "loyalty_label": loyalty_label,
        "attendance_rate": (
            round(progress.attendance_rate, 1)
            if progress and progress.attendance_rate is not None
            else None
        ),
        "quiz_completion_rate": (
            round(len(completed_quizzes) / len(candidate_quizzes) * 100, 1)
            if candidate_quizzes else 0
        ),
    }


# --- LIST ALL CANDIDATES ---
def _weekly_scores(db: Session, candidate_ids: list[str]) -> dict[str, dict]:
    """Per candidate: the average of their answered weekly questions, and its level."""
    scores: dict[str, list[float]] = {}
    if candidate_ids:
        rows = db.query(Quiz.candidate_id, Quiz.score).filter(
            Quiz.kind == "weekly", Quiz.completed_at.isnot(None), Quiz.candidate_id.in_(candidate_ids),
        ).all()
        for candidate_id, score in rows:
            scores.setdefault(candidate_id, []).append(score or 0.0)
    found = {}
    for candidate_id, values in scores.items():
        average = round(sum(values) / len(values), 1)
        found[candidate_id] = {"average": average, "level": weekly_questions.level_of(average), "answers": len(values)}
    return found


@router.get("/all")
def list_candidates(
    request: Request,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    candidates = (
        db.query(Candidate)
        .options(
            joinedload(Candidate.registrations).joinedload(Registration.program),
            joinedload(Candidate.registrations).joinedload(Registration.class_group),
        )
        .all()
    )
    next_steps = advisor.for_candidates(db, candidates)
    understanding = _weekly_scores(db, [candidate.id for candidate in candidates])
    week_ago = datetime.utcnow() - timedelta(days=7)

    rows = []
    for candidate in candidates:
        active = [item for item in candidate.registrations if item.status != RegistrationStatus.CANCELLED]
        active.sort(key=lambda item: item.registration_date or datetime.min, reverse=True)
        total = sum((item.total_amount or item.total_price or 0) for item in active)
        paid = sum(item.paid_amount or 0 for item in active)
        latest = active[0].registration_date if active else candidate.created_at
        programs, seen = [], set()
        for item in active:
            name = item.program.name if item.program else (item.program_name or "Program")
            if name in seen:
                continue
            seen.add(name)
            programs.append({
                "name": name,
                "icon": program_icons.view(item.program) if item.program else None,
                "paid": item.payment_status == PaymentStatus.PAID,
            })
        classes = list(dict.fromkeys(item.class_group.name for item in active if item.class_group))
        advice = next_steps.get(candidate.id)
        rows.append({
            "candidate": candidate,
            "name": candidate.full_name or candidate.email,
            "programs": programs,
            "classes": classes,
            "total": total,
            "paid": paid,
            "due": max(0.0, total - paid),
            "paid_pct": round(paid / total * 100) if total else 0,
            "advice": advice,
            "needs_you": advice is not None and advice.tone in ("urgent", "important") and not advice.set_aside,
            "understanding": understanding.get(candidate.id),
            "registered": utc_to_local(latest),
            "is_new": bool(latest and latest.replace(tzinfo=None) >= week_ago),
            "in_class": bool(classes),
        })
    # What needs the admin first, then the most recent registrations.
    rows.sort(key=lambda row: (
        -(row["advice"].priority if row["needs_you"] else 0),
        -(row["registered"].timestamp() if row["registered"] else 0),
    ))

    return templates.TemplateResponse(
        name="candidates.html",
        request=request,
        context={
            "active_page": "candidates",
            "rows": rows,
            "totals": {
                "candidates": len(rows),
                "needs_you": sum(1 for row in rows if row["needs_you"]),
                "owing": sum(1 for row in rows if row["due"] > 0),
                "due": sum(row["due"] for row in rows),
                "in_class": sum(1 for row in rows if row["in_class"]),
                "new": sum(1 for row in rows if row["is_new"]),
            },
            "search": (search or "").strip(),
            "show": request.query_params.get("show") or "all",
            "add_open": request.query_params.get("add") == "1",
            "all_programs": db.query(Program).order_by(Program.name.asc()).all(),
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/create")
def create_candidate(
    full_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    university: str = Form(""),
    city: str = Form(""),
    program_id: str = Form(""),
    delivery_mode: str = Form("online"),
    db: Session = Depends(get_db),
):
    """Add a candidate by hand, optionally with a first program."""
    name, address = " ".join(full_name.split()), email.strip().lower()
    if not name or "@" not in address or "." not in address.rsplit("@", 1)[-1]:
        return RedirectResponse(
            f"/candidate/all?{urlencode({'add': '1', 'error': 'Enter the name and a valid email address.'})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    existing = db.query(Candidate).filter(Candidate.email == address).first()
    if existing is not None:
        notice = f"{existing.full_name or address} is already a candidate; here is their file."
        return RedirectResponse(
            f"/candidate/{existing.id}?{urlencode({'notice': notice})}", status_code=status.HTTP_303_SEE_OTHER,
        )

    candidate = Candidate(
        full_name=name, email=address, phone=phone.strip() or None,
        university=university.strip() or None, city=city.strip() or None,
    )
    db.add(candidate)
    db.flush()
    program = db.get(Program, program_id) if program_id else None
    if program is not None:
        mode = delivery_mode if delivery_mode in ("online", "onsite") else "online"
        price = _program_price(program, mode)
        db.add(Registration(
            candidate_id=candidate.id, program_id=program.id, program_name=program.name,
            delivery_mode=mode, total_price=price, total_amount=price,
            status=RegistrationStatus.PENDING, payment_status=PaymentStatus.UNPAID,
        ))
    db.commit()
    notice = f"{name} added" + (f" and registered to {program.name}." if program is not None else ".")
    return RedirectResponse(
        f"/candidate/{candidate.id}?{urlencode({'notice': notice})}", status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/registrations/{registration_id}/program")
def choose_registration_program(registration_id: str, program_id: str = Form(""), db: Session = Depends(get_db)):
    """Say which program a registration is for, when its name was not recognised."""
    from app.services import program_names

    registration, program = db.get(Registration, registration_id), db.get(Program, program_id)
    if registration is None:
        raise HTTPException(status_code=404, detail="Registration not found")
    if program is None:
        return RedirectResponse(f"/candidate/{registration.candidate_id}?{urlencode({'error': 'Choose the program.'})}", status_code=status.HTTP_303_SEE_OTHER)
    program_names.link(db, registration, program)
    db.commit()
    notice = f"This registration is now for {program.name}, at {registration.total_amount:.0f} DT."
    return RedirectResponse(f"/candidate/{registration.candidate_id}?{urlencode({'notice': notice})}#registration-{registration.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/requests/{notification_id}/accept")
def accept_request(notification_id: str, db: Session = Depends(get_db)):
    """Somebody who wrote to ask for a place: add them and register them to that program."""
    from app.models.notification import Notification
    from app.services import registration_requests

    notification = db.get(Notification, notification_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="Request not found")
    candidate, registration, problem = registration_requests.accept(db, notification)
    if problem:
        return RedirectResponse(f"/candidate/all?{urlencode({'error': problem})}", status_code=status.HTTP_303_SEE_OTHER)
    notice = f"{candidate.full_name or candidate.email} is now a candidate, registered to {registration.program_name}. Check their name and phone."
    return RedirectResponse(f"/candidate/{candidate.id}?{urlencode({'notice': notice})}", status_code=status.HTTP_303_SEE_OTHER)


# --- CANDIDATE PROFILE & QUICK ACTIONS VIEW ---
@router.get("/{candidate_id}")
def profile(request: Request, candidate_id: str, db: Session = Depends(get_db)):
    candidate = (
        db.query(Candidate)
        .options(
            joinedload(Candidate.registrations).joinedload(Registration.program),
            joinedload(Candidate.registrations).joinedload(Registration.payments),
            joinedload(Candidate.registrations).joinedload(Registration.class_group),
        )
        .filter(Candidate.id == candidate_id)
        .first()
    )

    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    activities = (
        db.query(Activity)
        .filter(Activity.candidate_id == candidate_id)
        .order_by(Activity.created_at.desc())
        .all()
    )
    quizzes = db.query(Quiz).filter(Quiz.candidate_id == candidate_id).order_by(Quiz.sent_at.desc()).all()
    level_quizzes = [quiz for quiz in quizzes if level_check.is_placement(quiz)]
    by_registration = {registration.id: registration for registration in candidate.registrations}
    level_checks = [
        level_check.row_for(by_registration[quiz.registration_id], quiz)
        for quiz in level_quizzes if quiz.registration_id in by_registration
    ]
    candidate_metrics = _candidate_metrics(candidate, {candidate_id: level_quizzes})
    registration_history = sorted(
        candidate.registrations,
        key=lambda registration: registration.registration_date or datetime.min,
        reverse=True,
    )
    reminders = db.query(CandidateReminder).filter(
        CandidateReminder.candidate_id == candidate_id
    ).order_by(CandidateReminder.planned_for.desc()).all()
    email_conversation = db.query(CandidateEmail).filter(
        CandidateEmail.candidate_id == candidate_id
    ).order_by(CandidateEmail.received_at.desc()).all()

    all_programs = db.query(Program).all()
    class_groups = db.query(ClassGroup).all()

    # Class details per registration: where the candidate studies, when the
    # first session is, which reminders are planned, and where they can move.
    local_time = local_now()
    reminder_plan = reminders_by_registration(db, [reg.id for reg in candidate.registrations], local_time)
    enrollments = {}
    session_agenda = []
    professors_by_id = {}
    for registration in candidate.registrations:
        class_group = registration.class_group
        enrollments[registration.id] = {
            "class": class_group,
            "summary": class_summary(class_group, local_time) if class_group else None,
            "reminders": reminder_plan.get(registration.id, []),
            "options": [
                option for option in move_options(
                    db, registration.program_id, registration.class_group_id, local_time
                )
                if not option["full"]
            ],
        }
        session_agenda.extend(upcoming_sessions(registration, local_time))
        if class_group and class_group.professor and registration.status != RegistrationStatus.CANCELLED:
            entry = professors_by_id.setdefault(class_group.professor.id, {
                "name": class_group.professor.name,
                "specialties": [
                    item.strip() for item in (class_group.professor.specialties or "").split(",") if item.strip()
                ],
                "classes": [],
            })
            entry["classes"].append({"id": class_group.id, "name": class_group.name})
    session_agenda.sort(key=lambda item: (item["date"], str(item["start_time"] or "")))

    # One card per program the candidate signed up for, newest first.
    program_cards = []
    for registration in registration_history:
        if registration.status == RegistrationStatus.CANCELLED:
            continue
        enrollment = enrollments.get(registration.id) or {}
        total = registration.total_amount or registration.total_price or 0
        program_cards.append({
            "registration": registration,
            "name": registration.program.name if registration.program else (registration.program_name or "Program"),
            "icon": program_icons.view(registration.program) if registration.program else None,
            "total": total,
            "paid": registration.paid_amount or 0,
            "due": registration.remaining_amount,
            "paid_pct": round((registration.paid_amount or 0) / total * 100) if total else 0,
            "is_paid": registration.payment_status == PaymentStatus.PAID,
            "class": registration.class_group,
            "class_info": enrollment.get("summary"),
            "reminders": enrollment.get("reminders", []),
            "options": enrollment.get("options", []),
            "registered": utc_to_local(registration.registration_date),
        })

    return templates.TemplateResponse(
        name="candidate_profile.html",
        request=request,
        context={
            "active_page": "candidates",
            "candidate": candidate,
            "name": candidate.full_name or candidate.email,
            "advice": advisor.for_candidate(db, candidate),
            "learning": weekly_questions.candidate_weeks(db, candidate_id),
            "presence": attendance.candidate_summary(db, candidate_id),
            "feelings": week_insights.candidate_feelings(db, candidate_id),
            "whatsapp_link": whatsapp.link(candidate.phone),
            "phone_pretty": whatsapp.pretty(candidate.phone),
            "program_cards": program_cards,
            "cancelled": [item for item in registration_history if item.status == RegistrationStatus.CANCELLED],
            "activities": activities,
            "all_programs": all_programs,
            "session_agenda": session_agenda[:6],
            "class_professors": list(professors_by_id.values()),
            "today": local_time.date(),
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
            "level_checks": level_checks,
            "behaviour": behaviour.profile(db, candidate),
            "candidate_metrics": candidate_metrics,
            "reminders": reminders,
            "email_conversation": email_conversation,
            "emails_waiting": sum(1 for email in email_conversation if email.requires_admin and email.direction == "inbound"),
        },
    )


# --- PAYMENT REMINDER: the admin reads it, changes it if needed, and sends it ---
def _safe_back(back: str | None, fallback: str) -> str:
    back = (back or "").strip()
    return back if back.startswith("/") and not back.startswith("//") else fallback


def _candidate_with_debts(db: Session, candidate_id: str) -> Candidate:
    candidate = (
        db.query(Candidate)
        .options(
            joinedload(Candidate.registrations).joinedload(Registration.program),
            joinedload(Candidate.registrations).joinedload(Registration.class_group),
        )
        .filter(Candidate.id == candidate_id)
        .first()
    )
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@router.get("/{candidate_id}/payment-reminder")
def payment_reminder_page(request: Request, candidate_id: str, db: Session = Depends(get_db)):
    candidate = _candidate_with_debts(db, candidate_id)
    draft = payment_reminders.draft(db, candidate)
    if draft is None:
        return RedirectResponse(
            url=f"/candidate/{candidate_id}?{urlencode({'notice': 'This candidate owes nothing: there is no payment to remind about.'})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    story = payment_reminders.story(db, candidate)
    return templates.TemplateResponse(
        name="payment_reminder.html",
        request=request,
        context={
            "active_page": "candidates",
            "candidate": candidate,
            "name": candidate.full_name or candidate.email,
            "draft": draft,
            "story": story,
            "last_local": utc_to_local(story["last"].received_at) if story["last"] else None,
            "waited_days": (datetime.utcnow() - story["last"].received_at.replace(tzinfo=None)).days if story["last"] else None,
            "smtp_ready": email_threads.smtp_configured(),
            "whatsapp_link": whatsapp.link(candidate.phone, draft["body"]),
            "whatsapp_number": (whatsapp.normalise(candidate.phone) or "+")[1:],
            "whatsapp_pretty": whatsapp.pretty(candidate.phone),
            "whatsapp_auto": whatsapp.configured(),
            "back": _safe_back(request.query_params.get("back"), f"/candidate/{candidate_id}"),
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/{candidate_id}/payment-reminder")
def payment_reminder_send(
    candidate_id: str,
    subject: str = Form(""),
    body: str = Form(""),
    back: str = Form(""),
    db: Session = Depends(get_db),
):
    candidate = _candidate_with_debts(db, candidate_id)
    here = f"/candidate/{candidate_id}/payment-reminder"
    if not (candidate.email or "").strip():
        return RedirectResponse(f"{here}?{urlencode({'error': 'This candidate has no email address.'})}", status_code=303)
    if not subject.strip() or not body.strip():
        return RedirectResponse(f"{here}?{urlencode({'error': 'The reminder needs a subject and a message.'})}", status_code=303)

    message = payment_reminders.send(db, candidate, subject=subject, body=body, manual=True)
    db.commit()
    target = _safe_back(back, f"/candidate/{candidate_id}")
    joiner = "&" if "?" in target else "?"
    name = candidate.full_name or candidate.email
    if message is None:
        return RedirectResponse(f"{target}{joiner}{urlencode({'notice': f'{name} owes nothing any more: no reminder was sent.'})}", status_code=303)
    if not email_templates.delivered(message):
        problem = (
            "The reminder is saved in the candidate's discussion but was not delivered. "
            "Check the email settings, then use “Send again” in the Inbox."
        )
        return RedirectResponse(f"{target}{joiner}{urlencode({'error': problem})}", status_code=303)
    return RedirectResponse(
        f"{target}{joiner}{urlencode({'notice': f'Payment reminder sent to {name}. Nothing else goes out by itself.'})}",
        status_code=303,
    )


@router.post("/{candidate_id}/send-manual-reminder")
def send_manual_reminder(candidate_id: str):
    """Older buttons land on the page where the reminder is read before it goes."""
    return RedirectResponse(url=f"/candidate/{candidate_id}/payment-reminder", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{candidate_id}/recommendations/send-now")
def recommendation_send_now(candidate_id: str,
                             registration_id: Optional[str] = Form(None),
                             reminder_id: Optional[str] = Form(None),
                             db: Session = Depends(get_db)):
    """Send the recommended reminder immediately."""
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    reminder = None
    reg = None
    if reminder_id:
        reminder = db.query(CandidateReminder).filter(CandidateReminder.id == reminder_id).first()
        if not reminder:
            raise HTTPException(status_code=404, detail="Reminder not found")
        reg = db.query(Registration).filter(Registration.id == reminder.registration_id).first()
    elif registration_id:
        reg = db.query(Registration).filter(Registration.id == registration_id).first()
        if not reg:
            raise HTTPException(status_code=404, detail="Registration not found")
        # create a short-lived reminder object to record the send
        reminder = CandidateReminder(
            candidate_id=candidate_id,
            registration_id=reg.id,
            reminder_key=f"manual_{reg.id}_{int(datetime.utcnow().timestamp())}",
            reminder_type="payment" if getattr(reg, 'payment_status', None) and getattr(reg.payment_status, 'value', '').upper() in ('UNPAID','PARTIAL') else "training",
            planned_for=datetime.utcnow(),
            strategy="Admin requested immediate send from recommendations.",
        )
        db.add(reminder)
        db.flush()

    if reminder is None or reg is None:
        raise HTTPException(status_code=400, detail="Choose the registration to remind.")
    if reminder.reminder_type != 'training':
        # A payment reminder is read by the admin before it goes (one email for everything owed).
        db.rollback()
        return RedirectResponse(url=f"/candidate/{candidate_id}/payment-reminder", status_code=status.HTTP_303_SEE_OTHER)

    # The email is the template of that kind of reminder (see Email templates),
    # and it is recorded in the candidate's discussions.
    if reminder.reminder_type == 'training':
        if not reg.class_group or not class_start_dt(reg.class_group):
            db.rollback()
            problem = "This registration has no class with a start date yet, so there is no session to remind about."
            return RedirectResponse(
                url=f"/candidate/{candidate_id}?{urlencode({'error': problem})}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        message = email_templates.send(db, "training_reminder", registration=reg, manual=True)
    else:
        message = email_templates.send(db, "payment_reminder", registration=reg, manual=True)

    if not email_templates.delivered(message):
        # Keep the failed email in the discussion; the reminder itself stays planned.
        if not reminder_id:
            db.delete(reminder)
        db.commit()
        problem = (
            "The reminder is saved in the candidate's discussion but was not delivered. "
            "Check the email settings, then use “Send again” in the Inbox."
            if message is not None else "The reminder could not be written for this registration."
        )
        return RedirectResponse(
            url=f"/candidate/{candidate_id}?{urlencode({'error': problem})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # mark reminder as sent
    reminder.sent_at = datetime.utcnow()
    reminder.status = 'sent'
    activity = Activity(candidate_id=candidate_id, event_type='REMINDER_SENT', details=f"Sent reminder {reminder.id}")
    db.add(activity)
    db.commit()

    return RedirectResponse(url=f"/candidate/{candidate_id}", status_code=status.HTTP_303_SEE_OTHER)


# --- QUICK ACTION 2: EDIT CANDIDATE DETAILS ---
@router.post("/{candidate_id}/edit")
def edit_candidate(
    candidate_id: str,
    full_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    university: Optional[str] = Form(None),
    city: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    address = email.strip().lower()
    taken = db.query(Candidate).filter(Candidate.email == address, Candidate.id != candidate_id).first()
    if "@" not in address or taken is not None:
        problem = "Another candidate already uses this email address." if taken else "Enter a valid email address."
        return RedirectResponse(
            url=f"/candidate/{candidate_id}?{urlencode({'error': problem})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    candidate.full_name = " ".join(full_name.split())
    candidate.email = address
    candidate.phone = phone.strip() or None
    candidate.university = (university or "").strip() or None
    candidate.city = (city or "").strip() or None

    db.commit()
    return RedirectResponse(
        url=f"/candidate/{candidate_id}?{urlencode({'notice': 'Details saved.'})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{candidate_id}/notes")
def save_candidate_notes(candidate_id: str, admin_notes: str = Form(""), db: Session = Depends(get_db)):
    """The admin's private notes about a candidate."""
    candidate = db.get(Candidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    candidate.admin_notes = admin_notes.strip()[:5000] or None
    db.commit()
    return RedirectResponse(
        url=f"/candidate/{candidate_id}?{urlencode({'notice': 'Notes saved.'})}#notes",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{candidate_id}/registrations")
def add_registration(
    candidate_id: str,
    program_id: str = Form(...),
    delivery_mode: str = Form("online"),
    db: Session = Depends(get_db),
):
    candidate = db.get(Candidate, candidate_id)
    program = db.get(Program, program_id)
    if not candidate or not program:
        raise HTTPException(status_code=404, detail="Candidate or program not found")
    active = db.query(Registration).filter(
        Registration.candidate_id == candidate_id, Registration.program_id == program_id,
        Registration.status.notin_([RegistrationStatus.CANCELLED, RegistrationStatus.COMPLETED])
    ).first()
    if active:
        problem = f"{candidate.full_name or candidate.email} is already registered to {program.name}."
        return RedirectResponse(
            url=f"/candidate/{candidate_id}?{urlencode({'error': problem})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    reg = Registration(
        candidate_id=candidate_id, program_id=program_id, program_name=program.name,
        delivery_mode=delivery_mode, total_price=_program_price(program, delivery_mode),
        total_amount=_program_price(program, delivery_mode), status=RegistrationStatus.PENDING,
        payment_status=PaymentStatus.UNPAID,
    )
    db.add(reg)
    db.add(Notification(
        recipient_role="admin", category="REGISTRATION", title="Registration pending",
        message=f"{candidate.full_name or candidate.email} added to {program.name}.",
        action_url=f"/candidate/{candidate_id}", candidate_id=candidate_id, program_id=program_id,
    ))
    db.commit()
    return RedirectResponse(url=f"/candidate/{candidate_id}", status_code=status.HTTP_303_SEE_OTHER)


# --- QUICK ACTION 3: CONFIRM PAYMENT & ASSIGN CLASS GROUP ---
@router.post("/registrations/{registration_id}/confirm-payment")
def confirm_payment_and_assign(
    registration_id: str,
    amount_paid: float = Form(...),
    payment_status: str = Form("paid"),
    class_group_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    try:
        # Settle the payment. A full payment enrolls the candidate in a class
        # right away and notifies the admin (see payment_service).
        reg = settle_registration_payment(db, registration_id, payment_status, amount_paid)

        # An explicitly chosen class overrides the automatic one.
        if class_group_id:
            reg = move_registration(db, registration_id, class_group_id)

        return RedirectResponse(
            url=f"/candidate/{reg.candidate_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@router.post("/registrations/{registration_id}/assign")
def assign_registration(
    registration_id: str,
    class_group_id: str = Form(...),
    db: Session = Depends(get_db),
):
    """Assign a paid registration to a class, or move it to another one."""
    registration = db.get(Registration, registration_id)
    if not registration:
        raise HTTPException(status_code=404, detail="Registration not found")
    profile_url = f"/candidate/{registration.candidate_id}"
    try:
        registration = move_registration(db, registration_id, class_group_id)
    except ValueError as err:
        db.rollback()
        return RedirectResponse(
            url=f"{profile_url}?{urlencode({'error': str(err)})}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    notice = f"Now in {registration.class_group.name}."
    return RedirectResponse(
        url=f"{profile_url}?{urlencode({'notice': notice})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# --- QUICK ACTION 4: SEND CUSTOM EMAIL ---
@router.post("/{candidate_id}/send-email")
def send_custom_email(
    candidate_id: str,
    subject: str = Form(...),
    body: str = Form(...),
    db: Session = Depends(get_db),
):
    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not candidate or not candidate.email:
        raise HTTPException(status_code=404, detail="Candidate or candidate email not found")

    try:
        send_email(candidate.email, subject, body)
        
        activity_log = Activity(
            candidate_id=candidate.id,
            event_type="CUSTOM_EMAIL_SENT",
            details=f"Email envoyé. Objet: '{subject}'"
        )
        db.add(activity_log)
        db.commit()

        return RedirectResponse(
            url=f"/candidate/{candidate_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to send email: {str(e)}")


# --- PURGE CANDIDATE REGISTRATIONS ---
@router.delete("/{candidate_id}/purge-registrations")
def purge_registrations_endpoint(candidate_id: str, db: Session = Depends(get_db)):
    try:
        count = purge_candidate_registrations(db, candidate_id)
        return JSONResponse(content={"message": f"Deleted {count} prior registrations."})
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))


# --- REGISTER NEW CANDIDATE VIA FIREBASE ---
@router.post("/register")
def register_candidate_with_firebase_id(
    firebase_id: str,
    full_name: str,
    email: str,
    phone: str,
    university: str = None,
    city: str = None,
    program_id: int = None,
    paid_amount: float = 0.0,
    total_price: float = None,
    delivery_mode: str = "online",
    db: Session = Depends(get_db)
):
    existing_registration = (
        db.query(Registration)
        .filter(Registration.firebase_id == firebase_id)
        .first()
    )

    if existing_registration:
        return {
            "status": "already_exists",
            "message": f"Registration {firebase_id} is already in the database.",
            "registration_id": existing_registration.id,
        }

    candidate = db.query(Candidate).filter(Candidate.email == email).first()
    if not candidate:
        candidate = Candidate(
            full_name=full_name,
            email=email,
            phone=phone,
            university=university,
            city=city,
        )
        db.add(candidate)
        db.flush()

    program = db.get(Program, str(program_id)) if program_id else None
    if not program:
        raise HTTPException(status_code=400, detail="A valid program is required.")
    total_price = _program_price(program, delivery_mode)
    duplicate = db.query(Registration).filter(
        Registration.candidate_id == candidate.id, Registration.program_id == program.id,
        Registration.status.notin_([RegistrationStatus.CANCELLED, RegistrationStatus.COMPLETED])
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="Candidate already has an active registration for this program.")
    if paid_amount < 0 or paid_amount > total_price:
        raise HTTPException(status_code=400, detail="Invalid payment amount.")
    payment_status = PaymentStatus.PAID if paid_amount >= total_price else (PaymentStatus.PARTIAL if paid_amount > 0 else PaymentStatus.UNPAID)

    new_registration = Registration(
        firebase_id=firebase_id,
        candidate_id=candidate.id,
        program_id=program_id,
        status=RegistrationStatus.PENDING,
        payment_status=payment_status,
        total_price=total_price,
        paid_amount=paid_amount,
        delivery_mode=delivery_mode,
        program_name=program.name,
    )

    db.add(new_registration)
    db.flush()
    db.add(Notification(
        recipient_role="admin",
        category="NEW_REGISTRATION",
        title=(
            f"{candidate.full_name or candidate.email} added another registration"
            if db.query(Registration).filter(
                Registration.candidate_id == candidate.id,
                Registration.id != new_registration.id,
            ).first()
            else f"New candidate registration: {candidate.full_name or candidate.email}"
        ),
        message=(
            f"{candidate.full_name or candidate.email} registered for {program.name}. "
            "Review the candidate profile and registration history."
        ),
        action_url=f"/candidate/{candidate.id}",
        candidate_id=candidate.id,
        program_id=program.id,
        registration_id=new_registration.id,
        payload={"registration_id": new_registration.id},
    ))
    if paid_amount:
        db.add(Payment(registration_id=new_registration.id, amount=paid_amount, payment_method="initial"))
    db.commit()
    db.refresh(new_registration)
    if payment_status == PaymentStatus.PAID:
        # Already paid on registration: enroll in a class immediately.
        enroll_paid_registration(db, new_registration)
        run_in_background(level_check.after_payment_task, new_registration.id)

    return {
        "status": "created",
        "message": f"Successfully registered candidate with ID {firebase_id}",
        "registration_id": new_registration.id,
    }
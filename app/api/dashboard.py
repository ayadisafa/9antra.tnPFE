from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta, timezone
from datetime import date, time
from urllib.parse import quote
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.models.candidate import Candidate
from app.models.program import Program
from app.models.professor import Professor
from app.models.registration import Registration, RegistrationStatus
from app.services import advisor, feedback, guides, overview, payment_reminders, website_sync, whatsapp
from app.services import auth as auth_service
from app.services.class_service import get_calendar_events
from app.services import reminders
from app.services.schedule_utils import local_now, utc_to_local

# Safe imports for optional models
from app.models.activity import Activity as ActivityLog

try:
    from app.models.notification import Notification
except (ImportError, AttributeError):
    Notification = None

router = APIRouter(tags=["Dashboard"])
from app.template_context import notification_context

templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- LIVE CLASS TRACKER (stickers / week-stamp computation) ---
#
# Reuses `get_calendar_events`, the same service call that already powers
# the FullCalendar scheduler — its events already carry professor, enrolled,
# capacity, current_week, total_weeks and status per class. Building the
# sticker tracker from this one source keeps both views perfectly in sync
# instead of recomputing progress a second, possibly-divergent way.

STICKER_BY_STATUS = {
    "ongoing": "🔥",
    "not_started": "🚀",
    "completed": "🏆",
}


@router.get("/api/dashboard/classes/live")
def live_classes(db: Session = Depends(get_db)):
    """Powers the 'Live Cohorts' sticker tracker on the dashboard."""
    raw_events = get_calendar_events(db)

    seen_ids = set()
    classes = []
    for event in raw_events:
        class_id = event.get("id")
        if class_id in seen_ids:
            continue
        seen_ids.add(class_id)

        status = event.get("status") or "not_started"
        current_week = event.get("current_week")
        total_weeks = event.get("total_weeks")

        weeks = []
        if current_week and total_weeks:
            weeks = [
                {
                    "week": w,
                    "state": (
                        "done" if status == "completed" or w < current_week
                        else "active" if w == current_week and status == "ongoing"
                        else "upcoming"
                    ),
                }
                for w in range(1, total_weeks + 1)
            ]

        start_date = event.get("start_date") or event.get("start")
        is_launch_day = bool(start_date) and str(start_date)[:10] == date.today().isoformat()
        is_final_week = status == "ongoing" and current_week and total_weeks and current_week == total_weeks

        classes.append({
            "id": class_id,
            "name": event.get("title"),
            "professor": event.get("professor") or "Professor TBD",
            "enrolled": event.get("enrolled", 0),
            "capacity": event.get("capacity"),
            "status": status,
            "sticker": STICKER_BY_STATUS.get(status, "📘"),
            "current_week": current_week,
            "total_weeks": total_weeks,
            "is_launch_day": is_launch_day,
            "is_final_week": bool(is_final_week),
            "weeks": weeks,
            "url": event.get("url"),
        })

    # Active first, then upcoming, then completed
    order = {"ongoing": 0, "not_started": 1, "completed": 2}
    classes.sort(key=lambda c: (order.get(c["status"], 3), c["name"] or ""))

    return {
        "classes": classes,
        "summary": {
            "active": sum(1 for c in classes if c["status"] == "ongoing"),
            "upcoming": sum(1 for c in classes if c["status"] == "not_started"),
            "completed": sum(1 for c in classes if c["status"] == "completed"),
        },
    }


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    """What needs the admin first, then where money, classes and candidates stand."""
    now_utc, now_local = datetime.utcnow(), local_now()
    advice = advisor.for_dashboard(db)
    return templates.TemplateResponse(
        name="dashboard.html",
        request=request,
        context={
            "active_page": "dashboard",
            "now": now_local,
            "advice": advice,
            "advice_summary": advisor.summary(advice),
            "figures": overview.figures(db, now_utc),
            "sessions": overview.coming_sessions(db, now_local),
            "classes": overview.classes_in_progress(db, now_local),
            "registrations": overview.newest_registrations(db),
            "programs": overview.popular_programs(db),
            "parts": overview.activity_parts(db, now_utc),
            "news": website_sync.week_news(db, now_utc),
            "said": feedback.overview(db),
            "smart": {"ideas": reminders.suggestions(db)[:3],
                      "planned": [{"reminder": item, "when": utc_to_local(item.send_at)} for item in reminders.upcoming(db)[:3]]},
            "dashboard_programs": db.query(Program).order_by(Program.name.asc()).all(),
            "dashboard_professors": (
                db.query(Professor).filter(Professor.is_available.is_(True)).order_by(Professor.name.asc()).all()
            ),
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/guide")
def guide_page(request: Request):
    """The whole application as one journey."""
    user = auth_service.current(request)
    return templates.TemplateResponse(
        request=request, name="guide.html",
        context={"active_page": "guide", "journey": guides.JOURNEY, "for_admin": user is None or user.is_admin},
    )


@router.get("/payments")
def payments_page(request: Request, db: Session = Depends(get_db)):
    """Who still has to pay, where their reminder stands, and what to do."""
    candidates = (
        db.query(Candidate)
        .options(joinedload(Candidate.registrations).joinedload(Registration.program),
                 joinedload(Candidate.registrations).joinedload(Registration.class_group))
        .all()
    )
    now_utc, now_local = datetime.utcnow(), local_now()
    history = advisor._payment_history(db, [candidate.id for candidate in candidates])
    rows, paid_total = [], 0.0
    for candidate in candidates:
        active = [item for item in candidate.registrations if item.status != RegistrationStatus.CANCELLED]
        paid_total += sum(item.paid_amount or 0 for item in active)
        debts = payment_reminders.owing(candidate)
        if not debts:
            continue
        entry = history[candidate.id]
        last = advisor._naive_utc(entry["last"])
        rows.append({
            "candidate": candidate, "name": candidate.full_name or candidate.email,
            "due": sum(item.remaining_amount for item in debts),
            "programs": [item.program.name if item.program else (item.program_name or "Program") for item in debts],
            "sent": entry["sent"], "last": last, "days": (now_utc - last).days if last else None,
            "wrote": entry["wrote_since"],
            "advice": advisor.payment_advice(candidate, entry, now_utc, now_local),
            "whatsapp": whatsapp.link(candidate.phone),
        })
    rows.sort(key=lambda row: -(row["advice"].priority if row["advice"] else 0))
    return templates.TemplateResponse(
        request=request, name="payments.html",
        context={
            "active_page": "payments", "rows": rows,
            "totals": {
                "due": sum(row["due"] for row in rows), "people": len(rows), "paid": paid_total,
                "to_decide": sum(1 for row in rows if row["advice"] and row["advice"].action),
            },
            "notice": request.query_params.get("notice"), "error": request.query_params.get("error"),
        },
    )


@router.post("/advice/snooze")
def snooze_advice(key: str = Form(...), back: str = Form("/"), db: Session = Depends(get_db)):
    """Hide a recommendation for a few days ("not now")."""
    advisor.snooze(db, key)
    # Only back to a page of this application.
    target = back if back.startswith("/") and not back.startswith("//") else "/"
    return RedirectResponse(target, status_code=303)


# --- ADMIN APPROVAL API ROUTE ---

@router.post("/admin/api/courses/{course_id}/approve")
def approve_course(course_id: int, db: Session = Depends(get_db)):
    course = db.query(Program).filter(Program.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    course.is_approved = True

    # Log action if ActivityLog exists
    if ActivityLog is not None:
        title = getattr(course, "name", getattr(course, "title", "Course"))
        log = ActivityLog(event_type="Course Approved", details=f"Admin approved course: '{title}'.")
        db.add(log)

    db.commit()

    return {"status": "success", "message": "Course approved successfully."}


@router.get("/admin/api/registrations/recent")
def recent_registration_candidates(db: Session = Depends(get_db)):
    registrations = (
        db.query(Registration)
        .options(joinedload(Registration.candidate), joinedload(Registration.program))
        .order_by(Registration.registration_date.desc())
        .limit(25)
        .all()
    )
    grouped = {}
    for registration in registrations:
        candidate = registration.candidate
        if not candidate or candidate.id in grouped:
            continue
        candidate_registrations = list(candidate.registrations or [])
        grouped[candidate.id] = {
            "id": candidate.id,
            "full_name": candidate.full_name or "Unnamed Candidate",
            "registration_count": len(candidate_registrations),
            "registration_programs": [
                item.program.name if item.program else item.program_name
                for item in candidate_registrations
                if item.program or item.program_name
            ],
            "has_new_registration": True,
        }
    return {"candidates": list(grouped.values())}


@router.get("/api/dashboard/calendar/events")
def calendar_events(db: Session = Depends(get_db)):
    day_numbers = {
        "sun": 0, "sunday": 0,
        "mon": 1, "monday": 1,
        "tue": 2, "tuesday": 2,
        "wed": 3, "wednesday": 3,
        "thu": 4, "thursday": 4,
        "fri": 5, "friday": 5,
        "sat": 6, "saturday": 6,
    }
    events = []
    for event in get_calendar_events(db):
        event["daysOfWeek"] = [
            day_numbers[day.strip().lower()]
            for day in event.get("daysOfWeek", [])
            if day.strip().lower() in day_numbers
        ]
        events.append(event)
    return events


@router.get("/api/dashboard/calendar/export.ics")
def export_calendar_ics(db: Session = Depends(get_db)):
    """Export recurring class schedules as an iCalendar file."""
    events = get_calendar_events(db)
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//9antra//Class Calendar//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
    ]
    day_codes = {"0": "SU", "1": "MO", "2": "TU", "3": "WE", "4": "TH", "5": "FR", "6": "SA"}
    for event in events:
        days = ",".join(day_codes[str(day)] for day in event.get("daysOfWeek", []) if str(day) in day_codes)
        start = event.get("startTime", "09:00").replace(":", "") + "00"
        end = event.get("endTime", "12:00").replace(":", "") + "00"
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        summary = event["title"].replace(",", "\\,")
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{event['id']}@9antra",
            f"DTSTAMP:{stamp}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:Professor: {event.get('professor') or 'TBD'}\\nCapacity: {event.get('enrolled', 0)}/{event.get('capacity', 0)}",
            f"DTSTART;TZID=Africa/Tunis:20260101T{start}",
            f"DTEND;TZID=Africa/Tunis:20260101T{end}",
            f"RRULE:FREQ=WEEKLY;BYDAY={days}" if days else "RRULE:FREQ=WEEKLY",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return Response(
        content="\r\n".join(lines) + "\r\n",
        media_type="text/calendar",
        headers={"Content-Disposition": "attachment; filename=9antra-classes.ics"},
    )
"""The admin's own reminders: write one, choose who and when, see what is planned and what went."""
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.admin_reminder import AdminReminder
from app.models.candidate import Candidate
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.program import Program
from app.services import auth, email_composer, email_threads, reminders
from app.services.email_layout import STYLES
from app.services.schedule_utils import local_now, utc_to_local
from app.template_context import notification_context

router = APIRouter(tags=["Reminders"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def _back(notice: str | None = None, error: str | None = None, anchor: str = "") -> RedirectResponse:
    query = urlencode({key: value for key, value in (("notice", notice), ("error", error)) if value})
    return RedirectResponse(f"/reminders{'?' + query if query else ''}{anchor}", status_code=303)


def _row(db: Session, reminder: AdminReminder) -> dict:
    return {"reminder": reminder, "when": utc_to_local(reminder.send_at), "sent": utc_to_local(reminder.sent_at),
            "people": len(reminders.recipients(db, reminder.audience, reminder.audience_id)) if reminder.status == "scheduled" else reminder.sent_count}


@router.get("/reminders", response_class=HTMLResponse)
def reminders_page(request: Request, db: Session = Depends(get_db)):
    params = request.query_params
    chosen = reminders.idea(params.get("idea"))
    tomorrow = (local_now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return templates.TemplateResponse(
        request=request,
        name="reminders.html",
        context={
            "active_page": "reminders",
            "audiences": reminders.AUDIENCES,
            "audience": params.get("audience") if params.get("audience") in reminders.AUDIENCES else "class",
            "to": params.get("to") or "",
            "people": [{"id": person.id, "name": person.full_name or person.email}
                       for person in db.query(Candidate).order_by(Candidate.full_name).all() if person.email],
            "classes": db.query(ClassGroup).filter(ClassGroup.status != ClassStatus.COMPLETED).order_by(ClassGroup.name).all(),
            "programs": db.query(Program).order_by(Program.name).all(),
            "ideas": reminders.IDEAS,
            "chosen": chosen,
            "styles": STYLES,
            "variables": email_composer.VARIABLES,
            "suggestions": reminders.suggestions(db),
            "upcoming": [_row(db, item) for item in reminders.upcoming(db)],
            "history": [_row(db, item) for item in reminders.history(db)],
            "default_date": tomorrow.strftime("%Y-%m-%d"),
            "default_time": tomorrow.strftime("%H:%M"),
            "smtp_ready": email_threads.smtp_configured(),
            "notice": params.get("notice"),
            "error": params.get("error"),
        },
    )


class Preview(BaseModel):
    subject: str = ""
    title: str = ""
    body: str = ""
    style: str = "brand"
    audience: str = ""
    to: str = ""


@router.post("/reminders/preview")
def preview(payload: Preview, db: Session = Depends(get_db)):
    """The reminder as its first recipient will see it (a sample person when nobody is chosen yet)."""
    people = reminders.recipients(db, payload.audience, payload.to or None) if payload.audience in reminders.AUDIENCES else []
    candidate, registration_id = people[0] if people else (None, None)
    rendered = email_composer.render(
        db, subject=payload.subject, title=payload.title, body=payload.body or "Votre message apparaît ici.",
        style=payload.style, candidate=candidate, registration_id=registration_id, browser=True,
    )
    return {"html": rendered["html"], "subject": rendered["subject"], "people": len(people),
            "unknown": email_composer.unknown_variables(payload.subject, payload.title, payload.body)}


@router.post("/reminders/create")
def create(request: Request, audience: str = Form(""), to_candidate: str = Form(""), to_class: str = Form(""), to_program: str = Form(""),
           subject: str = Form(""), title: str = Form(""), body: str = Form(""), style: str = Form("brand"),
           when: str = Form("later"), date: str = Form(""), time: str = Form(""), db: Session = Depends(get_db)):
    target = {"candidate": to_candidate, "class": to_class, "program": to_program}.get(audience, "")
    unknown = email_composer.unknown_variables(subject, title, body)
    if unknown:
        return _back(error="Unknown variable: " + ", ".join("{" + name + "}" for name in unknown) + ".", anchor="#new")
    send_at = None
    if when != "now":
        try:
            local = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        except ValueError:
            return _back(error="Choose the day and the hour the reminder goes.", anchor="#new")
        if local < local_now() - timedelta(minutes=2):
            return _back(error="That time is already past. Choose a later one, or “Now”.", anchor="#new")
        send_at = reminders.to_utc(local)
    user = auth.current(request)
    try:
        reminder = reminders.create(db, audience=audience, audience_id=target or None, subject=subject, title=title, body=body,
                                    style=style, send_at=send_at, by=getattr(user, "name", None))
    except ValueError as problem:
        return _back(error=str(problem), anchor="#new")
    people = len(reminders.recipients(db, reminder.audience, reminder.audience_id))
    moment = "within a minute" if send_at is None else "on " + utc_to_local(reminder.send_at).strftime("%A %d %B at %H:%M")
    return _back(notice=f"The reminder goes {moment} to {people} candidate{'s' if people != 1 else ''} ({reminder.audience_label}).")


def _one(db: Session, reminder_id: str) -> AdminReminder:
    reminder = db.get(AdminReminder, reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return reminder


@router.post("/reminders/{reminder_id}/cancel")
def cancel(reminder_id: str, db: Session = Depends(get_db)):
    if not reminders.cancel(db, _one(db, reminder_id)):
        return _back(error="This reminder has already gone.")
    return _back(notice="The reminder was cancelled. Nothing was sent.")


@router.post("/reminders/{reminder_id}/send-now")
def send_now(reminder_id: str, db: Session = Depends(get_db)):
    reminder = _one(db, reminder_id)
    if reminder.status != "scheduled":
        return _back(error="This reminder has already gone.")
    reminder.send_at = datetime.utcnow()
    db.commit()
    return _back(notice="The reminder goes within a minute.")

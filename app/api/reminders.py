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
from app.services import (auth, durations, email_composer, email_layout, email_threads, reminder_board, reminder_kinds, reminders,
                          smart_reminders)
from app.services.email_layout import STYLES
from app.services.schedule_utils import local_now, utc_to_local
from app.template_context import notification_context
from app.services.ui_words import t as _t

router = APIRouter(tags=["Reminders"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def _back(notice: str | None = None, error: str | None = None, anchor: str = "") -> RedirectResponse:
    query = urlencode({key: value for key, value in (("notice", notice), ("error", error)) if value})
    return RedirectResponse(f"/reminders{'?' + query if query else ''}{anchor}", status_code=303)


def _row(db: Session, reminder: AdminReminder) -> dict:
    waits_for = ""
    if reminder.repeat in reminders.WATCHING:
        offset = reminders.offset_of(reminder)
        words = reminder_kinds.TRIGGERS.get(reminder.repeat, ("", ""))[0]
        waits_for = f"{offset.label()} {words}" if offset.value else words.replace("after ", "right after ", 1)
    elif reminder.repeat == "every" or reminder.repeat in reminders.OLD_REPEATS:
        waits_for = f"Every {reminders.every_of(reminder).label()}"
    return {"reminder": reminder, "when": utc_to_local(reminder.send_at), "sent": utc_to_local(reminder.sent_at), "waits_for": waits_for,
            "people": len(reminders.recipients(db, reminder.audience, reminder.audience_id)) if reminder.status == "scheduled" else reminder.sent_count}


@router.get("/reminders", response_class=HTMLResponse)
def reminders_page(request: Request, db: Session = Depends(get_db)):
    params = request.query_params
    chosen = reminders.idea(params.get("idea"))
    kind = reminder_kinds.get(params.get("kind"))
    if kind is not None:
        chosen = {**kind, "style": "brand"}
    tomorrow = (local_now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    # A smart suggestion for one candidate: the form comes filled (who, words, look, day and hour).
    smart = smart_reminders.prefill(db, params.get("smart") or "") if params.get("smart") else None
    if smart is not None:
        chosen = smart
    return templates.TemplateResponse(
        request=request,
        name="reminders.html",
        context={
            "active_page": "reminders",
            "audiences": reminders.AUDIENCES,
            "audience": "candidate" if smart else params.get("audience") if params.get("audience") in reminders.AUDIENCES else "class",
            "to": smart["candidate_id"] if smart else params.get("to") or "",
            "smart": smart,
            "smart_board": smart_reminders.board(db),
            "people": [{"id": person.id, "name": person.full_name or person.email}
                       for person in db.query(Candidate).order_by(Candidate.full_name).all() if person.email],
            "classes": db.query(ClassGroup).filter(ClassGroup.status != ClassStatus.COMPLETED).order_by(ClassGroup.name).all(),
            "programs": db.query(Program).order_by(Program.name).all(),
            "professors": [person for person in db.query(__import__("app.models", fromlist=["Professor"]).Professor).order_by("name").all()
                           if person.email and not (person.name or "").startswith("À nommer")],
            "repeats": reminders.REPEATS,
            "units": durations.NAMES,
            "offset_defaults": {key: durations.rows(durations.parse(code, "hours"), durations.Duration(1, "days"))
                                for key, code in reminders.OFFSET_DEFAULTS.items()},
            "kind": kind,
            "kind_offset": durations.rows(durations.parse((kind or {}).get("offset") or "", "hours"), durations.Duration(1, "days")),
            "catalogue": reminder_kinds.catalogue(db),
            "recommended": reminder_kinds.recommend(db),
            "trigger_words": reminder_kinds.TRIGGERS,
            "chosen": chosen,
            "styles": STYLES,
            "variables": email_composer.VARIABLES,
            "suggestions": reminders.suggestions(db),
            "automatic": reminder_board.items(db),
            "design_options": email_layout.design_options(),
            "upcoming": [_row(db, item) for item in reminders.upcoming(db)],
            "history": [_row(db, item) for item in reminders.history(db)],
            "default_date": smart["date"] if smart else tomorrow.strftime("%Y-%m-%d"),
            "default_time": smart["time"] if smart else tomorrow.strftime("%H:%M"),
            "smtp_ready": email_threads.smtp_configured(),
            "notice": params.get("notice"),
            "error": params.get("error"),
        },
    )


@router.get("/reminders/smart/{candidate_id}", response_class=HTMLResponse)
def smart_for(candidate_id: str, request: Request, db: Session = Depends(get_db)):
    """What is smart to remind this candidate, for the form (a piece of page)."""
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return templates.TemplateResponse(request=request, name="smart_person.html", context={"row": smart_reminders.for_candidate(db, candidate)})


class Preview(BaseModel):
    subject: str = ""
    title: str = ""
    body: str = ""
    style: str = "brand"
    audience: str = ""
    to: str = ""
    design: dict = {}


@router.post("/reminders/preview")
def preview(payload: Preview, db: Session = Depends(get_db)):
    """The reminder as its first recipient will see it (a sample person when nobody is chosen yet)."""
    people = reminders.recipients(db, payload.audience, payload.to or None) if payload.audience in reminders.AUDIENCES else []
    candidate, registration_id = people[0] if people else (None, None)
    rendered = email_composer.render(
        db, subject=payload.subject, title=payload.title, body=payload.body or "Votre message apparaît ici.",
        style=payload.style, candidate=candidate, registration_id=registration_id, browser=True, design=payload.design,
        values={name: sample for name, _, sample in reminders.SESSION_VARIABLES},      # a session's, shown with samples
    )
    return {"html": rendered["html"], "subject": rendered["subject"], "people": len(people),
            "unknown": email_composer.unknown_variables(payload.subject, payload.title, payload.body, extra=reminders.SESSION_VARIABLES)}


@router.post("/reminders/create")
def create(request: Request, audience: str = Form(""), to_candidate: str = Form(""), to_class: str = Form(""), to_program: str = Form(""),
           to_professor: str = Form(""), subject: str = Form(""), title: str = Form(""), body: str = Form(""), style: str = Form("brand"),
           when: str = Form("later"), date: str = Form(""), time: str = Form(""), channel: str = Form("email"),
           repeat: str = Form("once"), until: str = Form(""), design: str = Form(""), hours_before: str = Form(""),
           more_date: list[str] = Form(default=[]), more_time: list[str] = Form(default=[]), amount: str = Form(""),
           unit: str = Form("days"), in_amount: str = Form(""), in_unit: str = Form("minutes"), kind: str = Form(""),
           db: Session = Depends(get_db)):
    target = {"candidate": to_candidate, "class": to_class, "program": to_program, "professor": to_professor}.get(audience, "")
    stop = None
    if until.strip() and repeat != "once":
        try:
            stop = reminders.to_utc(datetime.strptime(until.strip(), "%Y-%m-%d").replace(hour=23, minute=59))
        except ValueError:
            return _back(error="Choose the last day it goes, or leave it empty.", anchor="#new")
    unknown = email_composer.unknown_variables(subject, title, body, extra=reminders.SESSION_VARIABLES if repeat == "session" else ())
    if unknown:
        return _back(error="Unknown variable: " + ", ".join("{" + name + "}" for name in unknown) + ".", anchor="#new")
    send_at = None
    try:
        hours = int(hours_before) if hours_before.strip() else None
    except ValueError:
        return _back(error="Before each session: write how many hours before (1 to 168).", anchor="#new")
    # The timer of "again and again" and of the watching ones: a number and its unit, seconds to months.
    timer = None
    if repeat == "every" or repeat in reminders.WATCHING:
        try:
            timer = durations.of(amount, unit) if amount.strip() else None
        except ValueError as problem:
            return _back(error=str(problem), anchor="#new")
    if when == "in" and repeat not in reminders.WATCHING:
        try:
            wait = durations.checked(durations.of(in_amount, in_unit), most=durations.Duration(12, "months"))
        except ValueError as problem:
            return _back(error=str(problem), anchor="#new")
        send_at = reminders.to_utc(wait.after(local_now()))
    elif when != "now" and repeat not in reminders.WATCHING:
        try:
            local = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        except ValueError:
            return _back(error="Choose the day and the hour the reminder goes.", anchor="#new")
        if local < local_now() - timedelta(minutes=2):
            return _back(error="That time is already past. Choose a later one, or “Now”.", anchor="#new")
        send_at = reminders.to_utc(local)
    # The same reminder at other times too (each goes on its own, and repeats the same way).
    others = []
    if repeat not in reminders.WATCHING:
        for day, hour in zip(more_date, more_time):
            if not day.strip():
                continue
            try:
                local = datetime.strptime(f"{day.strip()} {hour.strip() or '09:00'}", "%Y-%m-%d %H:%M")
            except ValueError:
                return _back(error="One of the other times cannot be read: choose its day and hour.", anchor="#new")
            if local < local_now() - timedelta(minutes=2):
                return _back(error="One of the other times is already past.", anchor="#new")
            others.append(reminders.to_utc(local))
    user = auth.current(request)
    try:
        reminder = reminders.create(db, audience=audience, audience_id=target or None, subject=subject, title=title, body=body,
                                    style=style, send_at=send_at, by=getattr(user, "name", None), channel=channel, repeat=repeat, until=stop,
                                    design=design or None, hours_before=hours, every=timer.code if timer and repeat == "every" else None,
                                    offset=timer.code if timer and repeat in reminders.WATCHING else None,
                                    kind=kind if reminder_kinds.get(kind) else None)
    except ValueError as problem:
        return _back(error=str(problem), anchor="#new")
    for moment in dict.fromkeys(others):
        if moment != send_at:
            reminders.create(db, audience=audience, audience_id=target or None, subject=subject, title=title, body=body, style=style,
                             send_at=moment, by=getattr(user, "name", None), channel=channel, repeat=repeat, until=stop,
                             design=design or None, hours_before=hours, every=timer.code if timer and repeat == "every" else None,
                             kind=kind if reminder_kinds.get(kind) else None)
    if reminder.repeat in reminders.WATCHING:
        offset = reminders.offset_of(reminder)
        words = reminder_kinds.TRIGGERS.get(reminder.repeat, ("", ""))[0]
        return _back(notice=_t("Saved: each candidate of {0} receives it {1}, once each time. It is with the automatic reminders.",
                               reminder.audience_label, (f"{offset.label()} " if offset.value else "") + words), anchor="#auto")
    people = len(reminders.recipients(db, reminder.audience, reminder.audience_id))
    moment = _t("within a minute") if send_at is None else utc_to_local(reminder.send_at).strftime("%d/%m %H:%M")
    rhythm = {"daily": _t("then every day"), "weekly": _t("then every week"), "monthly": _t("then every month")}.get(reminder.repeat, "")
    if reminder.repeat == "every":
        rhythm = _t("then every {0}", reminders.every_of(reminder).label())
    notice = _t("The reminder goes {0} {1} to {2} ({3}).", moment, rhythm, people, reminder.audience_label).replace("  ", " ")
    extra = len([moment for moment in dict.fromkeys(others) if moment != send_at])
    if extra:
        notice += " " + _t("It also goes at {0} other times.", extra)
    return _back(notice=notice)


class WriteRequest(BaseModel):
    topic: str = ""
    audience: str = "class"
    repeat: str = "once"


@router.post("/reminders/write")
def write_for_me(payload: WriteRequest, db: Session = Depends(get_db)):
    """“Write it for me”: the subject and the message from the topic (only the topic goes to the online writer)."""
    from app.services import reminder_writer

    return reminder_writer.write(db, payload.topic, audience=payload.audience, repeat=payload.repeat)


@router.post("/reminders/auto/{key}/switch")
def switch_automatic(key: str, on: str = Form("1"), db: Session = Depends(get_db)):
    try:
        return _back(notice=reminder_board.switch(db, key, on == "1"), anchor="#auto")
    except (ValueError, KeyError) as problem:
        db.rollback()
        return _back(error=str(problem), anchor="#auto")


@router.post("/reminders/auto/{key}/timing")
def time_automatic(key: str, value: list[str] = Form(default=[]), unit: list[str] = Form(default=[]), field: list[str] = Form(default=[]),
                   db: Session = Depends(get_db)):
    try:
        units = unit or ["days" if key == "training_reminder" else "hours"] * len(value)
        return _back(notice=reminder_board.set_timing(db, key, value, units, field or None), anchor="#auto")
    except ValueError as problem:
        db.rollback()
        return _back(error=str(problem), anchor="#auto")


@router.post("/reminders/auto/{key}/reset")
def reset_automatic(key: str, db: Session = Depends(get_db)):
    try:
        return _back(notice=reminder_board.reset(db, key), anchor="#auto")
    except ValueError as problem:
        db.rollback()
        return _back(error=str(problem), anchor="#auto")


def _one(db: Session, reminder_id: str) -> AdminReminder:
    reminder = db.get(AdminReminder, reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return reminder


@router.post("/reminders/kinds/{key}/setup")
def set_up_kind(key: str, request: Request, audience: str = Form("everyone"), to: str = Form(""), channel: str = Form("email"),
                db: Session = Depends(get_db)):
    """One click: a kind from the suggestions, with its words and its timer, for everyone (or the choice given)."""
    kind = reminder_kinds.get(key)
    if kind is None:
        raise HTTPException(status_code=404, detail="Unknown kind")
    if kind["trigger"] == "once":
        return RedirectResponse(f"/reminders?kind={key}#new", status_code=303)       # a date to choose first
    user = auth.current(request)
    repeat = kind["trigger"]
    try:
        reminder = reminders.create(db, audience=audience if audience in reminders.AUDIENCES else "everyone", audience_id=to or None,
                                    subject=kind["subject"], title=kind.get("title"), body=kind["body"], style="brand",
                                    send_at=reminders.to_utc(durations.parse(kind["offset"], "days").after(local_now())) if repeat == "every" else None,
                                    by=getattr(user, "name", None), channel=channel, repeat=repeat,
                                    every=kind["offset"] if repeat == "every" else None,
                                    offset=kind["offset"] if repeat in reminders.WATCHING else None, kind=key)
    except ValueError as problem:
        return _back(error=str(problem), anchor="#suggested")
    return _back(notice=_t("“{0}” is set up: it goes by itself, {1}. Change its words or its timer below.", _t(kind["name"]),
                           reminder_board.rule_items(db)[-1]["when"] if reminder else ""), anchor=f"#auto-rule-{reminder.id}")


@router.post("/reminders/{reminder_id}/words")
def change_words(reminder_id: str, subject: str = Form(""), body: str = Form(""), db: Session = Depends(get_db)):
    """The words of a reminder that goes by itself (its timer and its people stay)."""
    reminder = _one(db, reminder_id)
    extra = reminders.SESSION_VARIABLES if reminder.repeat == "session" else ()
    unknown = email_composer.unknown_variables(subject, reminder.title or "", body, extra=extra + (("start_date", "", ""), ("start_time", "", "")))
    if unknown:
        return _back(error="Unknown variable: " + ", ".join("{" + name + "}" for name in unknown) + ".", anchor=f"#auto-rule-{reminder.id}")
    if not subject.strip() or not body.strip():
        return _back(error="Write a subject and a message.", anchor=f"#auto-rule-{reminder.id}")
    reminder.subject, reminder.body = " ".join(subject.split())[:200], body.strip()
    db.commit()
    return _back(notice=_t("Its words are saved."), anchor=f"#auto-rule-{reminder.id}")


@router.post("/reminders/{reminder_id}/cancel")
def cancel(reminder_id: str, db: Session = Depends(get_db)):
    if not reminders.cancel(db, _one(db, reminder_id)):
        return _back(error="This reminder has already gone.")
    return _back(notice="The reminder was stopped. Nothing more is sent.")


@router.post("/reminders/{reminder_id}/reset")
def reset(reminder_id: str, db: Session = Depends(get_db)):
    """Start a reminder again, as if just written (a stopped or finished one is on again)."""
    reminder = reminders.reset(db, _one(db, reminder_id))
    if reminder.status == "watching":
        return _back(notice="Started again: it waits from now on.", anchor="#planned")
    return _back(notice=_t("Started again: it goes {0}.", utc_to_local(reminder.send_at).strftime("%d/%m %H:%M")), anchor="#planned")


@router.post("/reminders/{reminder_id}/send-now")
def send_now(reminder_id: str, db: Session = Depends(get_db)):
    reminder = _one(db, reminder_id)
    if reminder.status != "scheduled":
        return _back(error="This reminder has already gone.")
    reminder.send_at = datetime.utcnow()
    db.commit()
    return _back(notice="The reminder goes within a minute.")

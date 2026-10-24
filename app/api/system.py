"""The System page: how the application is working, and how to open it on a phone."""
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.notification import Notification
from app.models.user import User, UserSession
from app.services import backups, brand, email_files, notification_fixes, system_status, theme, weekly_questions
from app.services.background import run_in_background
from app.services.settings_service import get_settings, public_base_url
from app.template_context import notification_context

router = APIRouter(tags=["System"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def _jobs():
    from app.scheduler import jobs      # here: it starts nothing, but it imports every service

    return jobs


def _next_runs() -> dict:
    """When each piece of work runs next, from the scheduler."""
    jobs = _jobs()
    found = {}
    for key, job_id in jobs.WATCHED.items():
        try:
            job = jobs.scheduler.get_job(job_id)
        except Exception:
            job = None
        found[key] = getattr(job, "next_run_time", None) if job is not None else None
    return found


@router.get("/system", response_class=HTMLResponse)
def system_page(request: Request, db: Session = Depends(get_db)):
    base_url = public_base_url(db, request)
    local = weekly_questions.is_local_address(base_url)
    work = system_status.work(db, _next_runs())
    connections = system_status.connections(db, base_url, local)
    port = request.url.port or 8000
    address = system_status.lan_address()
    recent = (
        db.query(Notification)
        .filter(Notification.recipient_role == "admin")
        .order_by(Notification.created_at.desc())
        .limit(8)
        .all()
    )
    for item in recent:
        item.fix = notification_fixes.fix_for(db, item)
    return templates.TemplateResponse(
        request=request,
        name="system.html",
        context={
            "active_page": "system",
            "work": work,
            "automations": __import__("app.services.automations", fromlist=["listing"]).listing(db),
            "resend": _resend_context(db),
            "missing": __import__("app.services.completeness", fromlist=["grouped"]).grouped(db),
            "connections": connections,
            "summary": system_status.summary(work, connections),
            "started_at": system_status.STARTED_AT,
            "recent": recent,
            "safety": backups.view(db),
            "human_size": email_files.human_size,
            "accounts": {"people": db.query(User).filter(User.is_active.is_(True)).count(), "sessions": db.query(UserSession).count()},
            "phone": {
                "address": f"http://{address}:{port}" if address else None,
                "open": system_status.open_to_the_network(address, port),
                "public": None if local else base_url,
            },
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/system/run/{key}")
def run_now(key: str):
    """Do one piece of automatic work now instead of waiting for its turn."""
    jobs = _jobs()
    item = next((row for row in system_status.WORK if row["key"] == key and row["run"]), None)
    job = jobs.RUN_NOW.get(key)
    if item is None or job is None:
        raise HTTPException(status_code=404, detail="This work cannot be started by hand")
    run_in_background(job)
    return RedirectResponse("/system?" + urlencode({"notice": f"“{item['name']}” was started. This page shows when it is done."}), status_code=303)


@router.get("/system/backups/{name}")
def download_backup(name: str):
    """One of the daily copies, to keep somewhere else."""
    path = backups.find(name)
    if path is None:
        raise HTTPException(status_code=404, detail="This copy does not exist")
    return FileResponse(path, media_type="application/zip", filename=name)


@router.post("/system/backup-folder")
def backup_folder(folder: str = Form(""), db: Session = Depends(get_db)):
    """The second folder the copies go to (empty: none)."""
    kept, problem = backups.check_folder(folder)
    if problem:
        return RedirectResponse("/system?" + urlencode({"error": problem}) + "#safety", status_code=303)
    settings = get_settings(db)
    settings.backup_second_dir = kept
    db.commit()
    if kept is None:
        notice = "The copies now stay in one folder only."
    else:
        outcome = backups.make(db)
        notice = (f"Copies now also go to {kept}. One was made there just now." if outcome["ok"] and not outcome["problem"]
                  else f"The folder is saved, but the copy did not go through: {outcome['problem']}")
    return RedirectResponse("/system?" + urlencode({"notice": notice}) + "#safety", status_code=303)


@router.get("/app.webmanifest")
def manifest(db: Session = Depends(get_db)):
    """What a phone needs to put the application on its home screen, with its own name and icon."""
    settings = get_settings(db)
    look = brand.view(settings)
    colour = theme.current_colour(settings)
    icons = [
        {"src": url, "sizes": size, "type": "image/png", "purpose": "any"}
        for url, size in ((look.get("icon_big"), "192x192"), (look.get("logo"), "512x512")) if url
    ]
    return JSONResponse(
        {
            "name": look["name"], "short_name": look["name"][:12], "start_url": "/", "scope": "/",
            "display": "standalone", "background_color": "#f6f3fb", "theme_color": colour, "icons": icons,
        },
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


def _resend_context(db: Session) -> dict:
    from app.models import Candidate, ClassGroup, ClassStatus
    from app.services import resends

    return {
        "kinds": [{"key": key, "name": name, "for": whom} for key, (name, whom, _) in resends.KINDS.items()],
        "candidates": db.query(Candidate).order_by(Candidate.full_name).all(),
        "classes": db.query(ClassGroup).filter(ClassGroup.status != ClassStatus.COMPLETED).order_by(ClassGroup.name).all(),
        "planned": resends.planned(db), "recent": resends.recent(db),
    }


@router.post("/system/resend")
def resend_plan(request: Request, kind: str = Form(""), candidate_id: str = Form(""), class_id: str = Form(""), when: str = Form("now"),
                at: str = Form(""), db: Session = Depends(get_db)):
    """Send again something the application sends by itself: now, or at the day and hour chosen."""
    from app.services import auth, resends
    from app.services.schedule_utils import local_now
    from app.services.settings_service import public_base_url

    now = local_now()
    try:
        moment = now if when == "now" else datetime.fromisoformat(at)
    except ValueError:
        return RedirectResponse("/system?" + urlencode({"error": "Choose the day and the hour it should go again."}) + "#resend", status_code=303)
    if moment < now - timedelta(minutes=1):
        return RedirectResponse("/system?" + urlencode({"error": "That moment has passed: choose one to come."}) + "#resend", status_code=303)
    whom = resends.KINDS.get(kind, (None, None))[1]
    target = candidate_id if whom == "candidate" else class_id if whom == "class" else None
    user = auth.current(request)
    try:
        row = resends.plan(db, kind, target_id=target, when=moment, created_by=getattr(user, "name", None) or "Admin")
    except ValueError as error:
        return RedirectResponse("/system?" + urlencode({"error": str(error)}) + "#resend", status_code=303)
    notice = resends.said(row, now)
    if row.due_at <= now + timedelta(minutes=1):
        notice += " " + resends.run(db, row, public_base_url(db, request))
    return RedirectResponse("/system?" + urlencode({"notice": notice}) + "#resend", status_code=303)


@router.post("/system/resend/{resend_id}/cancel")
def resend_cancel(resend_id: str, db: Session = Depends(get_db)):
    from app.services import resends

    done = resends.cancel(db, resend_id)
    return RedirectResponse("/system?" + urlencode({"notice": "Cancelled: it will not go again." if done else "Nothing to cancel."}) + "#resend", status_code=303)

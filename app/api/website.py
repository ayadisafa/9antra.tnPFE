"""The Website page: what was taken from 9antra.tn, and what the admin decides about it."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.program import Program
from app.models.website import WebsiteItem
from app.services import brand, feedback, knowledge_service, website_sync
from app.services.background import run_in_background
from app.services.schedule_utils import utc_to_local
from app.template_context import notification_context

router = APIRouter(tags=["Website"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _back(notice: str | None = None, error: str | None = None, anchor: str = "") -> RedirectResponse:
    query = urlencode({key: value for key, value in (("notice", notice), ("error", error)) if value})
    return RedirectResponse(f"/website{'?' + query if query else ''}{anchor}", status_code=303)


@router.get("/website")
def website_page(request: Request, db: Session = Depends(get_db)):
    view = website_sync.overview(db)
    return templates.TemplateResponse(
        request=request,
        name="website.html",
        context={
            "active_page": "website",
            "view": view,
            "said": feedback.overview(db),
            "kept": website_sync.kept(db),
            "kinds": website_sync.KINDS,
            "local": utc_to_local,
            "last_local": utc_to_local(view["last"].checked_at) if view["last"] else None,
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/website/items/{item_id}/remove")
def remove_item(item_id: str, db: Session = Depends(get_db)):
    """Remove a record that is no longer on the website. Only the admin does this; the application never does."""
    from app.models.website import WebsiteItem

    item = db.get(WebsiteItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Record not found")
    if item.gone_at is None:
        return _back(error="This is still on the website: it would come back at the next reading.", anchor="#kept")
    website_sync.remove(db, item)
    return _back(notice=f"“{item.title}” was removed from your records.", anchor="#kept")


@router.post("/website/check")
def check_now(db: Session = Depends(get_db)):
    record = website_sync.check(db, by="admin")
    brand.refresh_from_facebook()
    if not record.ok:
        return _back(error=f"The website could not be read: {record.note} Nothing was changed.")
    count = len(record.lines)
    return _back(notice="The website was read: nothing new." if not count else f"The website was read: {count} change{'s' if count != 1 else ''}, listed below.")


@router.get("/website/feedback/{item_id}/picture")
def feedback_picture(item_id: str, db: Session = Depends(get_db)):
    """The picture of a feedback, as kept on this computer."""
    item = db.get(WebsiteItem, item_id)
    path = feedback.picture_path(item) if item is not None and item.kind == "feedback" else None
    if path is None:
        raise HTTPException(status_code=404, detail="This picture is not kept here.")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


def _read_all_feedback() -> None:
    """Background: read every feedback still waiting, then tell the assistant."""
    db = SessionLocal()
    try:
        done = 0
        while feedback.tick(db, limit=4):
            done += 1
            if done > 60:
                break
        knowledge_service.sync_knowledge(db)
    finally:
        db.close()


@router.post("/website/feedback/read")
def read_feedback():
    run_in_background(_read_all_feedback)
    return _back(notice="Reading the feedback: each one appears with its words and its tone as it is read.", anchor="#feedback")


@router.post("/website/programs/{program_id}/take")
def take_from_website(program_id: str, field: str = Form("all"), db: Session = Depends(get_db)):
    """Replace the admin's own version of a detail (or of all of them) with the website's."""
    program = db.get(Program, program_id)
    item = website_sync.formation_of(db, program) if program else None
    if program is None or item is None:
        raise HTTPException(status_code=404, detail="This program does not follow a formation of the website.")
    wanted = set(website_sync.FIELDS) | {"plan"} if field == "all" else {field}
    if not wanted <= set(website_sync.FIELDS) | {"plan"}:
        raise HTTPException(status_code=400, detail="Unknown detail.")
    outcome = website_sync.apply_to_program(db, program, item, take=wanted)
    db.commit()
    changed = outcome["filled"] + outcome["updated"]
    return _back(
        notice=f"{program.name}: {', '.join(changed).lower()} now as on the website." if changed
        else f"{program.name} already says what the website says.",
        anchor="#formation",
    )


@router.post("/website/formations/{item_id}/program")
def choose_program(item_id: str, program_id: str = Form(""), db: Session = Depends(get_db)):
    """Say which program follows a formation: one of the programs, a new one, or none."""
    item = db.get(WebsiteItem, item_id)
    if item is None or item.kind != "formation":
        raise HTTPException(status_code=404, detail="Formation not found.")
    slug = item.slug or item.external_id
    for program in db.query(Program).filter(Program.website_slug == slug).all():
        program.website_slug, program.website_values = None, None
    if program_id == website_sync.NO_PROGRAM:
        item.program_id = website_sync.NO_PROGRAM
        db.commit()
        return _back(notice=f"“{item.title}” is kept as information only: no program follows it.", anchor="#formation")
    if program_id == "new":
        program = Program(name=item.title, is_approved=True)
        db.add(program)
        db.flush()
    else:
        program = db.get(Program, program_id)
        if program is None:
            raise HTTPException(status_code=404, detail="Program not found.")
        if program.website_slug and program.website_slug != slug:
            return _back(error=f"{program.name} already follows another formation of the website.", anchor="#formation")
    outcome = website_sync.apply_to_program(db, program, item)
    db.commit()
    filled = ", ".join(outcome["filled"]).lower()
    return _back(notice=f"{program.name} now follows “{item.title}”" + (f": {filled} filled in." if filled else "."), anchor="#formation")

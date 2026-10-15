"""The online meeting of a session: its room, each candidate's own link, and the door they come through."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.classgroup import ClassGroup
from app.models.classsession import ClassSession
from app.services import email_threads, meetings, weekly_questions
from app.services.settings_service import get_settings, public_base_url

router = APIRouter(tags=["Meetings"])
# The page a candidate comes through shows nothing of the application.
public_templates = Jinja2Templates(directory="app/templates")


def _back(class_id: str, session_id: str, notice: str | None = None, error: str | None = None) -> RedirectResponse:
    query = urlencode({key: value for key, value in (("session", session_id), ("notice", notice), ("error", error)) if value})
    return RedirectResponse(f"/classes/{class_id}/attendance?{query}#meeting", status_code=303)


def _session(db: Session, class_id: str, session_id: str) -> tuple[ClassGroup, ClassSession]:
    class_group = db.get(ClassGroup, class_id)
    session = db.get(ClassSession, session_id)
    if class_group is None or session is None or session.class_group_id != class_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return class_group, session


@router.post("/classes/{class_id}/attendance/{session_id}/room")
def set_room(class_id: str, session_id: str, meeting_url: str = Form(""), db: Session = Depends(get_db)):
    """The meeting of a session: the address the professor pastes, or a private room made here."""
    class_group, session = _session(db, class_id, session_id)
    try:
        address = meetings.clean_url(meeting_url)
    except ValueError as exc:
        return _back(class_id, session_id, error=str(exc))
    session.meeting_url = address or meetings.new_room(class_group)
    db.commit()
    meetings.links_for(db, class_group, session)
    return _back(class_id, session_id, notice="The meeting is set. Each candidate has their own link to it."
                 if address else "A private room was created. Each candidate has their own link to it.")


@router.post("/classes/{class_id}/attendance/{session_id}/send-links")
def send_links(class_id: str, session_id: str, request: Request, again: bool = Form(False), db: Session = Depends(get_db)):
    """Email each candidate of the class their own link to this session's meeting."""
    class_group, session = _session(db, class_id, session_id)
    if not email_threads.smtp_configured():
        return _back(class_id, session_id, error="Email is not set up, so nothing was sent. See Settings.")
    base_url = public_base_url(db, request)
    outcome = meetings.send_links(db, class_group, session, base_url, again=again)
    notice = f"{outcome['sent']} candidate{'s' if outcome['sent'] != 1 else ''} received their own link." if outcome["sent"] else None
    error = None
    if outcome["failed"]:
        error = f"{outcome['failed']} could not be sent; they are kept in the candidates' discussions (Inbox)."
    if not outcome["sent"] and not outcome["failed"]:
        notice = "Everybody already received their link. Tick “send again” to send it once more."
    if outcome["sent"] and weekly_questions.is_local_address(base_url):
        error = ((error + " ") if error else "") + (
            f"The link in these emails starts with {base_url}, which only opens on this computer. "
            "Set the address candidates can reach in Settings."
        )
    return _back(class_id, session_id, notice=notice, error=error)


@router.get("/join/{token}", response_class=HTMLResponse)
def join(token: str, request: Request, db: Session = Depends(get_db)):
    """A candidate comes to their session: their arrival is noted and the meeting opens."""
    row = meetings.by_token(db, token)
    found = meetings.enter(db, row) if row is not None else {"state": "unknown", "url": None, "session": None, "class_group": None}
    session, class_group = found.get("session"), found.get("class_group")
    start = end = None
    if session is not None and class_group is not None:
        start, end = meetings.hours_of(session, class_group)
    settings = get_settings(db)
    return public_templates.TemplateResponse(
        request=request,
        name="join.html",
        context={
            "state": found["state"], "url": found["url"], "session": session, "class_group": class_group,
            "start": start, "end": end, "timed": bool(session and (session.start_time or (class_group and class_group.start_time))),
            "day": meetings._french_date(session.session_date) if session is not None else "",
            "opens_minutes": int(meetings.OPENS_BEFORE.total_seconds() // 60),
            "app_name": settings.app_name or settings.platform_name or "9antra",
        },
        status_code=200 if found["state"] != "unknown" else 404,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"},
    )

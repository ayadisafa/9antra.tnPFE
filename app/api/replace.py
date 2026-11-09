"""The page a professor opens from "could you replace a colleague that day?": yes or no, without signing in (the link
is theirs alone, and works once). See services/replacements."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import brand, replacements
from app.services.settings_service import get_settings, public_base_url

router = APIRouter(tags=["Replacement"])
templates = Jinja2Templates(directory="app/templates")
_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}


def _page(request: Request, db: Session, row, *, done: str | None = None, status_code: int = 200):
    from app.models import ClassGroup, Professor
    from app.models.classsession import ClassSession

    session = db.get(ClassSession, row.session_id) if row is not None else None
    group = db.get(ClassGroup, row.class_group_id) if row is not None else None
    return templates.TemplateResponse(
        request=request, name="replace.html", status_code=status_code, headers=_HEADERS,
        context={"row": row, "session": session, "group": group, "done": done,
                 "when": replacements._when(session, group) if session is not None and group is not None else "",
                 "professor": db.get(Professor, row.asked_professor_id) if row is not None and row.asked_professor_id else None,
                 "app_name": brand.view(get_settings(db))["name"]},
    )


@router.get("/replace/{token}", response_class=HTMLResponse)
def replace_page(token: str, request: Request, db: Session = Depends(get_db)):
    row = replacements.by_token(db, token)
    return _page(request, db, row, status_code=200 if row is not None and row.status == "asked" else 404)


@router.post("/replace/{token}")
def replace_answer(token: str, request: Request, answer: str = Form(""), db: Session = Depends(get_db)):
    row = replacements.by_token(db, token)
    if row is None or row.status != "asked":
        return _page(request, db, None, status_code=404)
    yes = answer == "yes"
    professor_id = row.asked_professor_id
    replacements.answer(db, row, yes, public_base_url(db, request))
    db.expire_all()
    row = db.get(replacements.SessionReplacement, row.id)
    if yes and row.status == "accepted" and row.asked_professor_id == professor_id:
        return _page(request, db, row, done="yes")
    return _page(request, db, row, done="no" if not yes else "taken")

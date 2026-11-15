"""The page a candidate opens from "do you continue with the next month?": continue or stop after this month, without
signing in (the link is theirs alone). A GET only shows the choice (mail scanners open every link); the choice is a
POST. It can be changed until that month starts. See services/monthly.py."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import brand, monthly
from app.services.schedule_utils import format_date_fr
from app.services.settings_service import get_settings

router = APIRouter(tags=["Monthly payments"])
templates = Jinja2Templates(directory="app/templates")
_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}


def _page(request: Request, db: Session, registration, *, done: dict | None = None, status_code: int = 200):
    number = monthly.upcoming(registration) if registration is not None else None
    context = {"app_name": brand.view(get_settings(db))["name"], "registration": registration, "done": done, "number": number}
    if registration is not None:
        first = ((registration.candidate.full_name or "").split() or [""])[0]
        begins = monthly.month_start(registration, number) if number else None
        context.update({"first": first, "program": registration.program_name or "", "begins": format_date_fr(begins) if begins else "",
                        "price": monthly.money(monthly.price_now(registration)), "previous": (number or 2) - 1,
                        "answer": monthly.answer_for(registration, number) if number else None,
                        "total": monthly.program_months(registration.program) if registration.program else 1})
    return templates.TemplateResponse(request=request, name="month_choice.html", context=context, status_code=status_code, headers=_HEADERS)


@router.get("/month/{token}", response_class=HTMLResponse)
def choice_page(token: str, request: Request, db: Session = Depends(get_db)):
    registration = monthly.by_token(db, token)
    if registration is None or monthly.upcoming(registration) is None:
        return _page(request, db, None, status_code=404)
    return _page(request, db, registration)


@router.post("/month/{token}")
def choose(token: str, request: Request, answer: str = Form(""), db: Session = Depends(get_db)):
    registration = monthly.by_token(db, token)
    if registration is None or monthly.upcoming(registration) is None or answer not in ("yes", "no"):
        return _page(request, db, None, status_code=404)
    done = monthly.answer(db, registration, answer == "yes")
    return _page(request, db, registration, done=done)

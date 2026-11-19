"""System › Control room: the live map of the application, every functionality tracked, what to enhance and what is
needed, what is new and how to test everything (services/control_room.py, services/test_book.py). Under /system, so the
access rights of the System page apply."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import test_mark  # noqa: F401  (its table is made at start)
from app.services import auth, control_room, enhancements, test_book, weekly_questions
from app.services.settings_service import public_base_url
from app.template_context import notification_context

router = APIRouter(tags=["Control room"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


@router.get("/system/control", response_class=HTMLResponse)
def control_page(request: Request, db: Session = Depends(get_db)):
    user = auth.current(request)
    base_url = public_base_url(db, request)
    tests = test_book.book(db, user)
    needed = control_room.needs(db, base_url, weekly_questions.is_local_address(base_url))
    market = enhancements.ranking(db)
    return templates.TemplateResponse(
        request=request,
        name="control_room.html",
        context={
            "active_page": "control",
            "map": control_room.snapshot(db),
            "tracking": control_room.tracking(db),
            "suggestions": control_room.suggestions(db, tests["progress"], needed),
            "needs": needed,
            "ideas": control_room.IDEAS,
            "market": market,
            "market_read": control_room._ago(market["read_at"]),
            "reading": request.query_params.get("reading") == "1",
            "tests": tests,
        },
    )


@router.get("/system/control/data")
def control_data(db: Session = Depends(get_db)):
    """The map's figures and flows, for the page to refresh itself (and light the lines that moved)."""
    return control_room.snapshot(db)


@router.post("/system/control/read-market")
def read_market():
    """Every news feed read again now (as every 6 hours): the suggestions follow what it brings."""
    from fastapi.responses import RedirectResponse

    from app.database import SessionLocal
    from app.services import journal
    from app.services.background import run_in_background

    def read():
        db = SessionLocal()
        try:
            journal.read_all(db)
        finally:
            db.close()

    run_in_background(read)
    return RedirectResponse("/system/control?reading=1#ideas", status_code=303)


class Mark(BaseModel):
    key: str = Field(min_length=3, max_length=200)
    status: str                      # ok | problem | clear
    note: str = ""


@router.post("/system/control/mark")
def mark_test(payload: Mark, request: Request, db: Session = Depends(get_db)):
    user = auth.current(request)
    try:
        done = test_book.mark(db, payload.key, payload.status, payload.note, by=getattr(user, "name", "") or "")
    except ValueError as error:
        return JSONResponse(status_code=400, content={"detail": str(error)})
    progress = test_book.book(db, user)["progress"]
    return {**done, "progress": progress}

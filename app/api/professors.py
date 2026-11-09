"""The professors' space: every professor on one page, and each one's own space (who they are, their classes, what
candidates thought of them, the messages, their problems). See services/professor_space.py. A professor opens their
own at /teacher/space (api/teacher.py)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Professor
from app.services import professor_space
from app.template_context import notification_context

router = APIRouter(prefix="/professors", tags=["Professors"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


@router.get("", response_class=HTMLResponse)
def every_professor(request: Request, db: Session = Depends(get_db)):
    rows = professor_space.every(db)
    return templates.TemplateResponse(request=request, name="professors.html", context={
        "active_page": "professors", "rows": rows,
        "totals": {"professors": len(rows), "problems": sum(len(row["problems"]) for row in rows),
                   "urgent": sum(row["bad"] for row in rows), "teaching": sum(1 for row in rows if row["figures"]["running"])},
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


@router.get("/{professor_id}", response_class=HTMLResponse)
def one_professor(professor_id: str, request: Request, db: Session = Depends(get_db)):
    professor = db.get(Professor, professor_id)
    if professor is None:
        raise HTTPException(status_code=404, detail="Professor not found")
    return templates.TemplateResponse(request=request, name="professor_space.html", context={
        "active_page": "professors", **professor_space.space(db, professor),
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})

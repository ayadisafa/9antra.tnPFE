"""The Videos page: every video of the programs and of the website, played inside the application (services/videos.py)."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import program_icons, videos
from app.template_context import notification_context

router = APIRouter(tags=["Videos"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


@router.get("/programs/videos", response_class=HTMLResponse)
def videos_page(request: Request, db: Session = Depends(get_db)):
    found = videos.library(db)
    for group in found["programs"]:
        group["icon"] = program_icons.view(group["program"])
    return templates.TemplateResponse(request, "videos.html", {"active_page": "courses", **found})

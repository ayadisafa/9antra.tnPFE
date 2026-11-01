"""The agents page (who handles what, and what they did), trying the orchestrator on a sentence, and What's new."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import auth, message_categories, orchestrator, whats_new
from app.template_context import notification_context

router = APIRouter(tags=["Agents"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


@router.get("/assistant/agents", response_class=HTMLResponse)
def agents_page(request: Request, db: Session = Depends(get_db)):
    days = request.query_params.get("days")
    days = int(days) if days in ("1", "7", "30", "90") else 7
    return templates.TemplateResponse(request=request, name="agents.html", context={
        "active_page": "agents", "view": orchestrator.overview(db, days), "categories": message_categories.CATEGORIES,
        "examples": ["Le lien de la séance ne marche pas", "Combien coûte la formation DevOps ?", "Je serai absent samedi",
                     "What is trendy in AI?", "Quels services pourrions-nous ajouter ?", "C'est inadmissible, personne ne répond"],
    })


class Sentence(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.post("/assistant/agents/route")
def try_route(payload: Sentence, request: Request):
    """Which category and which agent a sentence would go to, and the words that decided (nothing is answered)."""
    routed = orchestrator.route(payload.message, auth.current(request))
    return {"category": routed["category"], "agent": routed["agent"]}


@router.get("/whats-new", response_class=HTMLResponse)
def whats_new_page(request: Request, db: Session = Depends(get_db)):
    whats_new.mark_seen(db)
    return templates.TemplateResponse(request=request, name="whats_new.html", context={
        "active_page": "whats_new", "releases": whats_new.RELEASES})

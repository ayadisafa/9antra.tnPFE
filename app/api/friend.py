"""What Yasmine, the animated friend, says on the page that is open (see services/friend.py).

Under /account, so everyone signed in may ask her; the counts of the whole centre
are only given to the admin.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import auth, friend, translations

router = APIRouter(tags=["Friend"])
_templates = Jinja2Templates(directory="app/templates")


class Question(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    lang: str | None = None          # chosen in her window: en, fr, ar, tn (none: the language of the question)


class Conversation(Question):
    history: list[dict[str, str]] = Field(default_factory=list)
    path: str | None = None          # the page the window is open on ("explique cette page")


@router.post("/account/guide")
def ask_the_guide(payload: Conversation, request: Request, db: Session = Depends(get_db)):
    """The assistant for everyone who is not given the whole assistant: a professor gets the professors' agent (their
    classes, their candidates, the courses, the guide), a team member the guide agent (how to do things here, for what
    they may do, what the courses teach)."""
    from app.services import conversation, orchestrator

    user = auth.current(request)
    page = payload.path if payload.path and payload.path.startswith("/") and not payload.path.startswith("//") else None
    found = orchestrator.dispatch_chat(db, payload.message.strip(), payload.history, user=user, lang=payload.lang, limited=True,
                                       path=(page or "")[:200] or None)
    conversation.save(db, user, payload.message, found, page)
    shown = conversation.response(found)
    shown["category"] = {key: found["category"][key] for key in ("key", "label", "icon", "colour")}
    return shown


@router.get("/account/conversation")
def the_conversation(request: Request, db: Session = Depends(get_db)):
    """The conversation going on with her (this person's only), to show again on any page."""
    from app.services import conversation

    return {"lines": conversation.lines(db, auth.current(request))}


@router.post("/account/conversation/new")
def new_conversation(request: Request, db: Session = Depends(get_db)):
    """A fresh conversation: the earlier one stays for "where were we?", out of the window."""
    from app.services import conversation

    conversation.start_new(db, auth.current(request))
    return {"ok": True}


@router.get("/account/guide/suggestions")
def guide_suggestions(request: Request, lang: str = ""):
    """A few questions to start with, for a professor or a team member."""
    from app.services import professor_assistant

    user = auth.current(request)
    if getattr(user, "is_professor", False):
        return {"suggestions": professor_assistant.suggestions(lang or translations.clean_language(request.cookies.get("lang")))}
    return {"suggestions": ["How do I change my password?", "What does the DevOps program teach?", "How does someone register?"]}


@router.get("/account/friend")
def what_she_says(request: Request, path: str = "/", lang: str = "", since: str = "", db: Session = Depends(get_db)):
    from datetime import datetime

    user = auth.current(request)
    path = path if path.startswith("/") and not path.startswith("//") else "/"
    # When this browser last heard from her (milliseconds, from the page): back after a while, she says what happened.
    try:
        last = datetime.utcfromtimestamp(int(since) / 1000) if since else None
    except (ValueError, OverflowError, OSError):
        last = None
    return friend.talk(db, user, path.split("?")[0][:200], translations.clean_language(request.cookies.get("lang")), guide_lang=lang or None,
                       since=last)


# ------------------------------------------------------------------
# The journal beside every page (services/journal.py): under /account, so professors and the team open it too.
# ------------------------------------------------------------------

@router.get("/account/journal", response_class=HTMLResponse)
def journal_panel(request: Request, db: Session = Depends(get_db)):
    """The latest articles, with their photo; for a professor, the fields they teach and what could make their courses better."""
    from app.services import journal

    user = auth.current(request)
    return _templates.TemplateResponse(request=request, name="_journal_panel.html",
                                       context={"journal": journal.panel(db, user), "is_admin": bool(getattr(user, "is_admin", False))},
                                       headers={"Cache-Control": "no-store"})


@router.get("/account/journal/new")
def journal_new(since: str = "", db: Session = Depends(get_db)):
    """How many articles arrived since this browser last opened the journal (its button's dot)."""
    from datetime import datetime

    from app.services import journal

    try:
        moment = datetime.utcfromtimestamp(int(since) / 1000) if since else None
    except (ValueError, OverflowError, OSError):
        moment = None
    return {"new": journal.new_since(db, moment)}

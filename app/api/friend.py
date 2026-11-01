"""What Yasmine, the animated friend, says on the page that is open (see services/friend.py).

Under /account, so everyone signed in may ask her; the counts of the whole centre
are only given to the admin.
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import auth, friend, translations

router = APIRouter(tags=["Friend"])


class Question(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    lang: str | None = None          # chosen in her window: en, fr, ar, tn (none: the language of the question)


class Conversation(Question):
    history: list[dict[str, str]] = Field(default_factory=list)


@router.post("/account/guide")
def ask_the_guide(payload: Conversation, request: Request, db: Session = Depends(get_db)):
    """The assistant for everyone who is not given the whole assistant: a professor gets the professors' agent (their
    classes, their candidates, the courses, the guide), a team member the guide agent (how to do things here, for what
    they may do, what the courses teach)."""
    import time

    from app.services import howto, orchestrator

    user = auth.current(request)
    message = payload.message.strip()
    started = time.monotonic()
    routed = orchestrator.route(message, user)
    if getattr(user, "is_professor", False):
        from app.services import professor_assistant

        found = professor_assistant.answer(db, message, payload.history, user=user, lang=payload.lang)
        agent = "professor"
    else:
        found = howto.answer(db, message, user, lang=payload.lang)
        agent = "guide"
    orchestrator.note(db, agent=agent, category=routed["category"], channel="professor" if agent == "professor" else "chat",
                      outcome="answered", asked=message, user_id=getattr(user, "id", None), took_ms=int((time.monotonic() - started) * 1000))
    db.commit()
    return {"reply": found["reply"], "links": found.get("links") or [], "sources": found.get("sources") or [], "resources": found.get("resources") or [],
            "schema": found.get("schema"), "suggestions": found.get("suggestions") or [],
            "agent": orchestrator.agent_of(agent), "category": {key: routed["category"][key] for key in ("key", "label", "icon", "colour")}}


@router.get("/account/guide/suggestions")
def guide_suggestions(request: Request, lang: str = ""):
    """A few questions to start with, for a professor or a team member."""
    from app.services import professor_assistant

    user = auth.current(request)
    if getattr(user, "is_professor", False):
        return {"suggestions": professor_assistant.suggestions(lang or translations.clean_language(request.cookies.get("lang")))}
    return {"suggestions": ["How do I change my password?", "What does the DevOps program teach?", "How does someone register?"]}


@router.get("/account/friend")
def what_she_says(request: Request, path: str = "/", lang: str = "", db: Session = Depends(get_db)):
    user = auth.current(request)
    path = path if path.startswith("/") and not path.startswith("//") else "/"
    return friend.talk(db, user, path.split("?")[0][:200], translations.clean_language(request.cookies.get("lang")), guide_lang=lang or None)

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


@router.post("/account/guide")
def ask_the_guide(payload: Question, request: Request, db: Session = Depends(get_db)):
    """Yasmine's answers for everyone: how to do things here (for what this person may do), what the courses teach."""
    from app.services import howto

    found = howto.answer(db, payload.message.strip(), auth.current(request), lang=payload.lang)
    return {"reply": found["reply"], "links": found.get("links") or [], "sources": found.get("sources") or [], "resources": found.get("resources") or [],
            "schema": found.get("schema")}


@router.get("/account/friend")
def what_she_says(request: Request, path: str = "/", lang: str = "", db: Session = Depends(get_db)):
    user = auth.current(request)
    path = path if path.startswith("/") and not path.startswith("//") else "/"
    return friend.talk(db, user, path.split("?")[0][:200], translations.clean_language(request.cookies.get("lang")), guide_lang=lang or None)

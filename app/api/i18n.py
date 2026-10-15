"""The language of the interface: choosing it, and the translations a page asks for."""
from fastapi import APIRouter, Depends, Form, Request
from urllib.parse import urlencode

from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import translations
from app.template_context import notification_context

router = APIRouter(tags=["Language"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])
COOKIE = "lang"


def language_of(request: Request) -> str:
    return translations.clean_language(request.cookies.get(COOKIE))


@router.post("/language")
def choose_language(request: Request, lang: str = Form("en"), next: str = Form("/")):
    """Remember the language on this browser and come back to the same page."""
    lang = translations.clean_language(lang)
    target = next if next.startswith("/") and not next.startswith("//") and "\\" not in next else "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(COOKIE, lang, max_age=365 * 86400, samesite="lax", path="/")
    return response


@router.get("/i18n/{lang}.json")
def dictionary(lang: str, db: Session = Depends(get_db)):
    if lang not in translations.TARGETS:
        return JSONResponse({"t": {}, "p": []})
    return JSONResponse(translations.dictionary(db, lang), headers={"Cache-Control": "private, max-age=600"})


class Seen(BaseModel):
    lang: str = ""
    texts: list[str] = []


@router.post("/i18n/seen")
def seen(payload: Seen, db: Session = Depends(get_db)):
    """Texts a page showed without a translation yet: they go first."""
    return {"marked": translations.mark_seen(db, payload.lang, payload.texts)}


@router.get("/settings/translations", response_class=HTMLResponse)
def translations_page(request: Request, db: Session = Depends(get_db)):
    """Read the translations and put any of them right."""
    lang = request.query_params.get("lang") if request.query_params.get("lang") in translations.TARGETS else "fr"
    search, only = request.query_params.get("q") or "", request.query_params.get("only") or ""
    return templates.TemplateResponse(
        request=request,
        name="translations.html",
        context={
            "active_page": "settings", "lang": lang, "search": search, "only": only,
            "rows": translations.listing(db, lang, search, only),
            "progress": translations.progress(db),
            "notice": request.query_params.get("notice"), "error": request.query_params.get("error"),
        },
    )


@router.post("/settings/translations/save")
def save_translation(lang: str = Form(""), source: str = Form(""), text: str = Form(""), q: str = Form(""), only: str = Form(""),
                     db: Session = Depends(get_db)):
    back = "/settings/translations?" + urlencode({key: value for key, value in (("lang", lang), ("q", q), ("only", only)) if value})
    if lang not in translations.TARGETS or not translations.correct(db, lang, source, text):
        return RedirectResponse(back + "&" + urlencode({"error": "Write the translation before saving."}), status_code=303)
    return RedirectResponse(back + "&" + urlencode({"notice": "Saved. Your wording is kept and the assistant will not change it."}), status_code=303)

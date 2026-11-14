"""The professors, all on one page (/professors): each professor's card (who they are, what they teach, their figures,
their access, what waits for them), their absences and the requests to decide, who teaches each program, and the
reminders to add their resources. Each professor also has their own space (/professors/{id}). See
services/professor_space.py and services/absences.py. A professor opens their own space at /teacher/space
(api/teacher.py); the answer to a refused absence is /absence/{token}, without signing in.

Adding, changing, giving access and deleting a professor stay under /classes/professors/… (api/classgroup.py)."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Professor
from app.models.professor_absence import ProfessorAbsence
from app.services import absences, auth, professor_space
from app.services.settings_service import get_settings, public_base_url
from app.services.ui_words import t as _t
from app.template_context import notification_context

router = APIRouter(prefix="/professors", tags=["Professors"])
public = APIRouter(tags=["Professors"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])
plain_templates = Jinja2Templates(directory="app/templates")
_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}


def _back(url: str, *, notice: str | None = None, error: str | None = None) -> RedirectResponse:
    path, _, anchor = url.partition("#")
    query = urlencode({key: value for key, value in (("notice", notice), ("error", error)) if value})
    joiner = "&" if "?" in path else "?"
    return RedirectResponse(path + (joiner + query if query else "") + (("#" + anchor) if anchor else ""), status_code=303)


def _who(request: Request) -> str:
    user = auth.current(request)
    return getattr(user, "name", None) or "Admin"


@router.get("", response_class=HTMLResponse)
def every_professor(request: Request, db: Session = Depends(get_db)):
    context = professor_space.page(db)
    asked = request.query_params.get("absence")
    return templates.TemplateResponse(request=request, name="professors.html", context={
        "active_page": "professors", **context, "min_password": auth.MIN_PASSWORD, "opened_absence": asked,
        "absence_decision": get_settings(db).absence_decision or "nobody", "settings": get_settings(db),
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


def _absence(db: Session, absence_id: str) -> ProfessorAbsence:
    absence = db.get(ProfessorAbsence, absence_id)
    if absence is None:
        raise HTTPException(status_code=404, detail="Absence not found")
    return absence


@router.post("/absences/{absence_id}/accept")
def accept_absence(absence_id: str, request: Request, note: str = Form(""), back: str = Form(""), db: Session = Depends(get_db)):
    absence = _absence(db, absence_id)
    if absences.state(absence) not in absences.WAITING:
        return _back(_safe(back), error=_t("This request is already decided."))
    opened = absences.accept(db, absence, public_base_url(db, request), by=_who(request), note=note)
    nobody = sum(1 for row in opened if row.status == "none")
    notice = _t("Accepted; {0} was told.", absence.professor.name)
    if opened:
        notice += " " + _t("{0} session(s) look for a replacement, {1} with nobody free: choose for them on the class.", len(opened), nobody) if nobody \
            else " " + _t("{0} session(s) look for a replacement.", len(opened))
    return _back(_safe(back), notice=notice)


@router.post("/absences/{absence_id}/refuse")
def refuse_absence(absence_id: str, request: Request, note: str = Form(""), back: str = Form(""), db: Session = Depends(get_db)):
    absence = _absence(db, absence_id)
    problem = absences.refuse(db, absence, public_base_url(db, request), by=_who(request), note=note)
    if problem:
        return _back(_safe(back), error=problem)
    return _back(_safe(back), notice=_t("Refused; {0} was told and can answer with one click.", absence.professor.name))


@router.post("/absences/{absence_id}/remove")
def remove_absence(absence_id: str, request: Request, back: str = Form(""), db: Session = Depends(get_db)):
    absence = _absence(db, absence_id)
    absences.cancel(db, absence, by=_who(request))
    return _back(_safe(back), notice=_t("The absence of {0} is removed.", absence.professor.name))


def _safe(back: str) -> str:
    return back if back.startswith("/professors") else "/professors#absences"


@router.get("/{professor_id}", response_class=HTMLResponse)
def one_professor(professor_id: str, request: Request, db: Session = Depends(get_db)):
    professor = db.get(Professor, professor_id)
    if professor is None:
        raise HTTPException(status_code=404, detail="Professor not found")
    return templates.TemplateResponse(request=request, name="professor_space.html", context={
        "active_page": "professors", **professor_space.space(db, professor),
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


# ----------------------------------------------------------------------
# The professor answers a refusal, from the link in their message
# ----------------------------------------------------------------------

def _answer_page(request: Request, db: Session, absence, *, done: str | None = None, status_code: int = 200):
    from app.services import brand

    return plain_templates.TemplateResponse(
        request=request, name="absence_answer.html", status_code=status_code, headers=_HEADERS,
        context={"absence": absence, "done": done, "app_name": brand.view(get_settings(db))["name"],
                 "period": absences.period_label(absence, french=True) if absence is not None else "",
                 "check": absences.of_absence(db, absence) if absence is not None and done in (None, "needs-words") else None,
                 "first": absences._first_name(absence.professor) if absence is not None else ""})


@public.get("/absence/{token}", response_class=HTMLResponse)
def answer_page(token: str, request: Request, db: Session = Depends(get_db)):
    absence = absences.by_token(db, token)
    if absence is None or absences.state(absence) != "refused":
        return _answer_page(request, db, None, status_code=404)
    return _answer_page(request, db, absence)


@public.post("/absence/{token}")
def answer_refusal(token: str, request: Request, answer: str = Form(""), words: str = Form(""), db: Session = Depends(get_db)):
    absence = absences.by_token(db, token)
    if absence is None or absences.state(absence) != "refused":
        return _answer_page(request, db, None, status_code=404)
    comes = answer == "come"
    if not comes and not words.strip():
        return _answer_page(request, db, absence, done="needs-words", status_code=400)
    absences.respond(db, absence, comes, words)
    return _answer_page(request, db, absence, done="come" if comes else "insists")

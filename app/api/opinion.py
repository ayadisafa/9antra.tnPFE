"""What candidates think at the end of their program: their private page, and
the admin's button to ask a class."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.classgroup import ClassGroup
from app.services import brand, program_feedback
from app.services.settings_service import get_settings

router = APIRouter(tags=["Opinion"])
# The candidate's page is opened without signing in: it gets nothing of the admin's pages.
templates = Jinja2Templates(directory="app/templates")
_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}


def _page(request: Request, db: Session, row, *, problem: str | None = None, edit: bool = False, status_code: int = 200):
    shown = row
    if row is not None and edit and row.status == "received":
        # Changing an opinion already given: the form again, filled in.
        shown = type("Editing", (), {
            "external_id": row.external_id, "status": "asked", "rating": row.rating,
            "comment": row.comment, "program_name": row.program_name,
        })()
    data = program_feedback._payload(row) if row is not None else {}
    name = ((row.author_name or "").split() or [""])[0].capitalize() if row is not None else ""
    return templates.TemplateResponse(
        request=request, name="opinion.html", status_code=status_code, headers=_HEADERS,
        context={
            "row": shown, "first_name": name, "problem": problem,
            "may_publish": bool(row is not None and program_feedback._payload(row).get("may_publish")),
            "facebook": program_feedback.facebook_reviews(),
            "professor": program_feedback.professor_of(db, row) if row is not None else "",
            "given": data, "thanks": request.query_params.get("thanks") == "1",
            "app_name": brand.view(get_settings(db))["name"],
        },
    )


@router.get("/opinion/{token}", response_class=HTMLResponse)
def opinion_page(token: str, request: Request, db: Session = Depends(get_db)):
    row = program_feedback.by_token(db, token)
    return _page(request, db, row, edit=request.query_params.get("edit") == "1", status_code=200 if row else 404)


@router.post("/opinion/{token}")
def give_opinion(
    token: str,
    request: Request,
    stars: int = Form(0),
    words: str = Form(""),
    publish: bool = Form(False),
    happy: int = Form(0),
    improve: str = Form(""),
    prof_stars: int = Form(0),
    prof_words: str = Form(""),
    recommend: str = Form(""),
    db: Session = Depends(get_db),
):
    row = program_feedback.by_token(db, token)
    if row is None:
        return _page(request, db, None, status_code=404)
    try:
        program_feedback.answer(db, row, stars=stars, words=words, may_publish=publish, happy=happy, improve=improve,
                                prof_stars=prof_stars, prof_words=prof_words, recommend=recommend)
    except ValueError as err:
        db.rollback()
        return _page(request, db, row, problem=str(err), edit=True, status_code=400)
    db.commit()
    return RedirectResponse(f"/opinion/{token}?thanks=1", status_code=303)


@router.post("/candidate/{candidate_id}/ask-review/{registration_id}")
def ask_one(candidate_id: str, registration_id: str, db: Session = Depends(get_db)):
    """Ask one candidate, now, what they thought of a program they finished."""
    from app.models.registration import Registration

    registration = db.get(Registration, registration_id)
    if registration is None or registration.candidate_id != candidate_id:
        raise HTTPException(status_code=404, detail="Registration not found")
    row = program_feedback.ask(db, registration, manual=True)
    db.commit()
    query = urlencode({"notice": "Asked: they get a link to give their review."} if row is not None
                      else {"error": "Not sent: they already answered, or no email or phone can reach them."})
    return RedirectResponse(f"/candidate/{candidate_id}?{query}#reviews", status_code=303)


@router.post("/classes/{class_id}/ask-opinions")
def ask_class(class_id: str, db: Session = Depends(get_db)):
    """The admin asks every candidate of a class what they thought, now."""
    class_group = db.get(ClassGroup, class_id)
    if class_group is None:
        raise HTTPException(status_code=404, detail="Class not found")
    outcome = program_feedback.ask_class(db, class_group)
    if outcome["sent"]:
        notice = f"Asked {outcome['sent']} candidate{'s' if outcome['sent'] != 1 else ''} for their opinion."
        if outcome["answered"]:
            notice += f" {outcome['answered']} had already answered."
        query = {"notice": notice}
    elif outcome["answered"] and not outcome["failed"]:
        query = {"notice": "Every candidate of this class already gave their opinion."}
    else:
        query = {"error": "No request could be sent: check that email sending works (Settings) and that the candidates have an address."}
    return RedirectResponse(f"/classes/{class_id}?{urlencode(query)}#opinions", status_code=303)

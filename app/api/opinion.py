"""What candidates think at the end of their program: their private page, and
the admin's button to ask a class.

The page takes ten seconds: the stars first (one tap, or the star they touched
in the email), then a few words if they want, then their professor if they want.
"""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.classgroup import ClassGroup
from app.services import brand, program_feedback, settings_service
from app.services.settings_service import get_settings
from app.template_context import notification_context

router = APIRouter(tags=["Opinion"])
# The candidate's page is opened without signing in: it gets nothing of the admin's pages.
templates = Jinja2Templates(directory="app/templates")
admin_templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])
_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}
STEPS = ("stars", "more", "prof")


def _step_of(request: Request, db: Session, row) -> str:
    """Which part of the page to show: the stars, a few words, the professor, or the thank-you."""
    asked = request.query_params.get("step")
    if row is None:
        return "none"
    if row.status != "received" or request.query_params.get("edit") == "1" or request.query_params.get("stars"):
        return "stars"
    if asked == "more":
        return "more"
    if asked == "prof" and program_feedback.professor_of(db, row):
        return "prof"
    return "thanks"


def _page(request: Request, db: Session, row, *, problem: str | None = None, status_code: int = 200, step: str | None = None):
    step = step or _step_of(request, db, row)
    data = program_feedback._payload(row) if row is not None else {}
    name = ((row.author_name or "").split() or [""])[0].capitalize() if row is not None else ""
    chosen = request.query_params.get("stars")
    chosen = int(chosen) if chosen in ("1", "2", "3", "4", "5") else None
    rated = row.rating if row is not None and row.status == "received" else None
    words = (row.comment or "") if row is not None and row.status == "received" else ""
    return templates.TemplateResponse(
        request=request, name="opinion.html", status_code=status_code, headers=_HEADERS,
        context={
            "row": row, "step": step, "first_name": name, "problem": problem,
            "chosen": chosen or rated, "tapped": chosen is not None and step == "stars", "rated": rated, "words": words,
            "low": bool(row is not None and row.status == "received" and program_feedback.is_low(row)),
            "may_publish": bool(data.get("may_publish")),
            "facebook": program_feedback.facebook_reviews(), "google": program_feedback.google_reviews(db),
            "professor": program_feedback.professor_of(db, row) if row is not None else "",
            "given": data, "app_name": brand.view(get_settings(db))["name"],
            "rating": program_feedback.rating(db), "rating_line": program_feedback.rating_line(db),
        },
    )


@router.get("/opinion/class/{token}", response_class=HTMLResponse)
def class_opinion_page(token: str, request: Request, db: Session = Depends(get_db)):
    """Where the QR code of the last session leads: each candidate finds their own review with their email or phone."""
    class_group = program_feedback.by_class_token(db, token)
    return templates.TemplateResponse(
        request=request, name="opinion.html", status_code=200 if class_group else 404, headers=_HEADERS,
        context={"row": None, "step": "find" if class_group else "none", "class_group": class_group, "problem": None,
                 "program": (class_group.program.name if class_group is not None and class_group.program else ""),
                 "app_name": brand.view(get_settings(db))["name"], "rating_line": program_feedback.rating_line(db)},
    )


@router.post("/opinion/class/{token}")
def class_opinion_find(token: str, request: Request, said: str = Form(""), db: Session = Depends(get_db)):
    class_group = program_feedback.by_class_token(db, token)
    if class_group is None:
        raise HTTPException(status_code=404, detail="This QR code is no longer valid")
    row = program_feedback.find_in_class(db, class_group, said)
    if row is None:
        return templates.TemplateResponse(
            request=request, name="opinion.html", status_code=400, headers=_HEADERS,
            context={"row": None, "step": "find", "class_group": class_group, "said": said,
                     "program": class_group.program.name if class_group.program else "",
                     "problem": "Nous ne trouvons pas cet email ou ce numéro dans cette classe. Essayez celui donné à l'inscription.",
                     "app_name": brand.view(get_settings(db))["name"], "rating_line": program_feedback.rating_line(db)},
        )
    db.commit()
    return RedirectResponse(f"/opinion/{row.external_id}", status_code=303)


@router.get("/opinion/{token}", response_class=HTMLResponse)
def opinion_page(token: str, request: Request, db: Session = Depends(get_db)):
    row = program_feedback.by_token(db, token)
    return _page(request, db, row, status_code=200 if row else 404)


@router.post("/opinion/{token}")
def give_opinion(
    token: str,
    request: Request,
    step: str = Form(""),
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
    after = "?thanks=1"
    try:
        if step == "stars":
            # One tap: the stars are kept now; the words and the professor come next, only if they want.
            if stars not in (1, 2, 3, 4, 5):
                raise ValueError("Touchez une étoile : de 1 (décevant) à 5 (excellent).")
            program_feedback.change(db, row, stars=stars)
            after = "?step=more"
        elif step == "more":
            program_feedback.change(db, row, words=words, improve=improve, may_publish=publish and (row.rating or 0) >= 4)
            after = "?step=prof" if program_feedback.professor_of(db, row) else "?thanks=1"
        elif step == "prof":
            program_feedback.change(db, row, prof_stars=prof_stars, prof_words=prof_words, recommend=recommend)
        else:
            # Everything at once (an older page, or a form posted whole).
            program_feedback.answer(db, row, stars=stars, words=words, may_publish=publish, happy=happy, improve=improve,
                                    prof_stars=prof_stars, prof_words=prof_words, recommend=recommend)
    except ValueError as err:
        db.rollback()
        return _page(request, db, row, problem=str(err), status_code=400, step="stars" if step in ("", "stars") else None)
    db.commit()
    return RedirectResponse(f"/opinion/{token}{after}", status_code=303)


@router.get("/classes/{class_id}/opinion-qr", response_class=HTMLResponse)
def class_qr(class_id: str, request: Request, db: Session = Depends(get_db)):
    """The QR code to show in the room at the last session: each candidate scans it and gives their review there."""
    class_group = db.get(ClassGroup, class_id)
    if class_group is None:
        raise HTTPException(status_code=404, detail="Class not found")
    token = program_feedback.class_token(db, class_group)
    db.commit()
    base = settings_service.public_base_url(db)
    from app.services import weekly_questions

    return admin_templates.TemplateResponse(
        request=request, name="opinion_qr.html",
        context={"class_group": class_group, "link": f"{base}/opinion/class/{token}", "local": weekly_questions.is_local_address(base),
                 "reviews": program_feedback.of_class(db, class_group.id)},
    )


@router.post("/candidate/{candidate_id}/review/{registration_id}/contacted")
def review_contacted(candidate_id: str, registration_id: str, request: Request, db: Session = Depends(get_db)):
    """The team contacted someone who was not fully happy (a call, a visit): the notification closes."""
    from app.services import auth

    row = program_feedback.of_registration(db, registration_id)
    if row is None or row.candidate_id != candidate_id:
        raise HTTPException(status_code=404, detail="Review not found")
    program_feedback.contacted(db, row, by=getattr(auth.current(request), "name", "") or "")
    db.commit()
    return RedirectResponse(f"/candidate/{candidate_id}?{urlencode({'notice': 'Noted: they were contacted.'})}#reviews", status_code=303)


@router.post("/candidate/{candidate_id}/celebrate/{registration_id}")
def celebrate(candidate_id: str, registration_id: str, kind: str = Form(""), words: str = Form(""), db: Session = Depends(get_db)):
    """A candidate succeeded (an internship, a PFE, a certification, a job): congratulated, and asked to tell it."""
    from app.models.registration import Registration

    registration = db.get(Registration, registration_id)
    if registration is None or registration.candidate_id != candidate_id:
        raise HTTPException(status_code=404, detail="Registration not found")
    try:
        message = program_feedback.celebrate(db, registration, kind, words)
    except ValueError as err:
        return RedirectResponse(f"/candidate/{candidate_id}?{urlencode({'error': str(err)})}#reviews", status_code=303)
    db.commit()
    sent = message is not None and message.status in ("sent", "to_send", "in_letter")
    query = ({"notice": "Congratulated: they are asked, at this happy moment, to tell it on Google or Facebook."} if sent
             else {"error": "Kept, but nothing could be sent: no email or phone reaches them."})
    return RedirectResponse(f"/candidate/{candidate_id}?{urlencode(query)}#reviews", status_code=303)


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

"""PDFs for clients: a program (see it, download it, send it to a candidate), several programs or all of them
in detail or in short, and any information written on the "PDF documents" page."""
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.candidate import Candidate
from app.models.program import Program
from app.models.registration import Registration, RegistrationStatus
from app.services import document_pdf, program_icons, program_pdf
from app.services.settings_service import get_settings
from app.template_context import notification_context

router = APIRouter(prefix="/programs", tags=["Program PDF"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def _program(db: Session, program_id: str) -> Program:
    program = db.get(Program, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    return program


def default_look(db: Session) -> str:
    return program_pdf.clean_style(getattr(get_settings(db), "program_pdf_style", None))


def _page(program_id: str, **values) -> RedirectResponse:
    query = urlencode({key: value for key, value in values.items() if value})
    return RedirectResponse(f"/programs/{program_id}/pdf{'?' + query if query else ''}", status_code=303)


@router.post("/pdf-look")
def choose_default_look(style: str = Form(""), program_id: str = Form(""), lang: str = Form("fr"), db: Session = Depends(get_db)):
    """The look used when nothing else is asked (the candidate's page, a quick download)."""
    if style not in program_pdf.STYLES:
        raise HTTPException(status_code=400, detail="Unknown look")
    settings = get_settings(db)
    settings.program_pdf_style = style
    db.commit()
    notice = f"“{program_pdf.STYLES[style]['name']}” is now the look used by default."
    if program_id and db.get(Program, program_id) is not None:
        return _page(program_id, look=style, lang=program_pdf.clean_language(lang), notice=notice)
    return RedirectResponse("/programs/all?" + urlencode({"notice": notice}), status_code=303)


def _some(db: Session, ids: str | None) -> list[Program]:
    """The programs named by ids ("a,b,c"); all of them when none is given."""
    wanted = [part for part in (ids or "").split(",") if part]
    chosen = [program for program in (db.get(Program, item) for item in wanted) if program is not None]
    return chosen or db.query(Program).all()


NOT_INSTALLED = "The PDF library is not installed: run “pip install reportlab” and restart."


def _pdf(data: bytes, name: str, download: bool) -> Response:
    """A PDF shown in the browser, or downloaded."""
    plain = name.encode("ascii", "ignore").decode("ascii").replace('"', "") or "document.pdf"
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f"{'attachment' if download else 'inline'}; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "no-store",
    })


def _compose(file, about: str, candidate: Candidate | None = None, **extra) -> RedirectResponse:
    """The composer, with the PDF attached and its email written. Nothing is sent before the admin presses Send."""
    query = {"attach": file.id, "program": about, **extra}
    if candidate is not None:
        query["candidate"] = candidate.id
    return RedirectResponse("/inbox/compose?" + urlencode(query), status_code=303)


def _programs_file(request: Request, db: Session, summary: bool) -> Response:
    if not program_pdf.available():
        raise HTTPException(status_code=503, detail=NOT_INSTALLED)
    lang = program_pdf.clean_language(request.query_params.get("lang"))
    chosen = _some(db, request.query_params.get("ids"))
    if not chosen:
        raise HTTPException(status_code=404, detail="There is no program yet.")
    candidate = db.get(Candidate, request.query_params.get("for") or "")
    data = (program_pdf.build_summary if summary else program_pdf.build_catalogue)(db, chosen, lang, candidate.full_name if candidate is not None else None)
    name = program_pdf.catalogue_filename(lang, len(chosen), len(chosen) >= db.query(Program).count(), summary)
    return _pdf(data, name, bool(request.query_params.get("download")))


@router.get("/catalogue.pdf")
def catalogue_file(request: Request, db: Session = Depends(get_db)):
    """Several programs, or all of them, in detail in one PDF: shown in the browser, or downloaded with ?download=1."""
    return _programs_file(request, db, summary=False)


@router.get("/summary.pdf")
def summary_file(request: Request, db: Session = Depends(get_db)):
    """Several programs, or all of them, in short: the essentials of each on a few pages."""
    return _programs_file(request, db, summary=True)


@router.post("/catalogue/send")
def send_catalogue(candidate_id: str = Form(""), lang: str = Form("fr"), ids: str = Form(""), personal: str = Form(""),
                   kind: str = Form(""), db: Session = Depends(get_db)):
    """Prepare the email that carries the programs (in detail, or in short with kind=summary). Nothing is sent here."""
    if not program_pdf.available():
        return RedirectResponse("/programs/documents?" + urlencode({"error": NOT_INSTALLED}), status_code=303)
    chosen = _some(db, ids)
    if not chosen:
        return RedirectResponse("/programs/all?" + urlencode({"error": "There is no program yet."}), status_code=303)
    candidate = db.get(Candidate, candidate_id) if candidate_id else None
    summary = kind == "summary"
    file = program_pdf.store_catalogue(db, chosen, program_pdf.clean_language(lang),
                                       candidate.full_name if candidate is not None and personal else None, summary=summary)
    return _compose(file, "summary" if summary else "all", candidate, count=len(chosen))


# ------------------------------------------------------------------
# Any information as a PDF
# ------------------------------------------------------------------

def _starters(db: Session) -> list[dict]:
    """Texts to start a document from, written from the application's own data."""
    programs = db.query(Program).order_by(Program.name).all()
    prices = "\n".join(f"- {program.name} : {program.price_online:.0f} DT en ligne, {program.price_onsite:.0f} DT en présentiel" for program in programs)
    lengths = "\n".join(
        f"- {program.name} : " + (f"{sum(len(level.weeks) for level in program.levels)} semaines" if program.levels else "durée à préciser")
        + (f", {program.hours_per_week:g} h par semaine" if program.hours_per_week else "") for program in programs)
    return [
        {"key": "prices", "name": "Price list", "icon": "bi-cash-coin", "title": "Tarifs de nos formations",
         "text": f"Voici les tarifs de nos formations :\n\n{prices}\n\n[box] Le paiement peut se faire en plusieurs fois : demandez-nous."},
        {"key": "programs", "name": "Programs and length", "icon": "bi-journal-bookmark", "title": "Nos formations et leur durée",
         "text": f"Nos formations :\n\n{lengths}\n\nLe programme détaillé de chacune est disponible sur demande."},
        {"key": "letter", "name": "An information letter", "icon": "bi-envelope-paper", "title": "Informations importantes",
         "text": "Bonjour à toutes et à tous,\n\nNous vous écrivons pour vous informer que …\n\nCe qu'il faut retenir :\n- …\n- …\n\n"
                 "[box gold] La date à ne pas oublier : …\n\nPour toute question, répondez simplement à notre email."},
    ]


@router.get("/documents", response_class=HTMLResponse)
def documents_page(request: Request, db: Session = Depends(get_db)):
    """Everything that can go to clients as a PDF: the programs in short or in detail, one program, or any text."""
    params = request.query_params
    return templates.TemplateResponse(request=request, name="documents.html", context={
        "active_page": "documents",
        "programs": db.query(Program).order_by(Program.name).all(),
        "count": db.query(Program).count(),
        "starters": _starters(db),
        "title": params.get("title") or "",
        "text": params.get("text") or "",
        "ready": program_pdf.available(),
        "limit": document_pdf.LIMIT,
        "notice": params.get("notice"),
        "error": params.get("error"),
    })


@router.post("/documents/pdf")
def document_file(title: str = Form(""), text: str = Form(""), lang: str = Form("fr"), for_name: str = Form(""), download: str = Form(""),
                  db: Session = Depends(get_db)):
    """The document as a PDF, made from what was written on the page."""
    if not program_pdf.available():
        raise HTTPException(status_code=503, detail=NOT_INSTALLED)
    if not text.strip():
        raise HTTPException(status_code=400, detail="Write the text of the document first.")
    data = document_pdf.build(db, title, text, lang, for_name or None)
    return _pdf(data, document_pdf.filename_of(title), bool(download))


@router.post("/documents/send")
def send_document(title: str = Form(""), text: str = Form(""), lang: str = Form("fr"), for_name: str = Form(""), db: Session = Depends(get_db)):
    """Prepare the email that carries the document: the admin chooses who receives it, reads it and presses Send."""
    if not program_pdf.available():
        return RedirectResponse("/programs/documents?" + urlencode({"error": NOT_INSTALLED}), status_code=303)
    if not text.strip():
        return RedirectResponse("/programs/documents?" + urlencode({"error": "Write the text of the document first.", "title": title}), status_code=303)
    file = document_pdf.store(db, title, text, lang, for_name or None)
    return _compose(file, "document", title=program_pdf.plain(title)[:140] or "Document")


@router.get("/{program_id}/pdf", response_class=HTMLResponse)
def pdf_page(program_id: str, request: Request, db: Session = Depends(get_db)):
    program = _program(db, program_id)
    chosen = default_look(db)
    look = request.query_params.get("look")
    look = look if look in program_pdf.STYLES else chosen
    lang = program_pdf.clean_language(request.query_params.get("lang"))
    registered = {
        row.candidate_id for row in db.query(Registration.candidate_id).filter(
            Registration.program_id == program.id, Registration.status != RegistrationStatus.CANCELLED).all()
    }
    people = [{"id": person.id, "name": person.full_name or person.email, "email": person.email, "registered": person.id in registered}
              for person in db.query(Candidate).order_by(Candidate.full_name).all() if person.email]
    content = program_pdf.content_of(db, program, lang)
    return templates.TemplateResponse(
        request=request,
        name="program_pdf.html",
        context={
            "active_page": "courses",
            "program": program,
            "icon": program_icons.view(program),
            "styles": program_pdf.STYLES,
            "languages": program_pdf.LANGUAGES,
            "look": look,
            "lang": lang,
            "default_look": chosen,
            "ready": program_pdf.available(),
            "thin": not content["levels"],
            "registered": [person for person in people if person["registered"]],
            "others": [person for person in people if not person["registered"]],
            "preselected": request.query_params.get("candidate") or "",
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/{program_id}/pdf/{style}")
def pdf_file(program_id: str, style: str, request: Request, db: Session = Depends(get_db)):
    """The PDF itself: shown in the browser, or downloaded with ?download=1."""
    program = _program(db, program_id)
    if style not in program_pdf.STYLES:
        raise HTTPException(status_code=404, detail="Unknown look")
    if not program_pdf.available():
        raise HTTPException(status_code=503, detail="The PDF library is not installed: run “pip install reportlab” and restart.")
    lang = program_pdf.clean_language(request.query_params.get("lang"))
    candidate = db.get(Candidate, request.query_params.get("for") or "")
    data = program_pdf.build(db, program, style, lang, candidate.full_name if candidate is not None else None)
    name = program_pdf.filename_of(program, lang)
    plain = name.encode("ascii", "ignore").decode("ascii").replace('"', "") or "programme.pdf"
    kind = "attachment" if request.query_params.get("download") else "inline"
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f"{kind}; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "no-store",
    })


@router.post("/{program_id}/pdf/send")
def send_to_candidate(program_id: str, candidate_id: str = Form(""), style: str = Form(""), lang: str = Form("fr"),
                      personal: str = Form(""), db: Session = Depends(get_db)):
    """Prepare the email that carries the PDF. Nothing is sent here: the admin reads it and presses Send."""
    program = _program(db, program_id)
    candidate = db.get(Candidate, candidate_id) if candidate_id else None
    style = style if style in program_pdf.STYLES else default_look(db)
    lang = program_pdf.clean_language(lang)
    if candidate is None or not candidate.email:
        return _page(program_id, look=style, lang=lang, error="Choose the candidate who receives the program.")
    if not program_pdf.available():
        return _page(program_id, look=style, lang=lang, error="The PDF library is not installed: run “pip install reportlab” and restart.")
    file = program_pdf.store(db, program, style, lang, candidate.full_name if personal else None)
    return RedirectResponse(
        "/inbox/compose?" + urlencode({"candidate": candidate.id, "attach": file.id, "program": program.id}), status_code=303)

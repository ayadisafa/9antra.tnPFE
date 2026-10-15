"""The level check: the admin's page, and the page a candidate answers on."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.classgroup import ClassGroup
from app.models.program import Program
from app.models.quiz import Quiz
from app.models.registration import Registration
from app.services import email_threads, level_bank, level_check, program_icons, weekly_questions
from app.services.enrollment_service import class_members
from app.services.settings_service import get_settings, public_base_url
from app.template_context import notification_context

router = APIRouter(tags=["Level checks"])

templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])
# The page a candidate answers on shows nothing of the application.
public_templates = Jinja2Templates(directory="app/templates")

PAGE = "/quiz/all"


def _back(notice: str | None = None, error: str | None = None) -> RedirectResponse:
    query = urlencode({key: value for key, value in (("notice", notice), ("error", error)) if value})
    return RedirectResponse(f"{PAGE}{'?' + query if query else ''}", status_code=303)


def _rich(text: str) -> Markup:
    """A question as the page shows it: `code` between backticks keeps its letters."""
    parts = str(escape(text or "")).split("`")
    return Markup("".join(f"<code>{part}</code>" if index % 2 else part for index, part in enumerate(parts)))


def _sections(items: list[dict]) -> list[dict]:
    """The form in the three parts a candidate goes through."""
    numbered = [dict(item, index=index, html=_rich(item.get("question", ""))) for index, item in enumerate(items)]
    for item in numbered:
        if item.get("type") == "check":
            item["options_html"] = [_rich(option) for option in item.get("options") or []]
    you = [item for item in numbered if item.get("type") in ("experience", "goal")]
    topics = [item for item in numbered if item.get("type") == "topic"]
    checks = [item for item in numbered if item.get("type") == "check"]
    notes = [item for item in numbered if item.get("type") == "note"]
    sections = [{"key": "you", "title": "Vous", "icon": "👋", "items": you, "notes": []}]
    if topics:
        sections.append({"key": "topics", "title": "Le programme", "icon": "🧭", "items": topics, "notes": []})
    if checks:
        sections.append({"key": "checks", "title": "Quelques questions", "icon": "💡", "items": checks, "notes": []})
    sections[-1]["notes"] = notes
    return sections


def _candidate_page(request: Request, db: Session, *, quiz=None, items=None, program_name="", first_name="",
                    result=None, problem=None, answers=None, preview=False, status_code=200):
    settings = get_settings(db)
    return public_templates.TemplateResponse(
        request=request,
        name="level_check.html",
        context={
            "quiz": quiz,
            "sections": _sections(items or []),
            "program_name": program_name,
            "first_name": first_name,
            "scale": level_check.SCALE,
            "steps": level_check.STEPS,
            "dont_know": level_check.DONT_KNOW,
            "result": result,
            "welcome": level_check.WELCOME.get(result["level"]) if result else None,
            "problem": problem,
            "answers": answers or [],
            "preview": preview,
            "app_name": settings.app_name or settings.platform_name or "9antra",
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )


# ------------------------------------------------------------------
# The admin's page
# ------------------------------------------------------------------

@router.get("/quiz/all", response_class=HTMLResponse)
def level_checks_page(request: Request, db: Session = Depends(get_db)):
    data = level_check.overview(db)
    base_url = public_base_url(db, request)
    programs = []
    for program in db.query(Program).order_by(Program.name.asc()).all():
        subject = level_bank.subject_of(program.name, program.website_slug)
        programs.append({
            "program": program,
            "icon": program_icons.view(program),
            "topics": len(level_check.topics_of(db, program)),
            "checks": len(level_bank.SUBJECTS[subject][1]) if subject else 0,
            "subject": level_bank.SUBJECTS[subject][0] if subject else None,
        })
    return templates.TemplateResponse(
        request=request,
        name="quizzes.html",
        context={
            "active_page": "quizzes",
            **data,
            "programs": programs,
            "levels_info": [level_check.level_info(key) for key in data["levels"]],
            "steps": level_check.STEPS,
            "focus": request.query_params.get("focus"),
            "email_ready": email_threads.smtp_configured(),
            "public_url": base_url,
            "public_url_local": weekly_questions.is_local_address(base_url),
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/quiz/center")
@router.get("/quiz/quizzes")
def old_addresses():
    return RedirectResponse(PAGE, status_code=307)


@router.post("/quiz/prepare-missing")
def prepare_missing(db: Session = Depends(get_db)):
    made = level_check.prepare_missing(db)
    if not made:
        return _back(notice="Every paid candidate whose class has not started already has a level check.")
    return _back(notice=f"{made} level check{'s' if made != 1 else ''} prepared. Nothing was sent: send them when you are ready.")


@router.post("/quiz/send-ready")
def send_ready(request: Request, db: Session = Depends(get_db)):
    """Send every level check that is prepared and was never sent."""
    if not email_threads.smtp_configured():
        return _back(error="Email is not set up, so nothing was sent. See Settings.")
    base_url = public_base_url(db, request)
    sent, failed = 0, 0
    for row in level_check.overview(db)["rows"]:
        if row["state"] != "ready" or not row["has_email"]:
            continue
        if level_check.send(db, row["quiz"], base_url) is not None and row["quiz"].sent_at is not None:
            sent += 1
        else:
            failed += 1
    if not sent and not failed:
        return _back(notice="No level check was waiting to be sent.")
    notice = f"{sent} level check{'s' if sent != 1 else ''} sent." if sent else None
    error = f"{failed} could not be sent; they are kept in the candidates' discussions (Inbox)." if failed else None
    if sent and weekly_questions.is_local_address(base_url):
        error = ((error + " ") if error else "") + (
            f"The link in these emails starts with {base_url}, which only opens on this computer. "
            "Set the address candidates can reach in Settings."
        )
    return _back(notice=notice, error=error)


@router.post("/quiz/reminders")
def switch_reminders(on: str = Form("1"), db: Session = Depends(get_db)):
    """Start or stop the one reminder sent to those who do not answer."""
    wanted = on == "1"
    level_check.set_reminders(db, wanted)
    return _back(notice="Reminders are on again: one reminder, two days after the level check, never a second."
                 if wanted else "Reminders are stopped: nobody is reminded to answer by itself.")


@router.post("/quiz/registration/{registration_id}/prepare")
def prepare_one(registration_id: str, db: Session = Depends(get_db)):
    registration = db.get(Registration, registration_id)
    if registration is None:
        raise HTTPException(status_code=404, detail="Registration not found")
    if not level_check.can_have_one(registration):
        return _back(error="A level check is for a candidate who paid and whose training is not over.")
    quiz = level_check.prepare(db, registration)
    return RedirectResponse(f"{PAGE}?focus={quiz.id}", status_code=303)


@router.post("/quiz/{quiz_id}/send")
def send_one(quiz_id: str, request: Request, db: Session = Depends(get_db)):
    quiz = db.get(Quiz, quiz_id)
    if quiz is None or not level_check.is_placement(quiz):
        raise HTTPException(status_code=404, detail="Level check not found")
    if quiz.completed_at is not None:
        return _back(notice="This candidate already answered.")
    base_url = public_base_url(db, request)
    message = level_check.send(db, quiz, base_url)
    if message is None:
        return _back(error="This candidate has no email address, so the level check could not be sent.")
    if quiz.sent_at is None:
        return _back(error="The email could not be sent. It is kept in the candidate's discussion (Inbox), "
                           "where you can send it again once email works.")
    error = None
    if weekly_questions.is_local_address(base_url):
        error = (f"The link in this email starts with {base_url}, which only opens on this computer. "
                 "Set the address candidates can reach in Settings.")
    return RedirectResponse(
        f"{PAGE}?" + urlencode({key: value for key, value in (("focus", quiz.id), ("notice", "Level check sent."), ("error", error)) if value}),
        status_code=303,
    )


@router.post("/quiz/class/{class_id}/send")
def send_class(class_id: str, request: Request, db: Session = Depends(get_db)):
    """Send the level check to the candidates of a class who have not received it."""
    class_group = db.get(ClassGroup, class_id)
    if class_group is None:
        raise HTTPException(status_code=404, detail="Class not found")
    back = f"/classes/{class_id}"
    if not email_threads.smtp_configured():
        return RedirectResponse(f"{back}?" + urlencode({"error": "Email is not set up, so nothing was sent. See Settings."}) + "#levels", status_code=303)
    base_url = public_base_url(db, request)
    sent = 0
    for registration in class_members(db, class_id):
        if not level_check.can_have_one(registration) or not (registration.candidate and registration.candidate.email):
            continue
        quiz = level_check.prepare(db, registration)
        if quiz.sent_at is None and quiz.completed_at is None:
            level_check.send(db, quiz, base_url)
            sent += quiz.sent_at is not None
    message = {"notice": f"Level check sent to {sent} candidate{'s' if sent != 1 else ''} of {class_group.name}."}
    if sent and weekly_questions.is_local_address(base_url):
        message["error"] = (f"The link in these emails starts with {base_url}, which only opens on this computer. "
                            "Set the address candidates can reach in Settings.")
    return RedirectResponse(f"{back}?" + urlencode(message) + "#levels", status_code=303)


@router.get("/quiz/preview/{program_id}", response_class=HTMLResponse)
def preview(program_id: str, request: Request, db: Session = Depends(get_db)):
    """What a candidate of this program is asked, exactly as they see it. Nothing is saved."""
    program = db.get(Program, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    items = level_check.build_form(db, program, program.name, f"preview-{program.id}")
    return _candidate_page(request, db, items=items, program_name=program.name, first_name="Prénom", preview=True)


# ------------------------------------------------------------------
# The candidate's page
# ------------------------------------------------------------------

def _first_name(db: Session, quiz: Quiz) -> str:
    registration = db.get(Registration, quiz.registration_id) if quiz.registration_id else None
    candidate = registration.candidate if registration else None
    name = (candidate.full_name or "").strip() if candidate else ""
    return name.split()[0].capitalize() if name else ""


@router.get("/level/{token}", response_class=HTMLResponse)
def level_page(token: str, request: Request, db: Session = Depends(get_db)):
    quiz = level_check.by_token(db, token)
    if quiz is None:
        return _candidate_page(request, db, status_code=404)
    return _candidate_page(
        request, db, quiz=quiz, items=level_check.items_of(quiz), program_name=quiz.course_name or "",
        first_name=_first_name(db, quiz), result=level_check.result_of(quiz),
    )


@router.post("/level/{token}", response_class=HTMLResponse)
async def answer_level(token: str, request: Request, db: Session = Depends(get_db)):
    quiz = level_check.by_token(db, token)
    if quiz is None:
        return _candidate_page(request, db, status_code=404)
    items = level_check.items_of(quiz)
    if quiz.completed_at is not None:       # answered once: the result stays
        return RedirectResponse(f"/level/{token}", status_code=303)
    form = await request.form()
    answers = level_check.read_answers(items, form)
    if level_check.missing(items, answers):
        return _candidate_page(
            request, db, quiz=quiz, items=items, program_name=quiz.course_name or "", first_name=_first_name(db, quiz),
            answers=answers, problem="Il manque une réponse : les questions concernées sont marquées ci-dessous.",
            status_code=422,
        )
    level_check.submit(db, quiz, answers, level_check.read_timing(items, form.get("timing")))
    return RedirectResponse(f"/level/{token}", status_code=303)

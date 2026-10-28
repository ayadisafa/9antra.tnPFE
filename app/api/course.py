"""A class's course, week by week: the files the professor adds, the questions
written from them, and what candidates answered. Also the private page where
a candidate answers their questions of the week."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ClassGroup, ClassStatus
from app.models.course_material import ClassMaterial, ClassWeek, WeekQuestion
from app.services import (
    answer_sync, auth, course_materials, email_assistant, email_threads, program_icons, week_insights, weekly_questions,
)
from app.services.ui_words import t as _t
from app.services.class_views import class_summary
from app.services.schedule_utils import local_now, utc_to_local
from app.services.settings_service import public_base_url
from app.template_context import notification_context

router = APIRouter(tags=["Course"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])
# The candidate's page shows nothing of the admin application.
public_templates = Jinja2Templates(directory="app/templates")


def _app_name() -> str:
    """The application's name, for the pages candidates open without signing in."""
    from app.database import SessionLocal
    from app.services import brand
    from app.services.settings_service import get_settings

    db = SessionLocal()
    try:
        return brand.view(get_settings(db))["name"]
    finally:
        db.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_class(db: Session, class_id: str) -> ClassGroup:
    class_group = db.get(ClassGroup, class_id)
    if class_group is None:
        raise HTTPException(status_code=404, detail="Class not found")
    return class_group


def _back(class_id: str, week: int | None = None, notice: str | None = None,
          error: str | None = None, anchor: str = "") -> RedirectResponse:
    query = urlencode({
        key: value for key, value in (("week", week), ("notice", notice), ("error", error)) if value
    })
    return RedirectResponse(f"/classes/{class_id}/course{'?' + query if query else ''}{anchor}", status_code=303)


def _get_question(db: Session, class_id: str, question_id: str) -> WeekQuestion:
    question = db.get(WeekQuestion, question_id)
    if question is None or question.class_group_id != class_id:
        raise HTTPException(status_code=404, detail="Question not found")
    return question


def _week_overview(db: Session, class_group: ClassGroup, selected: int) -> list[dict]:
    """One line per week of the class: its subject and where its course and questions stand."""
    rows = {row.week_number: row for row in db.query(ClassWeek).filter(ClassWeek.class_group_id == class_group.id).all()}
    files: dict[int, int] = {}
    for (number,) in db.query(ClassMaterial.week_number).filter(ClassMaterial.class_group_id == class_group.id).all():
        files[number] = files.get(number, 0) + 1
    questions: dict[int, dict[str, int]] = {}
    for number, status in db.query(WeekQuestion.week_number, WeekQuestion.status).filter(
        WeekQuestion.class_group_id == class_group.id
    ).all():
        questions.setdefault(number, {})
        questions[number][status] = questions[number].get(status, 0) + 1
    current = class_group.current_week or 0
    overview = []
    for number in range(1, course_materials.week_count(class_group) + 1):
        row = rows.get(number)
        counts = questions.get(number, {})
        results = weekly_questions.week_results(db, class_group, number) if row is not None and row.sent_at else None
        overview.append({
            "number": number,
            "topic": course_materials.week_topic(db, class_group.id, number),
            "files": files.get(number, 0),
            "has_notes": bool(row is not None and (row.notes or "").strip()),
            "ready": counts.get("ready", 0),
            "review": counts.get("review", 0),
            "status": row.questions_status if row is not None else "idle",
            "sent": bool(row is not None and row.sent_at),
            "average": results["average"] if results else None,
            "level": results["level"] if results else None,
            "is_current": class_group.status == ClassStatus.ONGOING and number == current,
            "is_past": class_group.status == ClassStatus.COMPLETED or number < current,
            "is_selected": number == selected,
        })
    return overview


# ------------------------------------------------------------------
# The course page of a class
# ------------------------------------------------------------------

@router.get("/classes/{class_id}/course", response_class=HTMLResponse)
def course_page(class_id: str, request: Request, db: Session = Depends(get_db)):
    class_group = _get_class(db, class_id)
    total = course_materials.week_count(class_group)
    try:
        selected = int(request.query_params.get("week") or 0)
    except ValueError:
        selected = 0
    if not 1 <= selected <= total:
        selected = min(total, max(1, class_group.current_week or 1))

    week = db.query(ClassWeek).filter(
        ClassWeek.class_group_id == class_group.id, ClassWeek.week_number == selected,
    ).first()
    materials = (
        db.query(ClassMaterial)
        .filter(ClassMaterial.class_group_id == class_group.id, ClassMaterial.week_number == selected)
        .order_by(ClassMaterial.created_at.asc())
        .all()
    )
    questions = weekly_questions.questions_of(db, class_group.id, selected)
    members = weekly_questions.members_of(db, class_group.id)
    base_url = public_base_url(db, request)
    now = local_now()

    return templates.TemplateResponse(
        request=request,
        name="course.html",
        context={
            "active_page": "classes",
            "cls": class_group,
            "summary": class_summary(class_group, now),
            "program_icon": program_icons.view(class_group.program) if class_group.program else None,
            "weeks": _week_overview(db, class_group, selected),
            "selected": selected,
            "topic": course_materials.week_topic(db, class_group.id, selected),
            "week": week,
            "materials": [
                {"file": item, "learned": course_materials.learned_label(item), "url": course_materials.url_of(item),
                 "icon": course_materials.KIND_ICONS.get(item.kind, "bi-file-earmark"),
                 "size": course_materials.human_size(item.size),
                 "review": course_materials.review_view(db, item, class_group.program)}
                for item in materials
            ],
            "can_learn": sum(len(source["text"]) for source in course_materials.sources(db, class_group.id, selected)) >= weekly_questions.MIN_TO_LEARN,
            "questions": questions,
            "counts": {
                "ready": sum(1 for item in questions if item.status == "ready"),
                "review": sum(1 for item in questions if item.status == "review"),
                "off": sum(1 for item in questions if item.status == "off"),
            },
            "results": weekly_questions.week_results(db, class_group, selected),
            "feelings": week_insights.week_feelings(
                db, class_group, selected, hardest=weekly_questions.week_results(db, class_group, selected)["hardest"],
            ),
            "forms": answer_sync.status(),
            "week_form": answer_sync.form_of(week),
            "members": members,
            "week_over": weekly_questions.week_is_over(db, class_group, selected, now),
            "sent_local": utc_to_local(week.sent_at) if week is not None and week.sent_at else None,
            "per_candidate": class_group.weekly_questions_count or weekly_questions.DEFAULT_PER_CANDIDATE,
            "mode": class_group.weekly_questions_mode or "manual",
            "accept": course_materials.ACCEPT,
            "max_video_mb": course_materials.MAX_MEDIA_BYTES // course_materials.MB,
            "model_ready": email_assistant.model_available(),
            "smtp_ready": email_threads.smtp_configured(),
            "public_url": base_url,
            "public_url_local": weekly_questions.is_local_address(base_url),
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/classes/{class_id}/course/settings")
def save_course_settings(
    class_id: str,
    week: int = Form(1),
    weekly_questions_mode: str = Form("manual"),
    weekly_questions_count: int = Form(5),
    db: Session = Depends(get_db),
):
    class_group = _get_class(db, class_id)
    if not 1 <= weekly_questions_count <= 20:
        return _back(class_id, week, error="Each candidate gets between 1 and 20 questions.")
    class_group.weekly_questions_mode = "auto"            # always on: every week, after its session
    class_group.weekly_questions_count = weekly_questions_count
    db.commit()
    return _back(class_id, week, notice=f"Saved: {weekly_questions_count} questions per candidate, sent every week after its session.")


# ------------------------------------------------------------------
# Course files and notes
# ------------------------------------------------------------------

@router.post("/classes/{class_id}/course/{week}/files")
def add_course_files(
    class_id: str,
    week: int,
    request: Request,
    files: list[UploadFile] = File(default=[]),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    """Add files to a week. The system reads them, then writes questions from them."""
    class_group = _get_class(db, class_id)
    added, refused = [], []
    for upload in files:
        if not upload.filename:
            continue
        try:
            user = auth.current(request)
            added.append(course_materials.save_upload(db, class_group, week, upload, note, by=user.name if user else None))
        except course_materials.UploadRefused as exc:
            db.rollback()
            refused.append(str(exc))
    if not added and not refused:
        refused.append("Choose at least one file.")

    notice = None
    if added:
        learned = [item for item in added if (item.learned_status == "read" or item.note) and item.review_status != "waiting"]
        notice = f"{len(added)} file{'s' if len(added) != 1 else ''} added to week {week}."
        held = [item for item in added if item.review_status == "waiting"]
        if held:
            viewer = auth.current(request)
            notice += " " + _t("“{0}” waits for the admin's approval: it does not look like part of this program.", held[0].filename)
            if viewer is None or not viewer.is_admin:
                _tell_admin_file(db, class_group, held[0])
        from app.services import course_requests
        from app.services.background import run_in_background

        run_in_background(course_requests.deliver_waiting_task, class_group.id, week)   # those who asked for this week's course
        if learned:
            weekly_questions.queue(db, class_group, week)
            notice += " The system is writing questions from what it read; they appear here in a few minutes."
        else:
            notice += " Write a few lines about what they show, so the system has something to learn from."
    error = " ".join(refused) or None
    # The page's own script asks for a short answer; a plain form gets the page back.
    if request.headers.get("x-requested-with") == "fetch":
        return {"ok": bool(added), "notice": notice, "error": error,
                "url": f"/classes/{class_id}/course?" + urlencode({k: v for k, v in (("week", week), ("notice", notice), ("error", error)) if v})}
    return _back(class_id, week, notice=notice, error=error)


def _tell_admin_file(db: Session, class_group: ClassGroup, material: ClassMaterial) -> None:
    from app.models import Program
    from app.services.notification_service import create_notification

    own = class_group.program.name if class_group.program else class_group.name
    other = db.get(Program, material.fit_program_id) if material.fit_program_id and material.fit_program_id != class_group.program_id else None
    message = (f"A file added to week {material.week_number} of {class_group.name} looks like part of {other.name}, not {own}."
               if other is not None else f"A file added to week {material.week_number} of {class_group.name} does not look like part of {own}.")
    create_notification(db, title=f"A course file waits for you: {material.filename}", message=message, category="COURSE_FILE",
                        action_url=f"/classes/{class_group.id}/course?week={material.week_number}&file={material.id}#course-files")


def _admin_only(request: Request) -> str | None:
    user = auth.current(request)
    return user.name if user is not None and user.is_admin else None


@router.post("/classes/{class_id}/course/files/{material_id}/accept")
def accept_course_file(class_id: str, material_id: str, request: Request, db: Session = Depends(get_db)):
    """The admin keeps a file that did not look like part of the program: it is used from now on."""
    class_group = _get_class(db, class_id)
    material = db.get(ClassMaterial, material_id)
    if material is None or material.class_group_id != class_id:
        raise HTTPException(status_code=404, detail="File not found")
    who = _admin_only(request)
    if who is None:
        return _back(class_id, material.week_number, error="Only the admin decides whether this file is part of the course.")
    course_materials.accept(db, material, who)
    if material.learned_status == "read" or material.note:
        weekly_questions.queue(db, class_group, material.week_number)
    from app.services import course_requests
    from app.services.background import run_in_background

    run_in_background(course_requests.deliver_waiting_task, class_group.id, material.week_number)
    return _back(class_id, material.week_number, notice=_t("“{0}” is part of week {1} now: the system learns from it.", material.filename, material.week_number))


@router.post("/classes/{class_id}/course/files/{material_id}/move")
def move_course_file(class_id: str, material_id: str, request: Request, target: str = Form(""), db: Session = Depends(get_db)):
    """The admin puts a file where it belongs: a class of the program it is part of, or that program's own files."""
    from app.models import Program
    from app.services import program_files

    _get_class(db, class_id)
    material = db.get(ClassMaterial, material_id)
    if material is None or material.class_group_id != class_id:
        raise HTTPException(status_code=404, detail="File not found")
    who = _admin_only(request)
    if who is None:
        return _back(class_id, material.week_number, error="Only the admin decides whether this file is part of the course.")
    kind, _, rest = target.partition(":")
    if kind == "program":
        program = db.get(Program, rest)
        if program is None:
            return _back(class_id, material.week_number, error="Choose where the file goes.")
        name = material.filename
        program_files.from_material(db, material, program, who)
        return RedirectResponse(f"/programs/expertise?{urlencode({'notice': _t('“{0}” is a file of {1} now.', name, program.name)})}#ex-{program.id}",
                                status_code=303)
    target_id, _, week_text = rest.partition(":")
    group = db.get(ClassGroup, target_id) if kind == "class" else None
    if group is None:
        return _back(class_id, material.week_number, error="Choose where the file goes.")
    try:
        week = int(week_text or 1)
        course_materials.move(db, material, group, week, who)
    except (ValueError, course_materials.UploadRefused) as exc:
        db.rollback()
        return _back(class_id, material.week_number, error=str(exc) or "Choose where the file goes.")
    if material.learned_status == "read" or material.note:
        weekly_questions.queue(db, group, week)
    return _back(group.id, week, notice=_t("“{0}” is in {1}, week {2} now.", material.filename, group.name, week))


@router.get("/classes/files/{material_id}")
def open_course_file(material_id: str, db: Session = Depends(get_db)):
    material = db.get(ClassMaterial, material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="File not found")
    path = course_materials.path_of(material)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This file is no longer on the server")
    inline = material.kind in course_materials.INLINE_KINDS or material.content_type == "application/pdf"
    return FileResponse(
        path,
        media_type=material.content_type,
        filename=material.filename,
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )


@router.post("/classes/{class_id}/course/files/{material_id}/note")
def save_file_note(class_id: str, material_id: str, note: str = Form(""), db: Session = Depends(get_db)):
    class_group = _get_class(db, class_id)
    material = db.get(ClassMaterial, material_id)
    if material is None or material.class_group_id != class_id:
        raise HTTPException(status_code=404, detail="File not found")
    before = (material.note or "").strip()
    course_materials.set_note(db, material, note)
    notice = "Note saved."
    if (material.note or "").strip() and (material.note or "").strip() != before:
        weekly_questions.queue(db, class_group, material.week_number)
        notice = "Note saved. The system is writing questions from it."
    return _back(class_id, material.week_number, notice=notice)


@router.post("/classes/{class_id}/course/files/{material_id}/delete")
def delete_course_file(class_id: str, material_id: str, db: Session = Depends(get_db)):
    _get_class(db, class_id)
    material = db.get(ClassMaterial, material_id)
    if material is None or material.class_group_id != class_id:
        raise HTTPException(status_code=404, detail="File not found")
    week, name = material.week_number, material.filename
    course_materials.delete(db, material)
    return _back(class_id, week, notice=f"“{name}” removed. The questions already written from it are kept.")


@router.post("/classes/{class_id}/course/{week}/notes")
def save_week_notes(class_id: str, week: int, notes: str = Form(""), db: Session = Depends(get_db)):
    class_group = _get_class(db, class_id)
    try:
        before = db.query(ClassWeek).filter(ClassWeek.class_group_id == class_id, ClassWeek.week_number == week).first()
        before_text = (before.notes or "").strip() if before else ""
        row = course_materials.set_week_notes(db, class_group, week, notes)
    except course_materials.UploadRefused as exc:
        return _back(class_id, error=str(exc))
    notice = "Notes saved."
    if (row.notes or "") and (row.notes or "").strip() != before_text:
        # New notes are new course: questions about them are written again.
        db.query(WeekQuestion).filter(
            WeekQuestion.class_group_id == class_id, WeekQuestion.week_number == week,
            WeekQuestion.material_id == "notes", WeekQuestion.author == "system",
        ).delete(synchronize_session=False)
        weekly_questions.queue(db, class_group, week)
        notice = "Notes saved. The system is writing questions from them."
    return _back(class_id, week, notice=notice)


# ------------------------------------------------------------------
# Questions
# ------------------------------------------------------------------

@router.post("/classes/{class_id}/course/{week}/questions/write")
def write_questions(class_id: str, week: int, mode: str = Form("new"), db: Session = Depends(get_db)):
    class_group = _get_class(db, class_id)
    try:
        course_materials.check_week(class_group, week)
    except course_materials.UploadRefused as exc:
        return _back(class_id, error=str(exc))
    weekly_questions.queue(db, class_group, week, mode if mode in ("new", "more", "fresh") else "new")
    return _back(class_id, week, notice="The system is writing the questions. It takes a few minutes; this page updates by itself.", anchor="#questions")


@router.post("/classes/{class_id}/course/{week}/questions/add")
async def add_question(class_id: str, week: int, request: Request, db: Session = Depends(get_db)):
    class_group = _get_class(db, class_id)
    try:
        data = weekly_questions.read_question_form(await request.form())
        weekly_questions.add_question(db, class_group, week, data)
    except (ValueError, course_materials.UploadRefused) as exc:
        return _back(class_id, week, error=str(exc), anchor="#questions")
    return _back(class_id, week, notice="Your question was added.", anchor="#questions")


@router.post("/classes/{class_id}/course/questions/{question_id}/save")
async def save_question(class_id: str, question_id: str, request: Request, db: Session = Depends(get_db)):
    question = _get_question(db, class_id, question_id)
    try:
        weekly_questions.save_question(db, question, weekly_questions.read_question_form(await request.form()))
    except ValueError as exc:
        return _back(class_id, question.week_number, error=str(exc), anchor="#questions")
    return _back(class_id, question.week_number, notice="Question saved and ready.", anchor="#questions")


@router.post("/classes/{class_id}/course/questions/{question_id}/status")
def set_question_status(class_id: str, question_id: str, status: str = Form(...), db: Session = Depends(get_db)):
    question = _get_question(db, class_id, question_id)
    try:
        weekly_questions.set_status(db, question, status)
    except ValueError as exc:
        return _back(class_id, question.week_number, error=str(exc), anchor="#questions")
    return _back(class_id, question.week_number, anchor="#questions")


@router.post("/classes/{class_id}/course/{week}/questions/approve-all")
def approve_all_questions(class_id: str, week: int, db: Session = Depends(get_db)):
    _get_class(db, class_id)
    waiting = [item for item in weekly_questions.questions_of(db, class_id, week) if item.status == "review"]
    for question in waiting:
        question.status = "ready"
        question.review_reason = None
    db.commit()
    return _back(class_id, week, notice=f"{len(waiting)} question(s) marked ready.", anchor="#questions")


@router.post("/classes/{class_id}/course/questions/{question_id}/delete")
def delete_question(class_id: str, question_id: str, db: Session = Depends(get_db)):
    question = _get_question(db, class_id, question_id)
    week = question.week_number
    db.delete(question)
    db.commit()
    return _back(class_id, week, notice="Question deleted.", anchor="#questions")


# ------------------------------------------------------------------
# Sending
# ------------------------------------------------------------------

@router.post("/classes/{class_id}/course/{week}/send")
def send_week_questions(class_id: str, week: int, request: Request, remind: bool = Form(False),
                        db: Session = Depends(get_db)):
    """Email each candidate their questions; `remind` sends them again to those who have not answered."""
    class_group = _get_class(db, class_id)
    if not email_threads.smtp_configured():
        return _back(class_id, week, error="Email is not configured, so nothing can be sent. See Settings.", anchor="#send")
    base_url = public_base_url(db, request)
    if remind:
        outcome = weekly_questions.remind_week(db, class_group, week, base_url=base_url)
    else:
        outcome = weekly_questions.send_week(db, class_group, week, base_url=base_url, manual=True)
    if outcome.get("error"):
        return _back(class_id, week, error=outcome["error"], anchor="#send")

    parts = []
    if outcome["sent"]:
        parts.append(f"{outcome['sent']} candidate(s) received their questions")
    if outcome.get("already"):
        parts.append(f"{outcome['already']} already had them")
    notice = (", ".join(parts) + ".") if parts else None
    error = None
    if outcome["failed"]:
        error = (
            f"{outcome['failed']} email(s) could not be delivered. They are saved in the candidates' discussions; "
            "press Send again once email works."
        )
    if weekly_questions.is_local_address(base_url) and outcome["sent"]:
        from app.services.ui_words import t as _t

        notice = ((notice + " ") if notice else "") + _t(
            "This computer cannot be reached from outside: the questions are written in the emails, and candidates answer by replying."
        )
    return _back(class_id, week, notice=notice, error=error, anchor="#send")


# ------------------------------------------------------------------
# The candidate's page
# ------------------------------------------------------------------

def _learn_page(request: Request, quiz, result: dict, problem: str | None = None):
    return public_templates.TemplateResponse(
        request=request,
        name="learn.html",
        context={
            "quiz": quiz, "result": result, "problem": problem,
            "feel": {
                "questions": week_insights.QUESTIONS,
                "mood": [(value, *words.split(" ", 1)) for value, words in week_insights.choices("mood")],
                "pace": week_insights.choices("pace"), "confidence": week_insights.choices("confidence"),
            },
            "said": week_insights.feedback_of(quiz) if quiz is not None else {},
            "app_name": _app_name(),
        },
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"},
    )


@router.get("/learn/{token}", response_class=HTMLResponse)
def learn_page(token: str, request: Request, db: Session = Depends(get_db)):
    quiz = weekly_questions.quiz_by_token(db, token)
    if quiz is None:
        return public_templates.TemplateResponse(
            request=request, name="learn.html", context={"quiz": None, "result": None, "problem": None},
            status_code=404, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
        )
    return _learn_page(request, quiz, weekly_questions.result_of(quiz))


@router.post("/learn/{token}", response_class=HTMLResponse)
async def learn_answer(token: str, request: Request, db: Session = Depends(get_db)):
    quiz = weekly_questions.quiz_by_token(db, token)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Not found")
    if quiz.completed_at is not None:
        return RedirectResponse(f"/learn/{token}", status_code=303)
    form = await request.form()
    questions = weekly_questions.questions_in(quiz)
    answers: list[int | None] = []
    for index, question in enumerate(questions):
        try:
            value = int(form.get(f"q{index}", ""))
        except (TypeError, ValueError):
            value = None
        answers.append(value if value is not None and 0 <= value < len(question.get("options", [])) else None)
    if any(answer is None for answer in answers):
        return _learn_page(request, quiz, weekly_questions.result_of(quiz),
                           problem="Merci de répondre à toutes les questions avant d'envoyer.")
    weekly_questions.grade(db, quiz, answers, feedback=week_insights.clean_feedback(form), source="page")
    return RedirectResponse(f"/learn/{token}", status_code=303)

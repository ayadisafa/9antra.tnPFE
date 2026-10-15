"""Admin settings page: default class parameters, reminders and email delivery."""
import os
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ClassGroup, ClassStatus
from app.services import (
    answer_sync, brand as app_identity, class_emails, email_assistant, google_forms, mailbox_service, theme,
    website_sync, weekly_questions, whatsapp,
)
from app.services.email_layout import DEFAULT_ACCENT, DEFAULT_SIGNATURE, branding, clean_accent, render_email
from app.services.email_service import send_email_safely
from app.services.enrollment_service import (
    START_AFTER_DAYS,
    START_FIXED_DATE,
    START_MIN_ENROLLMENT,
    class_capacity,
    update_class_parameters,
)
from app.services.schedule_utils import DAY_KEYS, DAY_SHORT_EN, format_days, parse_days, parse_time
from app.services.settings_service import (
    clean_public_url,
    format_reminder_days,
    get_settings,
    parse_reminder_days,
    public_base_url,
)
from app.template_context import notification_context

router = APIRouter(prefix="/settings", tags=["Settings"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def _back(notice: str | None = None, error: str | None = None, anchor: str = "") -> RedirectResponse:
    query = urlencode({key: value for key, value in (("notice", notice), ("error", error)) if value})
    return RedirectResponse(f"/settings?{query}{anchor}", status_code=303)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def settings_page(request: Request, db: Session = Depends(get_db)):
    settings = get_settings(db)
    open_classes = db.query(ClassGroup).filter(ClassGroup.status == ClassStatus.NOT_STARTED).count()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_page": "settings",
            "settings": settings,
            "reminder_days": format_reminder_days(parse_reminder_days(settings.training_reminder_days)),
            "day_options": list(zip(DAY_KEYS, DAY_SHORT_EN)),
            "selected_days": [DAY_KEYS[index] for index in parse_days(settings.default_session_days)],
            "open_classes": open_classes,
            "smtp_ready": class_emails.smtp_configured(),
            "smtp_sender": os.getenv("SMTP_EMAIL") or "",
            "gmail_steps": __import__("app.services.email_account", fromlist=["GMAIL_STEPS"]).GMAIL_STEPS,
            "languages_progress": __import__("app.services.translations", fromlist=["progress"]).progress(db),
            "assistant_mode": email_assistant.mode_of(settings),
            "assistant_threshold": round(email_assistant.threshold_of(settings) * 100),
            "assistant_rewrites": email_assistant.rewrites_taught(settings),
            "assistant_model": email_assistant.MODEL,
            "model_ready": email_assistant.model_available(),
            "brand": branding(settings),
            "default_signature": DEFAULT_SIGNATURE,
            "mailbox_ready": mailbox_service.configured(),
            "mailbox": mailbox_service.config(),
            "mailbox_state": mailbox_service.get_state(db),
            "ui_colour": theme.current_colour(settings),
            "colour_presets": theme.PRESETS,
            "name_ideas": app_identity.NAME_IDEAS,
            "whatsapp_ready": whatsapp.configured(),
            "whatsapp_template": (os.getenv("WHATSAPP_TEMPLATE") or "").strip(),
            "forms": answer_sync.status(),
            "forms_trial": _trial["steps"],
            "facebook_ready": website_sync.facebook_connected(),
            "public_url": public_base_url(db, request),
            "public_url_local": weekly_questions.is_local_address(public_base_url(db, request)),
            "modes": {"after_days": START_AFTER_DAYS, "min_enrollment": START_MIN_ENROLLMENT},
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/classes")
def save_class_settings(
    auto_create_class_on_payment: bool = Form(False),
    auto_assign_professor: bool = Form(False),
    class_start_mode: str = Form(...),
    default_class_start_days: int = Form(...),
    min_students_to_start: int = Form(...),
    min_start_notice_days: int = Form(...),
    default_class_capacity: int = Form(...),
    default_program_weeks: int = Form(...),
    days: list[str] = Form(default=[]),
    default_session_start_time: str = Form(""),
    default_session_end_time: str = Form(""),
    training_reminder_days: str = Form(""),
    enable_automated_reminders: bool = Form(False),
    apply_to_open_classes: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Defaults used for every class created from now on."""
    start = parse_time(default_session_start_time)
    end = parse_time(default_session_end_time)
    session_days = parse_days(days)
    offsets = parse_reminder_days(training_reminder_days)
    problems = [
        message for failed, message in (
            (class_start_mode not in (START_AFTER_DAYS, START_MIN_ENROLLMENT), "Choose a start rule."),
            (not 0 <= default_class_start_days <= 365, "Start delay must be between 0 and 365 days."),
            (default_class_capacity < 1, "Capacity must be at least 1."),
            (not 1 <= min_students_to_start <= max(default_class_capacity, 1), "Minimum candidates to start must be between 1 and the capacity."),
            (not 0 <= min_start_notice_days <= 60, "Notice must be between 0 and 60 days."),
            (not 1 <= default_program_weeks <= 104, "Duration must be between 1 and 104 weeks."),
            (not session_days, "Choose at least one session day."),
            (start is None, "Set the session start time, so reminders can tell candidates when to attend."),
            (bool(start and end and end <= start), "Session end time must be after its start time."),
            (enable_automated_reminders and not offsets, "Enter at least one reminder day, for example 7, 3, 1."),
        ) if failed
    ]
    if problems:
        return _back(error=problems[0], anchor="#classes")

    settings = get_settings(db)
    settings.auto_create_class_on_payment = auto_create_class_on_payment
    settings.auto_assign_professor = auto_assign_professor
    settings.class_start_mode = class_start_mode
    settings.default_class_start_days = default_class_start_days
    settings.min_students_to_start = min_students_to_start
    settings.min_start_notice_days = min_start_notice_days
    settings.default_class_capacity = default_class_capacity
    settings.default_program_weeks = default_program_weeks
    settings.default_session_days = format_days(session_days)
    settings.default_session_start_time = f"{start:%H:%M}"
    settings.default_session_end_time = f"{end:%H:%M}" if end else None
    settings.training_reminder_days = format_reminder_days(offsets) or settings.training_reminder_days
    settings.enable_automated_reminders = enable_automated_reminders
    db.commit()

    notice = "Class parameters saved. They apply to every class created from now on."
    if apply_to_open_classes:
        updated = 0
        open_classes = (
            db.query(ClassGroup)
            .filter(ClassGroup.status == ClassStatus.NOT_STARTED, ClassGroup.start_mode != START_FIXED_DATE)
            .all()
        )
        for class_group in open_classes:
            try:
                update_class_parameters(
                    db, class_group,
                    name=class_group.name,
                    professor_id=class_group.professor_id,
                    capacity=class_capacity(class_group),
                    total_weeks=class_group.total_weeks or default_program_weeks,
                    start_mode=class_start_mode,
                    start_delay_days=default_class_start_days,
                    min_students_to_start=min(min_students_to_start, class_capacity(class_group)),
                    min_start_notice_days=min_start_notice_days,
                    fixed_start_date=None,
                    days_of_week=class_group.days_of_week,
                    start_time=class_group.start_time,
                    end_time=class_group.end_time,
                    room=class_group.room,
                    reminder_days=settings.training_reminder_days,
                )
                updated += 1
            except ValueError:
                db.rollback()
        notice = f"Class parameters saved and applied to {updated} class(es) that have not started."
    return _back(notice=notice, anchor="#classes")


@router.post("/assistant")
def save_assistant_settings(
    email_auto_reply_mode: str = Form("auto"),
    email_confidence_threshold: int = Form(70),
    email_rewrite_taught: bool = Form(False),
    email_signature: str = Form(""),
    email_footer_note: str = Form(""),
    email_accent_color: str = Form(DEFAULT_ACCENT),
    db: Session = Depends(get_db),
):
    """How candidate emails are answered, and how every email looks."""
    if email_auto_reply_mode not in email_assistant.MODES:
        return _back(error="Choose how candidate emails are answered.", anchor="#assistant")
    low, high = round(email_assistant.MIN_THRESHOLD * 100), round(email_assistant.MAX_THRESHOLD * 100)
    if not low <= email_confidence_threshold <= high:
        return _back(error=f"The confidence needed must be between {low}% and {high}%.", anchor="#assistant")

    settings = get_settings(db)
    settings.email_auto_reply_mode = email_auto_reply_mode
    settings.email_confidence_threshold = email_confidence_threshold / 100
    settings.email_rewrite_taught = email_rewrite_taught
    settings.email_signature = email_signature.strip()[:400] or None
    settings.email_footer_note = email_footer_note.strip()[:400] or None
    settings.email_accent_color = clean_accent(email_accent_color)
    db.commit()
    return _back(notice="Email assistant settings saved.", anchor="#assistant")


_trial: dict = {"steps": None}      # what the last "Test the connection" found


@router.post("/identity")
async def save_identity(name: str = Form(""), picture: UploadFile | None = File(None), db: Session = Depends(get_db)):
    """The application's name, and optionally a picture of the admin's own as its icon."""
    settings = get_settings(db)
    settings.app_name = app_identity.clean_name(name) or None
    db.commit()
    if picture is not None and picture.filename:
        problem = app_identity.save_upload(await picture.read())
        if problem:
            return _back(error=problem, anchor="#identity")
    return _back(notice=f"The application is now called {settings.app_name or app_identity.DEFAULT_NAME}.", anchor="#identity")


@router.post("/identity/facebook")
def icon_from_facebook():
    outcome = app_identity.refresh_from_facebook(force=True)
    if outcome == "offline":
        return _back(error="Facebook could not be reached, so the icon was not changed.", anchor="#identity")
    return _back(notice="The icon is the current picture of your Facebook page.", anchor="#identity")


@router.post("/google/test")
def test_google():
    """Try the whole road once: a small form is created, opened, read, and removed."""
    _trial["steps"] = google_forms.trial()
    failed = next((step for step in _trial["steps"] if not step["ok"]), None)
    if failed:
        return _back(error=f"Not ready yet: {failed['label'].lower()} — not yet.", anchor="#google")
    return _back(notice="Google Forms works: the next weekly questions go through a Google Form and Firebase.", anchor="#google")


@router.post("/email-account")
def save_email_account(smtp_email: str = Form(""), smtp_password: str = Form(""), db: Session = Depends(get_db)):
    """The address the centre's emails are sent from and read at, with its app password."""
    from app.services import email_account

    before = email_account.current()["email"]
    problem = email_account.save(smtp_email, smtp_password)
    if problem:
        return _back(error=problem, anchor="#email")
    settings = get_settings(db)
    settings.smtp_email = email_account.current()["email"] or None
    db.commit()
    now = email_account.current()["email"]
    return _back(notice=(f"Emails are now sent from {now}, and answers are read there. The mail server accepted it." if smtp_password.strip()
                         else f"Nothing changed: emails are sent from {before}."), anchor="#email")


@router.post("/appearance")
def save_appearance(ui_color: str = Form(""), ui_menu_side: str = Form(""), ui_background: str = Form(""), ui_mode: str = Form(""),
                    db: Session = Depends(get_db)):
    """The design: its main colour, the side of the menu, and whether the background moves."""
    colour = theme.clean(ui_color)
    if colour is None:
        return _back(error="Choose a colour.", anchor="#appearance")
    settings = get_settings(db)
    settings.ui_color = None if colour == theme.DEFAULT else colour
    if ui_menu_side in ("left", "right"):
        settings.ui_menu_side = None if ui_menu_side == "left" else "right"
    if ui_background in ("animated", "plain"):
        settings.ui_background = None if ui_background == "animated" else "plain"
    if ui_mode in ("soft", "colours", "night", "night-blue"):
        settings.ui_mode = None if ui_mode == "soft" else ui_mode
    db.commit()
    return _back(notice="Colour saved. Every page now uses it.", anchor="#appearance")


@router.post("/address")
def save_public_address(typed: str = Form("", alias="public_base_url"), db: Session = Depends(get_db)):
    """Where the links in candidates' emails lead."""
    try:
        address = clean_public_url(typed)
    except ValueError as exc:
        return _back(error=str(exc), anchor="#address")
    get_settings(db).public_base_url = address
    db.commit()
    if address is None:
        return _back(notice="Address cleared: links use the address you open the application with.", anchor="#address")
    return _back(notice=f"Links in candidates' emails now start with {address}.", anchor="#address")


@router.post("/test-email")
def send_test_email(recipient: str = Form(...), db: Session = Depends(get_db)):
    if not class_emails.smtp_configured():
        return _back(error="Email is not configured yet: add SMTP_EMAIL and SMTP_PASSWORD to the .env file and restart the server.", anchor="#email")
    delivered = send_email_safely(
        to_email=recipient.strip(),
        subject="Test email — 9antra",
        body=render_email(
            title="Email delivery is working",
            body_html="<p style=\"margin:0 0 16px;\">This is a test from the 9antra training dashboard. "
                      "Candidates will receive their emails in this design.</p>",
            lang="en", **branding(get_settings(db), "en"),
        ),
        is_html=True,
        journal=False,
    )
    if delivered:
        return _back(notice=f"Test email sent to {recipient.strip()}.", anchor="#email")
    return _back(error="The test email could not be delivered. Check SMTP_EMAIL and SMTP_PASSWORD (a Gmail app password is required).", anchor="#email")

from fastapi import Request
from sqlalchemy import func

from app.database import SessionLocal
from app.models.email_thread import EmailThread
from app.models.notification import Notification
from app.models.program import Program
from app.models.registration import Registration, RegistrationStatus
from app.services import auth, brand, guides, notification_fixes, program_icons, theme
from app.services import translations
from app.services.settings_service import get_settings

SIDEBAR_PROGRAMS = 6

# Pages rendered without the sign-in gate (it is always there when the
# application runs) are shown as the admin sees them.
_NO_ONE = auth.CurrentUser(id="", name="Admin", email="", role=auth.ADMIN)


def notification_context(request: Request) -> dict:
    """What every page shows around its content: who is signed in, the colour
    of the design and, for the admin, the bell, the inbox count and the most
    chosen programs in the side menu."""
    user = auth.current(request) or _NO_ONE
    db = SessionLocal()
    try:
        settings = get_settings(db)
        context = {
            "current_user": user,
            "is_admin": user.is_admin,
            "theme_css": theme.css(settings.ui_color),
            "ui_menu_side": "right" if getattr(settings, "ui_menu_side", None) == "right" else "left",
            "ui_mode": getattr(settings, "ui_mode", None) if getattr(settings, "ui_mode", None) in ("colours", "night", "night-blue") else "soft",
            "ui_lang": translations.clean_language(request.cookies.get("lang")),
            "staff_menu": __import__("app.services.access", fromlist=["menu"]).menu(user.permissions) if getattr(user, "is_staff", False) else [],
            "ui_languages": translations.LANGUAGES,
            "i18n_version": translations.version(db, translations.clean_language(request.cookies.get("lang")))
                            if translations.clean_language(request.cookies.get("lang")) != "en" else "",
            "ui_background": "plain" if getattr(settings, "ui_background", None) == "plain" else "animated",
            "app_brand": brand.view(settings),
            "page_guide": guides.for_path(request.url.path),
            "notifications": [],
            "inbox_pending": 0,
            "sidebar_programs": [],
        }
        if not user.is_admin:
            return context

        context["notifications"] = (
            db.query(Notification)
            .filter(
                Notification.recipient_role == "admin",
                Notification.is_read.is_(False),
            )
            .order_by(Notification.created_at.desc())
            .limit(10)
            .all()
        )
        for item in context["notifications"]:      # what each one lets the admin do at once
            item.fix = notification_fixes.fix_for(db, item)
        context["inbox_pending"] = db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).count()

        counts = dict(
            db.query(Registration.program_id, func.count(Registration.id))
            .filter(Registration.program_id.isnot(None), Registration.status != RegistrationStatus.CANCELLED)
            .group_by(Registration.program_id)
            .all()
        )
        programs = db.query(Program).all()
        programs.sort(key=lambda program: (-counts.get(program.id, 0), program.name or ""))
        context["sidebar_programs"] = [
            {
                "id": program.id,
                "name": program.name,
                "candidate_count": counts.get(program.id, 0),
                "icon": program_icons.view(program),
            }
            for program in programs[:SIDEBAR_PROGRAMS]
        ]
        return context
    finally:
        db.close()

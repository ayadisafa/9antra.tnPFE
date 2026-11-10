from fastapi import Request
from sqlalchemy import func

from app.database import SessionLocal
from app.models.email_thread import EmailThread
from app.models.notification import Notification
from app.models.program import Program
from app.models.registration import Registration, RegistrationStatus
from app.services import auth, brand, guides, notification_fixes, page_pdf_links, program_icons, theme
from app.services import translations, videos
from app.services.settings_service import get_settings

SIDEBAR_PROGRAMS = 6

# Pages rendered without the sign-in gate (it is always there when the
# application runs) are shown as the admin sees them.
_NO_ONE = auth.CurrentUser(id="", name="Admin", email="", role=auth.ADMIN)


def _tour_of(db, request: Request):
    """What this page lets you do, part by part, in the page's language (the “?” at the top)."""
    from app.services import page_tours

    lang = translations.clean_language(request.cookies.get("lang"))
    words = page_tours.words_for(db, lang, translations.version(db, lang) if lang != "en" else "")
    return page_tours.translated(page_tours.for_path(request.url.path), lang, words)


def notification_context(request: Request) -> dict:
    """What every page shows around its content: who is signed in, the colour
    of the design and, for the admin, the bell, the inbox count and the most
    chosen programs in the side menu."""
    user = auth.current(request) or _NO_ONE
    db = SessionLocal()
    try:
        settings = get_settings(db)
        look = theme.look(settings)
        from app.services import dynamic_words

        context = {
            # Whole sentences built with names and figures (a notice, a check), in the page's language.
            "shown": dynamic_words.shower(db, translations.clean_language(request.cookies.get("lang"))),
            "current_user": user,
            "is_admin": user.is_admin,
            "theme_css": look["css"],
            "ui_menu_side": look["menu_side"],
            "ui_mode": look["mode"],
            "ui_bar": look["bar"],
            "ui_lang": translations.clean_language(request.cookies.get("lang")),
            "staff_menu": __import__("app.services.access", fromlist=["menu"]).menu(user.permissions) if getattr(user, "is_staff", False) else [],
            "ui_languages": translations.LANGUAGES,
            "i18n_version": translations.version(db, translations.clean_language(request.cookies.get("lang")))
                            if translations.clean_language(request.cookies.get("lang")) != "en" else "",
            "ui_background": look["background"],
            "app_brand": brand.view(settings),
            "page_guide": guides.for_path(request.url.path),
            "page_tour": _tour_of(db, request),
            "page_pdf": page_pdf_links.link_for(request.url.path, user),
            "page_pdf_more": page_pdf_links.more_for(request.url.path, user),
            "notifications": [],
            # What this page listens for when its session is signed out (services/presence.py).
            "session_tag": __import__("app.services.presence", fromlist=["tag_of"]).tag_of(auth._fingerprint(request.cookies.get(auth.COOKIE)))
                           if request.cookies.get(auth.COOKIE) else "",
            "online_now": None,
            "ringing": 0,                          # the notifications that need the admin (the bell's count)
            "not_reached_count": 0,
            "inbox_pending": 0,
            "video_embed": videos.embed_url,      # a video link -> its player, opened inside the page (_video_player.html)
            "video_kind": videos.kind_of,
            "team_unread": 0,
            # A team member the admin gave the assistant to (it reads all the data).
            "assistant_auto_send": __import__("app.services.chatbot_send", fromlist=["auto_send"]).auto_send(db),
            "can_use_assistant": bool(getattr(user, "is_staff", False) and (user.permissions or {}).get("assistant") in ("view", "edit")),
            "member_unread": 0,
            "sidebar_programs": [],
            # The assistant's character (Settings), drawn in the corner and in the assistant's window.
            "friend_look": __import__("app.services.friend", fromlist=["look"]).look(db),
            "whats_new_unread": 0,
        }
        if not user.is_admin:
            if user.id:
                from app.services import team_messages

                context["member_unread"] = team_messages.member_unread(db, user)
            return context

        from app.services import notification_rules, reach

        # Opening a page reads what leads there; what was handled elsewhere, or only told something, closes by itself.
        notification_rules.seen_at(db, request.url.path, dict(request.query_params))
        notification_rules.tidy(db)
        shown = (
            db.query(Notification)
            .filter(
                Notification.recipient_role == "admin",
                Notification.is_read.is_(False),
            )
            .order_by(Notification.created_at.desc())
            .limit(30)
            .all()
        )
        for item in shown:      # what each one lets the admin do at once, and whether it needs them
            item.fix = notification_fixes.fix_for(db, item)
            item.needs_you = notification_rules.needs_you(db, item)
        # What needs the admin first (it rings); what only tells something after, quietly.
        context["notifications"] = [item for item in shown if item.needs_you][:12] + [item for item in shown if not item.needs_you][:6]
        context["ringing"] = sum(1 for item in shown if item.needs_you)
        context["not_reached_count"] = len(reach.not_reached(db))
        from app.services import presence

        context["online_now"] = presence.summary(presence.connected(db, auth._fingerprint(request.cookies.get(auth.COOKIE))
                                                                     if request.cookies.get(auth.COOKIE) else None))
        from app.services import dynamic_words

        # In the page's language, and where each one leads.
        dynamic_words.localize(db, context["notifications"], translations.clean_language(request.cookies.get("lang")))
        context["inbox_pending"] = db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).count()
        from app.services import team_messages

        context["team_unread"] = team_messages.unread_count(db)
        context["whats_new_unread"] = len(__import__("app.services.whats_new", fromlist=["unread"]).unread(db)["items"]) \
            if __import__("app.services.whats_new", fromlist=["unread"]).unread(db) else 0

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

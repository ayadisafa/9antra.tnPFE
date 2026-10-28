"""Forgot password: the email with the link to choose a new password (the link itself: auth.start_reset).

Written in the language of the sign-in page. Sent in the background, so the page answers as fast whether the address
has an account or not.
"""
from __future__ import annotations

import html
import logging

from app.services.settings_service import get_settings

logger = logging.getLogger(__name__)

_WORDS = {
    "en": {"subject": "Choose a new password · {app}", "hello": "Hello {name},",
           "text": "Someone, surely you, asked for a new password for your {app} account. The button below opens the page "
                   "where you choose it. The link is valid for one hour and works only once.",
           "after": "If you did not ask for anything, ignore this email: your password stays the same.",
           "button": "Choose my new password", "or": "Or open this address:"},
    "fr": {"subject": "Choisir un nouveau mot de passe · {app}", "hello": "Bonjour {name},",
           "text": "Quelqu'un, sans doute vous, a demandé un nouveau mot de passe pour votre compte {app}. Le bouton ci-dessous "
                   "ouvre la page où vous le choisissez. Le lien est valable une heure et ne sert qu'une fois.",
           "after": "Si vous n'avez rien demandé, ignorez cet email : votre mot de passe reste le même.",
           "button": "Choisir mon nouveau mot de passe", "or": "Ou ouvrez cette adresse :"},
    "ar": {"subject": "اختيار كلمة مرور جديدة · {app}", "hello": "مرحبا {name}،",
           "text": "طلب أحدهم، وأنت على الأرجح، كلمة مرور جديدة لحسابك في {app}. يفتح الزر أدناه الصفحة التي تختارها فيها. "
                   "الرابط صالح لمدة ساعة واحدة ويعمل مرة واحدة فقط.",
           "after": "إذا لم تطلب شيئا، تجاهل هذه الرسالة: تبقى كلمة مرورك كما هي.",
           "button": "اختيار كلمة المرور الجديدة", "or": "أو افتح هذا العنوان:"},
}


def message(db, user, link: str, lang: str = "fr") -> dict:
    """{"subject", "html", "text"} of the email."""
    from app.services import brand
    from app.services.email_layout import clean_accent

    settings = get_settings(db)
    words = _WORDS.get(lang) or _WORDS["fr"]
    app_name = brand.view(settings)["name"]
    accent = clean_accent(getattr(settings, "email_accent_color", None))
    name = (user.name or user.email or "").split()[0] if (user.name or user.email) else ""
    hello, text, after = words["hello"].format(name=name), words["text"].format(app=app_name), words["after"]
    direction = "rtl" if lang == "ar" else "ltr"
    page = f"""<!DOCTYPE html>
<html lang="{lang}" dir="{direction}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(words["subject"].format(app=app_name))}</title></head>
<body style="margin:0;padding:0;background:#f6f3fb;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f6f3fb;"><tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:560px;background:#ffffff;border:1px solid #eee9f5;border-radius:20px;">
<tr><td style="padding:22px 30px;border-radius:20px 20px 0 0;background:{accent};color:#ffffff;font-family:Segoe UI,Arial,sans-serif;font-size:18px;font-weight:700;" dir="{direction}">{html.escape(app_name)}</td></tr>
<tr><td style="padding:26px 30px 30px;font-family:Segoe UI,Arial,sans-serif;font-size:15px;line-height:1.65;color:#241a38;" dir="{direction}">
<p style="margin:0 0 14px;">{html.escape(hello)}</p>
<p style="margin:0 0 22px;">{html.escape(text)}</p>
<p style="margin:0 0 22px;"><a href="{html.escape(link)}" style="display:inline-block;background:{accent};color:#ffffff;text-decoration:none;font-weight:700;padding:12px 22px;border-radius:12px;">{html.escape(words["button"])}</a></p>
<p style="margin:0 0 6px;font-size:13px;color:#6b6480;">{html.escape(words["or"])}</p>
<p style="margin:0 0 18px;font-size:13px;word-break:break-all;" dir="ltr"><a href="{html.escape(link)}" style="color:{accent};">{html.escape(link)}</a></p>
<p style="margin:0;font-size:13px;color:#6b6480;">{html.escape(after)}</p>
</td></tr></table></td></tr></table></body></html>"""
    plain = f"{hello}\n\n{text}\n\n{words['button']} : {link}\n\n{after}\n\n{app_name}"
    return {"subject": words["subject"].format(app=app_name), "html": page, "text": plain}


def send(db, user, link: str, lang: str = "fr") -> bool:
    from app.services.email_service import send_email

    content = message(db, user, link, lang)
    return send_email(to_email=user.email, subject=content["subject"], body=content["html"], is_html=True,
                      text_body=content["text"], journal=False)


def send_task(user_id: str, link: str, lang: str = "fr") -> None:
    """In the background, with its own connection to the database."""
    from app.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is not None:
            send(db, user, link, lang)
            logger.info("A link to choose a new password was sent to an account.")
    except Exception:
        logger.exception("The email to choose a new password could not be sent.")
    finally:
        db.close()

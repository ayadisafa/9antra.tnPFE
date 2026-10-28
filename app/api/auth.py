"""Signing in and out, the first-time setup, forgot password, and each person's own account."""
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services import auth, brand, theme, translations
from app.services.settings_service import get_settings
from app.template_context import notification_context

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Access"])
# The sign-in pages show nothing of the application.
public_templates = Jinja2Templates(directory="app/templates")
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def _tell_admin(db: Session, title: str, message: str) -> None:
    """Every password asked for or changed is told to the admin (Team and access)."""
    from app.services.notification_service import create_notification

    try:
        create_notification(db, title=title, message=message, category="ACCOUNT", action_url="/settings/team")
    except Exception:
        db.rollback()
        logger.exception("The admin could not be told about a password.")


def _language(request: Request) -> str:
    """The language of the sign-in pages: the one chosen on the page (?lang=), else the one remembered."""
    return translations.clean_language(request.query_params.get("lang") or request.cookies.get("lang"))


def _sign_in_page(request: Request, db: Session, mode: str, *, error: str | None = None,
                  values: dict | None = None, status_code: int = 200, next_page: str | None = None, extra: dict | None = None):
    lang = _language(request)
    words = translations.dictionary(db, lang)["t"] if lang in translations.TARGETS else {}
    if lang in translations.TARGETS:
        # The hand-made translations too, before the server's start has put them in the database.
        from app.services.translation_seed import MORE, SEED

        position = 0 if lang == "fr" else 1
        words = {**{text: pair[position] for text, pair in {**SEED, **MORE}.items() if pair and pair[position]}, **words}
    response = public_templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "mode": mode,
            "error": error,
            "values": values or {},
            # Where to go once signed in; kept through a wrong password.
            "next": next_page if next_page is not None else request.query_params.get("next", ""),
            "theme_css": theme.css(get_settings(db).ui_color),
            "app_name": brand.view(get_settings(db))["name"],
            "min_password": auth.MIN_PASSWORD,
            "lang": lang,
            "t": lambda text: words.get(text, text),
            **(extra or {}),
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )
    if request.query_params.get("lang") in translations.LANGUAGES:
        response.set_cookie("lang", lang, max_age=365 * 86400, samesite="lax", path="/")
    return response


def _signed_in(request: Request, token: str, target: str) -> RedirectResponse:
    response = RedirectResponse(target, status_code=303)
    auth.set_cookie(response, token, secure=request.url.scheme == "https")
    return response


# ------------------------------------------------------------------
# First time: the admin creates their account
# ------------------------------------------------------------------

@router.get("/account/view-as/stop")
def stop_viewing_as(request: Request, next: str = "/settings/team"):
    """Back to the admin's own view."""
    response = RedirectResponse(auth.safe_next(next, "/settings/team"), status_code=303)
    response.delete_cookie(auth.VIEW_AS_COOKIE, path="/")
    return response


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, db: Session = Depends(get_db)):
    if auth.has_admin(db):
        return RedirectResponse("/login", status_code=303)
    return _sign_in_page(request, db, "setup")


@router.post("/setup")
def create_admin(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirmation: str = Form(""),
    db: Session = Depends(get_db),
):
    if auth.has_admin(db):
        return RedirectResponse("/login", status_code=303)
    values = {"name": name, "email": email}
    problem = auth.password_problem(password, confirmation)
    if not name.strip():
        problem = "Enter your name."
    if problem:
        return _sign_in_page(request, db, "setup", error=problem, values=values, status_code=400)
    try:
        auth.create_user(db, email=email, name=name, password=password, role=auth.ADMIN)
    except ValueError as exc:
        return _sign_in_page(request, db, "setup", error=str(exc), values=values, status_code=400)
    token, _ = auth.sign_in(
        db, email, password, request.client.host if request.client else "", device=request.headers.get("user-agent", ""),
    )
    return _signed_in(request, token, "/")


# ------------------------------------------------------------------
# Signing in and out
# ------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if not auth.has_admin(db):
        return RedirectResponse("/setup", status_code=303)
    user = auth.user_for(db, request.cookies.get(auth.COOKIE))
    if user is not None:
        return RedirectResponse(user.home, status_code=303)
    return _sign_in_page(request, db, "login", extra={"changed": request.query_params.get("changed") == "1"})


# ------------------------------------------------------------------
# Forgot password
# ------------------------------------------------------------------

@router.get("/login/forgot", response_class=HTMLResponse)
def forgot_page(request: Request, db: Session = Depends(get_db)):
    return _sign_in_page(request, db, "forgot", values={"email": request.query_params.get("email", "")})


@router.post("/login/forgot")
def forgot_send(request: Request, email: str = Form(""), db: Session = Depends(get_db)):
    """Always the same answer, whether the address has an account or not; the email goes in the background."""
    from app.services import email_threads, password_reset
    from app.services.background import run_in_background

    ready = email_threads.smtp_configured()
    if ready:
        user, token = auth.start_reset(db, email, request.client.host if request.client else "")
        if user is not None:
            link = str(request.base_url).rstrip("/") + f"/login/reset/{token}"
            run_in_background(password_reset.send_task, user.id, link, _language(request))
            _tell_admin(db, f"{user.name} asked for a new password", f"A link to choose it was sent to {user.email}. If it was not them, nothing changes until the link is used.")
    return _sign_in_page(request, db, "forgot_sent", values={"email": email}, extra={"smtp_ready": ready})


@router.get("/login/reset/{token}", response_class=HTMLResponse)
def reset_page(token: str, request: Request, db: Session = Depends(get_db)):
    user = auth.user_for_reset(db, token)
    if user is None:
        return _sign_in_page(request, db, "reset_bad", status_code=410)
    return _sign_in_page(request, db, "reset", values={"email": user.email}, extra={"token": token})


@router.post("/login/reset/{token}")
def reset_save(token: str, request: Request, password: str = Form(""), confirmation: str = Form(""), db: Session = Depends(get_db)):
    user = auth.user_for_reset(db, token)
    if user is None:
        return _sign_in_page(request, db, "reset_bad", status_code=410)
    problem = auth.password_problem(password, confirmation)
    if problem:
        return _sign_in_page(request, db, "reset", error=problem, values={"email": user.email}, extra={"token": token}, status_code=400)
    auth.finish_reset(db, user, password)
    logger.info("A password was changed with a link sent by email.")
    _tell_admin(db, f"{user.name} chose a new password", "With the link sent by email. Every browser signed in with this account was signed out.")
    return RedirectResponse("/login?changed=1", status_code=303)


@router.post("/login")
def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    token, problem = auth.sign_in(
        db, email, password, request.client.host if request.client else "", device=request.headers.get("user-agent", ""),
    )
    if token is None:
        return _sign_in_page(request, db, "login", error=problem, values={"email": email}, status_code=401, next_page=next)
    user = auth.user_for(db, token)
    # The page that was asked for, else the one this person had open last on
    # this browser, else their home. A professor only ever lands in their own
    # space: the gate sends them back if the page is not theirs.
    resume = auth.last_page(request, user)
    if user.is_admin:
        target = auth.safe_next(next, resume or user.home)
    else:
        target = resume if resume and auth.professor_may_open(db, user, resume.split("?")[0]) else user.home
    return _signed_in(request, token, target)


@router.post("/logout")
@router.get("/logout", include_in_schema=False)
def logout(request: Request, db: Session = Depends(get_db)):
    auth.sign_out(db, request.cookies.get(auth.COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE, path="/")
    return response


# ------------------------------------------------------------------
# My account
# ------------------------------------------------------------------

def _back(notice: str | None = None, error: str | None = None) -> RedirectResponse:
    query = urlencode({key: value for key, value in (("notice", notice), ("error", error)) if value})
    return RedirectResponse(f"/account{'?' + query if query else ''}", status_code=303)


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, db: Session = Depends(get_db)):
    me = auth.current(request)
    if me is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context={
            "active_page": "account",
            "me": db.get(User, me.id),
            "sessions": auth.sessions_of(db, me.id, request.cookies.get(auth.COOKIE)),
            "min_password": auth.MIN_PASSWORD,
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/account/sessions/end")
def end_other_sessions(request: Request, key: str = Form(""), db: Session = Depends(get_db)):
    """Sign out of one other browser, or of all the others."""
    me = auth.current(request)
    if me is None:
        return RedirectResponse("/login", status_code=303)
    count = auth.end_sessions(db, me.id, request.cookies.get(auth.COOKIE), key=key.strip() or None)
    return _back(notice="Signed out there." if count == 1 else f"Signed out of {count} other browsers." if count else "There was nothing to sign out.")


@router.post("/account/password")
def change_password(
    request: Request,
    current: str = Form(""),
    password: str = Form(""),
    confirmation: str = Form(""),
    db: Session = Depends(get_db),
):
    me = auth.current(request)
    if me is None:
        return RedirectResponse("/login", status_code=303)
    user = db.get(User, me.id)
    if not auth.verify_password(current, user.password_hash):
        return _back(error="Your current password is not right.")
    problem = auth.password_problem(password, confirmation)
    if problem:
        return _back(error=problem)
    auth.set_password(db, user, password, keep_token=request.cookies.get(auth.COOKIE))
    _tell_admin(db, f"{user.name} changed their password", "From their account page. Other browsers signed in with this account were signed out.")
    return _back(notice="Password changed. Other browsers signed in with this account were signed out.")


@router.post("/account/details")
def change_details(request: Request, name: str = Form(""), db: Session = Depends(get_db)):
    me = auth.current(request)
    if me is None:
        return RedirectResponse("/login", status_code=303)
    user = db.get(User, me.id)
    name = " ".join(name.split())
    if not name:
        return _back(error="Enter your name.")
    user.name = name[:120]
    db.commit()
    return _back(notice="Name saved.")

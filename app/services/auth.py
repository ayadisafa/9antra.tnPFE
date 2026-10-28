"""Signing in, and who may open what.

Two kinds of people sign in: the admin, who sees everything, and professors,
who only see their own space (their profile, the course and the presence
sheet of the classes they teach). Candidates never sign in: the pages they
open (their weekly questions, their level check) are reached with a private link.

A signed-in browser holds a random token in a cookie; the database keeps only
its fingerprint. Passwords are stored as salted PBKDF2 hashes.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from app.database import SessionLocal
from app.models.classgroup import ClassGroup
from app.models.course_material import ClassMaterial
from app.models.user import User, UserSession

COOKIE = "nantra_session"
# An admin looking at the application as someone else (a professor, a team member): only to look, nothing can be changed.
VIEW_AS_COOKIE = "nantra_view_as"
SESSION_DAYS = 14
MIN_PASSWORD = 8
_ITERATIONS = 240_000

ADMIN, PROFESSOR, STAFF = "admin", "professor", "staff"

# Opened without signing in: the sign-in pages, the files of the design, and
# what candidates reach from the private links in their emails.
# "/opinion/" is the candidate's private page to say what they thought of their program,
# "/level/" the one where they say where they stand before it starts.
# "/app.webmanifest" lets a phone put the application on its home screen.
# "/whatsapp/webhook" is Meta's: it proves each message with the app's secret (see api/whatsapp.py).
_OPEN_PREFIXES = ("/login", "/logout", "/setup", "/static/", "/learn/", "/opinion/", "/level/", "/join/", "/favicon", "/app.webmanifest",
                  "/whatsapp/webhook", "/sms/webhook")
# What a professor may open besides the pages of their own classes.
_PROFESSOR_PREFIXES = ("/teacher", "/account", "/guide", "/language", "/i18n/", "/events/stream")
_CLASS_PAGE = re.compile(r"^/classes/([^/]+)/(course|attendance)(/|$)")
_CLASS_FILE = re.compile(r"^/classes/files/([^/]+)$")
_PROGRAM_ICON = re.compile(r"^/programs/[^/]+/icon$")


@dataclass(frozen=True)
class CurrentUser:
    """The person behind a request, as pages and routes need to know them."""
    id: str
    name: str
    email: str
    role: str
    professor_id: str | None = None
    permissions: dict | None = None          # a team member's access, by part of the application
    viewing_as: str | None = None            # set when an admin looks as this person: the admin's name (looking only)
    viewer_id: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN

    @property
    def is_professor(self) -> bool:
        return self.role == PROFESSOR

    @property
    def is_staff(self) -> bool:
        return self.role == STAFF

    @property
    def home(self) -> str:
        if self.is_staff:
            from app.services import access

            return access.home_of(self.permissions or {})
        return "/" if self.is_admin else "/teacher"

    @property
    def initials(self) -> str:
        words = self.name.split()
        return ((words[0][:1] + (words[1][:1] if len(words) > 1 else "")) or "?").upper()


# ------------------------------------------------------------------
# Passwords
# ------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, iterations, salt, digest = (stored or "").split("$")
        if scheme != "pbkdf2_sha256":
            return False
        computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(computed.hex(), digest)


def password_problem(password: str, confirmation: str | None = None) -> str | None:
    """Why a new password cannot be used, or None when it can."""
    if len(password or "") < MIN_PASSWORD:
        return f"Choose a password of at least {MIN_PASSWORD} characters."
    if confirmation is not None and password != confirmation:
        return "The two passwords are not the same."
    return None


def clean_email(value: str | None) -> str | None:
    """A sign-in address in lower case, or None when it is not an address."""
    value = (value or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) or len(value) > 200:
        return None
    return value


# ------------------------------------------------------------------
# Accounts
# ------------------------------------------------------------------

_admin_exists = False


def has_admin(db: Session) -> bool:
    """Whether the application was set up. Remembered once true."""
    global _admin_exists
    if not _admin_exists:
        _admin_exists = db.query(User.id).filter(User.role == ADMIN, User.is_active.is_(True)).first() is not None
    return _admin_exists


def create_user(db: Session, *, email: str, name: str, password: str, role: str,
                professor_id: str | None = None) -> User:
    """Raises ValueError with a message for the person filling the form."""
    address = clean_email(email)
    if address is None:
        raise ValueError("Enter a valid email address.")
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    if db.query(User.id).filter(User.email == address).first() is not None:
        raise ValueError("This email address already has an account.")
    user = User(
        email=address, name=" ".join((name or "").split()) or address, password_hash=hash_password(password),
        role=role, professor_id=professor_id,
    )
    db.add(user)
    db.commit()
    return user


def set_password(db: Session, user: User, password: str, keep_token: str | None = None) -> None:
    """Change a password and sign the account out everywhere else."""
    user.password_hash = hash_password(password)
    query = db.query(UserSession).filter(UserSession.user_id == user.id)
    if keep_token:
        query = query.filter(UserSession.token_hash != _fingerprint(keep_token))
    query.delete(synchronize_session=False)
    db.commit()


# ------------------------------------------------------------------
# Forgot password: a link by email, one hour, used once
# ------------------------------------------------------------------

RESET_MINUTES = 60
_MAX_RESETS = 3                 # links asked for one address from one place, in one hour
_resets: dict[str, list[float]] = {}


def start_reset(db: Session, email: str, client: str = "") -> tuple[User | None, str | None]:
    """A new link for this account: (user, token). (None, None) when there is no such active account, or when it was
    asked for too often; the page says the same thing either way, so it does not tell who has an account."""
    address = clean_email(email)
    if address is None:
        return None, None
    key = f"{client}|{address}"
    recent = [moment for moment in _resets.get(key, []) if time.time() - moment < 3600]
    if len(recent) >= _MAX_RESETS:
        _resets[key] = recent
        return None, None
    _resets[key] = recent + [time.time()]
    user = db.query(User).filter(User.email == address, User.is_active.is_(True)).first()
    if user is None:
        return None, None
    token = secrets.token_urlsafe(32)
    user.reset_hash = _fingerprint(token)           # a new link replaces the one before
    user.reset_until = datetime.utcnow() + timedelta(minutes=RESET_MINUTES)
    db.commit()
    return user, token


def user_for_reset(db: Session, token: str | None) -> User | None:
    """The account a reset link is for, while it still works."""
    if not token or len(token) < 20:
        return None
    user = db.query(User).filter(User.reset_hash == _fingerprint(token)).first()
    if user is None or not user.is_active or user.reset_until is None or user.reset_until < datetime.utcnow():
        return None
    return user


def finish_reset(db: Session, user: User, password: str) -> None:
    """The new password; the link stops working and every browser signed in with this account is signed out."""
    user.reset_hash, user.reset_until = None, None
    set_password(db, user, password)


# ------------------------------------------------------------------
# Signing in and out
# ------------------------------------------------------------------

_failures: dict[str, list[float]] = {}
_MAX_FAILURES, _FAILURE_WINDOW = 6, 600        # six wrong passwords in ten minutes


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def too_many_attempts(key: str) -> bool:
    recent = [moment for moment in _failures.get(key, []) if time.time() - moment < _FAILURE_WINDOW]
    _failures[key] = recent
    return len(recent) >= _MAX_FAILURES


def device_of(user_agent: str | None) -> str:
    """"Chrome on Windows", from what the browser says about itself."""
    agent = user_agent or ""
    browser = next((name for mark, name in (
        ("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"),
    ) if mark in agent), "A browser")
    system = next((name for mark, name in (
        ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"),
        ("Mac OS", "Mac"), ("Linux", "Linux"),
    ) if mark in agent), "")
    return f"{browser} on {system}" if system else browser


def sign_in(db: Session, email: str, password: str, client: str = "", device: str = "") -> tuple[str | None, str | None]:
    """(token, None) when the email and password are right, else (None, why).

    Signing in on one more browser does not sign the others out: each one is
    a session of its own, listed on the account page.
    """
    address = (email or "").strip().lower()
    key = f"{client}|{address}"
    if too_many_attempts(key):
        return None, "Too many wrong passwords. Wait ten minutes, then try again."
    user = db.query(User).filter(User.email == address).first()
    # The hash is checked even for an unknown address, so both cases take as long.
    known = verify_password(password or "", user.password_hash if user else hash_password("unknown"))
    if user is None or not known or not user.is_active:
        _failures.setdefault(key, []).append(time.time())
        return None, "The email address or the password is not right."
    _failures.pop(key, None)
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    db.add(UserSession(
        token_hash=_fingerprint(token), user_id=user.id, expires_at=now + timedelta(days=SESSION_DAYS),
        device=device_of(device)[:80], last_seen_at=now,
    ))
    user.last_login_at = now
    db.query(UserSession).filter(UserSession.expires_at < now).delete(synchronize_session=False)
    db.commit()
    return token, None


def sign_out(db: Session, token: str | None) -> None:
    if token:
        db.query(UserSession).filter(UserSession.token_hash == _fingerprint(token)).delete(synchronize_session=False)
        db.commit()


def user_for(db: Session, token: str | None) -> CurrentUser | None:
    """The person a session cookie belongs to, when it is still valid."""
    if not token:
        return None
    session = db.get(UserSession, _fingerprint(token))
    if session is None or session.expires_at < datetime.utcnow():
        return None
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    now = datetime.utcnow()
    if session.last_seen_at is None or now - session.last_seen_at > timedelta(minutes=5):
        session.last_seen_at = now      # written at most every five minutes
        db.commit()
    permissions = None
    if user.role == STAFF:
        from app.services import access

        permissions = access.clean(user.permissions)
    return CurrentUser(id=user.id, name=user.name, email=user.email, role=user.role, professor_id=user.professor_id, permissions=permissions)


def seen_as(db: Session, admin: CurrentUser, user_id: str | None) -> CurrentUser | None:
    """The person an admin chose to look as: everything as they see it, nothing they could change."""
    if not user_id or not admin.is_admin:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.role == ADMIN or user.id == admin.id:
        return None
    permissions = None
    if user.role == STAFF:
        from app.services import access

        permissions = access.clean(user.permissions)
    return CurrentUser(id=user.id, name=user.name, email=user.email, role=user.role, professor_id=user.professor_id, permissions=permissions,
                       viewing_as=admin.name, viewer_id=admin.id)


def sessions_of(db: Session, user_id: str, token: str | None) -> list[dict]:
    """Where this account is signed in, the browser asking first."""
    mine = _fingerprint(token) if token else ""
    now = datetime.utcnow()
    rows = db.query(UserSession).filter(UserSession.user_id == user_id, UserSession.expires_at >= now).all()
    found = [
        {
            "key": row.token_hash[:16], "device": row.device or "A browser", "is_this_one": row.token_hash == mine,
            "since": row.created_at, "last_seen": row.last_seen_at or row.created_at,
        }
        for row in rows
    ]
    return sorted(found, key=lambda item: (not item["is_this_one"], -(item["last_seen"] or now).timestamp()))


def end_sessions(db: Session, user_id: str, token: str | None, key: str | None = None) -> int:
    """Sign this account out of one other browser (`key`), or of all the others."""
    query = db.query(UserSession).filter(UserSession.user_id == user_id)
    if token:
        query = query.filter(UserSession.token_hash != _fingerprint(token))
    if key:
        query = query.filter(UserSession.token_hash.like(key.replace("%", "") + "%"))
    count = query.delete(synchronize_session=False)
    db.commit()
    return count


LAST_PAGE = "nantra_last"       # "<user id>|<address>": written by every page (see base.html)


def last_page(request: Request, user: CurrentUser) -> str | None:
    """The page this person had open last on this browser."""
    from urllib.parse import unquote

    owner, _, address = unquote(request.cookies.get(LAST_PAGE) or "").partition("|")
    if owner != user.id or not address.startswith("/") or address.startswith(("/login", "/logout", "/setup")):
        return None
    return safe_next(address, user.home)


def set_cookie(response, token: str, secure: bool) -> None:
    response.set_cookie(
        COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=secure, path="/",
    )


def current(request: Request) -> CurrentUser | None:
    """The signed-in person of this request (set by the gate below)."""
    return request.scope.get("state", {}).get("user")


def safe_next(target: str | None, fallback: str) -> str:
    """Where to go after signing in: only a page of this application."""
    target = target or ""
    if target.startswith("/") and not target.startswith("//") and "\\" not in target and not target.startswith("/login"):
        return target
    return fallback


# ------------------------------------------------------------------
# Who may open what
# ------------------------------------------------------------------

def is_open(path: str) -> bool:
    return path.startswith(_OPEN_PREFIXES)


def professor_may_open(db: Session, user: CurrentUser, path: str) -> bool:
    """A professor opens their own space and the pages of the classes they teach."""
    if path.startswith(_PROFESSOR_PREFIXES) or _PROGRAM_ICON.match(path):
        return True
    match = _CLASS_PAGE.match(path)
    if match:
        class_group = db.get(ClassGroup, match.group(1))
        return class_group is not None and class_group.professor_id == user.professor_id
    match = _CLASS_FILE.match(path)
    if match:
        material = db.get(ClassMaterial, match.group(1))
        class_group = db.get(ClassGroup, material.class_group_id) if material else None
        return class_group is not None and class_group.professor_id == user.professor_id
    return False


# Sent with every page: a browser must not guess file types, show these pages inside another site,
# or tell another site the address it comes from (a candidate's personal link is in the address).
_SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"geolocation=(), payment=(), usb=()"),
)


class SecurityHeaders:
    """Adds the headers above to every answer that does not set them itself."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}
                headers += [(name, value) for name, value in _SECURITY_HEADERS if name not in present]
                message = dict(message, headers=headers)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _staff_may(user: CurrentUser, path: str, method: str) -> bool:
    from app.services import access

    return access.allows(user.permissions or {}, path, method)


class AccessGate:
    """Stands in front of every page: no one in without signing in, and
    professors only where they belong."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "/")
        if is_open(path):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        # The sentences services write for this page (the advice…) come out in the page's language.
        __import__("app.services.ui_words", fromlist=["use"]).use(request.cookies.get("lang"))
        db = SessionLocal()
        try:
            if not has_admin(db):
                response = self._refuse(request, "/setup")
            else:
                user = user_for(db, request.cookies.get(COOKIE))
                looking = seen_as(db, user, request.cookies.get(VIEW_AS_COOKIE)) if user is not None and not path.startswith("/account/view-as") else None
                if looking is not None:
                    user = looking
                if looking is not None and scope.get("method", "GET") not in ("GET", "HEAD", "OPTIONS") and not path.startswith("/i18n/seen"):
                    response = self._refuse_looking(request, looking)
                elif user is None:
                    wanted = path + (("?" + scope["query_string"].decode()) if scope.get("query_string") else "")
                    response = self._refuse(request, "/login" + (f"?next={quote(wanted, safe='')}" if path != "/" else ""))
                elif user.is_professor and not professor_may_open(db, user, path):
                    response = self._refuse(request, "/teacher", status=403)
                elif user.is_staff and not _staff_may(user, path, scope.get("method", "GET")):
                    response = self._refuse_staff(request, user)
                else:
                    scope.setdefault("state", {})["user"] = user
                    response = None
        finally:
            db.close()

        if response is not None:
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _refuse_looking(request: Request, user: CurrentUser):
        """An admin looking as someone else: nothing is changed in their name."""
        if "application/json" in request.headers.get("content-type", "") or "x-live-refresh" in request.headers:
            return JSONResponse({"detail": f"You are looking as {user.name}: nothing can be changed. Go back to your own view first."}, status_code=403)
        back = request.headers.get("referer") or "/"
        back = back if back.startswith(("/", str(request.base_url))) else "/"
        joiner = "&" if "?" in back else "?"
        return RedirectResponse(back + joiner + "error=" + quote(f"You are looking as {user.name}: nothing can be changed. Go back to your own view first."),
                                status_code=303)

    @staticmethod
    def _refuse_staff(request: Request, user: CurrentUser):
        """A team member outside their access: back to where they were, told why."""
        if request.method != "GET" and "x-live-refresh" not in request.headers and "application/json" not in request.headers.get("content-type", ""):
            back = request.headers.get("referer") or user.home
            back = back if back.startswith(("/", str(request.base_url))) else user.home
            joiner = "&" if "?" in back else "?"
            return RedirectResponse(back + joiner + "error=" + quote("You can look at this part, but not change it. Ask an admin."), status_code=303)
        return AccessGate._refuse(request, user.home, status=403)

    @staticmethod
    def _refuse(request: Request, where: str, status: int = 401):
        """Pages are sent to the right place; calls made by a page's script get a plain refusal."""
        wants_page = request.method == "GET" and "text/html" in request.headers.get("accept", "")
        if wants_page and "x-live-refresh" not in request.headers:
            return RedirectResponse(where, status_code=303)
        detail = "Sign in to continue." if status == 401 else "This page is not part of your space."
        return JSONResponse({"detail": detail}, status_code=status)

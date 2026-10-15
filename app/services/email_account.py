"""The address the centre's emails are sent from, changed from Settings.

The address and its app password live in the `.env` file next to the
application (never in the database, so never in its copies). Changing them
here checks that the mail server accepts them, writes the two lines, and
makes the running application use them at once. The password is never shown
again.
"""
from __future__ import annotations

import os
import re
import smtplib
from pathlib import Path

# How to get the password of a Gmail address: shown as steps in Settings.
GMAIL_STEPS = [
    ("Open your Google account", "Sign in at myaccount.google.com with the address the centre writes from.", "https://myaccount.google.com/security"),
    ("Turn on 2-Step Verification", "In “Security”, under “How you sign in to Google”. Google only gives app passwords to accounts that have it.", "https://myaccount.google.com/signinoptions/two-step-verification"),
    ("Create an app password", "Open “App passwords”, type a name such as 9antra, and press Create.", "https://myaccount.google.com/apppasswords"),
    ("Copy the 16 letters", "Google shows them once, in a yellow box. Paste them below; the spaces do not matter.", None),
    ("Let the application read the answers", "In Gmail: Settings → See all settings → Forwarding and POP/IMAP → Enable IMAP → Save.", "https://mail.google.com/mail/u/0/#settings/fwdandpop"),
]
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def env_file() -> Path:
    return Path(os.getenv("ENV_FILE") or ".env")


def current() -> dict:
    return {"email": (os.getenv("SMTP_EMAIL") or "").strip(), "has_password": bool(os.getenv("SMTP_PASSWORD"))}


def clean_password(value: str | None) -> str:
    """Google shows an app password in four groups; the spaces are not part of it."""
    return "".join((value or "").split())


def check(email: str, password: str) -> str | None:
    """Ask the mail server whether it accepts this address and password. Nothing is sent. Returns the problem."""
    host, port = os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "465"))
    try:
        if port == 587:
            with smtplib.SMTP(host=host, port=port, timeout=25) as server:
                server.starttls()
                server.login(email, password)
        else:
            with smtplib.SMTP_SSL(host=host, port=port, timeout=25) as server:
                server.login(email, password)
    except smtplib.SMTPAuthenticationError:
        return ("The mail server refused this address and password. For Gmail it must be an app password "
                "(16 letters), not the password you sign in with: follow the steps below.")
    except (OSError, smtplib.SMTPException) as exc:
        return f"The mail server could not be reached ({exc.__class__.__name__}). Check the internet connection and try again."
    return None


def _write(values: dict[str, str]) -> None:
    path = env_file()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    left = dict(values)
    for index, line in enumerate(lines):
        name = line.split("=", 1)[0].strip()
        if name in left and not line.lstrip().startswith("#"):
            lines[index] = f"{name}={left.pop(name)}"
    lines += [f"{name}={value}" for name, value in left.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save(email: str, password: str, *, verify: bool = True) -> str | None:
    """Use this address from now on. Returns the problem when it cannot be used; nothing is changed then."""
    email, password = (email or "").strip().lower(), clean_password(password)
    if not _ADDRESS.match(email):
        return "Write the whole address, for example contact@9antra.tn."
    if not password:
        if email == current()["email"].lower() and current()["has_password"]:
            return None                     # nothing to change
        return "A new address needs its own app password: follow the steps below, then paste the 16 letters."
    if verify:
        problem = check(email, password)
        if problem:
            return problem
    _write({"SMTP_EMAIL": email, "SMTP_PASSWORD": password})
    os.environ["SMTP_EMAIL"], os.environ["SMTP_PASSWORD"] = email, password
    from app.services import email_service        # it read the two values when the application started

    email_service.SMTP_EMAIL, email_service.SMTP_PASSWORD = email, password
    return None

"""Access to the single SystemSettings row that holds admin-editable defaults."""
from __future__ import annotations

import os

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.admin_config import SystemSettings

DEFAULT_REMINDER_DAYS = [7, 3, 1]


def get_settings(db: Session) -> SystemSettings:
    """Return the settings row, creating it with defaults on first use."""
    settings = db.query(SystemSettings).first()
    if settings is None:
        # Created in its own transaction so reading settings never leaves a
        # pending write in the caller's session.
        with SessionLocal() as setup:
            if setup.query(SystemSettings).first() is None:
                setup.add(SystemSettings(
                    smtp_email=os.getenv("SMTP_EMAIL") or None,
                    default_class_capacity=int(os.getenv("DEFAULT_CLASS_CAPACITY", "30")),
                    default_class_start_days=int(os.getenv("DEFAULT_CLASS_START_DAYS", "10")),
                ))
                setup.commit()
        settings = db.query(SystemSettings).first()
    return settings


def public_base_url(db: Session, request=None) -> str:
    """The address candidates use to open the links in their emails.

    The one saved in Settings (or PUBLIC_BASE_URL); else the address the
    admin is using right now, which only works for candidates when the
    application is reachable from outside this computer.
    """
    saved = (get_settings(db).public_base_url or os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if saved:
        return saved
    if request is not None:
        return str(request.base_url).rstrip("/")
    return f"http://127.0.0.1:{os.getenv('PORT', '8000')}"


def clean_public_url(value: str | None) -> str | None:
    """A web address typed by the admin, or None when the field was left empty.

    Raises ValueError when it is not an address.
    """
    value = (value or "").strip().rstrip("/")
    if not value:
        return None
    if "://" not in value:
        value = "https://" + value
    scheme, _, rest = value.partition("://")
    if scheme.lower() not in ("http", "https") or not rest or " " in rest:
        raise ValueError("Enter a web address such as https://app.9antra.tn")
    return value[:200]


def parse_reminder_days(value: str | list | None) -> list[int]:
    """'7, 3,1' -> [7, 3, 1] (unique, 1-60, furthest first)."""
    if value is None:
        return list(DEFAULT_REMINDER_DAYS)
    parts = value if isinstance(value, (list, tuple)) else str(value).replace(";", ",").split(",")
    days = set()
    for part in parts:
        try:
            number = int(str(part).strip())
        except ValueError:
            continue
        if 1 <= number <= 60:
            days.add(number)
    return sorted(days, reverse=True)


def format_reminder_days(days: list[int]) -> str:
    return ",".join(str(day) for day in sorted(set(days), reverse=True))

"""The timer of each reminder the application sends by itself, in any unit (services/durations.py), kept in
system_settings. Each one: where it is kept, its original value, its bounds, the units it takes, and what it means.
The older settings in whole hours or days (payment_reminder_hours, session_reminder_hours, resource_reminder_days) are
read when no timer was saved in the new way."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.services import durations
from app.services.durations import Duration

D = durations.parse
SPECS = {
    "payment_reminder": {"column": "payment_reminder_after", "default": "24h", "least": "10s", "most": "3mo",
                         "legacy": ("payment_reminder_hours", "hours"), "after": "after registering"},
    "session_reminder": {"column": "session_reminder_before", "default": "24h", "least": "1min", "most": "1mo",
                         "legacy": ("session_reminder_hours", "hours"), "after": "before each session"},
    "session_link": {"column": "session_link_before", "default": "60min", "least": "1min", "most": "2d", "after": "before each session"},
    "level_reminder": {"column": "level_reminder_after", "default": "2d", "least": "10min", "most": "1mo", "after": "without an answer"},
    "resources": {"column": "resource_reminder_before", "default": "2d", "least": "1h", "most": "1mo",
                  "legacy": ("resource_reminder_days", "days"), "after": "before the week"},
    "question_remind": {"column": "question_remind_after", "default": "24h", "least": "10min", "most": "2w", "after": "without an answer"},
    "question_admin": {"column": "question_admin_after", "default": "48h", "least": "10min", "most": "1mo", "after": "without an answer"},
}


def get(db: Session, key: str) -> Duration:
    from app.services.settings_service import get_settings

    spec = SPECS[key]
    settings = get_settings(db)
    found = durations.parse(getattr(settings, spec["column"], None) or "", "hours")
    if found is None and spec.get("legacy"):
        column, unit = spec["legacy"]
        value = getattr(settings, column, None)
        found = durations.parse(str(value), unit) if value else None
    return found if found is not None and found.value > 0 else D(spec["default"])


def save(db: Session, key: str, duration: Duration) -> Duration:
    """Keep a timer (raises ValueError when out of bounds); the older whole-hour or whole-day setting follows."""
    from app.services.settings_service import get_settings

    spec = SPECS[key]
    duration = durations.checked(duration, least=D(spec["least"]), most=D(spec["most"]))
    settings = get_settings(db)
    setattr(settings, spec["column"], duration.code)
    if spec.get("legacy"):
        column, unit = spec["legacy"]
        whole = max(1, -(-duration.seconds // (3600 if unit == "hours" else 86400)))
        setattr(settings, column, whole)
    db.commit()
    return duration


def reset(db: Session, key: str) -> Duration:
    return save(db, key, D(SPECS[key]["default"]))

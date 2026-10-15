"""Date, time and weekday helpers shared by class scheduling, reminders and emails.

Class dates and session times are wall-clock values in the platform timezone
(Africa/Tunis by default), stored as naive datetimes.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, time, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Africa/Tunis")

DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_LABELS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_SHORT_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_LABELS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

_DAY_ALIASES: dict[str, int] = {}
for _index, _names in enumerate([
    ("mon", "monday", "lun", "lundi"),
    ("tue", "tues", "tuesday", "mar", "mardi"),
    ("wed", "wednesday", "mer", "mercredi"),
    ("thu", "thur", "thurs", "thursday", "jeu", "jeudi"),
    ("fri", "friday", "ven", "vendredi"),
    ("sat", "saturday", "sam", "samedi"),
    ("sun", "sunday", "dim", "dimanche"),
]):
    for _name in _names:
        _DAY_ALIASES[_name] = _index


def _tz():
    if ZoneInfo is None:
        return timezone(timedelta(hours=1))
    try:
        return ZoneInfo(APP_TIMEZONE)
    except Exception:
        return timezone(timedelta(hours=1))


def local_now() -> datetime:
    """Current wall-clock time in the platform timezone, as a naive datetime."""
    return datetime.now(_tz()).replace(tzinfo=None)


def utc_to_local(value: datetime | None) -> datetime | None:
    """Convert a stored UTC timestamp (naive or aware) to naive local time."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_tz()).replace(tzinfo=None)


# ------------------------------------------------------------------
# Weekdays
# ------------------------------------------------------------------

def parse_days(value: str | list | None) -> list[int]:
    """'Mon, Wed' / 'sat' / ['sat'] -> sorted weekday indexes (Monday=0)."""
    if not value:
        return []
    parts = value if isinstance(value, (list, tuple, set)) else re.split(r"[,;/\s]+", str(value))
    days = {
        _DAY_ALIASES[str(part).strip().lower()]
        for part in parts
        if str(part).strip().lower() in _DAY_ALIASES
    }
    return sorted(days)


def format_days(days: list[int]) -> str | None:
    """Weekday indexes -> stored value such as 'mon,wed'."""
    keys = [DAY_KEYS[day] for day in sorted(set(days)) if 0 <= day <= 6]
    return ",".join(keys) or None


def normalize_days(value: str | list | None) -> str | None:
    return format_days(parse_days(value))


def parse_time(value: str | time | None) -> time | None:
    if isinstance(value, time):
        return value
    if not value or not str(value).strip():
        return None
    match = re.match(r"^\s*(\d{1,2})\s*[:h]\s*(\d{2})", str(value))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def parse_session_time_text(text: str | None) -> tuple[list[int], time | None, time | None]:
    """Read a program schedule such as 'Saturday from 9:00 until 13:00 (Online/Onsite)'."""
    if not text:
        return [], None, None
    lowered = text.lower()
    days = sorted({
        index for name, index in _DAY_ALIASES.items()
        if len(name) > 3 and re.search(rf"\b{name}s?\b", lowered)
    })
    times = []
    for hour, minute in re.findall(r"\b(\d{1,2})\s*[:h]\s*(\d{2})\b", lowered):
        if int(hour) <= 23 and int(minute) <= 59:
            times.append(time(int(hour), int(minute)))
    start = times[0] if times else None
    end = times[1] if len(times) > 1 else None
    if start and end and end <= start:
        end = None
    return days, start, end


def first_session_on_or_after(day: date, days: list[int]) -> date:
    """First date on or after `day` that falls on one of the session weekdays."""
    if not days:
        return day
    for offset in range(7):
        candidate = day + timedelta(days=offset)
        if candidate.weekday() in days:
            return candidate
    return day


# ------------------------------------------------------------------
# Class timing
# ------------------------------------------------------------------

def class_start_dt(class_group) -> datetime | None:
    """Moment the class starts (`start_date` carries date and time), or None if not scheduled yet."""
    return getattr(class_group, "start_date", None) or None


def reminder_anchor(class_group) -> datetime | None:
    """First session date at the session time; reminders count back from here.

    Classes without a session time are anchored at 09:00 so reminders never go out at midnight.
    """
    start = class_start_dt(class_group)
    if not start:
        return None
    return datetime.combine(start.date(), getattr(class_group, "start_time", None) or time(9, 0))


def class_end_dt(class_group) -> datetime | None:
    """End of the last session. `end_date` is only set when an admin closes a class."""
    end = getattr(class_group, "end_date", None)
    if end:
        return end
    start = class_start_dt(class_group)
    if not start:
        return None
    weeks = max(1, getattr(class_group, "total_weeks", None) or 12)
    last_day = start.date() + timedelta(weeks=weeks - 1)
    return datetime.combine(last_day, getattr(class_group, "end_time", None) or time(23, 59, 59))


def humanize_until(target: datetime | None, now: datetime | None = None) -> str:
    """'in 5 days', 'tomorrow', 'today at 09:00', '3 days ago'."""
    if target is None:
        return "not scheduled"
    now = now or local_now()
    days = (target.date() - now.date()).days
    if days > 1:
        return f"in {days} days"
    if days == 1:
        return "tomorrow"
    if days == 0:
        return f"today at {target:%H:%M}" if target > now else "today"
    if days == -1:
        return "yesterday"
    return f"{-days} days ago"


# ------------------------------------------------------------------
# Formatting
# ------------------------------------------------------------------

def format_date_en(value: datetime | date | None) -> str:
    if value is None:
        return "To be confirmed"
    return f"{DAY_SHORT_EN[value.weekday()]} {value:%d %b %Y}"


def format_date_fr(value: datetime | date | None) -> str:
    if value is None:
        return "à confirmer"
    day = "1er" if value.day == 1 else str(value.day)
    return f"{DAY_LABELS_FR[value.weekday()]} {day} {MONTHS_FR[value.month - 1]} {value.year}"


def format_time_range(start: time | None, end: time | None, separator: str = "–") -> str | None:
    if not start:
        return None
    if end:
        return f"{start:%H:%M}{separator}{end:%H:%M}"
    return f"{start:%H:%M}"


def describe_schedule_en(class_group) -> str:
    days = parse_days(getattr(class_group, "days_of_week", None))
    hours = format_time_range(class_group.start_time, class_group.end_time)
    parts = []
    if days:
        parts.append("Every " + ", ".join(DAY_SHORT_EN[day] for day in days))
    if hours:
        parts.append(hours)
    return " · ".join(parts) or "Schedule to be set"


def describe_schedule_fr(class_group) -> str:
    days = parse_days(getattr(class_group, "days_of_week", None))
    hours = None
    if class_group.start_time and class_group.end_time:
        hours = f"de {class_group.start_time:%H:%M} à {class_group.end_time:%H:%M}"
    elif class_group.start_time:
        hours = f"à {class_group.start_time:%H:%M}"
    parts = []
    if days:
        parts.append("Chaque " + ", ".join(DAY_LABELS_FR[day] for day in days))
    if hours:
        parts.append(hours)
    return ", ".join(parts) or "à confirmer"

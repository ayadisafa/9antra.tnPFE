"""A length of time with its unit, for every reminder's timer: seconds, minutes, hours, days, weeks or months.

Kept as a short code ("45s", "30min", "24h", "7d", "2w", "1mo"); a bare number is read in the unit the place expects
(days for the countdown before the first session, for instance). A month is a calendar month when a date moves by it
(the 31st becomes the last day of a shorter month), and about 30 days when it is only compared.

The jobs that send reminders look every minute (the admin's own reminders every 10 seconds): a few seconds go at the
next look.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

# (unit, code, seconds; a month is calendar-aware when applied)
UNITS = [("seconds", "s", 1), ("minutes", "min", 60), ("hours", "h", 3600), ("days", "d", 86400), ("weeks", "w", 604800),
         ("months", "mo", 2629746)]
_BY_UNIT = {unit: (code, seconds) for unit, code, seconds in UNITS}
_BY_CODE = {code: unit for unit, code, _ in UNITS}
NAMES = [unit for unit, _, _ in UNITS]
DATE_UNITS = ("days", "weeks", "months")          # for what is counted in whole days (a month starts on a date)
_WORDS = {
    "en": {"seconds": ("second", "seconds"), "minutes": ("minute", "minutes"), "hours": ("hour", "hours"), "days": ("day", "days"),
           "weeks": ("week", "weeks"), "months": ("month", "months")},
    "fr": {"seconds": ("seconde", "secondes"), "minutes": ("minute", "minutes"), "hours": ("heure", "heures"), "days": ("jour", "jours"),
           "weeks": ("semaine", "semaines"), "months": ("mois", "mois")},
}
_CODE = re.compile(r"^\s*(\d{1,5})\s*(s|sec|secs|seconds?|min|mins|minutes?|h|hr|hrs|hours?|d|days?|j|jours?|w|wk|weeks?|semaines?|"
                   r"mo|mon|months?|mois)?\s*$", re.IGNORECASE)
_ALIASES = {"s": "seconds", "sec": "seconds", "secs": "seconds", "second": "seconds", "seconds": "seconds",
            "min": "minutes", "mins": "minutes", "minute": "minutes", "minutes": "minutes",
            "h": "hours", "hr": "hours", "hrs": "hours", "hour": "hours", "hours": "hours",
            "d": "days", "day": "days", "days": "days", "j": "days", "jour": "days", "jours": "days",
            "w": "weeks", "wk": "weeks", "week": "weeks", "weeks": "weeks", "semaine": "weeks", "semaines": "weeks",
            "mo": "months", "mon": "months", "month": "months", "months": "months", "mois": "months"}


@dataclass(frozen=True)
class Duration:
    value: int
    unit: str

    @property
    def code(self) -> str:
        return f"{self.value}{_BY_UNIT[self.unit][0]}"

    @property
    def seconds(self) -> int:
        return self.value * _BY_UNIT[self.unit][1]

    @property
    def delta(self) -> timedelta:
        return timedelta(seconds=self.seconds)

    @property
    def days(self) -> int:
        """In whole days, rounded up (for what is counted in days)."""
        return max(0, -(-self.seconds // 86400))

    def label(self, lang: str = "en") -> str:
        one, many = (_WORDS.get(lang) or _WORDS["en"])[self.unit]
        return f"{self.value} {one if self.value == 1 else many}"

    def after(self, moment):
        """The moment this long after (a month: the same day of the next month, or its last day)."""
        if self.unit == "months":
            return _add_months(moment, self.value)
        return moment + self.delta

    def before(self, moment):
        if self.unit == "months":
            return _add_months(moment, -self.value)
        return moment - self.delta


def _add_months(moment, count: int):
    month = moment.month - 1 + count
    year, month = moment.year + month // 12, month % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def parse(text, default_unit: str = "hours") -> Duration | None:
    """"24h", "90 min", "2 weeks", "7" (in `default_unit`) -> Duration, or None when it cannot be read."""
    if isinstance(text, Duration):
        return text
    found = _CODE.match(str(text or ""))
    if found is None:
        return None
    unit = _ALIASES[found.group(2).lower()] if found.group(2) else default_unit
    return Duration(int(found.group(1)), unit)


def of(value, unit: str) -> Duration:
    """From a form's number and unit; raises ValueError with what is wrong."""
    if unit not in _BY_UNIT:
        raise ValueError("Choose a unit: seconds, minutes, hours, days, weeks or months.")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("Write the time as a whole number.") from None
    if number < 0 or number > 99999:
        raise ValueError("Write the time as a whole number.")
    return Duration(number, unit)


def checked(duration: Duration, *, least: Duration | None = None, most: Duration | None = None, units=None, zero: bool = False) -> Duration:
    """The duration when it is within bounds (and in the allowed units); raises ValueError with what is wrong."""
    if units and duration.unit not in units:
        raise ValueError("Here the time is counted in " + ", ".join(units) + ".")
    if duration.value == 0 and not zero:
        raise ValueError("Write a time of at least 1.")
    if least is not None and duration.seconds < least.seconds:
        raise ValueError(f"At least {least.label()}.")
    if most is not None and duration.seconds > most.seconds:
        raise ValueError(f"At most {most.label()}.")
    return duration


def parse_list(text, default_unit: str = "days") -> list[Duration]:
    """"7, 3, 1" (days) or "7d, 3d, 12h": each once, the longest first."""
    parts = text if isinstance(text, (list, tuple)) else str(text or "").replace(";", ",").split(",")
    found = {}
    for part in parts:
        duration = parse(part, default_unit)
        if duration is not None and duration.value > 0:
            found[duration.seconds] = duration
    return [found[key] for key in sorted(found, reverse=True)]


def format_list(durations) -> str:
    return ",".join(item.code for item in durations)


def label_list(durations, lang: str = "en") -> str:
    return ", ".join(item.label(lang) for item in durations)


def rows(duration: Duration | None, default: Duration) -> tuple[int, str]:
    """(value, unit) for a form."""
    duration = duration or default
    return duration.value, duration.unit


def to_date(moment) -> date:
    return moment.date() if isinstance(moment, datetime) else moment

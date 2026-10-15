"""Who sees what: the parts of the application, and what a team member may do in each.

The admin sees and changes everything. A professor has their own space (see
auth.professor_may_open). A team member ("staff") gets, for each part, one of:
not shown, can look, can change. Looking is opening pages; changing is any
form or button that writes. Managing the team itself stays with the admin.
"""
from __future__ import annotations

import json

SECTIONS = [
    # key, name shown, icon, where it starts, the addresses that belong to it
    ("dashboard", "Dashboard", "bi-grid-1x2-fill", "/", ("/guide", "/payments/reminders")),
    ("candidates", "Candidates", "bi-people-fill", "/candidate/all", ("/candidate",)),
    ("classes", "Classes", "bi-easel2-fill", "/classes/", ("/classes",)),
    ("programs", "Programs", "bi-journal-bookmark-fill", "/programs/all", ("/programs",)),
    ("levels", "Level checks", "bi-signpost-split-fill", "/quiz/all", ("/quiz",)),
    ("inbox", "Inbox", "bi-envelope-paper-fill", "/inbox", ("/inbox",)),
    ("payments", "Payments", "bi-cash-coin", "/payments", ("/payments", "/payment")),
    ("reminders", "Reminders", "bi-alarm-fill", "/reminders", ("/reminders",)),
    ("website", "Website", "bi-globe2", "/website", ("/website",)),
    ("system", "How it is working", "bi-activity", "/system", ("/system",)),
    ("settings", "Settings", "bi-gear-fill", "/settings", ("/settings",)),
    ("data", "All the data", "bi-table", "/data", ("/data",)),
]
LEVELS = {"none": "Not shown", "view": "Can look", "edit": "Can change"}
NAMES = {key: name for key, name, *_ in SECTIONS}
# Open to every signed-in person: their account, the language, the search, the live updates.
EVERYONE = ("/account", "/logout", "/language", "/i18n/", "/find", "/realtime", "/static/")
# Never for a team member, whatever they were given: managing the team and the raw copies of the data.
ADMIN_ONLY = ("/settings/team", "/system/backups", "/system/backup-folder", "/settings/email-account")
_READING = {"GET", "HEAD", "OPTIONS"}
# Some forms only read (a preview, the search, the assistant's chat): they need looking, not changing.
_READS_BY_POST = ("/reminders/preview", "/inbox/compose/preview", "/inbox/templates/preview", "/i18n/seen")


def clean(value) -> dict[str, str]:
    """A team member's access as {section: none | view | edit}, every section present."""
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except ValueError:
            value = {}
    value = value if isinstance(value, dict) else {}
    return {key: value.get(key) if value.get(key) in LEVELS else "none" for key, *_ in SECTIONS}


def dump(value) -> str:
    return json.dumps(clean(value), sort_keys=True)


def section_of(path: str) -> str | None:
    """The part of the application an address belongs to; None when it belongs to none of them."""
    if path == "/":
        return "dashboard"
    best, length = None, 0
    for key, _, _, start, prefixes in SECTIONS:
        for prefix in prefixes + ((start,) if start != "/" else ()):
            if (path == prefix or path.startswith(prefix.rstrip("/") + "/") or path.startswith(prefix)) and len(prefix) > length:
                best, length = key, len(prefix)
    return best


def allows(permissions: dict[str, str], path: str, method: str) -> bool:
    """Whether a team member with these permissions may open this address with this method."""
    if path.startswith(EVERYONE):
        return True
    if path.startswith(ADMIN_ONLY):
        return False
    section = section_of(path)
    if section is None:
        return False
    level = permissions.get(section, "none")
    if level == "edit":
        return True
    return level == "view" and (method.upper() in _READING or path.startswith(_READS_BY_POST))


def home_of(permissions: dict[str, str]) -> str:
    """Where a team member lands: the first part they may look at."""
    for key, _, _, start, _ in SECTIONS:
        if permissions.get(key) in ("view", "edit"):
            return start
    return "/account"


def menu(permissions: dict[str, str] | None) -> list[dict]:
    """The parts a team member sees in the menu, in order."""
    if permissions is None:
        return []
    return [{"key": key, "name": name, "icon": icon, "url": start, "level": permissions.get(key)}
            for key, name, icon, start, _ in SECTIONS if permissions.get(key) in ("view", "edit")]

"""Which PDF goes with which page: the address the "PDF" button of the top bar opens."""
from __future__ import annotations

import re

# (the page's address, the PDF's address); {0} is the record the page is about.
_LINKS = [
    (r"/", "/dashboard/pdf"),
    (r"/candidate/all", "/candidate/all/pdf"),
    (r"/candidate/([^/]+)", "/candidate/{0}/pdf"),
    (r"/classes/?", "/classes/pdf"),
    (r"/classes/([^/]+)/attendance", "/classes/{0}/attendance/pdf"),
    (r"/classes/([^/]+)", "/classes/{0}/pdf"),
    (r"/programs/all", "/programs/pdf"),
    (r"/programs/market", "/programs/market/pdf"),
    (r"/programs/([^/]+)", "/programs/{0}/pdf"),
    (r"/quiz/all", "/quiz/pdf"),
    (r"/inbox", "/inbox/pdf"),
    (r"/payments", "/payments/pdf"),
    (r"/reminders", "/reminders/pdf"),
    (r"/website", "/website/pdf"),
    (r"/system", "/system/pdf"),
    (r"/settings", "/settings/pdf"),
    (r"/data/([a-z_]+)", "/data/{0}.pdf"),
]
_PATTERNS = [(re.compile(rf"^{page}$"), pdf) for page, pdf in _LINKS]
# Words that are pages of their own, not records.
_NOT_RECORDS = {"all", "pdf", "documents", "upload", "market", "icons", "confirm-import", "create", "registration",
                "fuse-duplicates", "fuse-duplicates-stream", "summary.pdf", "catalogue.pdf"}


def link_for(path: str, user=None) -> str | None:
    """The PDF of the page at `path`, if it has one and this person may open it."""
    for pattern, pdf in _PATTERNS:
        found = pattern.match(path or "")
        if not found:
            continue
        if found.groups() and found.group(1) in _NOT_RECORDS:
            continue
        link = pdf.format(*found.groups())
        if user is None or getattr(user, "is_admin", False):
            return link
        if getattr(user, "is_professor", False):
            return link if link.endswith("/attendance/pdf") else None
        from app.services import access

        return link if access.allows(user.permissions or {}, link, "GET") else None
    return None


# Other reports a page offers next to its own PDF: (page, label, address, icon).
_MORE = [
    ("/", "Analysis: how we progress, what to improve", "/dashboard/analysis/pdf", "bi-graph-up-arrow"),
    ("/", "Candidates' progress: are they learning?", "/candidate/all/analysis/pdf", "bi-mortarboard"),
    ("/candidate/all", "Candidates' progress: are they learning?", "/candidate/all/analysis/pdf", "bi-mortarboard"),
    ("/programs/all", "The programs for the team (registrations, money)", "/programs/overview/pdf", "bi-table"),
    ("/programs/all", "Every program in detail, week by week", "/programs/catalogue.pdf", "bi-journal-richtext"),
]


def more_for(path: str, user=None) -> list[dict]:
    """The other reports of this page that this person may open: [{"label", "url", "icon"}]."""
    found = []
    for page, label, url, icon in _MORE:
        if page != (path or ""):
            continue
        if user is not None and not getattr(user, "is_admin", False):
            if getattr(user, "is_professor", False):
                continue
            from app.services import access

            if not access.allows(user.permissions or {}, url, "GET"):
                continue
        found.append({"label": label, "url": url, "icon": icon})
    return found

"""The progress report by email: how the centre is doing, sent to the admin on a rhythm they choose.

Every week, every 10 days, any number of days up to 60, or of weeks up to 8.
It carries two PDFs (the analysis of the centre and the candidates' progress)
and, in the email itself, the main figures and what to improve first.

It goes to the admin's own addresses only (never to candidates). The clock starts
when the report is switched on: the first one goes a full period later, never at once.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.services.settings_service import get_settings

logger = logging.getLogger(__name__)

MOST_DAYS, MOST_WEEKS = 60, 8
_ADDRESS = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+")


def is_on(settings) -> bool:
    value = getattr(settings, "report_email_on", None)
    return True if value is None else bool(value)


def every_days(settings) -> int:
    try:
        days = int(getattr(settings, "report_every_days", None) or 7)
    except (TypeError, ValueError):
        days = 7
    return max(1, min(MOST_DAYS, days))


def rhythm_label(days: int) -> str:
    if days == 7:
        return "every week"
    if days == 1:
        return "every day"
    if days % 7 == 0 and days // 7 <= MOST_WEEKS:
        return f"every {days // 7} weeks"
    return f"every {days} days"


def recipients(db: Session, settings=None) -> list[str]:
    """Where it goes: the addresses written in Settings, else the admins' own addresses."""
    import os

    from app.models.user import User

    settings = settings or get_settings(db)
    written = _ADDRESS.findall(getattr(settings, "report_recipients", None) or "")
    if written:
        return list(dict.fromkeys(address.lower() for address in written))[:10]
    admins = [user.email for user in db.query(User).filter(User.role == "admin", User.is_active.is_(True)).all() if user.email]
    found = admins or [os.getenv("SMTP_EMAIL") or ""]
    return [address for address in dict.fromkeys(found) if "@" in address]


def next_at(settings) -> datetime | None:
    """When the next report goes (UTC), or None when it is off or its clock has not started."""
    if not is_on(settings):
        return None
    starts = [moment for moment in (getattr(settings, "report_sent_at", None), getattr(settings, "report_since", None)) if moment]
    if not starts:
        return None
    return max(moment.replace(tzinfo=None) for moment in starts) + timedelta(days=every_days(settings))


def files(db: Session) -> list[dict]:
    """The two PDFs it carries."""
    from app.api.page_pdf import analysis_report, learners_report
    from app.services import report_pdf

    found = []
    for maker in (analysis_report, learners_report):
        report = maker(db)
        data = report_pdf.build(db, title=report["title"], blocks=report["blocks"], overline=report["overline"], subtitle=report["subtitle"])
        found.append({"data": data, "filename": report_pdf.filename(report["title"]), "content_type": "application/pdf"})
    return found


def message(db: Session) -> dict:
    """{"subject", "html", "text"}: the main figures and what to improve first."""
    from app.services import analysis
    from app.services.email_layout import branding, render_email, render_plain
    from app.services.settings_service import public_base_url

    settings = get_settings(db)
    data = analysis.centre(db)
    figures = data["figures"]
    rows = [
        ("Candidates", f"{data['candidates']} ({figures['new_candidates']} new this week)"),
        ("Collected", f"{figures['collected']:,.0f} DT · {figures['collection_rate']}% of what is due".replace(",", " ")),
        ("Still due", f"{figures['due']:,.0f} DT from {figures['owing_candidates']} people".replace(",", " ")),
        ("Classes running", str(figures["classes_running"])),
        ("Emails waiting for you", str(figures["emails_waiting"])),
    ]
    for row in data["progress"]:
        if row["change"]:
            rows.append((f"{row['label']} (last 4 weeks)", f"{row['change']} against the 4 weeks before"))
    bullets = [item["text"] for item in data["improve"][:6]]
    rhythm = rhythm_label(every_days(settings))
    brand = branding(settings, "en")
    closing = (f"The full analysis and the candidates' progress are attached as PDFs. This report comes {rhythm}; "
               "you can change that, or switch it off, in Settings, under “Report by email”.")
    link = public_base_url(db)
    subject = f"Your report: how the centre is doing ({datetime.now():%d %b %Y})"
    html = render_email(title="How the centre is doing", greeting="Hello,", rows=rows, bullets=bullets, bullets_title="What to improve first",
                        closing=closing, cta=("Open the dashboard", link), signature=brand["signature"], footer_note=brand["footer_note"],
                        accent=brand["accent"], lang="en", preheader="The analysis of the centre and the candidates' progress, attached.")
    text = render_plain(greeting="Hello,", rows=rows, bullets=bullets, bullets_title="What to improve first", closing=closing,
                        cta=("Open the dashboard", link), signature=brand["signature"])
    return {"subject": subject, "html": html, "text": text}


def send(db: Session, now: datetime | None = None) -> dict:
    """Send the report now: {"ok", "to", "problem"}."""
    from app.services import email_threads
    from app.services.email_service import send_email

    if not email_threads.smtp_configured():
        return {"ok": False, "to": [], "problem": "Email is not set up, so no report can leave."}
    to = recipients(db)
    if not to:
        return {"ok": False, "to": [], "problem": "There is no address to send the report to."}
    content, attached = message(db), files(db)
    sent = []
    for address in to:
        try:
            if send_email(to_email=address, subject=content["subject"], body=content["html"], is_html=True, text_body=content["text"],
                          journal=False, attachments=attached):
                sent.append(address)
        except Exception as error:      # one address that fails does not stop the others
            logger.warning("Report to %s failed: %s", address, error)
    if not sent:
        return {"ok": False, "to": to, "problem": "The mail server did not accept the report."}
    get_settings(db).report_sent_at = now or datetime.utcnow()
    db.commit()
    return {"ok": True, "to": sent, "problem": None}


def send_if_due(db: Session, now: datetime | None = None) -> dict | None:
    """Called every minute: starts the clock when the report is on, sends it when its time has come."""
    now = now or datetime.utcnow()
    settings = get_settings(db)
    if not is_on(settings):
        return None
    if not getattr(settings, "report_since", None) and not getattr(settings, "report_sent_at", None):
        settings.report_since = now         # the first one goes a full period from now
        db.commit()
        return None
    due = next_at(settings)
    if due is None or now < due:
        return None
    return send(db, now)


def choose(settings, *, on: bool, rhythm: str, days: str | int | None, weeks: str | int | None, addresses: str, now: datetime | None = None) -> str | None:
    """Save the admin's choice. Returns what is wrong, or None."""
    now = now or datetime.utcnow()
    if rhythm == "week":
        chosen = 7
    elif rhythm == "ten":
        chosen = 10
    elif rhythm == "days":
        try:
            chosen = int(days)
        except (TypeError, ValueError):
            return "Write how many days between two reports."
        if not 1 <= chosen <= MOST_DAYS:
            return f"Choose between 1 and {MOST_DAYS} days."
    elif rhythm == "weeks":
        try:
            count = int(weeks)
        except (TypeError, ValueError):
            return "Write how many weeks between two reports."
        if not 1 <= count <= MOST_WEEKS:
            return f"Choose between 1 and {MOST_WEEKS} weeks."
        chosen = count * 7
    else:
        return "Choose how often the report comes."
    written = (addresses or "").strip()
    if written and not _ADDRESS.findall(written):
        return "Write one or more email addresses, separated by commas."
    was_on = is_on(settings) and bool(getattr(settings, "report_since", None) or getattr(settings, "report_sent_at", None))
    settings.report_every_days = chosen
    settings.report_recipients = ", ".join(dict.fromkeys(address.lower() for address in _ADDRESS.findall(written))) or None
    settings.report_email_on = bool(on)
    if on and not was_on:
        settings.report_since = now          # switched on: the first report one period from now
    return None

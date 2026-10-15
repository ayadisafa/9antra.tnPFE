"""How the application is working right now, in plain words (the System page):
the work it does by itself, and the services it is connected to.

The scheduled jobs report here each time they run. This is kept in memory: it
starts again with the server, which is what "since it started" means.
"""
from __future__ import annotations

import functools
import os
import socket
import threading
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.candidate_email import CandidateEmail
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.email_thread import EmailThread
from app.models.program_proposal import ProgramProposal
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, Registration, RegistrationStatus
from app.models.website import WebsiteItem

STARTED_AT = datetime.now()
_lock = threading.Lock()
_runs: dict[str, dict] = {}

# The work the application does by itself: key, name, what it does, how often, icon, colour,
# where its rule is changed, where its result is seen, and whether "Run now" is offered
# (not for what sends emails to candidates by itself: that keeps to its own clock).
WORK = [
    {"key": "mailbox", "name": "Reads the mailbox", "icon": "bi-envelope-open-fill", "tone": "rose", "every": "every minute",
     "what": "Reads the emails candidates sent, answers what it is sure about and leaves you the rest.",
     "change": "/settings#assistant", "see": "/inbox", "see_label": "Inbox", "run": True},
    {"key": "classes", "name": "Keeps the classes in order", "icon": "bi-easel2-fill", "tone": "gold", "every": "every minute",
     "what": "Starts and ends classes on their dates, places candidates who paid, sends the reminders before a first session.",
     "change": "/settings#classes", "see": "/classes/", "see_label": "Classes", "run": True},
    {"key": "payments", "name": "Reminds who has not paid", "icon": "bi-cash-coin", "tone": "mint", "every": "every hour",
     "what": "One reminder per candidate, 24 hours after registering. Never repeated by itself.",
     "change": "/inbox/templates", "see": "/payments", "see_label": "Payments", "run": False},
    {"key": "website", "name": "Follows your website", "icon": "bi-globe2", "tone": "sky", "every": "every six hours",
     "what": "Reads 9antra.tn again: programs, prices, events, videos and published feedback.",
     "change": "/website", "see": "/website", "see_label": "Website", "run": True},
    {"key": "writing", "name": "Writes with the assistant", "icon": "bi-pencil-fill", "tone": "violet", "every": "one piece at a time",
     "what": "Weekly questions from the course, proposals for programs, what a candidate wrote about their week.",
     "change": "/settings#assistant", "see": "/programs/all", "see_label": "Programs", "run": False},
    {"key": "weekly", "name": "Asks the candidates", "icon": "bi-patch-question-fill", "tone": "teal", "every": "every ten minutes",
     "what": "Sends the questions of the weeks that just ended, asks the classes that finished what they thought, "
             "reminds once those who have not answered their level check, and sends each candidate their own link "
             "before a session.",
     "change": "/inbox/templates", "see": "/classes/", "see_label": "Classes", "run": False},
    {"key": "answers", "name": "Collects the answers", "icon": "bi-ui-checks", "tone": "slate", "every": "every two minutes",
     "what": "Brings back the answers candidates gave on a Google Form.",
     "change": "/settings#google", "see": "/settings#google", "see_label": "Settings", "run": True},
    {"key": "market", "name": "Follows the job market", "icon": "bi-graph-up-arrow", "tone": "gold", "every": "once a day",
     "what": "Reads two public job boards and counts what employers ask, so each program shows what to add.",
     "change": "/programs/market", "see": "/programs/market", "see_label": "Market", "run": True},
    {"key": "backup", "name": "Protects your data", "icon": "bi-shield-lock-fill", "tone": "mint", "every": "once a day",
     "what": "Copies the whole database and the uploaded files, checks the copy, and keeps two weeks of them.",
     "change": "/system#safety", "see": "/system#safety", "see_label": "The copies", "run": True},
    {"key": "reminders", "name": "Sends your reminders", "icon": "bi-alarm-fill", "tone": "rose", "every": "every minute",
     "what": "The reminders you wrote yourself, each at the time you chose. It sends nothing you did not plan.",
     "change": "/reminders", "see": "/reminders", "see_label": "Reminders", "run": False},
    {"key": "translations", "name": "Translates the pages", "icon": "bi-translate", "tone": "sky", "every": "every minute, a few texts",
     "what": "Puts the pages into French and Arabic with the assistant, starting with what you open. Each translation is made once and kept.",
     "change": "/settings#language", "see": "/settings#language", "see_label": "Languages", "run": True},
]
_BY_KEY = {item["key"]: item for item in WORK}


# ------------------------------------------------------------------
# What the jobs report
# ------------------------------------------------------------------

def _entry(key: str) -> dict:
    return _runs.setdefault(key, {"runs": 0, "running": False, "started": None, "ended": None, "problem": None, "problem_at": None})


def watched(key: str):
    """Around a scheduled job: remember when it ran. A job that catches its
    own errors says so with `problem`."""
    def wrap(job):
        @functools.wraps(job)
        def run(*args, **kwargs):
            with _lock:
                entry = _entry(key)
                entry.update(running=True, started=datetime.now())
            try:
                return job(*args, **kwargs)
            except Exception as exc:
                problem(key, exc)
                raise
            finally:
                with _lock:
                    entry = _entry(key)
                    entry.update(running=False, ended=datetime.now(), runs=entry["runs"] + 1)
        return run
    return wrap


def problem(key: str, error) -> None:
    """A job could not finish: kept until its next clean run."""
    with _lock:
        _entry(key).update(problem=" ".join(str(error).split())[:180] or type(error).__name__, problem_at=datetime.now())


def _ago(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return ""
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 50:
        return "just now"
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min ago"
    if seconds < 86400:
        return f"{round(seconds / 3600)} h ago"
    return f"{round(seconds / 86400)} d ago"


def _in(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return ""
    seconds = int((moment - now).total_seconds())
    if seconds <= 5:
        return "now"
    if seconds < 90:
        return f"in {seconds} s"
    if seconds < 3600:
        return f"in {round(seconds / 60)} min"
    return f"in {round(seconds / 3600)} h"


# ------------------------------------------------------------------
# What the page shows
# ------------------------------------------------------------------

def _facts(db: Session, now: datetime) -> dict[str, str]:
    """One line of fresh figures for each piece of work."""
    day = datetime.utcnow() - timedelta(hours=24)
    received = db.query(CandidateEmail).filter(CandidateEmail.direction == "inbound", CandidateEmail.received_at >= day).count()
    answered = db.query(CandidateEmail).filter(
        CandidateEmail.direction == "outbound", CandidateEmail.author == "system", CandidateEmail.intent == "reply",
        CandidateEmail.received_at >= day).count()
    waiting = db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).count()
    classes = db.query(ClassGroup).all()
    running = sum(1 for item in classes if item.status == ClassStatus.ONGOING)
    not_started = sum(1 for item in classes if item.status == ClassStatus.NOT_STARTED)
    unpaid = db.query(Registration).filter(
        Registration.payment_status != PaymentStatus.PAID, Registration.status != RegistrationStatus.CANCELLED).count()
    reminded = db.query(Registration).filter(Registration.payment_reminder_sent.is_(True)).count()
    items = db.query(WebsiteItem).filter(WebsiteItem.gone_at.is_(None)).count()
    proposals = db.query(ProgramProposal).filter(ProgramProposal.status.in_(("queued", "writing"))).count()
    sent_weeks = db.query(Quiz).filter(Quiz.kind == "weekly", Quiz.sent_at >= day).count()
    answers = db.query(Quiz).filter(Quiz.completed_at >= day).count()
    plural = lambda count, word: f"{count} {word}{'s' if count != 1 else ''}"       # noqa: E731
    return {
        "mailbox": f"{plural(received, 'email')} received in 24 h · {answered} answered by itself · {waiting} waiting for you",
        "classes": f"{running} running · {not_started} not started",
        "payments": f"{plural(unpaid, 'registration')} not paid in full · {reminded} already reminded",
        "website": f"{plural(items, 'page')} of the website kept here",
        "writing": f"{plural(proposals, 'proposal')} waiting to be written" if proposals else "Nothing is waiting to be written",
        "weekly": f"{plural(sent_weeks, 'set')} of weekly questions sent in 24 h",
        "answers": f"{plural(answers, 'answer')} received in 24 h",
    }


def work(db: Session, next_runs: dict[str, datetime | None] | None = None) -> list[dict]:
    """Each piece of automatic work with its state: {"state": ok|working|problem|waiting, ...}."""
    now = datetime.now()
    facts = _facts(db, now)
    rows = []
    for item in WORK:
        with _lock:
            entry = dict(_entry(item["key"]))
        failing = entry["problem"] is not None and (entry["ended"] is None or entry["problem_at"] >= (entry["started"] or entry["problem_at"]))
        if entry["running"]:
            state, label = "working", "Working now"
        elif failing:
            state, label = "problem", "Could not finish"
        elif entry["runs"]:
            state, label = "ok", "Working"
        else:
            state, label = "waiting", "Starts in a moment"
        coming = (next_runs or {}).get(item["key"])
        rows.append({
            **item,
            "state": state,
            "label": label,
            "last": _ago(entry["ended"], now) if entry["ended"] else "",
            "next": _in(coming.replace(tzinfo=None) if coming is not None else None, now),
            "runs": entry["runs"],
            "problem": entry["problem"] if failing else None,
            "fact": facts.get(item["key"], ""),
        })
    return rows


def lan_address() -> str | None:
    """This computer's address on the local network (what a phone on the same Wi-Fi opens)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))        # nothing is sent: it only picks the network card
        address = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    return None if address.startswith("127.") else address


def open_to_the_network(address: str | None, port: int) -> bool:
    """Whether the application answers on the local network, not only on this computer."""
    if not address:
        return False
    try:
        with socket.create_connection((address, port), timeout=0.4):
            return True
    except OSError:
        return False


def _masked(email: str) -> str:
    name, _, domain = (email or "").partition("@")
    return f"{name[:2]}…@{domain}" if domain else ""


def connections(db: Session, public_url: str, public_local: bool) -> list[dict]:
    """The services the application relies on: {"state": on|off|partial, ...}."""
    from app.services import answer_sync, email_assistant, email_threads, feedback, mailbox_service, website_sync, whatsapp

    def row(key, name, icon, state, text, change, change_label="Change"):
        return {"key": key, "name": name, "icon": icon, "state": state, "text": text, "change": change, "change_label": change_label}

    smtp = email_threads.smtp_configured()
    reads = mailbox_service.configured()
    model = email_assistant.model_available()
    check = website_sync.last_check(db)
    road = answer_sync.status()
    forms = (road.get("google") or {}).get("forms") if isinstance(road.get("google"), dict) else None
    facebook = len((os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN") or "").strip()) > 40
    rows = [
        row("smtp", "Sending emails", "bi-send-fill", "on" if smtp else "off",
            f"Emails leave from {_masked(os.getenv('SMTP_EMAIL') or '')}." if smtp else "Not set up: no email can leave.", "/settings#email"),
        row("imap", "Reading the mailbox", "bi-envelope-open-fill", "on" if reads else "off",
            "Candidates' emails are read every minute." if reads else "Not set up: candidates' emails are not read.", "/settings#email"),
        row("model", "The assistant", "bi-robot", "on" if model else "off",
            "It answers: emails, questions and proposals can be written." if model
            else "It does not answer right now: emails wait for you, nothing is written by itself.", "/settings#assistant"),
        row("website", "Your website 9antra.tn", "bi-globe2",
            "on" if check is not None and check.ok else "off",
            ("Last read " + _ago(check.checked_at.replace(tzinfo=None), datetime.utcnow()) + ".") if check is not None and check.ok
            else (check.note or "It could not be read last time.") if check is not None else "Not read yet.", "/website", "Open"),
        row("address", "The address candidates open", "bi-link-45deg", "partial" if public_local else "on",
            f"{public_url}: this only opens on this computer, so the links in emails do not work for candidates." if public_local
            else f"Links in emails start with {public_url}.", "/settings#address"),
        row("whatsapp", "WhatsApp", "bi-whatsapp", "on" if whatsapp.configured() else "partial",
            "Reminders are also sent on WhatsApp." if whatsapp.configured()
            else "No WhatsApp Business number: you get a button to send each reminder yourself.", "/settings#whatsapp"),
        row("forms", "Google Forms", "bi-ui-checks", "on" if forms == "on" else "partial",
            "Weekly questions can be answered on a Google Form." if forms == "on"
            else "Switched off at Google: weekly questions use this application's own page.", "/settings#google"),
        row("facebook", "Your Facebook page", "bi-facebook", "on" if facebook else "partial",
            "Connected." if facebook else "Only the page's picture is used: posts and reviews need the page's access key.", "/settings#identity"),
        row("reader", "Reading pictures", "bi-image", "on" if feedback.reader_available() else "partial",
            "Feedback published as pictures is read on this computer." if feedback.reader_available()
            else "This computer cannot read the text of pictures: published feedback is kept unread.", "/website", "Open"),
    ]
    return rows


def summary(work_rows: list[dict], connection_rows: list[dict]) -> dict:
    """The one line at the top of the page."""
    stopped = [row["name"] for row in work_rows if row["state"] == "problem"]
    off = [row["name"] for row in connection_rows if row["state"] == "off"]
    limited = [row["name"] for row in connection_rows if row["state"] == "partial"]
    if stopped or off:
        return {"tone": "bad", "icon": "bi-exclamation-octagon-fill",
                "title": f"{len(stopped) + len(off)} thing{'s' if len(stopped) + len(off) != 1 else ''} need{'' if len(stopped) + len(off) != 1 else 's'} you",
                "text": "Not working: " + ", ".join(stopped + off) + "."}
    if limited:
        return {"tone": "ok", "icon": "bi-check-circle-fill", "title": "Everything is running",
                "text": f"{len(limited)} connection{'s are' if len(limited) != 1 else ' is'} not complete; the application works without {'them' if len(limited) != 1 else 'it'}."}
    return {"tone": "ok", "icon": "bi-check-circle-fill", "title": "Everything is running", "text": "All the work and all the connections are in order."}

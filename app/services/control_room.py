"""The control room (System › Control room): the application as a live map of its parts and of what flows between them,
every functionality tracked (its state, when it last acted, how often), what to enhance, and what is needed from the admin.

Everything is counted from the records (messages sent by kind, payments, answers, the agents' runs, the jobs, the
connections); nothing is guessed. The ideas to enhance are written by hand, each with why and what it needs.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.services.ui_words import t as _t

WEEK = timedelta(days=7)
# The parts of the map: where they sit (percent of the stage), their moving picture, the live topics that wake them.
NODES = [
    ("register", "🧑‍🎓", "float", "Candidates", "/candidate/all", (11, 15), ("candidates",)),
    ("reminders", "⏰", "ring", "Reminders", "/reminders", (37, 15), ("reminders",)),
    ("inbox", "📬", "bounce", "Inbox", "/inbox", (63, 15), ("inbox",)),
    ("assistant", "🤖", "float", "Email assistant", "/inbox?filter=auto", (89, 15), ("inbox", "knowledge")),
    ("pay", "💳", "pulse", "Payments", "/payments", (11, 50), ("payments", "candidates")),
    ("classes", "🏫", "float", "Classes", "/classes/", (37, 50), ("classes",)),
    ("knowledge", "🌐", "spin", "What the assistants know", "/inbox/knowledge", (63, 50), ("website", "knowledge", "market", "programs")),
    ("yasmine", "🧕", "wave", "Yasmine and her agents", "/assistant/agents", (89, 50), ("activity",)),
    ("notify", "🔔", "ring", "Notifications", "/system#recent", (11, 85), ("dashboard",)),
    ("profs", "🎓", "tilt", "Professors", "/professors", (37, 85), ("team",)),
    ("learn", "🧭", "spin", "Level and weekly checks", "/quiz/all", (63, 85), ("levels",)),
    ("social", "⭐", "twinkle", "Reviews and social media", "/social", (89, 85), ("reviews",)),
]
# What flows between them: from, to, what it is (counted over the last 7 days).
EDGES = [
    ("register", "pay", "payment messages"),
    ("pay", "classes", "placed in a class"),
    ("classes", "reminders", "class reminders"),
    ("reminders", "register", "reminders sent"),
    ("classes", "learn", "questions sent"),
    ("learn", "knowledge", "answers back"),
    ("classes", "profs", "professors told"),
    ("inbox", "assistant", "messages read"),
    ("assistant", "inbox", "answered alone"),
    ("knowledge", "assistant", "emails read by the agents"),
    ("knowledge", "yasmine", "questions to Yasmine"),
    ("yasmine", "inbox", "sent by Yasmine"),
    ("classes", "social", "reviews asked"),
    ("social", "knowledge", "reviews in"),
    ("pay", "notify", "notifications"),
]
_PAYMENT_KINDS = ("payment_reminder", "payment_partial", "payment_received", "month_due", "month_paused", "month_received")
_CLASS_REMINDERS = ("training_reminder", "session_reminder", "class_rescheduled", "session_link")
_QUESTIONS = ("quiz_invitation", "weekly_questions", "session_check", "weekly_letter")


def _safe(work, default=0):
    try:
        return work()
    except Exception:
        return default


def _ago(moment: datetime | None) -> str:
    if moment is None:
        return _t("never")
    moment = moment.replace(tzinfo=None)
    seconds = max(0, (datetime.utcnow() - moment).total_seconds())
    if seconds < 3600:
        return _t("{0} min ago", int(seconds // 60))
    if seconds < 86400:
        return _t("{0} h ago", int(seconds // 3600))
    return _t("yesterday") if seconds < 2 * 86400 else _t("{0} days ago", int(seconds // 86400))


def _sent(db: Session, kinds, since: datetime) -> int:
    from app.models.candidate_email import CandidateEmail

    return db.query(func.count(CandidateEmail.id)).filter(CandidateEmail.direction == "outbound", CandidateEmail.intent.in_(kinds),
                                                         CandidateEmail.received_at >= since).scalar() or 0


def _last_sent(db: Session, kinds) -> datetime | None:
    from app.models.candidate_email import CandidateEmail

    return db.query(func.max(CandidateEmail.received_at)).filter(CandidateEmail.direction == "outbound", CandidateEmail.intent.in_(kinds)).scalar()


def flows(db: Session) -> dict[str, int]:
    """How much went along each line in the last 7 days."""
    from app.models import Activity, Notification, Registration
    from app.models.agent_run import AgentRun
    from app.models.candidate_email import CandidateEmail
    from app.models.feedback import CandidateFeedback
    from app.models.quiz import Quiz
    from app.models.team_message import TeamMessage

    since = datetime.utcnow() - WEEK
    inbound = lambda: db.query(func.count(CandidateEmail.id)).filter(CandidateEmail.direction == "inbound", CandidateEmail.received_at >= since).scalar() or 0  # noqa: E731
    return {
        "register>pay": _safe(lambda: _sent(db, _PAYMENT_KINDS, since)),
        "pay>classes": _safe(lambda: (db.query(func.count(Activity.id)).filter(Activity.event_type == "CLASS_ASSIGNED", Activity.created_at >= since).scalar() or 0)
                             + _sent(db, ("enrollment",), since)),
        "classes>reminders": _safe(lambda: _sent(db, _CLASS_REMINDERS, since)),
        "reminders>register": _safe(lambda: _sent(db, _CLASS_REMINDERS + ("welcome", "personal", "message"), since)),
        "classes>learn": _safe(lambda: db.query(func.count(Quiz.id)).filter(Quiz.sent_at >= since).scalar() or 0),
        "learn>knowledge": _safe(lambda: db.query(func.count(Quiz.id)).filter(Quiz.completed_at >= since).scalar() or 0),
        "classes>profs": _safe(lambda: db.query(func.count(TeamMessage.id)).filter(TeamMessage.direction == "out", TeamMessage.created_at >= since).scalar() or 0),
        "inbox>assistant": _safe(inbound),
        "assistant>inbox": _safe(lambda: db.query(func.count(CandidateEmail.id)).filter(CandidateEmail.direction == "outbound", CandidateEmail.author == "system",
                                                                                     CandidateEmail.intent == "reply", CandidateEmail.received_at >= since).scalar() or 0),
        "knowledge>assistant": _safe(lambda: db.query(func.count(AgentRun.id)).filter(AgentRun.channel.in_(("email", "whatsapp", "sms")), AgentRun.at >= since).scalar() or 0),
        "knowledge>yasmine": _safe(lambda: db.query(func.count(AgentRun.id)).filter(AgentRun.channel.in_(("chat", "professor")), AgentRun.at >= since).scalar() or 0),
        "yasmine>inbox": _safe(lambda: _sent(db, ("message",), since)),
        "classes>social": _safe(lambda: _sent(db, ("feedback_request", "success_review"), since)),
        "social>knowledge": _safe(lambda: db.query(func.count(CandidateFeedback.id)).filter(CandidateFeedback.created_at >= since).scalar() or 0),
        "pay>notify": _safe(lambda: db.query(func.count(Notification.id)).filter(Notification.created_at >= since).scalar() or 0),
        "inbox>register": _safe(lambda: db.query(func.count(func.distinct(CandidateEmail.candidate_id))).filter(
            CandidateEmail.direction == "inbound", CandidateEmail.received_at >= since).scalar() or 0),
        "_registrations": _safe(lambda: db.query(func.count(Registration.id)).filter(Registration.registration_date >= since).scalar() or 0),
    }


def _figures(db: Session, flow: dict[str, int]) -> dict[str, dict]:
    """What each part shows: two figures, its state (ok | waits | off) and one line."""
    from app.models import Candidate, ClassGroup, ClassStatus, Notification, Professor, Registration
    from app.models.admin_reminder import AdminReminder
    from app.models.email_thread import EmailThread
    from app.models.feedback import CandidateFeedback
    from app.models.knowledge import KnowledgeEntry
    from app.models.payment import Payment
    from app.models.professor_absence import ProfessorAbsence
    from app.models.registration import RegistrationStatus
    from app.services import email_assistant, email_threads, mailbox_service, notification_rules, website_sync
    from app.services.settings_service import get_settings

    since = datetime.utcnow() - WEEK
    soon = datetime.utcnow() + timedelta(days=14)
    owed = _safe(lambda: sum(1 for item in db.query(Registration).filter(Registration.status != RegistrationStatus.CANCELLED).all()
                             if (item.remaining_amount or 0) > 0))
    received = _safe(lambda: db.query(func.sum(Payment.amount)).filter(Payment.payment_date >= since).scalar() or 0)
    running = _safe(lambda: db.query(func.count(ClassGroup.id)).filter(ClassGroup.status == ClassStatus.ONGOING).scalar() or 0)
    starting = _safe(lambda: db.query(func.count(ClassGroup.id)).filter(ClassGroup.status == ClassStatus.NOT_STARTED, ClassGroup.start_date <= soon).scalar() or 0)
    asked_off = _safe(lambda: db.query(func.count(ProfessorAbsence.id)).filter(ProfessorAbsence.status == "requested").scalar() or 0)
    waiting = _safe(lambda: db.query(func.count(EmailThread.id)).filter(EmailThread.needs_admin.is_(True)).scalar() or 0)
    planned = _safe(lambda: db.query(func.count(AdminReminder.id)).filter(AdminReminder.status.in_(("scheduled", "watching"))).scalar() or 0)
    mode = _safe(lambda: email_assistant.mode_of(get_settings(db)), "off")
    from app.models.candidate_email import CandidateEmail

    brought = _safe(lambda: db.query(func.count(CandidateEmail.id)).filter(CandidateEmail.direction == "inbound", CandidateEmail.requires_admin.is_(True),
                                                                          CandidateEmail.received_at >= since).scalar() or 0)
    check = _safe(lambda: website_sync.last_check(db), None)
    reviews = _safe(lambda: db.query(func.avg(CandidateFeedback.rating), func.count(CandidateFeedback.id)).filter(CandidateFeedback.rating.isnot(None)).one(), (None, 0))
    ringing = _safe(lambda: notification_rules.ringing(db), 0)
    sends = _safe(lambda: email_threads.smtp_configured(), False)
    reads = _safe(lambda: mailbox_service.configured(), False)
    return {
        "register": {"a": (_t("new this week"), flow.get("_registrations", 0)), "b": (_t("candidates"), _safe(lambda: db.query(func.count(Candidate.id)).scalar() or 0)),
                     "state": "ok"},
        "pay": {"a": (_t("received this week"), f"{received:g} DT"), "b": (_t("still owe"), owed), "state": "waits" if owed else "ok"},
        "classes": {"a": (_t("running"), running), "b": (_t("start within 14 days"), starting), "state": "ok"},
        "profs": {"a": (_t("professors"), _safe(lambda: db.query(func.count(Professor.id)).scalar() or 0)), "b": (_t("absences to decide"), asked_off),
                  "state": "waits" if asked_off else "ok"},
        "learn": {"a": (_t("sent this week"), flow.get("classes>learn", 0)), "b": (_t("answered this week"), flow.get("learn>knowledge", 0)), "state": "ok"},
        "reminders": {"a": (_t("sent this week"), flow.get("classes>reminders", 0)), "b": (_t("planned"), planned), "state": "ok" if sends else "off"},
        "inbox": {"a": (_t("received this week"), flow.get("inbox>assistant", 0)), "b": (_t("waiting for you"), waiting),
                  "state": "off" if not reads else "waits" if waiting else "ok"},
        "assistant": {"a": (_t("answered alone"), flow.get("assistant>inbox", 0)), "b": (_t("brought to you"), brought), "state": "off" if mode == "off" else "ok"},
        "yasmine": {"a": (_t("questions this week"), flow.get("knowledge>yasmine", 0)), "b": (_t("messages she sent"), flow.get("yasmine>inbox", 0)), "state": "ok"},
        "knowledge": {"a": (_t("things known"), _safe(lambda: db.query(func.count(KnowledgeEntry.id)).scalar() or 0)),
                      "b": (_t("website read"), _ago(check.checked_at) if check is not None and getattr(check, "checked_at", None) else _t("never")),
                      "state": "ok" if check is not None and getattr(check, "ok", False) else "waits"},
        "social": {"a": (_t("reviews"), reviews[1] or 0), "b": (_t("average"), f"{reviews[0]:.1f}★" if reviews[0] else "–"), "state": "ok"},
        "notify": {"a": (_t("ringing now"), ringing), "b": (_t("this week"), flow.get("pay>notify", 0)), "state": "waits" if ringing else "ok"},
    }


def snapshot(db: Session) -> dict:
    """The live map: {"nodes": [...], "edges": [...], "at"}; what the page draws and refreshes."""
    flow = flows(db)
    figures = _figures(db, flow)
    nodes = []
    for key, emoji, motion, title, url, (x, y), topics in NODES:
        shown = figures.get(key, {})
        nodes.append({"key": key, "emoji": emoji, "motion": motion, "title": _t(title), "url": url, "x": x, "y": y, "topics": list(topics),
                      "state": shown.get("state", "ok"),
                      "figures": [{"label": label, "value": value} for label, value in (shown.get("a"), shown.get("b")) if label]})
    edges = [{"key": f"{source}>{target}", "from": source, "to": target, "label": _t(label), "count": flow.get(f"{source}>{target}", 0)}
             for source, target, label in EDGES]
    return {"nodes": nodes, "edges": edges, "at": datetime.utcnow().isoformat(timespec="seconds"),
            "total": sum(edge["count"] for edge in edges)}


# ------------------------------------------------------------------
# Every functionality, tracked
# ------------------------------------------------------------------

# What each automatic work sends (its kinds of message), to know when it last acted and how often.
_KINDS_OF = {"welcome": ("welcome",), "payment": ("payment_reminder",), "level": ("quiz_invitation", "level_reminder"),
             "countdown": ("training_reminder",), "session_reminder": ("session_reminder",), "links": ("session_link",),
             "weekly": ("weekly_questions", "weekly_letter"), "session": ("session_check",), "reviews": ("feedback_request",),
             "answers": ("reply",)}


def tracking(db: Session) -> list[dict]:
    """Every automatic functionality: state, when it last acted, how many times in 7 days, where to change it."""
    from app.services import automations

    since = datetime.utcnow() - WEEK
    rows = []
    for item in automations.listing(db):
        kinds = _KINDS_OF.get(item["key"])
        rows.append({**item, "title": _t(item["title"]), "what": _t(item["what"]), "when": _t(item["when"]), "label": _t(item["label"]),
                     "last": _ago(_safe(lambda: _last_sent(db, kinds), None)) if kinds else None,
                     "week": _safe(lambda: _sent(db, kinds, since)) if kinds else None})
    return rows


# ------------------------------------------------------------------
# What to enhance, and what is needed from the admin
# ------------------------------------------------------------------

# Ideas that come from how the application runs here (what the market says is ranked by services/enhancements.py).
IDEAS = [
    ("public", "🌍", "A public address for candidates",
     "Today the links in emails (level check, weekly questions, session links, reviews) only open on this computer, so candidates answer by email instead.",
     "Your yes to open the application to the internet (Tailscale Funnel or a Cloudflare tunnel), and a name like app.9antra.tn."),
    ("teach_missed", "🙋", "Questions Yasmine could not answer, kept for you",
     "A list of what she did not know, with one box to teach each answer: she knows it the next time.",
     "Nothing: it can be built now."),
    ("level_editor", "✏️", "Edit the level-check questions",
     "Today the questions of the level check are written in the application's code; an editor would let you change them per program.",
     "Nothing: it can be built now."),
    ("voice", "🎙️", "Talk to Yasmine",
     "Ask her by voice instead of typing.",
     "Your yes: the browser's speech recognition sends the sound to Google or Microsoft."),
    ("postgres", "🗄️", "A bigger database",
     "Move from the one file (SQLite) to PostgreSQL, already installed on this computer, for more people at once.",
     "The PostgreSQL database name, user and password, and a test of the move on a copy."),
    ("videos", "🎬", "Evaluate the program videos",
     "The 136 program videos are links to Google Drive; they could be checked (length, sound, what is said).",
     "What “evaluate” should cover for you, then a speech-to-text tool on this computer."),
]


def needs(db: Session, base_url: str, local: bool) -> list[dict]:
    """What only the admin can give: each with what it unlocks, its state and where to give it."""
    from app.services import system_status

    rows = [row for row in system_status.connections(db, base_url, local) if row["state"] != "on"]
    found = [{"icon": row["icon"], "title": _t(row["name"]), "text": _t(row["text"]), "url": row["change"], "state": row["state"]} for row in rows]
    from app.services.settings_service import get_settings

    if not (getattr(get_settings(db), "centre_ribs", None) or "").strip():
        found.append({"icon": "bi-bank", "title": _t("The centre's RIB"),
                      "text": _t("Empty: questions about how to pay by transfer go to you, and receipts cannot be checked against it."),
                      "url": "/settings#centre", "state": "partial"})
    from app.services import cloud_model

    if not _safe(cloud_model.available, False):
        found.append({"icon": "bi-stars", "title": _t("The online writer (Gemini keys)"),
                      "text": _t("Not available: the rewording and Yasmine use the model of this computer, which writes French less well."),
                      "url": "/settings#assistant", "state": "partial"})
    return found


def suggestions(db: Session, tests: dict | None = None, needed: list[dict] | None = None) -> list[dict]:
    """What to look at now, counted: [{"icon", "tone", "text", "url"}], the most useful first. What waits for something only
    the admin can give (a connection, a key) is said once, in "What I need from you" (`needed`), not here again."""
    from app.services import completeness, reach

    given = {(item.get("url") or "").split("#")[-1] for item in (needed or [])}
    found = []
    for row in tracking(db):
        if (row["change"] or "").split("#")[-1] in given:
            continue
        if row["state"] == "off":
            found.append({"icon": "bi-toggle-off", "tone": "warn", "text": _t("“{0}” is off: {1}", row["title"], row["what"]), "url": row["change"]})
        elif row["state"] == "setup":
            found.append({"icon": "bi-plug", "tone": "warn", "text": _t("“{0}” is not set up yet.", row["title"]), "url": row["change"]})
        elif row.get("week") == 0 and row["key"] in ("weekly", "session", "level"):
            found.append({"icon": "bi-hourglass", "tone": "info", "text": _t("“{0}” did nothing this week: is it expected?", row["title"]), "url": row["change"]})
    failed = _safe(lambda: len(reach.not_reached(db)), 0)
    if failed:
        found.append({"icon": "bi-envelope-x", "tone": "bad", "text": _t("{0} messages did not reach their person.", failed), "url": "/inbox/not-reached"})
    for group in _safe(lambda: completeness.grouped(db), []):
        for item in group["items"][:2]:
            if (item.get("url") or "").split("#")[-1] in given:
                continue
            found.append({"icon": item.get("icon") or "bi-pencil-square", "tone": "info", "text": item.get("text") or "", "url": item.get("url") or "/system#missing"})
    if tests and tests.get("problems"):
        found.insert(0, {"icon": "bi-bug", "tone": "bad", "text": _t("{0} tests found a problem: see “Test everything”.", tests["problems"]), "url": "#tab-problems"})
    return found[:14]

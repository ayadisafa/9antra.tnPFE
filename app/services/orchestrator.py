"""The orchestrator: every request (a question in the assistant's window, an email, a WhatsApp message, an SMS) is read
once for what it is about (message_categories), handed to the agent of that category, and written down (AgentRun).

    the request ──> the orchestrator reads its category ──> one agent answers ──> the knowledge agent if none could
                                                                               └─> a person when it must (complaints)

The agents share the application's tools (chatbot.py for the figures and the actions, howto.py for the guide,
knowledge_service for what was taught); each one only reads what its subject needs, and a professor's agent never sees
payments or other classes. The local model never chooses the agent and never decides: the category is read with word
lists, the multilingual model only breaks a tie, and every answer says where it comes from.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.agent_run import AgentRun
from app.services import message_categories

AGENTS = {
    "programs": {"name": "Programs agent", "icon": "bi-journal-bookmark", "colour": "#7c3aed",
                 "does": "What each program teaches, week by week, its files and videos, the level and the certificate.",
                 "reads": ["The programs and their weeks", "The program files", "What you taught the assistants"]},
    "planning": {"name": "Planning agent", "icon": "bi-calendar-week", "colour": "#0ea5e9",
                 "does": "Days, hours, start dates, sessions, absences and where the classes take place.",
                 "reads": ["The classes and their sessions", "The absences", "The centre's address"]},
    "payment": {"name": "Payment agent", "icon": "bi-cash-coin", "colour": "#16a34a",
                "does": "Prices, what is still owed, payment proofs and receipts. A refund always comes to you.",
                "reads": ["The payments", "The payment proofs", "The prices"]},
    "registration": {"name": "Registration agent", "icon": "bi-person-plus", "colour": "#db2777",
                     "does": "How to register, the form, changing or cancelling. A cancellation always comes to you.",
                     "reads": ["The registration form", "The registrations", "One program at a time"]},
    "technical": {"name": "Technical agent", "icon": "bi-tools", "colour": "#f59e0b",
                  "does": "A link that does not open, a password, the online class, the videos: the steps, or a person.",
                  "reads": ["The guide", "The class links", "What you taught the assistants"]},
    "complaint": {"name": "Complaints agent", "icon": "bi-emoji-frown", "colour": "#e11d48",
                  "does": "Never answers alone: it brings the message to you, with what it read in it.",
                  "reads": ["The message and the feelings in it"]},
    "trends": {"name": "Trends agent", "icon": "bi-graph-up-arrow", "colour": "#0d9488",
               "does": "What is new in every field, what the job offers ask, services to add, and what a name says.",
               "reads": ["The journal", "The job offers", "The programs"]},
    "records": {"name": "Figures agent", "icon": "bi-clipboard-data", "colour": "#475569",
                "does": "Your figures: who paid, who is absent, lists, money, what to do today.",
                "reads": ["The candidates", "The payments", "The classes"]},
    "guide": {"name": "Guide agent", "icon": "bi-signpost-split", "colour": "#6366f1",
              "does": "How to do anything in the application, step by step, for what each person may do.",
              "reads": ["The application's guide", "The page tours"]},
    "professor": {"name": "Professors' agent", "icon": "bi-mortarboard", "colour": "#9333ea",
                  "does": "For professors: their classes, their candidates, the course content and the guide. Never payments or other classes.",
                  "reads": ["Their classes", "Their candidates' attendance", "The course content"]},
    "knowledge": {"name": "Knowledge agent", "icon": "bi-book-half", "colour": "#0891b2",
                  "does": "When no other agent knows: the closest passages of everything the application knows, quoted with their numbers.",
                  "reads": ["What the assistant knows", "The program files", "9antra.tn", "The journal"]},
    "general": {"name": "General agent", "icon": "bi-chat-dots", "colour": "#6b7280",
                "does": "Thanks, greetings and everything else.",
                "reads": ["What you taught the assistants"]},
}
# The way requests come in, as the agents page shows them.
CHANNELS = {"chat": ("The assistant's window", "bi-chat-square-heart"), "professor": ("A professor's assistant", "bi-mortarboard"),
            "email": ("Email", "bi-envelope"), "whatsapp": ("WhatsApp", "bi-whatsapp"), "sms": ("SMS", "bi-phone")}
OUTCOMES = {"answered": "Answered", "sent": "Answered alone", "draft": "A draft for you", "admin": "Brought to you", "none": "No answer"}
KEEP_DAYS = 120


def agent_of(key: str | None) -> dict:
    found = AGENTS.get(key or "general") or AGENTS["general"]
    return {"key": key if key in AGENTS else "general", **found}


def _role(user) -> str:
    if user is None:
        return "admin"
    if getattr(user, "is_professor", False):
        return "professor"
    return "admin" if getattr(user, "is_admin", False) else "staff"


def route(text: str, user=None, *, channel: str = "chat", intent: str | None = None) -> dict:
    """{"category": {...}, "agent": {...}}: what the request is about and who handles it."""
    role = _role(user)
    for_admin = role in ("admin", "staff") and channel == "chat"
    category = message_categories.categorize(text, intent=intent, for_admin=for_admin, use_meaning=False)
    key = category["key"]
    if role == "professor":
        key = "professor"
    elif category["by"] == "none" and channel == "chat":
        # No word of a subject: a how-to of the guide first (by its words), the meaning of the sentence after.
        from app.services import howto

        if howto.task_for(text, user, by_meaning=False) is not None:
            key = "guide"
        else:
            category = message_categories.categorize(text, intent=intent, for_admin=for_admin)
            key = category["key"]
    elif category["by"] == "none":
        category = message_categories.categorize(text, intent=intent, for_admin=for_admin)
        key = category["key"]
    return {"category": category, "agent": agent_of(key)}


def note(db: Session, *, agent: str, category: dict | None, channel: str, outcome: str, asked: str = "",
         ref: str | None = None, user_id: str | None = None, took_ms: int | None = None) -> None:
    """Writes down one request handled (never stops the request when it cannot)."""
    try:
        db.add(AgentRun(agent=agent, category=(category or {}).get("key"), channel=channel, outcome=outcome,
                        read_by=(category or {}).get("by"), why=json.dumps((category or {}).get("why") or [], ensure_ascii=False),
                        asked=(asked or "")[:240], ref=ref, user_id=user_id, took_ms=took_ms))
        db.flush()
    except Exception:
        db.rollback()


def _outcome_of(found: dict) -> str:
    return "none" if found.get("source") == "none" else "answered"


def dispatch_chat(db: Session, message: str, history: list[dict] | None = None, *, user=None, lang: str | None = None) -> dict:
    """The answer to a question asked in the assistant's window (by the admin, a team member or a professor), with the
    agent that gave it."""
    from app.services import chatbot

    started = time.monotonic()
    routed = route(message, user)
    agent = routed["agent"]["key"]
    found = None
    if agent == "professor":
        from app.services import professor_assistant

        found = professor_assistant.answer(db, message, history, user=user, lang=lang)
    elif _role(user) in ("admin", "staff"):
        from app.services import trends_agent

        if trends_agent.wants(message):           # what is new, services to add, what a name says
            agent = "trends"
            found = trends_agent.answer(db, message, lang=lang)
    if found is None:
        found = chatbot.answer(db, message, history, user=user, lang=lang)
        source = found.get("source")
        if source == "guide":
            agent = "guide"            # the guide's steps answered, whatever the subject read
        elif source in ("taught", "knowledge", "website") and agent == "general":
            agent = "knowledge"
    if found.get("source") == "none" and agent != "professor":
        from app.services import rag

        cited = rag.answer(db, message, lang=lang, for_admin=_role(user) in ("admin", "staff"))
        if cited is not None:
            found, agent = cited, "knowledge"
    found["agent"] = agent_of(agent)
    if agent == "trends" and routed["category"]["key"] == "general":
        routed["category"] = {**message_categories.label_of("trends"), "why": [], "by": "agent"}
    found["category"] = {key: routed["category"][key] for key in ("key", "label", "icon", "colour", "why", "by")}
    note(db, agent=agent, category=routed["category"], channel="professor" if agent == "professor" else "chat",
         outcome=_outcome_of(found), asked=message, user_id=getattr(user, "id", None), took_ms=int((time.monotonic() - started) * 1000))
    try:
        db.commit()
    except Exception:
        db.rollback()
    return found


def email_outcome(status: str | None) -> str:
    return {"replied": "sent", "needs_admin": "admin", "no_reply": "none"}.get(status or "", "answered")


# ------------------------------------------------------------------
# The agents page
# ------------------------------------------------------------------

def overview(db: Session, days: int = 7) -> dict:
    """What each agent did lately, the latest requests, and the way they came."""
    since = datetime.utcnow() - timedelta(days=days)
    runs = db.query(AgentRun).filter(AgentRun.at >= since).all()
    by_agent: dict[str, dict] = {key: {"total": 0, "outcomes": {}} for key in AGENTS}
    by_channel: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for run in runs:
        row = by_agent.setdefault(run.agent, {"total": 0, "outcomes": {}})
        row["total"] += 1
        row["outcomes"][run.outcome or "answered"] = row["outcomes"].get(run.outcome or "answered", 0) + 1
        by_channel[run.channel or "chat"] = by_channel.get(run.channel or "chat", 0) + 1
        if run.category:
            by_category[run.category] = by_category.get(run.category, 0) + 1
    busiest = max((row["total"] for row in by_agent.values()), default=0) or 1
    agents = []
    for key, agent in AGENTS.items():
        row = by_agent.get(key, {"total": 0, "outcomes": {}})
        agents.append({"key": key, **agent, "total": row["total"], "pct": round(row["total"] / busiest * 100),
                       "outcomes": [{"key": name, "label": OUTCOMES.get(name, name), "count": count}
                                    for name, count in sorted(row["outcomes"].items(), key=lambda pair: -pair[1])]})
    latest = db.query(AgentRun).order_by(AgentRun.at.desc()).limit(30).all()
    return {
        "agents": agents, "total": len(runs), "days": days,
        "channels": [{"key": key, "label": CHANNELS[key][0], "icon": CHANNELS[key][1], "count": by_channel.get(key, 0)} for key in CHANNELS],
        "categories": [{**message_categories.label_of(key), "count": count}
                       for key, count in sorted(by_category.items(), key=lambda pair: -pair[1])],
        "latest": [{"at": run.at, "agent": agent_of(run.agent), "category": message_categories.label_of(run.category),
                    "channel": CHANNELS.get(run.channel or "chat", CHANNELS["chat"]), "outcome": OUTCOMES.get(run.outcome or "answered", run.outcome),
                    "outcome_key": run.outcome or "answered", "asked": run.asked or "", "ref": run.ref,
                    "why": _why(run.why), "took_ms": run.took_ms} for run in latest],
    }


def _why(value: str | None) -> list[str]:
    try:
        found = json.loads(value or "[]")
    except ValueError:
        return []
    return [str(item) for item in found][:4] if isinstance(found, list) else []


def forget_old(db: Session, now: datetime | None = None) -> int:
    """The runs older than KEEP_DAYS go (the figures of the page are about the last weeks)."""
    limit = (now or datetime.utcnow()) - timedelta(days=KEEP_DAYS)
    gone = db.query(AgentRun).filter(AgentRun.at < limit).delete(synchronize_session=False)
    db.commit()
    return gone

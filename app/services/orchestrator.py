"""The orchestrator: every request (a question in the assistant's window, an email, a WhatsApp message, an SMS) is read
once for what it is about (message_categories), handed to the agent of that category, and written down (AgentRun).

    the request ──> the orchestrator reads its category ──> one agent answers ──> the knowledge agent if none could
                                                                               └─> a person when it must (complaints)

The agents share the application's tools (chatbot.py for the figures and the actions, howto.py for the guide,
knowledge_service for what was taught); each one only reads what its subject needs, and a professor's agent never sees
payments or other classes. The local model never chooses the agent and never decides: the category is read with word
lists, the multilingual model only breaks a tie, and every answer says where it comes from.

A question in the assistant's window travels through a LangGraph graph (graph()): each agent is a step, and the way from
one step to the next is chosen by the rules above, never by a model. The agents page draws the same graph (drawing()).
"""
from __future__ import annotations

import json
import operator
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, TypedDict

from sqlalchemy.orm import Session

from app.models.agent_run import AgentRun
from app.services import access, message_categories
from app.services.ui_words import t as _t

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
    "system": {"name": "System agent", "icon": "bi-sliders2", "colour": "#0f766e",
               "does": "Changes the system when you ask (assistant, reminders, classes, prices, look…), says what it was, and undoes it.",
               "reads": ["The settings", "The automatic reminders", "What it changed"]},
    "summary": {"name": "Summary agent", "icon": "bi-text-paragraph", "colour": "#9333ea",
                "does": "Sums up a candidate, their discussion, a class, a program, the day, the week, the page, a text you paste, or her last answer.",
                "reads": ["The candidates, classes and programs", "The messages", "What came in and went out", "The page's guide"]},
    "keep": {"name": "Follow-up agent", "icon": "bi-person-heart", "colour": "#be123c",
             "does": "Who may drop out and why, and what to do for each: the message already prepared, the way and the hour, or a call.",
             "reads": ["The absences", "The weekly questions and their results", "What is still owed", "What they wrote"]},
    "marketing": {"name": "Marketing agent", "icon": "bi-megaphone", "colour": "#ea580c",
                  "does": "What is trendy in posts, when to post, this week's plan, and advice.",
                  "reads": ["The journal's marketing articles", "When your people are active"]},
    "inbox": {"name": "Inbox rules agent", "icon": "bi-sliders", "colour": "#7c3aed",
              "does": "Why a message was set aside, or answered as it was; bringing it back, correcting and teaching when you say so (and undoing it).",
              "reads": ["The set-aside messages", "The rules you changed"]},
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
    "talk": {"name": "Conversation", "icon": "bi-chat-heart", "colour": "#ec4899",
             "does": "How are you, a greeting, thanks, who she is, how you feel, a goodbye: answered like a person, in your language.",
             "reads": ["What waits for you today", "Her name and look"]},
    "reader": {"name": "Questions agent", "icon": "bi-stars", "colour": "#2563eb",
               "does": "Any question, in any wording or language: understood by the cloud model, answered only from the application's records, every figure checked.",
               "reads": ["What the other agents read, through fixed lookups", "Nothing else: it never answers from memory"]},
    "general": {"name": "General agent", "icon": "bi-chat-dots", "colour": "#6b7280",
                "does": "Everything else.",
                "reads": ["What you taught the assistants"]},
}
# The way requests come in, as the agents page shows them.
CHANNELS = {"chat": ("The assistant's window", "bi-chat-square-heart"), "professor": ("A professor's assistant", "bi-mortarboard"),
            "email": ("Email", "bi-envelope"), "whatsapp": ("WhatsApp", "bi-whatsapp"), "sms": ("SMS", "bi-phone")}
OUTCOMES = {"answered": "Answered", "sent": "Answered alone", "draft": "A draft for you", "admin": "Brought to you", "none": "No answer"}
KEEP_DAYS = 120


def agent_of(key: str | None, db: Session | None = None) -> dict:
    """An agent's name, icon and colour; a program's agent ("program:<id>") is named after its program."""
    if key and key.startswith("program:"):
        from app.services import program_agents

        program = program_agents.program_of(db, key) if db is not None else None
        name = _t("{0} agent", program.name) if program is not None else _t("A program's agent")
        return {"key": key, "name": name, "icon": "bi-journal-code", "colour": "#7c3aed",
                "does": _t("Everything about one program."), "reads": []}
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


@dataclass
class _Asker:
    """Who asks, and with what: given to every step, never kept in the graph's state."""
    db: Session
    user: Any = None
    lang: str | None = None
    history: list[dict] | None = None
    limited: bool = False                     # the guide window: a professor's agent or the guide, nothing more
    path: str | None = None                   # the page the window is open on ("résume cette page")
    chosen: str | None = None                 # the language chosen in the window (None: the one of the question)


class _Request(TypedDict, total=False):
    """What travels from step to step."""
    message: str
    fixes: list
    routed: dict
    to: str                                   # the step the rules chose after reading
    limited: bool
    agent: str
    social: dict                              # the small talk in the message (small_talk.read)
    found: dict | None
    program: Any
    consulted: list
    path: Annotated[list, operator.add]       # every step taken, in order


# The subjects a program's agent may answer before the subject's own agent.
_PROGRAM_SUBJECTS = ("programs", "general", "planning", "payment")
# The steps the agents page draws: icon, name, what it does.
STEPS = {
    "read": ("bi-eye", "Read the request", "Small talk told apart from a question, words written with mistakes put right, then its subject from word lists."),
    "talk": ("bi-chat-heart", "Conversation", "How are you, hello, thanks, who she is, a goodbye: answered like a person, with what waits today."),
    "professor": ("bi-mortarboard", "Professors' agent", "Their classes, their candidates and the course, nothing else."),
    "guide": ("bi-signpost-split", "Guide agent", "For a team member without the whole assistant: how to do things here, for what they may do."),
    "system": ("bi-sliders2", "System agent", "Changes a setting when you ask, and undoes it."),
    "summary": ("bi-text-paragraph", "Summary agent", "Sums up what you name (a candidate, a class, the day, the page…) in a few lines."),
    "keep": ("bi-person-heart", "Follow-up agent", "Who may drop out, why, and what to do for each."),
    "cases": ("bi-signpost-split", "What to do when…", "A professor absent, a candidate who has not paid, a missing link…: what the application does by itself, what is left to you."),
    "review": ("bi-question-circle", "Why it answered", "Why the email assistant answered someone that way, and putting it right."),
    "set_aside": ("bi-sliders", "Set-aside messages", "Why a message was set aside, and bringing it back."),
    "marketing": ("bi-megaphone", "Marketing agent", "What is trendy in posts, when to post, this week's plan."),
    "trends": ("bi-graph-up-arrow", "Trends agent", "What is new in every field and what the job offers ask."),
    "program": ("bi-journal-code", "The program's agent", "A question naming a program goes to its own agent."),
    "tools": ("bi-tools", "The subject's agent", "The figures, the guide and what was taught, for the subject read."),
    "consult": ("bi-people", "Ask the others", "When the first one does not know: the program's agent, then the knowledge agent."),
    "cloud": ("bi-stars", "Questions agent", "Any question, in any wording: the cloud model picks what to look up and writes from it, every figure checked; otherwise the subject's agent answers."),
    "page": ("bi-map", "Page guide", "What the page is for and every part of it, from its tour, with a walk through it on the page."),
    "go": ("bi-signpost-2", "Take you there", "Opens the page or the file you name, goes back, visits every page (by herself when asked), prepares an email in its page."),
    "abilities": ("bi-stars", "What she can do", "Every ability as a card to try, and a plain yes or not yet to “can you…?”."),
    "recall": ("bi-chat-left-heart", "Where were we", "What you were talking about, from the conversation she keeps."),
    "finish": ("bi-check2-circle", "Answer", "Says who answered, whom it asked and the words it read, then writes it down."),
}


def first_step(db: Session, message: str, user, agent: str, *, limited: bool = False) -> str:
    """The step that takes a request once read: rules only, in this order (nothing is answered here)."""
    from app.services import small_talk

    if small_talk.read(message)["only"]:
        return "talk"                             # nothing but small talk: answered like a person
    if agent == "professor":
        return "professor"
    if limited:
        return "guide"
    if _role(user) in ("admin", "staff"):
        from app.services import answer_review, cases, keep_them, marketing, reply_rules, summarize, system_actions, trends_agent

        if summarize.wants(message):              # "résume…": a candidate, a class, the day, the page, a text, her last answer
            return "summary"
        if keep_them.wants(message):              # someone may drop out: who, why, and what to do
            return "keep"
        if cases.wants(message):                  # "a professor is absent, what do I do?": what is done by itself, what is left
            return "cases"
        if system_actions.wants(db, message):     # change a setting, a reminder, the look… (or undo it)
            return "system"
        if answer_review.wants(message):          # why the email assistant answered someone that way; putting it right
            return "review"
        if reply_rules.wants(message):            # the messages set aside without an answer, and their rules
            return "set_aside"
        if _for_the_reader(message):
            return "cloud"                        # any other question: read by the cloud model, answered from the records
        if marketing.wants(message):              # posts: what is trendy, when, this week's plan
            return "marketing"
        if trends_agent.wants(message):           # what is new, services to add, what a name says
            return "trends"
    return "program" if agent in _PROGRAM_SUBJECTS else "tools"


def _read(state: _Request, runtime) -> dict:
    """Words written with mistakes are read as meant ("inscripton" → "inscription"), then what the request is about, and
    so which step takes it."""
    from app.services import spelling

    from app.services import friend, small_talk

    asker = runtime.context
    # Small talk first, told apart from a question: "ça va ?" is talk, "ça va les paiements ?" a question.
    social = small_talk.read(state["message"], name=friend.look(asker.db).get("name"))
    if asker.lang not in ("fr", "en", "ar", "tn"):
        asker.lang = social["lang"]               # no language chosen: she answers in the one the message is written in
    direct = _direct(state["message"])
    if direct:
        # A move to a page, what she can do, where we were: read whole, before the small talk in it ("what are you able to do?").
        routed = {"category": {**message_categories.label_of("general"), "why": [], "by": "rules"}, "agent": agent_of("guide")}
        return {"message": state["message"], "fixes": [], "routed": routed, "agent": "guide", "limited": asker.limited, "to": direct,
                "social": {}, "found": None, "program": None, "consulted": [], "path": ["read"]}
    if small_talk.another_joke(state["message"], asker.history):
        social = {**social, "kinds": ["joke"], "only": True, "question": ""}       # "une autre !" right after a joke
    if social["only"]:
        routed = {"category": {**message_categories.label_of("general"), "why": [], "by": "rules"}, "agent": agent_of("talk")}
        return {"message": state["message"], "fixes": [], "routed": routed, "agent": "talk", "limited": asker.limited, "to": "talk",
                "social": social, "found": None, "program": None, "consulted": [], "path": ["read"]}
    # Only the question goes on (the greeting around it is answered in a word, at the end). Tunisian and Arabic are not
    # "put right": their words are not misspelt French ("khallas" is not "khlas").
    asked = social["question"] if social["kinds"] else state["message"]
    message, fixes = (asked, []) if social["lang"] in ("tn", "ar") else spelling.correct(asked)
    routed = route(message, asker.user)
    agent = routed["agent"]["key"]
    return {"message": message, "fixes": fixes, "routed": routed, "agent": agent, "limited": asker.limited,
            "to": first_step(asker.db, message, asker.user, agent, limited=asker.limited), "social": social,
            "found": None, "program": None, "consulted": [], "path": ["read"]}


def _direct(message: str) -> str | None:
    """The steps a message goes to whole, whoever asks: a move ("ouvre les paiements"), what she can do, where we were."""
    from app.services import abilities, conversation, navigate, page_help

    if page_help.wants(message):
        return "page"                             # "explique cette page", "what can I do here?"
    if navigate.wants(message):
        return "go"
    if abilities.wants(message) or abilities.can_you(message):
        return "abilities"
    if conversation.wants_recall(message):
        return "recall"
    return None


def _talk(state: _Request, runtime) -> dict:
    """Nothing but small talk: she answers like a person, in the language it was written in."""
    from app.services import small_talk

    asker = runtime.context
    return {"agent": "talk", "found": small_talk.answer(asker.db, state["social"], user=asker.user, lang=asker.lang, history=asker.history),
            "path": ["talk"]}


def _go(state: _Request, runtime) -> dict:
    """She takes you where you ask (a page, someone's file, back, the visit), or prepares an email in its page."""
    from app.services import navigate

    asker = runtime.context
    return {"agent": "guide", "found": navigate.answer(asker.db, state["message"], user=asker.user, lang=asker.lang), "path": ["go"]}


def _page(state: _Request, runtime) -> dict:
    """The page named (else the one her window is open on): what it is for and every part of it, and a walk through it."""
    from app.services import page_help

    asker = runtime.context
    return {"agent": "guide", "found": page_help.answer(asker.db, state["message"], user=asker.user, lang=asker.lang, path=asker.path),
            "path": ["page"]}


def _abilities(state: _Request, runtime) -> dict:
    """What she can do, as cards to try; or a plain yes / not yet to "can you…?"."""
    from app.services import abilities

    asker = runtime.context
    if abilities.can_you(state["message"]) and not abilities.wants(state["message"]):
        found = abilities.answer_can_you(asker.db, state["message"], user=asker.user, lang=asker.lang)
    else:
        found = abilities.answer(asker.db, state["message"], user=asker.user, lang=asker.lang)
    return {"agent": "guide", "found": found, "path": ["abilities"]}


def _recall(state: _Request, runtime) -> dict:
    """Where we were: the last question before this one, and her answer."""
    from app.services import conversation

    asker = runtime.context
    return {"agent": "talk", "found": conversation.recall(asker.db, asker.user, asker.lang), "path": ["recall"]}


def _summary(state: _Request, runtime) -> dict:
    """Sums up what was named, from the records (or the text given), in a few lines."""
    from app.services import summarize

    asker = runtime.context
    return {"agent": "summary", "found": summarize.answer(asker.db, state["message"], asker.history, lang=asker.lang, path=asker.path),
            "path": ["summary"]}


# Asking the application to do something (send, remind, write, register, export, a PDF, remember…): its own rules do it.
_ACTION = re.compile(r"^\s*(?:please\s+|svp\s+|stp\s+|yasmine\s*,?\s*)?(?:send|envoie|envoyez|envoyer|renvoie|ab3ath|ab3th|abath|3awed|remind|rappelle|"
                     r"write|[ée]cris|register|inscris|export|exporte|retiens|remember|mail|email|whatsapp|sms)\b|\bpdf\b|"
                     r"\b(?:nheb|nhib|n7eb|bghit|je veux|i want to)\s+(?:n(?:eb3ath|ab3ath|eb3th)|envoyer|send|[ée]crire|write)\b", re.IGNORECASE)


def _for_the_reader(message: str) -> bool:
    from app.services import cloud_model

    return cloud_model.available() and not _ACTION.search(message or "")


# Where to look after the reader's answer, and its picture, by the lookups it used.
_LOOKED_AT = {"money_received": ("Payments", "/payments", "payments"), "not_paid": ("Payments", "/payments", "payments"),
              "registrations": ("Candidates", "/candidate/all", "candidates"), "candidate": ("Candidates", "/candidate/all", "candidates"),
              "best_candidates": ("Candidates", "/candidate/all", "candidates"), "classes": ("Classes", "/classes/", "classes"),
              "class_detail": ("Classes", "/classes/", "classes"), "sessions": ("Classes", "/classes/", "sessions"),
              "professors": ("Professors", "/classes/?tab=professors", "classes"), "may_drop_out": ("Who may drop out", "/#live-dashboard-risk", "feelings"),
              "messages": ("Inbox", "/inbox", "inbox"), "reviews": ("Programs", "/programs/all", "feelings"), "programs": ("Programs", "/programs/all", "programs"),
              "automatic": ("What the application does by itself", "/system#automatic", "settings"), "marketing": ("Social media", "/social", "posts"),
              "trends": ("The journal", "/journal", "news"), "overview": ("Dashboard", "/", "data"),
              "job_market": ("What the market asks", "/programs/market", "data"), "news": ("The journal", "/journal", "news"),
              "program_advice": ("Programs", "/programs/all", "programs"), "connected": ("Who is connected", "/settings/team#connected", "team"),
              "reminders": ("Reminders", "/reminders", "sessions"), "waiting_today": ("Dashboard", "/", "dashboard")}


def _cloud(state: _Request, runtime) -> dict:
    """Any other question: the cloud model picks the lookups and writes from what they return; an answer with a figure
    the records do not show is not given (the subject's agent answers instead, as before)."""
    from app.services import cloud_answer, lookups

    asker = runtime.context
    try:
        found = cloud_answer.answer(state["message"], lookups.for_admin(asker.db, asker.user, path=asker.path), "admin", history=asker.history, lang=asker.chosen)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("The cloud reader failed")
        found = None
    if found is None or not found["ok"] or not found["reply"]:
        return {"path": ["cloud"]}
    places = [_LOOKED_AT[name] for name in dict.fromkeys(found["used"]) if name in _LOOKED_AT]
    links = [] if found.get("clarify") else [{"label": _t(label), "url": url} for label, url, _ in dict.fromkeys(places)][:3]
    sources = []
    if any(name not in ("web_search", "ask_back") for name in found["used"]):
        sources.append({"label": _t("The application's records")})
    if "web_search" in found["used"]:
        sources.append({"label": _t("The web (Google)")})
    return {"agent": "reader", "path": ["cloud"],
            "found": {"reply": found["reply"], "links": links, "source": "cloud", "resources": [], "scene": places[0][2] if places else "data",
                      "sources": sources, "facts": found.get("facts") or {}, "language": found.get("language"),
                      "options": found.get("options") or []}}


def _keep(state: _Request, runtime) -> dict:
    """Someone may drop out: who, why (what was recorded), and what to do for each."""
    from app.services import keep_them

    asker = runtime.context
    return {"agent": "keep", "found": keep_them.answer(asker.db, state["message"], asker.history, lang=asker.lang), "path": ["keep"]}


def _cases(state: _Request, runtime) -> dict:
    """What to do when something happens (a professor absent, a candidate who has not paid…), and how it stands now."""
    from app.services import cases

    asker = runtime.context
    return {"agent": "guide", "found": cases.answer(asker.db, state["message"], lang=asker.lang), "path": ["cases"]}


def _professor(state: _Request, runtime) -> dict:
    from app.services import professor_assistant

    asker = runtime.context
    return {"found": professor_assistant.answer(asker.db, state["message"], asker.history, user=asker.user, lang=asker.lang),
            "path": ["professor"]}


def _guide(state: _Request, runtime) -> dict:
    from app.services import howto

    asker = runtime.context
    return {"agent": "guide", "found": howto.answer(asker.db, state["message"], asker.user, lang=asker.lang), "path": ["guide"]}


def _system(state: _Request, runtime) -> dict:
    from app.services import system_actions

    asker = runtime.context
    return {"agent": "system", "found": system_actions.answer(asker.db, state["message"], user=asker.user, lang=asker.lang),
            "path": ["system"]}


def _review(state: _Request, runtime) -> dict:
    from app.services import answer_review

    asker = runtime.context
    return {"agent": "inbox", "found": answer_review.for_chat(asker.db, state["message"], user=asker.user, lang=asker.lang),
            "path": ["review"]}


def _set_aside(state: _Request, runtime) -> dict:
    from app.services import reply_rules

    asker = runtime.context
    if _role(asker.user) == "admin" or access.allows(access.clean(getattr(asker.user, "permissions", None)), "/inbox/message/x/talk", "POST"):
        found = reply_rules.for_chat(asker.db, state["message"], user=asker.user, lang=asker.lang)
    else:
        found = {"reply": _t("Changing what the inbox sets aside needs the right to answer emails: ask the admin."), "links": [],
                 "source": "system", "sources": [], "resources": []}
    return {"agent": "inbox", "found": found, "path": ["set_aside"]}


def _marketing(state: _Request, runtime) -> dict:
    from app.services import marketing

    asker = runtime.context
    return {"agent": "marketing", "found": marketing.answer(asker.db, state["message"], lang=asker.lang), "path": ["marketing"]}


def _trends(state: _Request, runtime) -> dict:
    from app.services import trends_agent

    asker = runtime.context
    return {"agent": "trends", "found": trends_agent.answer(asker.db, state["message"], lang=asker.lang), "path": ["trends"]}


def _program(state: _Request, runtime) -> dict:
    """A question about one program goes to that program's agent."""
    from app.services import program_agents

    asker = runtime.context
    program = program_agents.find(asker.db, state["message"])
    found = program_agents.answer(asker.db, program, state["message"], lang=asker.lang) if program is not None else None
    update = {"program": program, "path": ["program"]}
    if found is not None:
        update.update(found=found, agent=program_agents.key_of(program))
    return update


def _tools(state: _Request, runtime) -> dict:
    """The subject's agent, with the application's tools: the figures, the guide, what was taught."""
    from app.services import chatbot

    asker = runtime.context
    found = chatbot.answer(asker.db, state["message"], asker.history, user=asker.user, lang=asker.lang)
    agent = state["agent"]
    if found.get("source") == "guide":
        agent = "guide"            # the guide's steps answered, whatever the subject read
    elif found.get("source") in ("taught", "knowledge", "website") and agent == "general":
        agent = "knowledge"
    return {"found": found, "agent": agent, "path": ["tools"]}


def _consult(state: _Request, runtime) -> dict:
    """The first agent does not know: Yasmine asks the others, the program's agent first, then the knowledge agent."""
    from app.services import program_agents, rag

    asker = runtime.context
    program = state.get("program") if state.get("program") is not None else program_agents.find(asker.db, state["message"])
    asked = program_agents.answer(asker.db, program, state["message"], lang=asker.lang) if program is not None else None
    if asked is not None:
        return {"found": asked, "agent": program_agents.key_of(program), "path": ["consult"],
                "consulted": [agent_of(program_agents.key_of(program), asker.db)["name"]]}
    cited = rag.answer(asker.db, state["message"], lang=asker.lang, for_admin=_role(asker.user) in ("admin", "staff"))
    if cited is not None:
        return {"found": cited, "agent": "knowledge", "consulted": [_t(AGENTS["knowledge"]["name"])], "path": ["consult"]}
    return {"path": ["consult"]}


def _finish(state: _Request, runtime) -> dict:
    """The answer says who gave it, whom Yasmine asked and the words she read as meant."""
    agent, routed = state["agent"], state["routed"]
    found = {**state["found"], "agent": agent_of(agent, runtime.context.db)}
    social = state.get("social") or {}
    if social.get("kinds") and not social.get("only") and found.get("reply"):
        # A greeting, "how are you" or thanks with a question: a word back first, then the answer.
        from app.services import small_talk

        said = small_talk.prefix(runtime.context.db, social, user=runtime.context.user, lang=runtime.context.lang)
        if said:
            found["reply"] = f"{said}\n\n{found['reply']}"
            found["social"] = social["kinds"]
    if state.get("consulted"):
        found["consulted"] = state["consulted"]
    if state.get("fixes"):
        found["understood"] = [{"written": wrong, "read": right} for wrong, right in state["fixes"]]
    category = routed["category"]
    if agent == "trends" and category["key"] == "general":
        category = {**message_categories.label_of("trends"), "why": [], "by": "agent"}
    found["category"] = {key: category[key] for key in ("key", "label", "icon", "colour", "why", "by")}
    # Short first (the whole answer one tap away), and the moving picture of its subject.
    from app.services import brief, cards, pictures

    # An agent may give its own short version (it knows what matters); it is kept only for an answer long enough.
    given = found.get("short")
    reply = found.get("reply") or ""
    found["short"] = given if given and len(reply) > brief.LONG and len(given) < len(reply) else brief.shorten(reply)
    # What the answer says, drawn as moving cards (only what it says); a list drawn is not written twice: its first line
    # shows, the whole text one tap away.
    facts, language = found.pop("facts", None), found.pop("language", None) or runtime.context.lang
    if not found.get("cards") and agent != "talk":
        drawn = cards.from_facts(facts, reply, language) if facts else cards.from_reply(reply, language)
        if drawn:
            found["cards"] = drawn
    if found.get("cards") and cards.lists_drawn(found["cards"]) and not given:
        head = brief.head(reply)
        if head and len(head) < len(reply):
            found["short"] = head
    found["picture"] = pictures.for_answer(runtime.context.db, found, agent, category["key"])
    found.pop("scene", None)
    return {"found": found, "routed": {**routed, "category": category}, "path": ["finish"]}


def _after(state: _Request) -> str:
    """After an agent: nobody answered yet → the program's agent (once), then the tools; nobody knew → ask the others.
    The guide window goes no further than its agent."""
    found = state.get("found")
    if state.get("limited"):
        return "finish"
    if found is None:
        return "program" if state["agent"] in _PROGRAM_SUBJECTS and "program" not in state["path"] else "tools"
    return "consult" if found.get("source") == "none" and state["agent"] != "professor" else "finish"


_GRAPH = None
_GRAPH_LOCK = threading.Lock()


def graph():
    """The graph every question of the assistant's window travels through (LangGraph), built once."""
    global _GRAPH
    with _GRAPH_LOCK:
        if _GRAPH is None:
            from langgraph.graph import END, START, StateGraph

            builder = StateGraph(_Request, context_schema=_Asker)
            for name, step in (("read", _read), ("talk", _talk), ("page", _page), ("go", _go), ("abilities", _abilities), ("recall", _recall), ("summary", _summary), ("keep", _keep), ("cases", _cases), ("cloud", _cloud), ("professor", _professor), ("guide", _guide), ("system", _system), ("review", _review),
                               ("set_aside", _set_aside), ("marketing", _marketing), ("trends", _trends),
                               ("program", _program), ("tools", _tools), ("consult", _consult), ("finish", _finish)):
                builder.add_node(name, step)
            builder.add_edge(START, "read")
            builder.add_conditional_edges("read", lambda state: state["to"],
                                          ["talk", "page", "go", "abilities", "recall", "summary", "keep", "cases", "cloud", "professor", "guide", "system", "review", "set_aside", "marketing", "trends", "program", "tools"])
            for name in ("professor", "system", "review", "set_aside", "cloud", "marketing", "trends"):
                builder.add_conditional_edges(name, _after, ["program", "tools", "consult", "finish"])
            builder.add_conditional_edges("program", _after, ["tools", "consult", "finish"])
            builder.add_conditional_edges("tools", _after, ["consult", "finish"])
            builder.add_edge("guide", "finish")
            builder.add_edge("talk", "finish")
            builder.add_edge("page", "finish")
            builder.add_edge("go", "finish")
            builder.add_edge("abilities", "finish")
            builder.add_edge("recall", "finish")
            builder.add_edge("summary", "finish")
            builder.add_edge("keep", "finish")
            builder.add_edge("cases", "finish")
            builder.add_edge("consult", "finish")
            builder.add_edge("finish", END)
            _GRAPH = builder.compile()
    return _GRAPH


def drawing() -> list[dict]:
    """The graph's steps in rows (a step sits one row below the furthest step that leads to it), with the steps each can
    lead to, said once for a row whose steps all lead to the same ones: read from the graph itself, so the drawing is
    always the graph that runs."""
    drawn = graph().get_graph()
    edges = [(edge.source, edge.target, bool(edge.conditional)) for edge in drawn.edges if edge.source in STEPS and edge.target in STEPS]
    row = {key: 0 for key in drawn.nodes if key in STEPS}
    for _ in row:                                     # longest way from "read"; the graph has no loop
        for source, target, _chosen in edges:
            row[target] = max(row[target], row[source] + 1)
    order = list(STEPS)
    after: dict[str, list[dict]] = {}
    for source, target, chosen in sorted(edges, key=lambda edge: order.index(edge[1])):
        after.setdefault(source, []).append({"key": target, "name": _t(STEPS[target][1]), "icon": STEPS[target][0], "chosen": chosen})
    rows: list[dict] = [{"steps": [], "next": []} for _ in range(max(row.values()) + 1)]
    for key in sorted(row, key=order.index):
        rows[row[key]]["steps"].append({"key": key, "icon": STEPS[key][0], "name": _t(STEPS[key][1]), "does": _t(STEPS[key][2]),
                                        "next": after.get(key, [])})
    for line in rows:
        if len(line["steps"]) > 1 and all(step["next"] == line["steps"][0]["next"] for step in line["steps"]):
            line["next"] = line["steps"][0]["next"]
            for step in line["steps"]:
                step["next"] = []
    return rows


def dispatch_chat(db: Session, message: str, history: list[dict] | None = None, *, user=None, lang: str | None = None,
                  limited: bool = False, path: str | None = None) -> dict:
    """The answer to a question asked in the assistant's window (by the admin or a team member), or in the guide window
    (limited: a professor, or a team member without the whole assistant), with the agent that gave it and the steps it
    went through."""
    started = time.monotonic()
    done = graph().invoke({"message": message}, context=_Asker(db=db, user=user, lang=lang, history=history, limited=limited, path=path,
                                                                     chosen=lang if lang in ("fr", "en", "ar", "tn") else None))
    found = {**done["found"], "path": done["path"]}
    note(db, agent=done["agent"], category=done["routed"]["category"], channel="professor" if done["agent"] == "professor" else "chat",
         outcome=_outcome_of(found), asked=done["message"], user_id=getattr(user, "id", None),
         took_ms=int((time.monotonic() - started) * 1000))
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
        "programs": _program_agents(db, by_agent),
        "learnt": _learnt(db),
        "latest": [{"at": run.at, "agent": agent_of(run.agent, db), "category": message_categories.label_of(run.category),
                    "channel": CHANNELS.get(run.channel or "chat", CHANNELS["chat"]), "outcome": OUTCOMES.get(run.outcome or "answered", run.outcome),
                    "outcome_key": run.outcome or "answered", "asked": run.asked or "", "ref": run.ref,
                    "why": _why(run.why), "took_ms": run.took_ms} for run in latest],
    }


def _program_agents(db: Session, by_agent: dict) -> list[dict]:
    """Every program's agent, with the requests it had."""
    from app.services import program_agents

    found = []
    for agent in program_agents.agents(db):
        row = by_agent.get(agent["key"], {"total": 0, "outcomes": {}})
        found.append({**agent, "total": row["total"]})
    return found


def _learnt(db: Session, days: int = 30) -> dict:
    """What the agents learnt lately, all of them together: answers taught by you, given by professors, corrected."""
    from app.models.knowledge import KnowledgeEntry

    since = datetime.utcnow() - timedelta(days=days)
    rows = db.query(KnowledgeEntry.origin, KnowledgeEntry.kind).filter(KnowledgeEntry.origin.in_(("admin", "learned")),
                                                                      KnowledgeEntry.created_at >= since).all()
    return {"days": days, "answers": sum(1 for origin, kind in rows if kind != "fact"), "facts": sum(1 for origin, kind in rows if kind == "fact")}


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

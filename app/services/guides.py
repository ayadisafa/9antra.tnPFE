"""What each page is for, said in a picture and three steps.

The explanation appears at the top of the page (see _ui_art.html) until the
person closes it; the "?" at the top of the page brings it back.
"""
from __future__ import annotations

import re


def _guide(key: str, art: str, title: str, text: str, *steps: tuple[str, str, str]) -> dict:
    return {"key": key, "art": art, "title": title, "text": text, "steps": list(steps)}


_PAGES = {
    "/": _guide(
        "dashboard", "dashboard", "What needs you today",
        "The application reads the payments, the emails, the classes and the candidates' answers, "
        "and puts the next thing to do at the top, with the reason.",
        ("bi-eye", "It watches", "payments, emails, classes, answers"),
        ("bi-lightbulb", "It advises", "what to do first, and why"),
        ("bi-hand-index-thumb", "You decide", "one click, or “Not now”"),
    ),
    "/candidate/all": _guide(
        "candidates", "candidates", "Everyone who registered",
        "One line per person: what they chose, what they paid, and the one thing they need from you now.",
        ("bi-ui-checks", "They register", "the form arrives here by itself"),
        ("bi-cash-coin", "They pay", "one reminder, then you decide"),
        ("bi-easel2", "They join a class", "placed by itself once paid"),
    ),
    "/classes/": _guide(
        "classes", "classes", "Your classes, one door each",
        "Each card is a class: its symbol, professor, hours and seats. “Parameters” changes everything in one form.",
        ("bi-door-open", "A class opens", "when a first candidate pays"),
        ("bi-people", "The seats fill", "until the class can start"),
        ("bi-calendar-week", "The weeks run", "course, presence, questions"),
    ),
    "/programs/all": _guide(
        "programs", "programs", "What you teach",
        "Programs follow your website 9antra.tn: their details and weekly plan are filled in and kept up to date. "
        "What the website does not say is proposed to you, and nothing is kept without your yes.",
        ("bi-globe2", "The website is read", "every six hours"),
        ("bi-magic", "What is missing is filled", "or proposed to you"),
        ("bi-check2-circle", "You confirm", "your own words always stay"),
    ),
    "/programs/market": _guide(
        "market", "programs", "What employers ask",
        "Job offers are read, the skills they name are counted, and each program is compared with them. "
        "Nothing is guessed: every percentage is a count of offers.",
        ("bi-briefcase", "Offers are read", "9antra.tn, a public job board, what you paste"),
        ("bi-bar-chart", "Skills are counted", "the share of offers that ask for each"),
        ("bi-plus-circle", "You see what to add", "what is asked and not yet taught"),
    ),
    "/quiz/all": _guide(
        "levels", "quiz", "Where each candidate starts from",
        "Before a program starts, one short form tells you where a candidate stands. It follows the program's "
        "own topics and ends with a few questions from easy to advanced. The weekly questions of a class are on "
        "the class's course page.",
        ("bi-person-raised-hand", "They say who they are", "experience, what they expect"),
        ("bi-signpost-split", "Topic by topic", "from “I discover” to “I am at ease”"),
        ("bi-bar-chart-steps", "A few questions", "their level, and what the group knows"),
    ),
    "/inbox": _guide(
        "inbox", "inbox", "Every email with your candidates",
        "The application answers what it is sure about and leaves you the rest. "
        "Write your answer in a few words: “Reformulate” turns it into a full email.",
        ("bi-envelope-arrow-down", "An email arrives", "it is read and understood"),
        ("bi-robot", "Sure: it answers", "not sure: it waits for you"),
        ("bi-pencil", "You write short", "“Reformulate” writes the email"),
    ),
    "/payments": _guide(
        "payments", "payments", "Who still has to pay",
        "A candidate gets one payment reminder by itself, for everything they owe. After three days "
        "without payment or answer, you are offered to send it again, by email or on WhatsApp.",
        ("bi-send", "One reminder", "by itself, 24 hours after registering"),
        ("bi-hourglass-split", "Three days", "nothing more is sent"),
        ("bi-person-check", "You decide", "read it, change it, send it"),
    ),
    "/website": _guide(
        "website", "website", "What your website says, kept here",
        "The formations, events, internship offers, news, videos and feedback of 9antra.tn are read every six hours. "
        "They fill your programs and what the email assistant knows.",
        ("bi-globe2", "9antra.tn is read", "only what changed is touched"),
        ("bi-database-check", "Your programs follow", "prices, hours, weekly plan"),
        ("bi-shield-check", "Your words stay", "you choose when the website wins"),
    ),
    "/settings": _guide(
        "settings", "settings", "How the application behaves",
        "Its name, icon and colour, who can sign in, how emails are written, and the services it is connected to.",
        ("bi-palette", "Make it yours", "name, icon, colour"),
        ("bi-plug", "Connect", "email, WhatsApp, Google Forms, Firebase"),
        ("bi-sliders", "Set the rules", "classes, reminders, answers"),
    ),
    "/system": _guide(
        "system", "settings", "How the application is working",
        "Everything it does by itself, when it last did it, and what it is connected to. "
        "A green dot means it works; anything else says what to do.",
        ("bi-activity", "It works by itself", "emails, classes, reminders, website"),
        ("bi-plug", "It is connected", "email, assistant, website, WhatsApp"),
        ("bi-phone", "On your phone too", "the same pages, made for a small screen"),
    ),
    "/teacher": _guide(
        "teacher", "classes", "Your classes",
        "For each class: add what you taught this week, and mark who was there. The questions for the candidates are written from your course.",
        ("bi-folder-plus", "Add the course", "files, videos, photos, notes"),
        ("bi-person-check", "Mark the presence", "present, late or absent"),
        ("bi-emoji-smile", "See how it went", "what they understood and how they feel"),
    ),
}

_PATTERNS = [
    (re.compile(r"^/classes/[^/]+/course$"), _guide(
        "course", "course", "The course, week by week",
        "Add what was taught each week. Questions are written from your own files, each candidate answers them "
        "and says how the week went, and you see what was understood and how the class feels.",
        ("bi-folder-plus", "Add the files", "PDF, slides, video, photo, notes"),
        ("bi-patch-question", "Questions are written", "from your course, checked twice"),
        ("bi-emoji-smile", "Candidates answer", "what they learnt, how they feel"),
    )),
    (re.compile(r"^/classes/[^/]+/attendance$"), _guide(
        "presence", "presence", "Who was there",
        "Pick a session on the left. Everyone starts as present: change only those who were late or absent, then save.",
        ("bi-calendar-check", "Pick the session", "today's is chosen for you"),
        ("bi-toggles", "Mark each person", "present, late or absent"),
        ("bi-bell", "The dashboard follows", "two absences in a row: a call is advised"),
    )),
]


def for_path(path: str) -> dict | None:
    """The explanation of the page at this address, when it has one."""
    found = _PAGES.get(path) or _PAGES.get(path.rstrip("/")) or _PAGES.get(path.rstrip("/") + "/")
    if found:
        return found
    for pattern, guide in _PATTERNS:
        if pattern.match(path):
            return guide
    return None


# The whole application as one journey (the "How it works" page).
JOURNEY = [
    {"art": "candidates", "tone": "sky", "title": "1 · A candidate registers",
     "text": "They fill the registration form. The answer goes to Firebase and arrives here by itself, with the phone number written +216.",
     "link": ("/candidate/all", "Candidates")},
    {"art": "payments", "tone": "mint", "title": "2 · They pay",
     "text": "One reminder goes out by itself after 24 hours. After three days of silence you are offered to send it again, by email or WhatsApp.",
     "link": ("/payments", "Payments")},
    {"art": "classes", "tone": "gold", "title": "3 · They get a seat",
     "text": "A paid candidate is placed in a class of their program. The class starts when it has enough people, and everyone is told the date.",
     "link": ("/classes/", "Classes")},
    {"art": "course", "tone": "violet", "title": "4 · Each week, the course",
     "text": "The professor adds what was taught and marks who was there. Questions are written from the course itself.",
     "link": ("/classes/", "Open a class, then “Course”")},
    {"art": "feelings", "tone": "rose", "title": "5 · They answer, you understand",
     "text": "Each candidate answers a few questions and says how the week went. You see what was understood, how they feel, and who needs you.",
     "link": ("/", "Dashboard")},
    {"art": "inbox", "tone": "rose", "title": "Any time · They write to you",
     "text": "Emails are answered when the answer is known, and wait for you when it is not. Your few words become a full email.",
     "link": ("/inbox", "Inbox")},
    {"art": "website", "tone": "sky", "title": "Behind it all · Your website",
     "text": "9antra.tn is read every six hours: programs, prices, events, videos and feedback stay the same here as there.",
     "link": ("/website", "Website")},
]

"""What each page is for, said in a picture and three steps (a small schema; each step links to where it is done).

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


# ------------------------------------------------------------------
# Every functionality, with its schema (2 November): the pages that had no explanation, a link on each step (where it
# is done), and the whole list for the "How it works" page.
# ------------------------------------------------------------------

_PAGES.update({
    "/reminders": _guide(
        "reminders", "sessions", "Every reminder, and yours",
        "What the application sends by itself, each with its switch and its timing; what is smart to send each candidate; and your own reminders.",
        ("bi-bell", "It reminds by itself", "payment, countdown, each session", "/reminders#auto"),
        ("bi-stars", "Smart for each one", "what, which way, at what hour", "/reminders#smart-people"),
        ("bi-pencil-square", "Your own", "once, weekly, at each absence", "/reminders#new"),
    ),
    "/social": _guide(
        "social", "posts", "Posts, videos, and when to post",
        "This week's plan at the best moments, what is trending, posts and videos made from your records with this year's design and music, and how each post did.",
        ("bi-calendar-week", "The plan", "what to post, and when", "/social#plan"),
        ("bi-fire", "What is trending", "counted from the articles", "/social#trends"),
        ("bi-camera-reels", "Make it", "posts, stories, videos with music", "/social#create"),
        ("bi-clock-history", "When to post", "when most of your people are there", "/social#timing"),
    ),
    "/inbox/team": _guide(
        "team_chat", "team", "Talking with your team",
        "One conversation per professor or team member, by message, email or WhatsApp, kept in one place.",
        ("bi-person-badge", "Choose a person", "a professor or the team", "/inbox/team"),
        ("bi-chat-dots", "Write", "they get it the way they read", "/inbox/team"),
        ("bi-clock-history", "It stays", "every message, in order", "/inbox/team"),
    ),
    "/data": _guide(
        "data", "data", "All the data, in tables",
        "Every table the application keeps, searchable, with a download for each.",
        ("bi-table", "Open a table", "candidates, payments, classes…", "/data"),
        ("bi-search", "Search", "a name, a number, a date", "/data"),
        ("bi-download", "Download", "as a CSV file", "/data"),
    ),
    "/quiz/weekly": _guide(
        "weekly", "quiz", "The questions of the week",
        "Each week, each class gets a few questions written from its course, and says how the week went. You see what was understood.",
        ("bi-journal-text", "From the course", "the files of the week", "/classes/"),
        ("bi-send", "Sent each week", "by email, or a link", "/quiz/weekly#wk-how"),
        ("bi-emoji-smile", "They answer", "what they learnt, how they feel", "/quiz/weekly#wk-list"),
    ),
    "/journal": _guide(
        "journal", "news", "What is new in every field",
        "Public news feeds are read every six hours; the words of each field are counted, and what they suggest for your programs is said.",
        ("bi-rss", "Feeds are read", "every six hours", "/journal"),
        ("bi-bar-chart", "Words are counted", "per field", "/journal"),
        ("bi-lightbulb", "Ideas for you", "a session, a workshop, a post", "/"),
    ),
    "/programs/expertise": _guide(
        "expertise", "assistant", "What the assistant knows",
        "Per program: its files, its weekly plan, what you taught and the facts it can use, and the questions it was asked.",
        ("bi-folder2-open", "Its files", "plans, courses, PDFs", "/programs/expertise"),
        ("bi-mortarboard", "What it learnt", "facts and taught answers", "/inbox/knowledge"),
        ("bi-chat-left-text", "It answers", "emails, WhatsApp, the chat", "/inbox"),
    ),
    "/inbox/knowledge": _guide(
        "knowledge", "assistant", "What you taught the assistants",
        "Your answers, kept to answer the same question next time, and the facts every assistant knows.",
        ("bi-reply", "You answer once", "it is kept", "/inbox"),
        ("bi-arrow-repeat", "Asked again", "it answers the same way", "/inbox/knowledge"),
        ("bi-pencil", "You change it", "any time", "/inbox/knowledge#facts"),
    ),
    "/inbox/templates": _guide(
        "templates", "inbox", "The emails that go by themselves",
        "Welcome, payment reminder, countdown, session link, level check: their words and design, and whether they go.",
        ("bi-envelope-paper", "Pick an email", "each automatic one", "/inbox/templates"),
        ("bi-brush", "Words and design", "with a live preview", "/inbox/templates"),
        ("bi-toggle-on", "On or off", "and when it goes", "/reminders#auto"),
    ),
    "/inbox/compose": _guide(
        "compose", "inbox", "One message, to the people you choose",
        "A person, a class, a program or everyone; by email, WhatsApp or SMS; with a photo, files and a button.",
        ("bi-people", "Choose who", "a person, a class, everyone", "/inbox/compose#recipient-list"),
        ("bi-pencil", "Write", "subject, text, squares, photo", "/inbox/compose#compose-form"),
        ("bi-send", "Send", "it stays in each discussion", "/inbox"),
    ),
    "/assistant/agents": _guide(
        "agents", "assistant", "Who handles each request",
        "Every question and message is read for its subject, then the agent of that subject answers, prepares, or brings it to you.",
        ("bi-inbox", "A request comes", "chat, email, WhatsApp, SMS", "/inbox"),
        ("bi-diagram-3", "Its subject is read", "by words, never guessed", "/assistant/agents#try"),
        ("bi-person-gear", "Its agent acts", "answers, prepares, or asks you", "/assistant/agents#latest"),
    ),
    "/whats-new": _guide(
        "whats_new", "look", "What is new in the application",
        "Each delivery, with what it brings and where to find it.",
        ("bi-stars", "What came", "newest first", "/whats-new"),
        ("bi-box-arrow-up-right", "Where it is", "one click to open it", "/whats-new"),
        ("bi-bell", "You are told", "one notification each time", "/whats-new"),
    ),
    "/programs/documents": _guide(
        "documents", "programs", "Any information as a PDF",
        "The programs, the classes, the candidates or your own words, as a clean PDF to open, download or send.",
        ("bi-file-earmark-text", "Choose", "what the PDF says", "/programs/documents"),
        ("bi-eye", "See it", "before it goes", "/programs/documents"),
        ("bi-envelope", "Send it", "by email, attached", "/inbox/compose"),
    ),
    "/programs/videos": _guide(
        "videos", "programs", "The videos of your programs",
        "The videos of 9antra.tn and your own, per program, for the courses and the posts.",
        ("bi-globe2", "From the website", "read by itself", "/website"),
        ("bi-play-btn", "Per program", "to show in the courses", "/programs/videos"),
        ("bi-camera-reels", "In your posts", "videos with music", "/social#videos"),
    ),
    "/settings/team": _guide(
        "team", "team", "Who can sign in, and what they may do",
        "Each team member sees and changes only the parts you give them.",
        ("bi-person-plus", "Add a person", "an email and a password", "/settings/team"),
        ("bi-shield-lock", "Give rights", "see or change, part by part", "/settings/team"),
        ("bi-eye", "They see their part", "nothing else", "/settings/team"),
    ),
    "/settings/translations": _guide(
        "translations", "language", "The application in French and Arabic",
        "Every word of every page, written by hand in French and Arabic; what is missing is listed.",
        ("bi-translate", "Every word", "of every page", "/settings/translations"),
        ("bi-pencil", "Written by hand", "French and Arabic", "/settings/translations?lang=ar"),
        ("bi-check2-all", "Nothing missing", "what is left is listed", "/settings/translations"),
    ),
})

# Where each step of the first explanations is done (the new ones above carry their own).
_STEP_LINKS = {
    "/": ("/system", "/", "/"),
    "/candidate/all": ("/candidate/all", "/payments", "/classes/"),
    "/classes/": ("/classes/", "/classes/", "/quiz/weekly"),
    "/programs/all": ("/website", "/programs/all", "/programs/all"),
    "/programs/market": ("/programs/market", "/programs/market", "/journal"),
    "/quiz/all": ("/quiz/all", "/quiz/all", "/quiz/all"),
    "/inbox": ("/inbox", "/inbox?filter=auto", "/inbox?filter=todo"),
    "/payments": ("/reminders#auto", "/payments", "/payments"),
    "/website": ("/website", "/programs/all", "/website"),
    "/settings": ("/settings#appearance", "/settings#email", "/settings#classes"),
    "/system": ("/system", "/system", "/system"),
}
for _path, _links in _STEP_LINKS.items():
    _PAGES[_path]["steps"] = [tuple(step[:3]) + (link,) for step, link in zip(_PAGES[_path]["steps"], _links)]
for _path, _found in _PAGES.items():
    _found.setdefault("url", _path)

# The "How it works" page: every functionality, by part of the menu.
GROUPS = [
    ("Every day", ["/", "/inbox", "/payments", "/reminders"]),
    ("Candidates and classes", ["/candidate/all", "/classes/", "/quiz/all", "/quiz/weekly"]),
    ("What you teach", ["/programs/all", "/programs/expertise", "/programs/documents", "/programs/videos", "/programs/market"]),
    ("Talking to people", ["/inbox/compose", "/inbox/templates", "/inbox/knowledge", "/inbox/team", "/assistant/agents"]),
    ("Growing", ["/social", "/website", "/journal"]),
    ("Running it", ["/settings", "/settings/team", "/settings/translations", "/system", "/data", "/whats-new"]),
]


def everything(user=None) -> list[dict]:
    """Every functionality the person may open, by group: [{"title", "pages": [guide]}]."""
    from app.services import access

    permissions = None if user is None or getattr(user, "is_admin", False) else access.clean(getattr(user, "permissions", None))
    found = []
    for title, paths in GROUPS:
        items = [_PAGES[path] for path in paths if path in _PAGES and (permissions is None or access.allows(permissions, path, "GET"))]
        if items:
            found.append({"title": title, "pages": items})
    return found

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

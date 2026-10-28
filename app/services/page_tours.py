"""What each page lets you do, part by part: the tour shown by the “?” at the top of every page.

A tour is the page's parts in order. Each part names the real place on the page (a CSS selector: a part that is not on
the page, or is hidden for this person, is simply skipped), what it is, what you can do there and what you can change.
The browser draws the page in a small moving map first, then walks the spotlight from part to part (base.html).
The assistant and Yasmine know the tours too (knowledge chunks "system:page:…").
"""
from __future__ import annotations

import re


def _step(sel: str, icon: str, name: str, what: str, do=(), change=(), click: str | None = None, optional: bool = False) -> dict:
    """`optional`: a part shown only when there is something in it (the tour skips it otherwise)."""
    return {"sel": sel, "icon": icon, "name": name, "what": what, "do": list(do), "change": list(change), "click": click, "optional": optional}


def _tour(key: str, title: str, purpose: str, *steps: dict, finish: str | None = None) -> dict:
    return {"key": key, "title": title, "purpose": purpose, "steps": list(steps), "finish": finish}


# Around every page: shown at the end of the first tours (the dashboard, the professors' home).
FRAME = [
    _step("aside.sidebar", "bi-list", "The menu", "Every part of the application, grouped. The numbers say what waits.",
          ["Open a part of the application"]),
    _step(".topbar-search", "bi-search", "Find anything", "A name, a page, a setting or what you want to do. Ctrl+K works from any page.",
          ["Search", "Jump straight to the result"]),
    _step(".topbar-pdf", "bi-file-earmark-pdf", "This page as a PDF", "Every page can be downloaded as a clean report.",
          ["Download the page", "Pick another report"]),
    _step("[data-tour-show]", "bi-question-lg", "This tour", "On any page, it shows what you can do there.", ["Replay the tour"]),
    _step(".topbar-help:has(.bi-translate)", "bi-translate", "Language", "The pages in English, French or Arabic.", [],
          ["The language of the pages"]),
    _step("button:has(#nav-messages-badge)", "bi-envelope", "Messages", "The candidates' emails waiting for your answer.",
          ["Open the waiting messages"]),
    _step("button:has(#nav-notif-badge)", "bi-bell", "Notifications", "What happened. Each one opens the exact place; some solve it in one click.",
          ["Open where it happened", "Solve it in one click", "Mark them all as read"]),
    _step(".assistant-launcher", "bi-chat-dots", "The assistant", "Ask anything about your data, or ask it to write and send a message.",
          ["Ask a question", "Send an email, a WhatsApp or an SMS", "Get a PDF"]),
    _step("#friend .fr-svg", "bi-emoji-smile", "Yasmine", "Your guide: what waits, how the page works, in your language.",
          ["Ask her a question", "Hide her"]),
]

TOURS: dict[str, dict] = {
    "/": _tour(
        "dashboard", "The dashboard", "Your day at a glance, what needs you first, and every figure opening its page.",
        _step(".db-hero", "bi-sun", "Your day at a glance", "Today in a few figures; each chip opens its place.",
              ["Open today's sessions", "Answer the waiting emails", "Write an email, add a candidate or create a class"]),
        _step("#live-dashboard-figures", "bi-grid", "Four pictures", "Candidates, payments, classes and emails: where things stand.",
              ["Open each part in one click"]),
        _step("#live-dashboard-advice", "bi-compass", "What needs you today", "The most pressing first, with the reason and one button that does it.",
              ["Do it in one click", "Put it off with “Not now”", "Show the others"]),
        _step("#live-dashboard-risk", "bi-person-walking", "Who may drop out", "From what was recorded: absences, unanswered questions, results, money owed.",
              ["Open their file", "Remind them to pay"]),
        _step("#live-dashboard-journal", "bi-newspaper", "The journal", "What is new in each field you teach, and what it suggests.", ["Open the journal"]),
        _step("#reports", "bi-file-earmark-arrow-down", "Reports as PDF", "This week, the analysis and the candidates' progress, ready to print or send.",
              ["Download a report"]),
        _step("#live-dashboard-reminders", "bi-alarm", "Reminders", "The reminders planned, and ideas worth sending.",
              ["Write a new reminder", "Open a planned reminder"], ["Who it goes to, its text and when"]),
        _step("#live-dashboard-sessions", "bi-calendar-event", "Next sessions", "The sessions of the next seven days.", ["Open a class"]),
        _step("#live-dashboard-activity", "bi-activity", "The last 7 days", "What happened, day by day, part by part.", ["Open the page of a figure"]),
        _step("#live-dashboard-classes", "bi-collection-play", "Classes and weekly timetable", "Each class once, at its hours. Click a class to unfold everything about it.",
              ["Unfold a class", "Show one professor's classes", "Download the calendar file"], ["A class's professor and hours, in its parameters"]),
        _step("#live-dashboard-registrations", "bi-people", "Registrations and programs", "The newest candidates and the programs chosen the most.",
              ["Open a candidate", "Open a program"]),
        _step("#live-dashboard-feedback", "bi-chat-heart", "What candidates say", "Every feedback published on 9antra.tn, read and analysed.",
              ["Read them all", "Answer the unhappy ones first"]),
        *FRAME,
    ),
    "/candidate/all": _tour(
        "candidates", "Candidates", "Everyone who registered: what they chose, what they paid, and their next step.",
        _step(".ui-head", "bi-person-plus", "The buttons at the top", "Add a candidate by hand, or download the list.",
              ["Add a candidate", "Download the list as PDF or Excel"]),
        _step("#live-candidate-stats", "bi-bar-chart", "The figures", "How many registered, paid and still owe."),
        _step("#candidateFilters", "bi-funnel", "Filters", "Show only those who need something.", ["Filter the list", "Search a name", "Sort the cards"]),
        _step("#candidateList", "bi-person-vcard", "Every candidate", "One card each: programs, payment and the next thing to do.",
              ["Open a candidate's file", "Do their next step in one click"]),
    ),
    "/classes/": _tour(
        "classes", "Classes", "Your classes and professors: who teaches what, when, and with how many seats.",
        _step(".ui-head", "bi-plus-lg", "The buttons at the top", "Create a class for those who paid, or add a professor.",
              ["Create a class", "Add a professor"]),
        _step("#live-classes-kpis", "bi-bar-chart", "The figures", "Classes running, about to start, and seats left."),
        _step("#classesTabs", "bi-segmented-nav", "Four tabs", "Classes, professors, the week, and the decisions to take."),
        _step("#classGrid", "bi-easel2", "Every class", "One card each: its symbol, professor, hours and seats.",
              ["Open a class", "Change its parameters", "Remove an empty class"], ["Professor, days, hours, seats and start"],
              click='[data-bs-target="#tab-classes"]'),
        _step("#coverage", "bi-diagram-3", "Who teaches each program", "Programs without a teacher come first, in red.",
              ["Give a program to a professor", "Add a new professor"], click='[data-bs-target="#tab-professors"]'),
        _step(".pf-grid", "bi-person-badge", "Professors", "One card each: classes, hours, access to the application.",
              ["Give them access", "Record an absence", "Delete a professor"], ["Name, email, phone, specialities, availability"],
              click='[data-bs-target="#tab-professors"]'),
        _step("#tab-timetable", "bi-calendar-week", "The week", "Which class meets on which day, and any professor booked twice.",
              ["Download the calendar file"], click='[data-bs-target="#tab-timetable"]'),
        _step("#tab-decisions", "bi-signpost-split", "Decisions", "Candidates who wait for a class, and the choices to make.",
              ["Place a candidate", "Create the class they need"], click='[data-bs-target="#tab-decisions"]'),
        finish='[data-bs-target="#tab-classes"]',
    ),
    "/programs/all": _tour(
        "programs", "Programs", "What you teach, following 9antra.tn: details, weekly plan, price and picture.",
        _step(".ui-head", "bi-plus-lg", "The buttons at the top", "Add a program, find duplicates, or get them all as a PDF.",
              ["Add a program", "Find duplicate programs", "Download all programs as a PDF"]),
        _step("#live-program-summary", "bi-bar-chart", "The figures", "How many programs, and what is still missing."),
        _step("#programFilters", "bi-funnel", "Filters", "Show only some programs.", ["Filter the programs"]),
        _step("#programGrid", "bi-journal-bookmark", "Every program", "Picture, prices, people registered, what is missing.",
              ["Open a program", "Change its picture"]),
    ),
    "/journal": _tour(
        "journal", "The journal", "The news of each field you teach, briefly, and what it suggests for your programs.",
        _step(".jr-filters", "bi-funnel", "The fields", "DevOps, AI, cyber security, the web, Python, mobile, BI, IoT, marketing.", ["Show one field"]),
        _step("#jr-list", "bi-newspaper", "The articles", "Their title, their first sentences and the words of their field.", ["Open an article on its site"]),
        _step(".jr-side .jr-box", "bi-lightbulb", "What it suggests", "A subject much talked about that a program does not teach yet, or one to say it does.",
              ["Open the program"]),
        _step('form[action="/journal/paste"]', "bi-linkedin", "A LinkedIn post", "LinkedIn lets no application read it: paste it here.", ["Paste a post or an article"]),
    ),
    "/programs/expertise": _tour(
        "expertise", "What the assistant knows", "Program by program and week by week: what the questions and the answers come from.",
        _step("#ex-sources", "bi-diagram-3", "Where it learns from", "The course plan, the website, the professors' files and the questions."),
        _step("#ex-list", "bi-journal-bookmark", "Every program", "What waits for you first, then the least known, with a bar for each source.",
              ["Open a program week by week", "Edit its weekly plan", "Add course files"], ["The weekly plan of a program"]),
    ),
    "/programs/market": _tour(
        "market", "What employers ask", "Job offers are read and their skills counted: every percentage is a count.",
        _step("#mk-trends", "bi-graph-up-arrow", "What is asked the most", "The skills and fields the offers name the most.", optional=True),
        _step("#mk-grid", "bi-grid-3x3-gap", "Program by program", "For each program: what employers ask that it teaches, and what it does not.",
              ["Open a program's comparison"]),
        _step("#paste", "bi-clipboard-plus", "Add an offer", "Paste an offer from LinkedIn or anywhere: it is counted like the others.",
              ["Paste an offer"]),
    ),
    "/programs/documents": _tour(
        "documents", "PDF documents", "Programs and any text as clean PDFs, to download or send.",
        _step("#summary", "bi-file-earmark-text", "All programs in short", "Two or three pages for a client.", ["Download it", "Send it to someone"]),
        _step("#detail", "bi-files", "All programs in detail", "Every program with its weekly plan.", ["Download it", "Send it to someone"]),
        _step("#one", "bi-file-earmark", "One program", "One program, in the look you choose.", ["Choose the program and its look"]),
        _step("#write", "bi-pencil-square", "Any text as a PDF", "Write it here: lists, headings, bold and coloured squares.",
              ["Write a document", "Download or send it"], ["The text and its title"]),
    ),
    "/programs/videos": _tour(
        "videos", "Videos", "Every video of your programs, played inside the application.",
        _step(".vd-group", "bi-play-btn", "The videos", "Grouped by where they come from.", ["Play a video here", "Open it on its site"]),
    ),
    "/programs/upload": _tour(
        "upload", "Import a catalogue", "A PDF of your programs is read and turned into programs, for you to confirm.",
        _step("#uploadCard", "bi-cloud-arrow-up", "The file", "Choose the catalogue PDF.", ["Upload the PDF"]),
        _step("#confirmForm", "bi-check2-circle", "What was read", "Check each program read from the file before it is kept.",
              ["Confirm the programs"], ["Names, prices and weeks read from the file"]),
    ),
    "/quiz/weekly": _tour(
        "weekly", "Weekly questions", "What candidates understood each week: how often the questions go, how they answer, and every answer.",
        _step(".qz-tabs", "bi-segmented-nav", "Level checks and weekly questions", "The two kinds of questions candidates get, side by side."),
        _step("#wk-how", "bi-sliders", "How often and how", "Every week, every 2 or 4 weeks, or stopped; in the email, a link, or automatic.",
              [], ["How often the questions go", "How candidates answer them"]),
        _step("#live-weekly-kpis", "bi-bar-chart", "The figures", "Sent, answered, waiting, and the levels found."),
        _step("#wk-filters", "bi-funnel", "Filters", "Show only the answered or the waiting, or one class.", ["Filter the list"]),
        _step("#wk-list", "bi-patch-check", "Every candidate's answers", "Each question with their answer, the right one and why.",
              ["Unfold their answers", "Send their questions again"], optional=True),
    ),
    "/quiz/all": _tour(
        "levels", "Level checks", "Before a program starts, a short form tells where each candidate stands.",
        _step("#timing", "bi-clock", "When they are sent", "Seven days before the class starts, or as soon as they paid.",
              [], ["When the level check goes", "The reminder after two days"]),
        _step("#live-levels-kpis", "bi-bar-chart", "The figures", "Sent, answered, and the levels found."),
        _step("#levelFilters", "bi-funnel", "Filters", "Show only some candidates.", ["Filter the list"]),
        _step("#levelList", "bi-signpost-split", "Every level check", "Each answer one by one, the level and how they behaved.",
              ["Open a candidate's answers", "Send it again"], optional=True),
    ),
    "/inbox": _tour(
        "inbox", "Inbox", "Every message from candidates, answered by the assistant when it is sure, kept for you otherwise.",
        _step("#ways", "bi-signpost-2", "How messages go", "Email first, then WhatsApp, then SMS, or the way you choose.",
              [], ["The way messages go, and the next one when it fails"]),
        _step("#live-inbox-kpis", "bi-bar-chart", "The figures", "Waiting for you, answered alone, sent today."),
        _step("#live-inbox-list", "bi-list-ul", "The conversations", "One line per conversation; those waiting for you first.",
              ["Open a conversation", "Filter by kind"]),
        _step("#live-inbox-messages", "bi-chat-left-text", "The messages", "What was said, with what the assistant understood and why.",
              ["Read the conversation", "Score the assistant's answer"]),
        _step("#live-inbox-reply", "bi-reply", "Your answer", "A draft is ready when the assistant was not sure.",
              ["Send it", "Reword it", "Teach it as the answer for next time"], ["The text of the answer"]),
    ),
    "/inbox/compose": _tour(
        "compose", "Write a message", "One message to one person, a class, or everyone, by email, WhatsApp or SMS.",
        _step("#recipient-list", "bi-people", "Who it goes to", "Pick people, a class or a group.", ["Choose who receives it"]),
        _step("#compose-form", "bi-pencil", "The message", "Subject and text; a photo and files can be added.",
              ["Write it", "Add a photo or files", "Send it"], ["Subject, text, photo, files"]),
        _step("#compose-squares", "bi-square", "Coloured squares", "Put a sentence in a square to make it stand out.", ["Add a square"]),
        _step("#compose-cta", "bi-hand-index", "A button in the email", "A button that opens a link.", [], ["Its words and its link"]),
    ),
    "/inbox/knowledge": _tour(
        "knowledge", "What the assistant knows", "The answers you taught, and everything it read about 9antra.",
        _step("#facts", "bi-lightbulb", "Tell the assistants something", "Anything, in any language: every assistant uses it.", ["Teach it", "Change or forget it"]),
        _step("#live-knowledge-written", "bi-mortarboard", "Answers you taught", "Your own answers, used first, in your words.",
              ["Teach a new answer", "Remove an answer"], ["A question and its answer"]),
        _step("#write", "bi-pencil-square", "Teach an answer", "A question as people ask it, and the answer to give.", ["Save it"]),
        _step("#learn", "bi-link-45deg", "Learn from a link or a text", "A page or a pasted text, kept with its source.", ["Give a link", "Paste a text"]),
        _step("#kb-test-form", "bi-search", "Try a question", "See what it would answer, and from where.", ["Ask a test question"]),
    ),
    "/inbox/team": _tour(
        "team-chat", "Team chat", "Write to your professors and team, in the application, by email, WhatsApp or SMS.",
        _step("#live-team-list", "bi-people", "The people", "Professors and team members; unread ones first.", ["Open a conversation"]),
        _step("#live-team-messages", "bi-chat-left-text", "The conversation", "What was said, and the answer the assistant prepared.",
              ["Read and answer", "Remember an answer for next time"]),
        _step(".tm-write", "bi-send", "Write", "One message, sent every way you tick.", ["Write and send"], ["The ways it goes"]),
    ),
    "/inbox/templates": _tour(
        "templates", "Emails and templates", "Every email the application sends by itself, and your own templates.",
        _step("#tp-can", "bi-magic", "What you can change", "Its words, a photo, files, a button, its design, how it goes; and the original again.", [], optional=True),
        _step("#live-templates-auto", "bi-robot", "Automatic emails", "Welcome, reminders, class news, weekly questions…", ["Open one to read or change it",
              "Restore the original"]),
        _step("#template-form", "bi-pencil-square", "The email", "Its subject, text, photo and files, with a live preview.",
              ["Preview it", "Send yourself a test"], ["Subject, text, photo, files, channel"], optional=True),
        _step("#auto-box", "bi-toggle-on", "When it goes", "When it is sent, and whether it goes by itself.", [], ["On or off", "Its timing"], optional=True),
        _step("#live-templates-own", "bi-collection", "Your templates", "Texts you reuse when writing.", ["Add a template"]),
    ),
    "/payments": _tour(
        "payments", "Payments", "Who still has to pay, where their reminder stands, and what to do.",
        _step(".ui-stats", "bi-cash-coin", "The figures", "Received, still due, and how many candidates owe."),
        _step("#proof-drop", "bi-camera", "Read a cheque", "Drop a photo: who it is made out to, the amount in figures and words, the bank, the RIB, the number.",
              ["Drop a photo of a cheque or a receipt", "Choose who paid"]),
        _step("#proofs", "bi-receipt", "Payment proofs", "Receipts candidates sent, read and checked.",
              ["Confirm a payment", "Refuse a proof"]),
        _step('section.ui-card[data-live-region="payments"]', "bi-list-check", "Who still has to pay", "One line each: what they owe, their reminder, the advice.",
              ["Send the payment reminder", "Record a payment", "Write on WhatsApp"]),
    ),
    "/reminders": _tour(
        "reminders", "Reminders", "Your own reminders to a candidate, a class or everyone, now or later.",
        _step("#new", "bi-plus-lg", "New reminder", "Who (candidates or professors), what, when: once, every week, every month, or at each absence.",
              ["Write a reminder", "Use an idea"], ["Who it goes to, its text, when it goes"]),
        _step("#planned", "bi-clock-history", "Planned", "What will go, and when.", ["Cancel a reminder"]),
        _step("#sent", "bi-check2-all", "Gone", "What was sent, and to how many people."),
    ),
    "/website": _tour(
        "website", "The website", "What 9antra.tn, its Facebook and LinkedIn pages say, read every six hours.",
        _step("#ws-changes", "bi-arrow-repeat", "What changed", "What the website changed in your programs.", ["Keep yours or take the website's"]),
        _step("#formation", "bi-journal-bookmark", "Formations", "Each formation of the website and the program it matches."),
        _step("#feedback", "bi-chat-heart", "Feedback", "Every published feedback, read and analysed."),
        _step("#linkedin", "bi-linkedin", "LinkedIn", "Its posts, once its access key is given.", ["Connect the page"]),
        _step("#post", "bi-facebook", "Facebook", "Its posts, read and learnt by the assistant."),
    ),
    "/system": _tour(
        "system", "How it is working", "Everything the application does by itself, whether it works, and your data's copies.",
        _step(".sy-state", "bi-heart-pulse", "How it is doing", "Everything fine, or what needs a look."),
        _step("#live-resend", "bi-arrow-counterclockwise", "Send again", "Any automatic message, sent again when you choose.",
              ["Send a message again", "Cancel a planned one"], ["When it goes again"]),
        _step(".sy-grid", "bi-gear-wide-connected", "What it does by itself", "Each automatic task: how often, the last time, any problem.",
              ["Run a task now"]),
        _step(".sy-safe-box", "bi-shield-check", "Your data is protected", "A checked copy every day, kept two weeks.",
              ["Make a copy now", "Download a copy"], ["A second folder for the copies"]),
    ),
    "/settings": _tour(
        "settings", "Settings", "Everything you can set, with what each choice means in plain words.",
        _step("#map", "bi-map", "Everything, in pictures", "Click a picture to go to its settings."),
        _step("#in-words", "bi-chat-square-text", "Your settings in plain words", "What the application does with your choices."),
        _step("#classes", "bi-easel2", "Classes", "How classes are made, started, and given a professor.", [],
              ["Seats, start rule, a professor picked automatically", "The questions after each session"]),
        _step("#assistant", "bi-robot", "The email assistant", "When it answers alone and when it asks you.", [], ["Answer alone or always ask"]),
        _step("#rules", "bi-shield-exclamation", "What it may answer", "Subjects it must never answer alone.", [], ["The subjects to keep for you"]),
        _step("#st-stack", "bi-arrows-collapse-vertical", "Every setting in a fold", "Open a part to change it; the open ones stay open.", ["Open all", "Close all"]),
        _step("#email", "bi-envelope-at", "Email delivery", "The address the emails go from.", [], ["The address and its app password"]),
        _step("#centre", "bi-building", "Your centre", "Official name, the names a cheque is made out to, RIB, tax number, phone, address.", [],
              ["Who you are", "Where candidates pay", "How to reach you"]),
        _step("#whatsapp", "bi-whatsapp", "WhatsApp Business", "Your business number, once Meta gives its keys.", [], ["The WhatsApp keys"]),
        _step("#appearance", "bi-palette", "The design", "Colours, day or night, the menu side.", [], ["The colour and the look"]),
        _step("#language", "bi-translate", "Language", "The language of the pages.", [], ["The language"]),
    ),
    "/settings/team": _tour(
        "team", "Team and access", "Who can sign in, and what each person can see or change.",
        _step("#matrix", "bi-grid-3x3", "Who can do what", "One line per person, one column per part: one click changes a cell.",
              ["See the application as them", "Remove an account"], ["What each person can see or change"]),
        _step("#add", "bi-person-plus", "Add a person", "An admin, a team member or a professor, with their rights.",
              ["Add a person"], ["Their rights, part by part"]),
    ),
    "/settings/translations": _tour(
        "translations", "Translations", "Every text of the pages in French and Arabic, written by hand.",
        _step(".tl-tabs", "bi-translate", "The language", "French or Arabic.", ["Choose the language"]),
        _step(".tl-box", "bi-pencil", "The texts", "Each English text with its translation.", ["Search a text"], ["Your own wording, kept for good"]),
    ),
    "/data": _tour(
        "data", "All the data", "Every record of the application, to read, correct or download.",
        _step(".dt-grid", "bi-table", "The tables", "Candidates, registrations, classes, payments and more.", ["Open a table", "Download it as Excel"]),
        _step(".dt-log", "bi-journal-text", "What was changed", "Every change made here: who, when, before and after.", optional=True),
    ),
    "/find": _tour(
        "find", "Find", "Everything that matches what you typed: people, pages, settings and actions.",
        _step(".fd-list", "bi-search", "The results", "Grouped by kind; open one to go straight there.", ["Open a result"]),
    ),
    "/guide": _tour(
        "guide", "How it works", "The whole application as one journey, from a registration to a review.",
        _step(".jr-hero", "bi-signpost", "The journey", "What happens by itself and what is yours, in order."),
        _step(".jr-road", "bi-map", "Every stop", "Each step of the journey, with where to find it.", ["Open a stop's page"]),
    ),
    "/account": _tour(
        "account", "My account", "Your name, your password and where you are signed in.",
        _step(".ui-card:has(.bi-person)", "bi-person", "Who you are", "Your name and email.", [], ["Your name"]),
        _step(".ui-card:has(.bi-lock)", "bi-lock", "Your password", "Change it whenever you like.", [], ["Your password"]),
        _step(".ui-card:has(.bi-laptop)", "bi-laptop", "Where you are signed in", "Each browser signed in to your account.", ["Sign out another browser"]),
    ),
    "/account/messages": _tour(
        "my-messages", "My messages", "Your conversation with the centre, and candidates' questions about your course.",
        _step(".mm-list", "bi-chat-left-text", "The messages", "What the centre wrote, and questions waiting for your answer.", ["Read them"]),
        _step(".mm-write", "bi-send", "Write", "Your answer goes back to the person who asked.", ["Answer a question", "Write to the centre"]),
    ),
    "/teacher": _tour(
        "teacher", "Your classes", "Your classes, your next sessions, the course and the presence.",
        _step("#tc-stats", "bi-bar-chart", "Your figures", "Classes, candidates and sessions to come."),
        _step("#tc-next", "bi-calendar-event", "Your next sessions", "The two weeks to come.", ["Open a session's presence"]),
        _step(".ui-head", "bi-easel2", "Your classes", "Each class: its course, its presence, its candidates.",
              ["Add the course of the week", "Mark who was there"]),
        *FRAME,
    ),
    "/teacher/profile": _tour(
        "teacher-profile", "Your profile", "How the centre and the candidates see you.",
        _step(".tp-card", "bi-person-badge", "Your card", "Your name, specialities and how to reach you.", [], ["Your phone and email"]),
    ),
}

_PATTERNS: list[tuple[re.Pattern, dict]] = [
    (re.compile(r"^/candidate/[^/]+$"), _tour(
        "candidate", "A candidate's file", "Everything about one person: their programs, payments, learning, behaviour and messages.",
        _step("#live-candidate-hero", "bi-person-vcard", "Who they are", "Name, contact, programs and what is paid.",
              ["Edit their details", "Write to them"], ["Name, email, phone, city"]),
        _step("#live-candidate-advice", "bi-compass", "Their next step", "What they need from you now, with one button.", ["Do it in one click"]),
        _step(".cp-tabs", "bi-segmented-nav", "Their file, in tabs", "Overview, programs and payments, learning, behaviour, emails, history."),
        _step("#notes", "bi-sticky", "Your notes", "Notes only you see.", [], ["Your notes on them"], click='.cp-tab[data-tab="overview"]'),
        _step("#programs", "bi-journal-bookmark", "Programs and payments", "What they chose, what they paid, their class.",
              ["Record a payment", "Send the payment reminder", "Move them to another class"], ["The amount paid and the class"],
              click='.cp-tab[data-tab="programs"]'),
        _step("#live-candidate-learning", "bi-patch-check", "What was understood", "Their weekly answers and where they started from.",
              click='.cp-tab[data-tab="learning"]'),
        _step("#behaviour", "bi-activity", "How they behave", "A score from eight counted parameters, each explained.", click='.cp-tab[data-tab="behaviour"]'),
        _step("#live-candidate-emails", "bi-envelope", "Emails", "Every message with them, by email, WhatsApp or SMS.",
              ["Answer them"], click='.cp-tab[data-tab="emails"]'),
        finish='.cp-tab[data-tab="overview"]',
    )),
    (re.compile(r"^/candidate/[^/]+/payment-reminder$"), _tour(
        "payment-reminder", "The payment reminder", "Read and change the reminder before it goes.",
        _step(".pr-step", "bi-clock-history", "Where it stands", "What was sent before, and when."),
        _step("#reminderForm", "bi-pencil-square", "The message", "The reminder as it will be sent.", ["Send it"], ["Its subject and text"]),
    )),
    (re.compile(r"^/classes/[^/]+/course$"), _tour(
        "course", "The course", "Week by week: what was taught, the questions written from it, and the answers.",
        _step("#course-settings", "bi-sliders", "Weekly questions", "They go by themselves every week; how many each candidate gets.",
              [], ["How many questions each candidate gets"]),
        _step("#live-course-weeks", "bi-list-ol", "The weeks", "Pick a week; each one says what it holds.", ["Choose a week"]),
        _step("#uploadForm", "bi-folder-plus", "Add the course", "PDF, slides, video, photo or a few lines: questions are written from it.",
              ["Upload files", "Write notes about the week"]),
        _step("#questions", "bi-patch-question", "Questions", "Written from the course and checked twice; yours too.",
              ["Write the questions", "Add your own", "Approve or remove one"], ["Each question and its answer"]),
        _step("#send", "bi-send-check", "Candidates' answers", "Who answered, what was understood, how the week went.",
              ["Send the week's questions now", "Remind those who did not answer"]),
    )),
    (re.compile(r"^/classes/[^/]+/attendance$"), _tour(
        "presence", "Presence", "Who was there at each session, and how they took part.",
        _step("#meeting", "bi-camera-video", "The meeting", "A Meet or Zoom link, or a private room; each candidate gets their own link.",
              ["Send everyone their link"], ["The meeting's address"]),
        _step("#presenceForm", "bi-person-check", "Who was there", "Everyone starts present: change only the late or absent.",
              ["Mark present, late or absent", "Note participation and questions", "Save"]),
        _step("#live-session-check", "bi-patch-check", "After the session", "What each candidate answered about the session.", optional=True),
    )),
    (re.compile(r"^/classes/[^/]+$"), _tour(
        "class", "A class", "One class: its candidates, its course, its presence and its settings.",
        _step("#live-class-hero", "bi-easel2", "The class", "Program, professor, hours, seats and where it stands.",
              ["Start or finish it", "Send a reminder"]),
        _step("#dynamics", "bi-activity", "How it is doing", "Presence, participation and answers: who leads, who needs a word."),
        _step("#live-class-members", "bi-people", "Candidates", "Everyone in the class.", ["Open a candidate", "Move one to another class"]),
        _step("#live-class-course", "bi-folder2-open", "Course and weekly questions", "What was taught and understood each week.", ["Open the course page"]),
        _step("#live-class-presence", "bi-person-check", "Presence", "Who came to each session.", ["Mark who was there"]),
        _step("#plan", "bi-list-ol", "Weekly plan", "The subject of each week.", [], ["The topic of a week"]),
        _step("#parameters", "bi-sliders", "Class parameters", "Everything about the class, in one form.", [],
              ["Name, professor, seats, weeks", "Days, hours, room, start"]),
    )),
    (re.compile(r"^/programs/[^/]+/pdf$"), _tour(
        "program-pdf", "The program as a PDF", "One program in the look you choose, to download or send.",
        _step(".pf-grid", "bi-palette", "The look", "Brochure, classic or one page; French or English.", [], ["The look and the language"]),
        _step(".pf-view", "bi-eye", "The preview", "The PDF as it will be.", ["Download it"]),
        _step("#send", "bi-send", "Send it", "To a candidate, with the PDF attached.", ["Send it to a candidate"]),
    )),
    (re.compile(r"^/programs/[^/]+$"), _tour(
        "program", "A program", "One program: what it teaches week by week, what learners said, and what employers ask.",
        _step(".pd-hero", "bi-journal-bookmark", "The program", "Its name, prices, duration and picture.",
              ["Edit the program", "Download it as a PDF"], ["Name, prices, duration, picture"]),
        _step(".pd-facts", "bi-info-circle", "The facts", "Duration, weeks, hours, prices: what is missing shows."),
        _step(".pd-cols", "bi-list-ol", "Week by week", "What each week teaches, as the website says it.", [], ["The weekly plan"]),
        _step("#reviews", "bi-star", "What learners said", "Their reviews, analysed."),
        _step("#market", "bi-briefcase", "What employers ask", "The skills job offers ask, and what to add.", ["See the offers"]),
    )),
    (re.compile(r"^/data/[^/]+/[^/]+$"), _tour(
        "data-row", "One record", "Every value of one record, to correct.",
        _step(".dt-two", "bi-pencil", "Its values", "Each field with its value.", ["Save", "Delete the record"], ["Any value"]),
        _step(".dt-log", "bi-journal-text", "Its changes", "Who changed what, and when."),
    )),
    (re.compile(r"^/data/[^/]+$"), _tour(
        "data-table", "A table", "Every record of one kind.",
        _step(".ui-actions", "bi-download", "The buttons", "Add a record, or download the table.", ["Add a record", "Download it as Excel"]),
        _step(".dt-wrap", "bi-table", "The records", "Click a line to open it.", ["Open a record", "Search"]),
    )),
]


def for_path(path: str) -> dict | None:
    """The tour of the page at this address, when it has one."""
    found = TOURS.get(path) or TOURS.get(path.rstrip("/")) or TOURS.get(path.rstrip("/") + "/")
    if found:
        return found
    for pattern, tour in _PATTERNS:
        if pattern.match(path):
            return tour
    return None


def every() -> list[tuple[str, dict]]:
    """Every tour with an address to show (patterns as a sample address), for the assistant's knowledge."""
    found = list(TOURS.items())
    samples = {"candidate": "/candidate/…", "payment-reminder": "/candidate/…/payment-reminder", "course": "/classes/…/course",
               "presence": "/classes/…/attendance", "class": "/classes/…", "program-pdf": "/programs/…/pdf", "program": "/programs/…",
               "data-row": "/data/…/…", "data-table": "/data/…"}
    found += [(samples.get(tour["key"], tour["key"]), tour) for _, tour in _PATTERNS]
    return found


# The words of the tour itself, in the page's language too.
LABELS = {
    "map": "This page, in a picture", "parts": "parts on this page", "start": "Show me each part", "later": "Later",
    "close": "Close", "next": "Next", "back": "Back", "finish": "Done", "can": "You can", "change": "You can change",
    "hint_title": "First time on this page?", "hint_text": "See what you can do here, part by part.", "hint_go": "Show me",
    "again": "Press ? at the top to see it again.",
}
_words: dict[tuple[str, str], dict] = {}


def words_for(db, lang: str, version: str) -> dict:
    """The hand-made translations of the interface for this language, kept while they do not change."""
    if lang == "en":
        return {}
    key = (lang, version or "")
    if key not in _words:
        from app.services import translations

        _words.clear()
        _words[key] = translations.dictionary(db, lang)["t"]
    return _words[key]


def translated(tour: dict | None, lang: str, words: dict) -> dict | None:
    """The tour in the page's language (the hand-made translations of the interface), ready for the browser."""
    if tour is None:
        return None
    say = (lambda text: words.get(text, text)) if lang != "en" else (lambda text: text)
    return {
        "key": tour["key"], "title": say(tour["title"]), "purpose": say(tour["purpose"]), "finish": tour["finish"],
        "labels": {name: say(text) for name, text in LABELS.items()},
        "steps": [{**step, "name": say(step["name"]), "what": say(step["what"]),
                   "do": [say(item) for item in step["do"]], "change": [say(item) for item in step["change"]]}
                  for step in tour["steps"]],
    }


def knowledge_line(address: str, tour: dict) -> str:
    """The tour as a paragraph the assistant can answer from (“what can I do on the payments page?”)."""
    parts = [f"{tour['title']} ({address}): {tour['purpose']}"]
    for step in tour["steps"]:
        line = f"- {step['name']}: {step['what']}"
        if step["do"]:
            line += " Do: " + "; ".join(step["do"]) + "."
        if step["change"]:
            line += " Change: " + "; ".join(step["change"]) + "."
        parts.append(line)
    return "\n".join(parts)

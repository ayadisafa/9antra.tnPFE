"""The guide: how to do anything in the application, step by step, for the person who asks (admin, team member,
professor), and what the courses teach. Used by Yasmine for everyone and by the assistant for "how do I…" questions.

Written by hand: each task has the words that ask for it, the steps, the page to open, and who may do it. The courses
are answered from the programs' own weeks and 9antra.tn (chatbot_sources.program_content), what 9antra is from
chatbot_sources.about. Nothing about the candidates' records is said here: that is the assistant's, for those allowed.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session

ADMIN, STAFF, PROFESSOR = "admin", "staff", "professor"
EVERYONE = (ADMIN, STAFF, PROFESSOR)

# (key, words that ask for it, title, steps, page, who, part of the application a team member needs)
TASKS = [
    ("email-setup", r"(connect|set ?up|configure|configurer|brancher|parametr\w*).*(e-?mail|gmail|mail)|(e-?mail|gmail|mail).*(connect|set ?up|configur|password|mot de passe)|app password",
     "Connect your Gmail so the application sends and reads emails",
     ["In your Google account, turn on 2-step verification (Security).", "Create an “app password” (Security › App passwords): 16 letters.",
      "Settings › Email delivery: write your Gmail address and that app password, then Save: it is checked with Gmail at once.",
      "The mailbox is then read every minute; candidates' emails arrive in the Inbox."], "/settings#email", (ADMIN,), None),
    ("whatsapp-setup", r"(connect|set ?up|configure|configurer|brancher|business).*whats ?app|whats ?app.*(connect|set ?up|configur|business|token|meta)",
     "Connect a WhatsApp Business number",
     ["On Meta for Developers, create an app of type Business and add WhatsApp.", "WhatsApp › API Setup: add your business number and copy its Phone number ID.",
      "Business settings › System users: create one, give it the app, and generate a permanent token.", "App settings › Basic: copy the App secret.",
      "Settings › WhatsApp Business: write the four values, Save, then “Test the connection”.",
      "Webhook: paste the callback address and the verify token shown in the card, then subscribe to “messages”."], "/settings#whatsapp", (ADMIN,), None),
    ("sms-setup", r"(connect|set ?up|configure|configurer|brancher|receive|recevoir|track|suivre).*sms|sms.*(connect|set ?up|configur|phone|android|gateway|track|suivre|recevoir)",
     "Send and receive SMS from your Android phone",
     ["On an Android phone with a SIM, install the free app “SMS Gateway for Android” (sms-gate.app).", "In the app, switch on Local server: note its address, username and password.",
      "In its Settings › Webhooks, copy the signing key.", "Settings › SMS from your Android phone: write the address, username, password and key, Save, then “Test the phone”.",
      "Add a webhook for sms:received pointing at the address shown in the card.",
      "Candidates' SMS then arrive in the Inbox (filter “SMS”) and are answered like WhatsApp messages."], "/settings#sms", (ADMIN,), None),
    ("ways", r"(which|choose|choisir).*(way|channel|canal)|messages? go by|par (email|whatsapp|sms) ou|fallback|next way",
     "Choose how messages go (email, WhatsApp, SMS)",
     ["Open the Inbox.", "At the top, “Messages go by”: Let the system choose, Email, WhatsApp or SMS.",
      "Tick “If it does not work, the next way” so a failed one tries the next."], "/inbox#ways", (ADMIN, STAFF), "inbox"),
    ("team-add", r"(add|ajouter|create|creer).*(person|user|team member|membre|utilisateur|staff|admin|account|compte)|give (him|her|them) access|grant",
     "Add someone to the team and choose what they can do",
     ["Settings › Team and access › Add a person.", "Choose: team member, admin, or a professor's own account.",
      "For a team member: for each part, Not shown / Can look / Can change; tick special rights, or “Add with access to everything”.",
      "Give them their first password yourself."], "/settings/team#add", (ADMIN,), "team"),
    ("view-as", r"(see|view|look|voir).*(as|comme|en tant que).*(professor|prof|team|staff|member|membre|formateur|user)|how (do|does) (they|the professor) see",
     "See the application as a professor or a team member",
     ["Settings › Team and access.", "On their card: “See the application as …”.", "You see exactly their menu and pages; nothing can be changed.",
      "The yellow bar at the top: “Change what they can do”, or “Back to my view”."], "/settings/team", (ADMIN,), None),
    ("professor-add", r"(add|ajouter|new|nouveau).*(professor|prof|formateur|teacher)|professors? per program|who teaches|no teacher|sans (prof|formateur)|pas de (prof|formateur)",
     "Add a professor to a program",
     ["Classes › Professors › “Who teaches each program”.", "“Also teaches it” for someone who already works with you, or “A new professor” (name, email, phone): as many as you like.",
      "The programs nobody teaches yet come first, in red: they cannot open a class until someone teaches them."], "/classes/?tab=professors#coverage", (ADMIN, STAFF), "classes"),
    ("resources-remind", r"remind\w* (the )?professors?|rappel\w* (aux )?(profs?|formateurs?)|ressources? (des|du) (profs?|formateurs?)|resources? reminder",
     "Remind professors to add their resources",
     ["Classes › Professors › “Remind professors to add their resources”.", "Choose how many days before the week, and write your own words.",
      "“Save and send the ones due now” sends the reminders due today."], "/classes/?tab=professors#resources", (ADMIN, STAFF), "classes"),
    ("resources-add", r"(add|put|upload|ajouter|mettre|deposer|téléverser).*(resources?|ressources?|slides?|support|pdf|video|vidéo|exercises?|exercices?|materials?)",
     "Add the resources of a week (professors)",
     ["My classes › open the class › Course.", "Choose the week, then add the files: slides, PDF, video, photos, with a note.",
      "Candidates find them for that week; the assistant also learns from them."], "/teacher", (PROFESSOR, ADMIN), None),
    ("presence", r"(mark|take|noter|faire).*(presence|attendance|absen\w*|appel)",
     "Mark who was there (professors)",
     ["My classes › open the class › Presence.", "For each candidate: present, late or absent; add how they took part and the questions they asked.",
      "Save: the candidate's file and the class's dynamics are updated."], "/teacher", (PROFESSOR, ADMIN), None),
    ("answer-candidate", r"(answer|reply|repondre|répondre).*(candidate|candidat|question|student|etudiant)",
     "Answer a candidate",
     ["Inbox › the discussion › write in the answer box (a few words are enough, even in Tunisian).",
      "Choose “in French / in English” and press Reformulate, or tick “Reword and send at once”.", "Professors: Messages › the candidate's question › Answer the candidate."],
     "/inbox", EVERYONE, None),
    ("send-message", r"(send|envoyer|envoie).*(email|whatsapp|sms|message).*(to|a|à)|assistant.*(send|envoyer)",
     "Send a message with the assistant",
     ["Open the assistant (the round button).", "Write for example: “envoie un email à Fares pour lui dire que la séance de samedi est annulée”.",
      "Say “whatsapp” or “sms” to choose the way; “tel quel” keeps your words.", "It tells you for each person what went, and why not.",
      "“Send at once” in its header turns automatic sending on or off."], "/", (ADMIN, STAFF), "assistant"),
    ("team-chat", r"team chat|(write|message|ecrire).*(professor|team|equipe|formateur)",
     "Write to a professor or the team",
     ["Inbox › Team (or Team chat in the menu).", "Choose the person, write once, tick the ways: in the application, email, WhatsApp, SMS.",
      "Their answers come back into the same conversation."], "/inbox/team", (ADMIN, STAFF), "team_chat"),
    ("proofs", r"payment proofs?|preuves? de paiement|receipts?|recus?|factures?|virements?|confirm (a )?payment",
     "Check and confirm a payment proof",
     ["Payments › “Payment proofs to check”.", "Look at the picture and each check (amount, RIB, bank, date, sent twice).",
      "Confirm (choose the registration and the amount) to record the payment, or Refuse."], "/payments#proofs", (ADMIN, STAFF), "payments"),
    ("rib", r"\brib\b|bank account|compte bancaire", "Write the centre's RIB",
     ["Settings › WhatsApp Business card › The centre's numbers.", "Write the RIB (20 digits): its key is checked; payment proofs are compared with it."],
     "/settings#whatsapp", (ADMIN,), None),
    ("welcome", r"welcome|bienvenue", "Change the welcome message",
     ["Inbox › Email templates › “Welcome, just registered”.", "Change its words; it goes once, as soon as someone registers."], "/inbox/templates?key=welcome", (ADMIN, STAFF), "inbox"),
    ("payment-reminder", r"payment reminder|rappel de paiement|relance (de )?paiement", "Change the payment reminder",
     ["Inbox › Email templates › Payment reminder.", "Change its words and “Goes … hours after registering”."], "/inbox/templates?key=payment_reminder", (ADMIN, STAFF), "inbox"),
    ("level-timing", r"level check|test de niveau", "Choose when the level check goes",
     ["Level checks › “It goes … days before the first session” (or as soon as they pay).", "One reminder goes if they do not answer."], "/quiz/all#timing", (ADMIN, STAFF), "levels"),
    ("reviews", r"reviews?|avis|feedback|opinion", "See and ask for reviews",
     ["They are asked by themselves when a class finishes.", "Program page › “What learners said”; the candidate's file › Their reviews › Ask for their review."],
     "/programs/all", (ADMIN, STAFF), "programs"),
    ("recommendations", r"recommend\w*|market|marche|what to add|que (faut il )?ajouter", "Read the recommendations of a program",
     ["Programs › open a program › Recommendations (renewed every 24 hours).", "Each line shows its numbers and sources; “Read again now” refreshes them."],
     "/programs/all", (ADMIN, STAFF), "programs"),
    ("catalogue", r"import|catalogue|catalog|planning courses|pdf des formations", "Import the programs' catalogue",
     ["Programs › Import a PDF.", "Choose the catalogue (Planning Courses): each program is read here, with prices, hours and weeks.",
      "Check each one, then save."], "/programs/upload", (ADMIN, STAFF), "programs"),
    ("videos", r"videos?|vidéos?", "Watch the programs' videos", ["Programs › Videos: every week's video, played here."], "/programs/videos", EVERYONE, None),
    ("phone", r"iphone|android|home screen|ecran d accueil|mobile|telephone|téléphone", "Use it on your phone",
     ["How it is working › On your phone: start it for the phone, then open the address on the same Wi-Fi.",
      "iPhone: Safari › Share › Add to Home Screen. Android: Chrome menu › Add to Home screen."], "/system#phone", EVERYONE, None),
    ("automatic", r"automatic\w*|by itself|tout seul|automatiquement", "See everything the application does by itself",
     ["How it is working › Everything it does for you, by itself: each one, when, whether it is on, and where to change it."], "/system#automatic", (ADMIN,), None),
    ("language", r"language|langue|arabic|arabe|french|francais|english|anglais|translate|traduire", "Change the language of the pages",
     ["The translate button at the top › Français, English or العربية (on this browser)."], "/", EVERYONE, None),
    ("password", r"password|mot de passe", "Change your password", ["Your account (Password) › write the old one, then the new one twice."], "/account", EVERYONE, None),
]

# The rest of the application (added 2026-10-21): every everyday task, step by step.
TASKS += [
    ("candidate-add", r"(add|ajouter|new|nouveau|create|creer|enter|saisir).*(candidat\w*|student|etudiant|learner|apprenant)",
     "Add a candidate",
     ["Candidates › “Add a candidate” (or Ctrl+K, “add a candidate”).", "Write the name, the email and the phone (+216 is added by itself), and the program if known.",
      "Save: the welcome message goes at once, and the payment reminder 24 hours later if nothing is paid."], "/candidate/all?add=1", (ADMIN, STAFF), "candidates"),
    ("payment-record", r"(record|enregistrer|mark|noter|saisir|add|ajouter).*(payment|paiement|paid|paye)|(has|a) paid|a paye|payment of a candidate",
     "Record a payment",
     ["Candidates › open the candidate › Programs & payments.", "On the program: “Record the full payment”, or write the amount paid for a part.",
      "Paid in full: a confirmation email goes and the candidate is placed in a class of the program."], "/candidate/all", (ADMIN, STAFF), "candidates"),
    ("class-create", r"(create|creer|new|nouvelle?|open|ouvrir|add|ajouter|make|faire).*(class|classe|groupe?)",
     "Create a class",
     ["Usually nothing to do: a class is created by itself when a candidate pays and no class of their program is open. By hand, it is only possible when paid candidates are waiting for one.",
      "Classes › “Create a class”.", "Choose the program, then a professor of its speciality (or let the system pick), the start rule, the days and the hours.",
      "Create: paid candidates waiting for that program are placed in it."], "/classes/", (ADMIN, STAFF), "classes"),
    ("class-time", r"(time|hours?|heures?|horaire|schedule|days?|jours?|planning).*(class|classe|sessions?|seances?)|(class|classe).*(time|hours?|heures?|horaire|schedule)",
     "Set the days and hours of a class",
     ["Classes › open the class › “Class parameters”.", "Choose the days, the start and end hours, and the room or meeting link.",
      "Save: the sessions follow, and enrolled candidates are emailed when the date or the hours change."], "/classes/", (ADMIN, STAFF), "classes"),
    ("class-move", r"(move|deplacer|transfer\w*|switch|changer de).*(class|classe)|(class|classe).*(move|deplac\w*|changer)",
     "Move a candidate to another class",
     ["Classes › open the class › Everyone enrolled.", "Select the candidate › “Change class” › choose the class › Move.",
      "The candidate gets an email with the new class, its date and its hours."], "/classes/", (ADMIN, STAFF), "classes"),
    ("program-add", r"(add|ajouter|create|creer|new|nouvelle?).*(program\w*|formation|course|training|bootcamp)",
     "Add a program",
     ["Programs › “Add a program”: only the name is needed.", "The assistant proposes the description, the hours and the weekly plan; a picture is found by itself.",
      "Read the proposal on the program's card and accept it: nothing is saved before."], "/programs/all", (ADMIN, STAFF), "programs"),
    ("program-edit", r"(change|changer|modif\w*|edit|update|mettre a jour|corriger).*(price|prix|tarif|hours?|heures?|weeks?|semaines?|duration|duree|description|plan)|(price|prix|tarif).*(program|formation)",
     "Change a program's price, hours or weeks",
     ["Programs › open the program.", "“Every value of this program” changes the price, the hours, the description; the weekly plan is edited week by week.",
      "Save: the PDFs, the emails and the assistant use the new values at once."], "/programs/all", (ADMIN, STAFF), "programs"),
    ("program-pdf", r"(pdf|brochure|catalogue).*(program|formation)|(program|formation).*(pdf|brochure)|send (the )?(program|formation)",
     "Send a program as a PDF",
     ["Programs › open the program › PDF (or the PDF documents page for all of them).", "Choose one of the three looks and the language, then “Send it to a candidate”. On a candidate's page, “Send the program (PDF)” does it in one click.",
      "Or ask the assistant: “send the DevOps program to Fares”; with “Send at once” on, it goes with the PDF attached."], "/programs/documents", (ADMIN, STAFF), "programs"),
    ("email-write", r"(write|ecrire|send|envoyer|envoie).*(email|e-mail|mail).*(candidat\w*|class|classe|everyone|tout le monde|all|tous|program)",
     "Write an email to a candidate, a class or everyone",
     ["Inbox › Write (or “Write an email” on the dashboard).", "Choose who receives it: one candidate, a class, a program, those who owe, or everyone.",
      "Write it, start from a template, or let the assistant write a draft; check the preview, then Send."], "/inbox/compose", (ADMIN, STAFF), "inbox"),
    ("reminder-plan", r"(plan|schedule|programmer|create|creer|add|ajouter|new|nouveau|own|propre).*(reminder|rappel)",
     "Plan a reminder of your own",
     ["Reminders › choose who: a candidate, a class, a program, those who owe, or everyone.", "Write it, or start from one of the ideas; add coloured squares for what must be seen.",
      "Choose the day and the hour: it goes once, by itself."], "/reminders", (ADMIN, STAFF), "reminders"),
    ("level-results", r"(see|voir|read|lire|results?|resultats?|answers?|reponses?).*(level|niveau)|(level|niveau).*(results?|resultats?|answers?|reponses?)",
     "Read a candidate's level check",
     ["Level checks › open the candidate: their answers one by one, and their level topic by topic.",
      "The class page shows what the whole group already knows."], "/quiz/all", (ADMIN, STAFF, PROFESSOR), "levels"),
    ("weekly-questions", r"weekly questions?|questions? (de la |of the )?(semaine|week)|questions? hebdo\w*",
     "Send the questions of the week",
     ["Classes › open the class › Course › the week.", "Add the week's files or a few lines of notes, then “Write the questions”.",
      "Check them, then “Send week …”; or set the class to send them by itself after each week."], "/classes/", (ADMIN, STAFF, PROFESSOR), "classes"),
    ("analysis", r"analys\w*|report|rapport|progress\w*|statisti\w*|how (is|are) (we|the centre) doing",
     "Read the analysis and the progress report",
     ["Dashboard › “Analysis: what to improve” and “Candidates' progress”: each opens as a PDF.",
      "Settings › Report by email sends them to you every week, every 10 days or as you choose."], "/", (ADMIN,), None),
    ("export", r"excel|export\w*|csv|download (the )?list|telecharger (la )?liste",
     "Export a list to Excel",
     ["All the data › choose the kind of record (candidates, payments, classes…).", "Press “Excel”: the file opens in Excel with every value."],
     "/data", (ADMIN,), None),
    ("edit-data", r"(change|edit|modif\w*|correct\w*|corriger).*(any|value|valeur|record|fiche|data|donnee\w*)|all the data|toutes les donnees",
     "Change any value",
     ["All the data › choose the kind of record › click the line.", "Change any value and save: who changed what, and what it was, is kept in its history."],
     "/data", (ADMIN,), None),
    ("backup", r"backup|sauvegard\w*|copy of the data|copie des donnees|restore|restaurer",
     "Make or bring back a copy of the data",
     ["How it is working › Your data is safe › “Copy now” (a copy is also made every day).", "Add a second folder on another disk for the copies.",
      "To bring one back: stop the application, put the copy's app.db and data folder in place, start it again."], "/system#safety", (ADMIN,), None),
    ("design", r"colou?rs?|couleurs?|theme|design|night|nuit|dark|sombre|menu (side|cote)|background|fond",
     "Change the colours, the menu side, day or night",
     ["Settings › Colours: the main colour, the menu on the left or the right, day or night, a still or moving background.", "Save: every page takes it at once."],
     "/settings#look", (ADMIN,), None),
    ("duplicates", r"duplicat\w*|doublons?|merge|fusionn\w*",
     "Find and merge duplicate programs",
     ["Programs › “Find duplicates” › Scan.", "For each pair, compare, then merge into the one to keep: its candidates, classes and weekly plan move with it."],
     "/programs/all", (ADMIN, STAFF), "programs"),
    ("find", r"\bfind\b|search|chercher|trouver|recherche|ctrl ?k|where is|ou est",
     "Find anything quickly",
     ["The search box at the top, or Ctrl+K on any page.", "Type a name, an email, a page, a setting or what you want to do, and open the line."],
     "/find", EVERYONE, None),
    ("professor-absence", r"absen\w*.*(professor|prof|formateur|teacher)|(professor|prof|formateur|teacher).*absen\w*",
     "Record a professor's absence",
     ["Classes › Professors › the professor's card › Absence.", "Choose the dates (and the hours if not the whole day) and the reason, then save.",
      "Their classes on those days show it, and a replacement can be chosen."], "/classes/?tab=professors", (ADMIN, STAFF), "classes"),
    ("professor-access", r"(access|acces|account|compte|sign in|connexion|login|password).*(professor|prof|formateur|teacher)|(professor|prof|formateur|teacher).*(access|acces|account|compte|sign in|login)",
     "Give a professor their own access",
     ["Classes › Professors › the professor's card › Access.", "Write the email they sign in with and a first password, then give it to them yourself.",
      "They then see only their classes: the course, the presence and their messages."], "/classes/?tab=professors", (ADMIN,), None),
    ("professor-delete", r"(delete|remove|supprim\w*|retir\w*|eff?ac\w*|enlev\w*|fasa5\w*|fasakh\w*|na7i|nahi)\s+(\w+\s+){0,3}(professors?|profs?|formateurs?|teachers?|trainers?)\b",
     "Delete a professor",
     ["Classes › Professors › the professor's card › the bin button.",
      "For each of their classes, choose who teaches it now (only professors who teach its program and are free at its hours are offered), or let it wait for one.",
      "“Delete for good”: their access, absences and team conversation go with them; finished classes keep their name."],
     "/classes/?tab=professors", (ADMIN, STAFF), "classes"),
    ("knowledge-teach", r"(teach|apprendre|learn).*(assistant|answer|reponse|system)|knowledge|connaissances?|from a link|from a text|d un lien",
     "Teach the assistant an answer, a page or a text",
     ["Inbox › Knowledge.", "“Teach a new answer”: a question and its answer, in your words.",
      "“Learn from a link or a text”: give a page (9antra.tn, Google, LinkedIn, Facebook…) or paste its text; it is used with its source."],
     "/inbox/knowledge", (ADMIN, STAFF), "inbox"),
    ("assistant-rules", r"(never|jamais|rules?|regles?|interdit\w*).*(assistant|answer|repond\w*)|what (the assistant|it) (may|can) answer",
     "Tell the assistant what it must never answer alone",
     ["Settings › Assistant's rules.", "One subject per line in “Never answer these alone” (a discount, a refund…): such messages come to you.",
      "“What it should know or say”: one idea per line, given to it each time it writes."], "/settings#rules", (ADMIN,), None),
    ("facebook", r"facebook|page token|meta page",
     "Connect the Facebook page (posts, videos, reviews)",
     ["Only an admin of the page can create its access token: Meta for Developers › your app › “Page access token”.",
      "Write it as FACEBOOK_PAGE_ACCESS_TOKEN in the .env file, then restart the application.",
      "The posts, videos and reviews are then read with the website every six hours, and the assistant knows them."], "/website", (ADMIN,), None),
    ("website", r"website|site web|9antra\.tn|read the site|lire le site",
     "Bring in what changed on 9antra.tn",
     ["Website › “Read the website now” (it is also read every six hours).", "Where your program says something else, choose yours or the website's."],
     "/website", (ADMIN, STAFF), "website"),
    ("notifications", r"notifications?|bell|cloche",
     "Use the notifications",
     ["The bell at the top: each notification opens the exact place to look, highlighted.", "Some carry a button that solves it at once; “Mark all as read” clears the bell."],
     "/", (ADMIN,), None),
    ("translations-fix", r"(correct|corriger|fix|change|changer|modif\w*).*(translations?|traductions?)",
     "Correct a translation",
     ["Settings › Language › “Read and correct the translations”.", "Search the text, write your own wording and save: it is never changed again."],
     "/settings/translations", (ADMIN,), None),
    ("session-link", r"(session|seance|class|classe).*(link|lien|meet|zoom|teams|room|salle)|(link|lien|meet|zoom).*(session|seance)",
     "Set the meeting of a session",
     ["Open the class › Presence › the session.", "Paste the address of your meeting (Meet, Zoom, Teams), or let a private room be created.",
      "“Send each candidate their own link”: each one enters with it, and their arrival is noted."], "/classes/", (ADMIN, STAFF, PROFESSOR), "classes"),
]

# The specific ones are looked at before the general ones (“a professor's absence” before “presence”).
_ORDER = ["missing", "professor-absence", "professor-access", "professor-delete", "translations-fix", "duplicates", "level-results", "email-write", "candidate-add",
          "payment-record", "class-move", "class-time", "session-link", "program-pdf", "program-edit", "reminder-plan", "weekly-questions"]
TASKS.sort(key=lambda task: _ORDER.index(task[0]) if task[0] in _ORDER else len(_ORDER))


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9@ ]+", " ", text)


def _role(user) -> str:
    if user is None or getattr(user, "is_admin", False):
        return ADMIN
    return PROFESSOR if getattr(user, "is_professor", False) else STAFF


def _may(user, who, part) -> bool:
    role = _role(user)
    if role not in who:
        return False
    if role == STAFF and part:
        return (getattr(user, "permissions", None) or {}).get(part) in ("view", "edit")
    return True


from app.services.howto_tn import REGISTER as _REGISTER, SAY as _SAY        # the words around the steps, in each language
_FRENCH_WORDS = re.compile(r"\b(comment|ou|quoi|est-ce|je|veux|faire|ajouter|voir|bonjour|quel|quelle|les|des|une|pour|qu)\b")


def language_of(message: str) -> str:
    """The language of the question: en, fr, ar (Arabic letters) or tn (Tunisian in Latin letters)."""
    from app.services import derja

    if derja.has_arabic_letters(message):
        return "ar"
    plain = _plain(message)
    if derja.is_tunisian(message) or (derja.words_in(message) and not _FRENCH_WORDS.search(plain)
                                       and not re.search(r"\b(how|what|where|the|do|can|i|my)\b", plain)):
        return "tn"
    return "fr" if _FRENCH_WORDS.search(plain) else "en"


def understood(message: str) -> str:
    """The question, with its Tunisian or Arabic words also written in French: what the rules read."""
    from app.services import derja

    return f"{message} {derja.to_french(message)}" if derja.words_in(message) else message


def _words(text: str, lang: str) -> str:
    """One of the guide's sentences in French or Arabic (the hand-made translations of the interface)."""
    if lang in ("fr", "ar"):
        from app.services.translation_seed import MORE, SEED

        pair = MORE.get(text) or SEED.get(text)
        if pair:
            return pair[0 if lang == "fr" else 1]
        core = text.strip(" ·:,;()[]–-|")              # kept as the pages keep it: “Mark who was there (professors”
        pair = MORE.get(core) or SEED.get(core) if core else None
        if pair:
            start = text.index(core)
            return text[:start] + pair[0 if lang == "fr" else 1] + text[start + len(core):]
    return text


def in_language(task: dict, lang: str) -> tuple[str, list[str]]:
    """The title and the steps of a task in the language asked."""
    if lang == "tn":
        from app.services.howto_tn import TN

        if task["key"] in TN:
            return TN[task["key"]]
        lang = "fr"
    return _words(task["title"], lang), [_words(step, lang) for step in task["steps"]]


def as_steps(title: str, steps: list[str], page: str, lang: str, source: str = "The application's guide") -> dict:
    """A how-to as the guide gives it: the text (for reading aloud and the conversation), and the diagram."""
    take = _SAY[lang]["take"]
    return {"reply": "\n".join([title + ":"] + [f"{number}. {step}" for number, step in enumerate(steps, start=1)]),
            "links": [{"label": take, "url": page}], "source": "guide", "lang": lang,
            "schema": {"title": title, "steps": steps, "url": page, "label": take},
            "sources": [{"label": source, "url": page}]}


# “nsajjel khlas”, “أسجل دفع”: recording a payment (the same verb also means “to register”).
_RECORD_PAYMENT = re.compile(r"\b(n?saj+e?l|sajjel|nsejjel)\b.{0,25}\b(khlas|5las|khal+as|khal+es|flous|paiement|payment|dfou3)\b"
                             r"|(أسجل|اسجل|نسجل|سجل|سجّل)\s+(ال)?(دفع|خلاص|دفعة|الدفعة)")


_BANK: dict[str, list[str]] = {}


def bank() -> dict[str, list[str]]:
    """How each task is asked: its title in the four languages and the examples written by hand (howto_examples.py)."""
    if not _BANK:
        from app.services.howto_examples import EXAMPLES
        from app.services.howto_tn import TN

        for key, words, title, steps, page, who, part in TASKS:
            said = [title, _words(title, "fr"), _words(title, "ar")] + ([TN[key][0]] if key in TN else []) + EXAMPLES.get(key, [])
            _BANK[key] = list(dict.fromkeys(text for text in said if text))
    return _BANK


def meaning_task(text: str, user=None, *, margin: float = 0.05) -> dict | None:
    """The task closest in meaning to a question the word rules did not recognise (services/meaning.py), when it is
    clearly the closest and this person may do it."""
    from app.services import meaning

    found = meaning.closest(text, bank(), floor=0.35, margin=margin)
    task = next((item for item in TASKS if found and item[0] == found["key"]), None)
    if task is None or not _may(user, task[5], task[6]):
        return None
    return {"key": task[0], "title": task[2], "steps": task[3], "page": task[4], "meaning": found}


def task_for(text: str, user=None, *, by_meaning: bool = True) -> dict | None:
    if _RECORD_PAYMENT.search(text or "") or _RECORD_PAYMENT.search(_plain(text)):
        found = next((task for task in TASKS if task[0] == "payment-record"), None)
        if found and _may(user, found[5], found[6]):
            return {"key": found[0], "title": found[2], "steps": found[3], "page": found[4]}
    plain = _plain(understood(text))
    by_words = next(({"key": key, "title": title, "steps": steps, "page": page} for key, words, title, steps, page, who, part in TASKS
                     if re.search(words, plain, re.I) and _may(user, who, part)), None)
    if not by_meaning:
        return by_words
    by_meaning = meaning_task(text, user)
    if by_words is None:
        return by_meaning
    # The word rules pick a task by one word (“site … mis à jour” is not about payment proofs): when the meaning says
    # clearly another task, it wins (measured on 2026-10-22: right in each of the four cases where they differed this much).
    if by_meaning and by_meaning["key"] != by_words["key"] and by_meaning["meaning"]["score"] >= 0.6 \
            and by_meaning["meaning"]["score"] - by_meaning["meaning"]["second"] >= 0.15:
        return by_meaning
    return by_words


def answer(db: Session, message: str, user=None, lang: str | None = None) -> dict:
    """What the guide says: the steps of a task, what a course teaches, what 9antra is, or where to look. In the
    language chosen in the window (`lang`), else in the language of the question."""
    from app.services import chatbot_sources

    lang = lang if lang in ("en", "fr", "ar", "tn") else language_of(message)
    fr = lang in ("fr", "tn")
    from app.services import completeness, registration_form

    if completeness.asks(understood(message)):
        return completeness.answer(db, user, lang)
    by_words = task_for(message, user, by_meaning=False)
    # “nsajjel khlas”, “أسجل دفع”: recording a payment, not how somebody registers (said by the word rules: by its
    # meaning, “how does a candidate register?” is near “record a payment”).
    if registration_form.asks_how(understood(message)) and not (by_words and by_words["key"] == "payment-record"):
        # How somebody registers: the form's link, which the team shares (WhatsApp, Facebook, email).
        link = registration_form.url(db)
        if lang in ("ar", "tn"):
            words = _REGISTER[lang]
            lines = [words[0] + link, words[1]] + ([words[2]] if _role(user) == ADMIN else [])
            return {"reply": "\n".join(lines), "links": [{"label": _SAY[lang]["form"], "url": link}], "source": "guide", "lang": lang,
                    "sources": [{"label": "The registration form", "url": link}]}
        lines = [("Pour s'inscrire, un candidat remplit le formulaire d'inscription : " if fr else
                  "To register, a candidate fills in the registration form: ") + link,
                 ("Partagez ce lien (WhatsApp, Facebook, email) : l'inscription arrive toute seule dans Candidats et le message de bienvenue part."
                  if fr else "Share this link (WhatsApp, Facebook, email): the registration arrives in Candidates by itself and the welcome message goes."),
                 ("Quand quelqu'un demande comment s'inscrire, par email, WhatsApp ou SMS, ce lien lui est envoyé."
                  if fr else "When someone asks how to register, by email, WhatsApp or SMS, this link is sent to them.")]
        if _role(user) == ADMIN:
            lines.append("Le lien se change dans Paramètres › Les numéros du centre." if fr else "Change the link in Settings › The centre's numbers.")
        return {"reply": "\n".join(lines), "links": [{"label": "Open the form", "url": link}], "source": "guide",
                "sources": [{"label": "The registration form", "url": link}]}
    task = task_for(message, user)
    if task:
        title, steps = in_language(task, lang)
        return as_steps(title, steps, task["page"], lang)
    found = chatbot_sources.about(db, message, fr)
    if found:
        return found
    from app.models import Program
    from app.services.chatbot import _program_in, _plain as chatbot_plain

    program = _program_in(db, chatbot_plain(understood(message)))
    found = chatbot_sources.program_content(db, message, program, fr)
    if found:
        return found
    if program is not None:
        weeks = [week for level in program.levels for week in level.weeks]
        lines = [f"{program.name} : {len(weeks)} semaines." if fr else f"{program.name}: {len(weeks)} weeks."]
        lines += [f"{'Sem.' if fr else 'Week'} {index}: {week.topic_title}" for index, week in enumerate(weeks[:12], start=1)]
        return {"reply": "\n".join(lines), "links": [{"label": program.name, "url": f"/programs/{program.id}" if _role(user) != PROFESSOR else "/teacher"}],
                "source": "knowledge", "sources": [{"label": f"The program in the application: {program.name}"}]}
    resources = chatbot_sources.resources_for(db, message) if _role(user) != PROFESSOR else []
    titles = [in_language({"key": key, "title": title, "steps": []}, lang)[0] for key, _, title, _, _, who, part in TASKS if _may(user, who, part)][:8]
    return {"reply": _SAY[lang]["list"] + "; ".join(titles) + ".", "links": [], "source": "guide", "resources": resources, "lang": lang,
            "sources": [{"label": "The application's guide"}]}

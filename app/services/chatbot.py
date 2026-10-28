"""The assistant of the application (the chat at the bottom of every page).

It answers from what is true right now, in this order:

0. what it can do, not only say (chatbot_actions): a program or all of them
   as a PDF, the email that carries it to a candidate, the email to write to
   someone, a reminder, a file for Excel. Nothing is sent from the chat;
1. the application's own data: candidates, payments, classes, professors,
   programs, level checks, emails, sessions, feedback, the job market, and
   whether everything is working. These answers are counted, not written by
   a language model, so they are exact and come at once;
2. how to do something here: a short answer and the page to open;
3. what the website 9antra.tn says and what the admin taught, found in the
   knowledge the email assistant already keeps;
4. only then the local language model, with those facts in front of it; and
   when it does not answer, the assistant says what it can be asked.

Each answer is {"reply", "links": [{"label", "url"}], "source"}.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.candidate import Candidate
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.classsession import ClassSession
from app.models.email_thread import EmailThread
from app.models.knowledge import KnowledgeEntry
from app.models.professor import Professor
from app.models.program import Program
from app.models.registration import PaymentStatus, Registration, RegistrationStatus

FACEBOOK = "https://www.facebook.com/9antra.tn"
WEBSITE = "https://9antra.tn"
_FRENCH = {"le", "la", "les", "des", "est", "qui", "que", "quoi", "combien", "comment", "quels", "quelles", "quel", "quelle",
           "pas", "pour", "dans", "avec", "sont", "ont", "une", "du", "où", "ou", "je", "mes", "nos", "cette", "ce"}


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", folded).split())


def _has(text: str, words: str) -> bool:
    return re.search(r"(?<![a-z0-9])(?:" + words + r")(?![a-z0-9])", text) is not None


from contextvars import ContextVar

# The language chosen in the assistant's window for this answer (en, fr, ar, tn), or None: the question's own.
_CHOSEN: ContextVar[str | None] = ContextVar("assistant_language", default=None)


def _french(message: str) -> bool:
    chosen = _CHOSEN.get()
    if chosen:
        return chosen != "en"         # Arabic and Tunisian: the guide answers in them; the figures come in French
    words = set(re.findall(r"[a-zàâçéèêëîïôûùüÿœ]+", (message or "").lower()))
    return len(words & _FRENCH) >= 2 or _has(_plain(message), "combien|comment|quels?|quelles?|paiement|classes? en attente|formateurs?|"
                                                              "envoie\\w*|envoyer|toutes|formations|svp|stp|bonjour|merci|donne moi|montre moi")


def _link(label: str, url: str) -> dict:
    return {"label": label, "url": url}


def _names(items: list[str], most: int = 6) -> str:
    shown = items[:most]
    return ", ".join(shown) + (f" and {len(items) - most} more" if len(items) > most else "")


def _who(candidate: Candidate | None) -> str:
    return (candidate.full_name or candidate.email or "A candidate") if candidate else "A candidate"


def _money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " DT"


# ------------------------------------------------------------------
# Who or what the question is about
# ------------------------------------------------------------------

def _candidate_in(db: Session, text: str) -> Candidate | None:
    """The candidate a question names: their whole name, else a distinctive part of it."""
    words = set(text.split())
    best, best_score = None, 0
    for candidate in db.query(Candidate).all():
        parts = [part for part in _plain(candidate.full_name or "").split() if len(part) >= 3]
        if not parts:
            continue
        score = sum(1 for part in parts if part in words)
        if score == len(parts) and score >= 2:
            score += 2
        if score > best_score or (score == best_score and score and best is not None and len(parts) < len(_plain(best.full_name or "").split())):
            best, best_score = candidate, score
    return best if best_score >= 2 or (best_score == 1 and best is not None and len(_plain(best.full_name or "").split()) == 1) else None


# Words that name a field for sure in a question (short ones like "ai" or "web" also are ordinary words).
_FIELD_WORDS = {
    "devops": "devops|docker|kubernetes", "python": "python", "flutter": "flutter|dart", "mern": "react|node js|nodejs|mern|javascript",
    "spring": "spring|angular|java", "cyber": "cyber\\w*|pentest\\w*", "marketing": "marketing|seo", "iot": "iot|arduino|esp32|internet of things",
    "ai": "chatbots?|artificial intelligence|intelligence artificielle|machine learning|nlp", "bi": "business intelligence|power bi|bi",
    "web": "bootcamp|html|css",
}


def _program_in(db: Session, text: str) -> Program | None:
    """The program a question names: by its whole name, else by a word that belongs to its field."""
    from app.services import level_bank

    programs = db.query(Program).all()
    for program in sorted(programs, key=lambda item: -len(item.name or "")):
        if _plain(program.name) and _has(text, re.escape(_plain(program.name))):
            return program
    for subject, words in _FIELD_WORDS.items():
        if _has(text, words):
            return next((program for program in programs if level_bank.subject_of(program.name, program.website_slug) == subject), None)
    return None


# ------------------------------------------------------------------
# Answers counted from the application's data
# ------------------------------------------------------------------

def _unpaid(db: Session, fr: bool) -> dict:
    rows = [row for row in db.query(Registration).filter(Registration.payment_status != PaymentStatus.PAID).all()
            if row.status != RegistrationStatus.CANCELLED]
    owed = sum(max(0.0, (row.total_amount or row.total_price or 0) - (row.paid_amount or 0)) for row in rows)
    people = sorted({_who(row.candidate) for row in rows})
    if not rows:
        reply = "Tout le monde a payé." if fr else "Everybody has paid in full."
    elif fr:
        reply = f"{len(people)} candidat(s) n'ont pas tout payé, pour {_money(owed)} au total : {_names(people)}."
    else:
        reply = f"{len(people)} candidate{'s have' if len(people) != 1 else ' has'} not paid in full, {_money(owed)} in all: {_names(people)}."
    return {"reply": reply, "links": [_link("Payments", "/payments")], "source": "system"}


def _classes(db: Session, fr: bool, only_waiting: bool = False) -> dict:
    from app.services.class_views import class_summary
    from app.services.schedule_utils import local_now

    now = local_now()
    lines, waiting = [], 0
    classes = [item for item in db.query(ClassGroup).all() if item.status != ClassStatus.COMPLETED]
    for item in sorted(classes, key=lambda item: (item.status != ClassStatus.ONGOING, item.name)):
        info = class_summary(item, now)
        if info["on_hold"] or (item.status == ClassStatus.NOT_STARTED and info["needs"]):
            waiting += 1
            lines.append(f"• {item.name}: {'on hold' if info['on_hold'] else 'not ready'}, {' and '.join(info['needs'])}")
        elif not only_waiting:
            state = f"running, week {info['current_week']} of {info['total_weeks']}" if item.status == ClassStatus.ONGOING else f"starts {info['starts_label']}"
            lines.append(f"• {item.name}: {state}, {info['enrolled']} candidate{'s' if info['enrolled'] != 1 else ''}, {item.professor_name or 'no professor yet'}")
    if only_waiting and not waiting:
        reply = "Aucune classe n'est en attente : toutes peuvent démarrer." if fr else "No class is waiting: every one of them can start."
    else:
        head = (f"{waiting} classe(s) ne peuvent pas démarrer :" if fr else f"{waiting} class{'es' if waiting != 1 else ''} cannot start:") if only_waiting else \
               (f"{len(classes)} classe(s) en cours ou à venir :" if fr else f"{len(classes)} class{'es' if len(classes) != 1 else ''} running or to come:")
        reply = head + "\n" + "\n".join(lines[:12])
        if waiting and not only_waiting:
            reply += "\n" + ("Une classe ne démarre qu'avec des candidats et un formateur de la spécialité." if fr
                             else "A class starts only with candidates in it and a professor of its speciality.")
    return {"reply": reply, "links": [_link("Classes", "/classes/")], "source": "system"}


def _professors(db: Session, fr: bool, program: Program | None) -> dict:
    from app.services.enrollment_service import teachers_of

    if program is not None:
        teachers = teachers_of(db, program)
        if teachers:
            reply = (f"Peuvent enseigner {program.name} : " if fr else f"These professors can teach {program.name}: ") + _names([item.name for item in teachers]) + "."
        else:
            reply = (f"Aucun formateur n'a {program.name} dans ses spécialités : ses classes ne peuvent pas démarrer." if fr
                     else f"No professor has {program.name} among their specialities, so its classes cannot start.")
    else:
        rows = db.query(Professor).order_by(Professor.name.asc()).all()
        reply = (f"{len(rows)} formateur(s) :\n" if fr else f"{len(rows)} professor{'s' if len(rows) != 1 else ''}:\n") + "\n".join(
            f"• {item.name}: {item.specialties or ('aucune spécialité' if fr else 'no speciality set')}" for item in rows)
    return {"reply": reply, "links": [_link("Professors", "/classes/?tab=professors")], "source": "system"}


def _programs(db: Session, fr: bool, program: Program | None) -> dict:
    if program is not None:
        weeks = sum(len(level.weeks) for level in program.levels)
        people = db.query(Registration).filter(Registration.program_id == program.id, Registration.status != RegistrationStatus.CANCELLED).count()
        parts = [f"{program.name}: {program.price_online:.0f} DT online, {program.price_onsite:.0f} DT on site"]
        if program.total_duration_days:
            parts.append(f"{program.total_duration_days} days")
        if weeks:
            parts.append(f"{weeks} weeks in {len(program.levels)} level{'s' if len(program.levels) != 1 else ''}")
        if program.class_session_time:
            parts.append(program.class_session_time)
        reply = ", ".join(parts) + f". {people} registration{'s' if people != 1 else ''}."
        if program.description:
            reply += "\n" + program.description
        return {"reply": reply, "links": [_link(program.name, f"/programs/{program.id}"), _link("What the market asks", "/programs/market")], "source": "system"}
    rows = db.query(Program).order_by(Program.name.asc()).all()
    reply = (f"{len(rows)} formations :\n" if fr else f"{len(rows)} programs:\n") + "\n".join(
        f"• {item.name}: {item.price_online:.0f} / {item.price_onsite:.0f} DT (online / on site)" for item in rows)
    return {"reply": reply, "links": [_link("Programs", "/programs/all")], "source": "system"}


def _levels(db: Session, fr: bool) -> dict:
    from app.services import level_check

    view = level_check.overview(db)
    counts = view["counts"]
    waiting = [row["name"] for row in view["rows"] if row["state"] == "sent"]
    levels = ", ".join(f"{count} {key}" for key, count in view["levels"].items() if count)
    reply = (f"Tests de niveau : {counts['ready']} prêts à envoyer, {counts['sent']} envoyés sans réponse, {counts['answered']} répondus."
             if fr else
             f"Level checks: {counts['ready']} ready to send, {counts['sent']} sent and not answered, {counts['answered']} answered.")
    if levels:
        reply += f" Levels: {levels}."
    if waiting:
        reply += ("\nPas encore répondu : " if fr else "\nNot answered yet: ") + _names(waiting) + "."
    return {"reply": reply, "links": [_link("Level checks", "/quiz/all")], "source": "system"}


def _inbox(db: Session, fr: bool) -> dict:
    waiting = db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).count()
    reply = (f"{waiting} discussion(s) attendent votre réponse dans la boîte." if fr
             else f"{waiting} discussion{'s are' if waiting != 1 else ' is'} waiting for your answer in the inbox.") if waiting else \
            ("Aucun email n'attend votre réponse." if fr else "No email is waiting for your answer.")
    return {"reply": reply, "links": [_link("Inbox", "/inbox")], "source": "system"}


def _feedback(db: Session, fr: bool) -> dict:
    from app.services import feedback

    said = feedback.overview(db)
    if not said["read"]:
        reply = "Aucun avis n'a encore été lu." if fr else "No published feedback has been read yet."
    else:
        themes = ", ".join(f"{theme['label'].lower()} ({theme['share']}%)" for theme in said["themes"][:4])
        reply = (f"{said['read']} avis publiés sur 9antra.tn ont été lus : {said['happy_share']}% sont contents"
                 if fr else f"{said['read']} feedback published on 9antra.tn were read: {said['happy_share']}% are happy")
        if said["stars"]:
            reply += f", {said['stars']} ★"
        reply += ("." + (f" Ils parlent surtout de : {themes}." if fr else f" They talk most about: {themes}.") if themes else ".")
        if said["to_answer"]:
            reply += f" {len(said['to_answer'])} " + ("ne sont pas pleinement contents." if fr else "are not fully happy.")
    return {"reply": reply, "links": [_link("Feedback", "/website#feedback")], "source": "system"}


def _market(db: Session, fr: bool, program: Program | None) -> dict:
    from app.services import market

    if program is None:
        view = market.overview(db)
        rows = [f"• {row['program'].name}: {row['fit']}% covered, from {row['offers']} offer{'s' if row['offers'] != 1 else ''}"
                for row in view["programs"] if row["fit"] is not None]
        reply = (f"Ce que le marché demande, d'après {view['total']} offres :\n" if fr else f"What the market asks, from {view['total']} offers:\n") + \
                ("\n".join(rows) if rows else ("Aucune offre lue pour l'instant." if fr else "No offer read yet."))
        trends = view.get("trends") or []
        if trends:
            reply += ("\n\nLes plus demandés : " if fr else "\n\nMost asked in the trade: ") + ", ".join(f"{row['name']} ({row['offers']})" for row in trends[:5]) + "."
            missing = [row for row in trends if not row["taught"] and row["enough"]]
            if missing:
                reply += ("\nSans formation ici : " if fr else "\nNo program here yet: ") + ", ".join(f"{row['name']} ({row['offers']})" for row in missing[:5]) + "."
        return {"reply": reply, "links": [_link("What the market asks", "/programs/market")], "source": "system"}
    result = market.analysis(db, program)
    if result["fit"] is None:
        reply = result["advice"][0] if result["advice"] else "Nothing to compare yet."
    else:
        reply = f"{program.name}: {result['fit']}% of what {result['offers']} offers ask is already in the program.\n" + "\n".join("• " + line for line in result["advice"][:5])
        if result.get("content"):
            reply += "\n\nContent to add:\n" + "\n".join(f"• {item['label']} ({item['pct']}%): {item['module']}" for item in result["content"][:4])
    return {"reply": reply, "links": [_link(program.name, f"/programs/{program.id}#market"), _link("What the market asks", "/programs/market")], "source": "system"}


def _sessions(db: Session, fr: bool) -> dict:
    from app.services.schedule_utils import local_now

    today = local_now().date()
    rows = (db.query(ClassSession).filter(ClassSession.session_date >= today, ClassSession.session_date <= today + timedelta(days=7))
            .order_by(ClassSession.session_date.asc()).all())
    lines = []
    for session in rows:
        group = db.get(ClassGroup, session.class_group_id)
        if group is None or group.status == ClassStatus.COMPLETED:
            continue
        hour = (session.start_time or group.start_time)
        lines.append(f"• {session.session_date:%a %d %b}{' ' + hour.strftime('%H:%M') if hour else ''}: {group.name}, {session.topic}"
                     + (" (meeting set)" if session.meeting_url else ""))
    reply = ((f"{len(lines)} séance(s) dans les 7 jours :\n" if fr else f"{len(lines)} session{'s' if len(lines) != 1 else ''} in the next 7 days:\n") + "\n".join(lines[:10])) if lines else \
            ("Aucune séance dans les 7 prochains jours." if fr else "No session in the next 7 days.")
    return {"reply": reply, "links": [_link("Classes", "/classes/")], "source": "system"}


def _safety(db: Session, fr: bool) -> dict:
    """How the data is protected, with the facts of this moment."""
    from app.services import backups

    view = backups.view(db)
    last = view["last"]
    if fr:
        reply = ("Toute la base et les fichiers sont copiés une fois par jour, chaque copie est vérifiée, et deux semaines sont gardées. "
                 + (f"Dernière copie : {last['made']:%d/%m à %H:%M} ({view['count']} gardées). " if last else "Aucune copie pour l'instant : la première se fait quelques minutes après le démarrage. ")
                 + (f"Elles vont aussi dans {view['second']}." if view["second"] else "Elles sont toutes sur le même disque que l'application : donnez un second dossier sur un autre disque."))
    else:
        reply = ("The whole database and the uploaded files are copied once a day, each copy is checked, and two weeks of them are kept. "
                 + (f"Last copy: {last['made']:%d %b at %H:%M} ({view['count']} kept). " if last else "No copy yet: the first one is made a few minutes after the application starts. ")
                 + (f"They also go to {view['second']}." if view["second"] else "They are all on the same disk as the application: give a second folder on another disk."))
    return {"reply": reply, "links": [_link("Your data", "/system#safety")], "source": "system"}


def _status(db: Session, fr: bool) -> dict:
    from app.services import system_status, weekly_questions
    from app.services.settings_service import public_base_url

    base_url = public_base_url(db)
    connections = system_status.connections(db, base_url, weekly_questions.is_local_address(base_url))
    summary = system_status.summary(system_status.work(db), connections)
    limited = [row["name"] for row in connections if row["state"] == "partial"]
    reply = f"{summary['title']}. {summary['text']}"
    if limited:
        reply += ("\nIncomplet : " if fr else "\nNot complete: ") + ", ".join(limited) + "."
    return {"reply": reply, "links": [_link("How it is working", "/system")], "source": "system"}


def _counts(db: Session, fr: bool) -> dict:
    candidates = db.query(Candidate).count()
    registrations = [row for row in db.query(Registration).all() if row.status != RegistrationStatus.CANCELLED]
    paid = sum(1 for row in registrations if row.payment_status == PaymentStatus.PAID)
    classes = db.query(ClassGroup).filter(ClassGroup.status != ClassStatus.COMPLETED).count()
    reply = (f"{candidates} candidats, {len(registrations)} inscriptions dont {paid} payées, {classes} classes en cours ou à venir, {db.query(Program).count()} formations."
             if fr else
             f"{candidates} candidates, {len(registrations)} registrations of which {paid} are paid, {classes} classes running or to come, {db.query(Program).count()} programs.")
    return {"reply": reply, "links": [_link("Dashboard", "/"), _link("Candidates", "/candidate/all")], "source": "system"}


def _candidate(db: Session, fr: bool, candidate: Candidate) -> dict:
    from app.services import behaviour, level_check

    lines = []
    for registration in candidate.registrations:
        if registration.status == RegistrationStatus.CANCELLED:
            continue
        total = registration.total_amount or registration.total_price or 0
        paid = registration.paid_amount or 0
        money = "paid in full" if registration.payment_status == PaymentStatus.PAID else f"paid {paid:.0f} of {total:.0f} DT"
        group = registration.class_group
        where = f"in {group.name} ({group.status.value.replace('_', ' ')})" if group else "no class yet"
        line = f"• {registration.program_name or (registration.program.name if registration.program else 'a program')}: {money}, {where}"
        check = level_check.current(db, registration.id)
        result = level_check.result_of(check) if check else None
        if result:
            line += f", starts as {result['info']['label'].lower()}"
        lines.append(line)
    profile = behaviour.profile(db, candidate)
    reply = f"{_who(candidate)}" + (f" ({candidate.phone})" if candidate.phone else "") + ":\n" + ("\n".join(lines) or "• no registration")
    facts = [part for part in (candidate.email, candidate.university, candidate.city) if part]
    if facts:
        reply += "\nContact: " + ", ".join(facts)
    from app.models.candidate_email import CandidateEmail
    from app.models.user import Attendance

    marks = [row.status for row in db.query(Attendance).filter(Attendance.candidate_id == candidate.id).all()]
    if marks:
        reply += f"\nPresence: {marks.count('present')} present, {marks.count('late')} late, {marks.count('absent')} absent"
    last = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == candidate.id).order_by(CandidateEmail.received_at.desc()).first()
    if last is not None:
        reply += f"\nLast email ({'from them' if last.direction == 'inbound' else 'to them'}): {last.subject or '(no subject)'}"
    if candidate.admin_notes:
        reply += f"\nYour note: {' '.join(candidate.admin_notes.split())[:200]}"
    if profile["score"] is not None:
        reply += f"\nBehaviour: {profile['score']}/100, {profile['headline'].lower()}"
        if profile["traits"]:
            reply += " (" + ", ".join(trait["label"].lower() for trait in profile["traits"][:3]) + ")"
        reply += "."
    return {"reply": reply, "links": [_link("Open their profile", f"/candidate/{candidate.id}")], "source": "system"}


# How to do something here: (words that ask for it, the answer, the page).
_HOW = [
    (r"(add|ajouter|create|creer) (a |an |un |une )?(person|user|team member|membre|utilisateur|staff|admin|account|compte|professor|prof)\w*|access|acces|permissions?|droits?|give (him|her|them) (access|everything)|grant",
     "Settings → Team and access: “Add a person” (team member, admin, or a professor's own account). For a team member, choose for each part "
     "Not shown / Can look / Can change, tick special rights (manage the team, the email/WhatsApp/SMS accounts, backups), or “Give everything”. "
     "Their name, email and phone can be changed there too; it applies at once.", ("Team and access", "/settings/team")),
    (r"team chat|messages? (to|with) (the )?(professors?|team)|ecrire (a|aux) (profs?|formateurs?|equipe)|write to (a |the )?(professor|team)",
     "Inbox → Team: choose someone, write once, tick how it goes (in the application, email, WhatsApp, SMS). Their answers come back into the same "
     "conversation; the assistant shows the answer it found.", ("Team chat", "/inbox/team")),
    (r"payment proofs?|preuves? de paiement|receipts?|recus?|factures?|virements?|rib",
     "Receipts candidates send (email, WhatsApp) are read on the Payments page under “Payment proofs to check”: amount, bank, RIB, date, reference "
     "and checks. You confirm (the payment is recorded) or refuse. Write the centre's RIB in Settings (WhatsApp card) so it is checked against it.",
     ("Payment proofs", "/payments#proofs")),
    (r"videos?|watch|regarder|voir les videos",
     "Programs → Videos: every week's video of the programs and the website's videos, played here. A program's page plays its own.",
     ("Videos", "/programs/videos")),
    (r"(how|which way|par ou|comment) (do )?(messages?|reminders?|emails?) (go|are sent|partent)|sms or whatsapp|whatsapp or (email|sms)|channel|canal",
     "At the top of the Inbox: “Messages go by” (let the system choose, email, WhatsApp or SMS) and “If it does not work, the next way”.",
     ("Inbox", "/inbox#ways")),
    (r"sms",
     "SMS go from an Android phone with the free app SMS Gateway for Android (Settings → SMS). Without it, each SMS waits in the inbox with a button "
     "that opens your phone's messages, written.", ("SMS settings", "/settings#sms")),
    (r"welcome|bienvenue|message (after|apres) (registration|inscription)",
     "Inbox → Email templates → “Welcome, just registered”: its words, and its switch. It goes once, as soon as someone registers.",
     ("Email templates", "/inbox/templates?key=welcome")),
    (r"(payment )?reminder (after|delay|timing|hours)|rappel de paiement|24 ?h",
     "Inbox → Email templates → “Payment reminder”: change its words and “Goes … hours after registering”.",
     ("Payment reminder", "/inbox/templates?key=payment_reminder")),
    (r"reword|reformul\w*|tunisian|tounsi|derja",
     "In the inbox reply box: write a few words, even in Tunisian, choose “in French / in English” and press Reformulate; or tick “Reword and send at once”.",
     ("Inbox", "/inbox")),
    (r"reviews?|avis|opinion|feedback (of|from) (the )?candidates",
     "When a class finishes, each candidate is asked once what they thought (email, WhatsApp or SMS). Their reviews are on the class page, the program's "
     "page and their file; “Ask for reviews” sends it again to a class.", ("Classes", "/classes/")),
    (r"iphone|android|home screen|ecran d accueil|phone app|application mobile|mobile app",
     "On How it is working → On your phone: start the application for the phone, open the address in Safari (iPhone) or Chrome, then Share → "
     "“Add to Home Screen”.", ("On your phone", "/system#phone")),
    (r"level check (timing|when)|test de niveau (quand|timing)|days before",
     "Level checks page → “It goes … days before the first session” (or as soon as they pay).", ("Level checks", "/quiz/all#timing")),
    (r"backups?|sauvegardes?|data (is )?(safe|lost|loss|security|protected)|securite des donnees|perdre (les|mes) donnees|copies? of the data",
     "The whole database and the uploaded files are copied once a day, each copy is checked, and two weeks of them are kept. "
     "On “How it is working”, under “Your data is protected”: copy now, download a copy, and give a second folder on another disk.", ("Your data", "/system#safety")),
    (r"(my own|custom|another|new|schedule|plan|add) (a |an )?(other )?(reminders?|rappels?)|rappel personnalise|remind (a|the|my|every) (class|candidate|program)\w*|(colou?red )?squares?",
     "On “Reminders”: choose who (a candidate, a class, a program, those who still owe, everyone), write or start from an idea, add coloured squares for what must be seen, "
     "then choose when. It goes once, at that time.", ("Reminders", "/reminders")),
    (r"create (a )?class|new class|creer une classe|nouvelle classe",
     "A class is created by itself when a candidate pays and no class of their program is open. By hand, "
     "“Create a class” on the Classes page works only when paid candidates are waiting for one: they are placed in it at once.",
     ("Classes", "/classes/")),
    (r"add (a )?(professor|teacher)|ajouter un (formateur|prof)|specialit",
     "On the Classes page, tab Professors: “Add a professor”, then tick the programs they teach. Only a professor "
     "who has a program among their specialities can be given its classes.", ("Professors", "/classes/?tab=professors")),
    (r"level check|test de niveau|quiz",
     "Each paid candidate gets one level check before their program starts. On the Level checks page press “Send”, "
     "or “Send the ready ones”. Whoever has not answered after two days is reminded once.", ("Level checks", "/quiz/all")),
    (r"record (a )?payment|enregistrer un paiement|mark .* paid|payer",
     "Open the candidate's profile and record the payment on their registration. When it is paid in full the candidate "
     "is placed in a class and gets the confirmation email.", ("Candidates", "/candidate/all")),
    (r"meeting|meet link|lien (meet|de la seance)|reunion|visio",
     "On a class's presence sheet, set the meeting of the session (paste your Meet link, or leave it empty for a private room) "
     "and press “Send each candidate their own link”. Their link notes when they arrive.", ("Classes", "/classes/")),
    (r"presence|attendance|absent",
     "Open a class, then “Presence”: pick the session, change only those who were late or absent, note how each one took part, and save.",
     ("Classes", "/classes/")),
    (r"weekly questions?|questions? de la semaine|course files?|fichiers? du cours",
     "Open a class, then “Course”: add the week's files or notes. Questions are written from them, checked, and sent to each candidate.",
     ("Classes", "/classes/")),
    (r"phone|telephone|mobile|home screen",
     "Start the application with start_for_phone.bat, open the address shown on the “How it is working” page on your phone "
     "(same Wi-Fi), and choose “Add to Home screen”.", ("How it is working", "/system#phone")),
    (r"public address|adresse publique|links? (do not|don't|ne) ",
     "Set the address candidates can reach in Settings, section “Address”. Until then the links in emails only open on this computer.",
     ("Settings", "/settings#address")),
    (r"email templates?|modele d'email|change (the )?(email|text)|wording",
     "Every email the application sends can be read, changed or switched off in Inbox, “Email templates”.", ("Email templates", "/inbox/templates")),
    (r"answer (an )?email|repondre|knowledge|teach|apprendre",
     "In the Inbox, answer a waiting question in a few words: it is sent, and the application learns it, so the same question is answered by itself next time.",
     ("Inbox", "/inbox")),
    (r"brochure|program\w* (as |en |in )?(a )?pdf|pdf (of|du|de la|for) (the |a )?(program|formation)\w*|(download|telecharger|send|envoyer) (the |le |a )?program\w*",
     "Open the program, then “PDF”: choose one of the three looks, download it, or send it to a candidate (the email is prepared with the PDF attached, you press Send). "
     "On a candidate's page, “Send the program (PDF)” does the same in one click.", ("Programs", "/programs/all")),
    (r"import|pdf|add (a )?program|ajouter une formation",
     "On the Programs page: “Add a program” (a name is enough, the rest is proposed) or “Import a PDF”.", ("Programs", "/programs/all")),
    (r"job|market|linkedin|skills|competences",
     "On “What the market asks” (Programs page): read the public job board, or paste offers you copied from LinkedIn. "
     "Each program then shows what offers ask, what it covers, and what to add.", ("What the market asks", "/programs/market")),
]


def _how(text: str) -> dict | None:
    if not _has(text, "how|comment|where|ou|can i|puis je|je veux|i want to|how to"):
        return None
    for words, reply, (label, url) in _HOW:
        if _has(text, words):
            return {"reply": reply, "links": [_link(label, url)], "source": "guide"}
    return None


# ------------------------------------------------------------------
# What the website says, and what was taught
# ------------------------------------------------------------------

def _knowledge(db: Session, message: str) -> dict | None:
    from app.services import knowledge_service

    try:
        hits = knowledge_service.search(db, message, limit=3)
    except Exception:
        return None
    for hit in hits:
        if (hit.get("quality") or 0) < 0.45:
            continue
        entry = db.get(KnowledgeEntry, hit["id"])
        if entry is None or not (entry.content or "").strip():
            continue
        taught = entry.origin != "system"
        website = (entry.source_key or "").startswith("website:")
        text = entry.content.strip()
        text = text if len(text) <= 900 else text[:900].rsplit(" ", 1)[0] + "…"
        from app.services import chatbot_sources

        return {
            "reply": (f"{entry.title}\n" if not taught and entry.kind != "fact" else "") + text,
            "links": [_link("9antra.tn", WEBSITE)] if website else [_link("What the assistant knows", "/inbox/knowledge")],
            "source": "taught" if taught else "website" if website else "knowledge",
            "sources": [chatbot_sources.of_entry(db, entry)],
        }
    return None


def _model(db: Session, message: str, fr: bool) -> dict | None:
    """Last resort: the local model, with the figures in front of it and told to say when it does not know."""
    from app.services import email_assistant

    if not email_assistant.model_available():
        return None
    facts = _counts(db, False)["reply"]
    from app.services import chatbot_sources

    found = chatbot_sources.grounded(db, message, fr, facts)    # from the closest extracts, saying which it used
    if found:
        return found
    data = email_assistant.llm_json(
        "You are the assistant of a training centre's management application. Answer in one or two short sentences, in "
        + ("French" if fr else "English") + ". Use only the facts given. If they do not answer the question, reply exactly: UNKNOWN. "
        'Answer with one JSON object: {"reply": "..."}',
        f"Facts: {facts}\n\nQuestion: {message}",
        max_tokens=160,
    )
    reply = str((data or {}).get("reply") or "").strip()
    if not reply or "UNKNOWN" in reply.upper() or len(reply) < 8:
        return None
    return {"reply": reply, "links": [], "source": "model",
            "sources": [{"label": "The application's figures"}, {"label": "Written by the local model: check it"}]}


SUGGESTIONS = [
    "What should I do today?", "Send me all the programs in PDF", "Who has not paid?", "Which classes cannot start?",
    "Who registered this week?", "What should we add to DevOps?", "What can you do?",
]


_REMEMBER = re.compile(r"^\s*(?:retiens(?:\s+bien)?|souviens[- ]toi|n['’]oublie\s+pas|note\s+bien|garde\s+en\s+t[eê]te|remember|keep\s+in\s+mind|"
                       r"don['’]?t\s+forget|note\s+that|تذكر|احفظ|a3?9el|3a9el|o?chfa)\b\s*(?:que|qu['’]|that|ان|أن|:|,|-)?\s*(.{3,})$",
                       re.IGNORECASE | re.DOTALL)


def _remember(db: Session, message: str, allowed: bool) -> dict | None:
    """“Retiens que le centre est fermé le 15 novembre”: kept for every assistant (knowledge_service.save_fact)."""
    found = _REMEMBER.match(message or "")
    if found is None:
        return None
    arabic = re.search(r"[؀-ۿ]", message or "") is not None
    fr = _french(message)
    if not allowed:
        return {"reply": "Seul un administrateur peut m'apprendre quelque chose." if fr else "Only an admin can teach me something.",
                "links": [], "source": "system"}
    from app.services import knowledge_service

    try:
        entry = knowledge_service.save_fact(db, text=found.group(1).strip())
        db.commit()
    except ValueError as error:
        db.rollback()
        return {"reply": str(error), "links": [], "source": "system"}
    said = ("حفظت ذلك: " if arabic else "C'est noté : " if fr else "Noted: ") + entry.content
    said += ("\nأستعمله أنا وكل المساعدين من الآن." if arabic else
             "\nMoi et les autres assistants l'utilisons dès maintenant, quelle que soit la langue de la question." if fr else
             "\nI and the other assistants use it from now on, whatever language the question is in.")
    return {"reply": said, "links": [_link("What the assistant knows", "/inbox/knowledge#facts")], "source": "taught",
            "sources": [{"label": "What you told the assistants", "url": "/inbox/knowledge#facts"}]}


def _earlier_questions(message: str, history: list[dict] | None) -> list[str]:
    """What the admin asked before this message, oldest first (the page sends the conversation, this message last)."""
    asked = [str(item.get("content") or "") for item in (history or []) if item.get("role") == "user" and item.get("content")]
    if asked and asked[-1].strip() == (message or "").strip():
        asked = asked[:-1]
    return asked[-6:]


def answer(db: Session, message: str, history: list[dict] | None = None, *, user=None, lang: str | None = None) -> dict:
    """The assistant's answer, in the language chosen in its window (`lang`) or else the language of the question."""
    token = _CHOSEN.set(lang if lang in ("en", "fr", "ar", "tn") else None)
    try:
        return _answer_now(db, message, history, user=user)
    finally:
        _CHOSEN.reset(token)


def _answer_now(db: Session, message: str, history: list[dict] | None = None, *, user=None) -> dict:
    """The assistant's answer to one message, always with where it comes from (`sources`), and the resources about it."""
    from app.services import chatbot_send, chatbot_sources

    may_send = user is None or getattr(user, "is_admin", False) or (getattr(user, "permissions", None) or {}).get("assistant") == "edit"
    from app.services import chatbot_actions

    chatbot_actions.may_send.set(bool(may_send) and user is not None)          # a program's PDF goes at once only for them
    found = _remember(db, message, may_send)       # “retiens que …”: told to every assistant
    if found is not None:
        return found
    # “Renvoie le message de bienvenue à Fares demain à 9h”, “3awed ab3ath el rappel l Fares ghodwa”: something the
    # application sends by itself, sent again (asks what, whom or when when it is missing).
    from app.services import resends

    again = resends.from_words(db, message)
    if again is None and history and any(text in (history[-1].get("content") or "") for text in resends.ASK_WHEN):
        earlier = next((item.get("content") for item in reversed(history[:-1]) if item.get("role") == "user"), "")
        again = resends.from_words(db, f"{earlier} {message}") if earlier else None
    if again is not None and may_send and user is not None:
        from app.services.settings_service import public_base_url

        found = resends.answer(db, again, resends.in_french(message),
                               author=getattr(user, "name", None) or "Admin", base_url=public_base_url(db))
    if found is None and not re.match(r"\s*(how|comment|where|ou)\b", _plain(message)) and not re.search(r"\bpdf\b", _plain(message)):
        found = chatbot_send.handle(db, message, _french(message), author=getattr(user, "name", None) or "Admin", allowed=may_send)
    if found is None and re.match(r"\s*(resources?|ressources?|liens?|links?|documents?|videos?)\b", _plain(message)):
        found = {"reply": ("Voici ce que j'ai trouvé à lire, regarder ou télécharger :" if _french(message)
                           else "Here is what I found to read, watch or download:"), "links": [], "source": "system",
                 "sources": [{"label": "The programs, the videos, 9antra.tn and what the assistant knows"}]}
        found["resources"] = chatbot_sources.resources_for(db, message, found, limit=10)
        if not found["resources"]:
            found["reply"] = "Je n'ai rien trouvé à ce sujet." if _french(message) else "I found nothing about that."
    from app.services import completeness, howto

    # Tunisian and Arabic: their words are also read in French (“kifech nzid prof” = “comment ajouter prof”).
    understood = howto.understood(message)
    if found is None and completeness.asks(understood):
        found = completeness.answer(db, user, _CHOSEN.get() or howto.language_of(message))     # what is still missing, and where
    if found is None:
        from app.services import registration_form

        # “How does someone register?”, “the registration link”: the form (not “I want to register Ali…”, an order).
        if registration_form.asks_how(understood) and not re.search(r"\b(je veux|je voudrais|i want|i would like)\b", _plain(message)):
            found = howto.answer(db, message, user, lang=_CHOSEN.get())
    asked = r"\s*(how|comment|where|ou|can i|puis je|je veux|i want|what do i do|que faire|kifech|kifeh|kifach|kif|win|chnowa|chnoua)\b"
    from app.services import derja

    if found is None and (re.match(asked, _plain(understood)) or re.match(asked, _plain(derja.to_french(message)))):
        if howto.task_for(message, user, by_meaning=False) is not None:      # the word rules: “how much …” is about the records
            found = howto.answer(db, message, user, lang=_CHOSEN.get())       # the steps, for what this person may do, in the language asked
    if found is None:
        # Asked in Arabic or Tunisian (or chosen so): a question about the programs gets their facts in that language.
        from app.services import prospects

        spoken = _CHOSEN.get() or howto.language_of(message)
        if spoken in ("ar", "tn"):
            facts = prospects.reply_for(db, message, spoken, registered=True)
            if facts is not None:
                found = {"reply": facts["text"], "links": [], "source": "knowledge", "lang": spoken,
                         "sources": [{"label": "The programs in the application"}]}
    found = found or _answer(db, understood, history, user=user)
    if not found.get("sources"):
        found["sources"] = chatbot_sources.for_answer(db, found)
    if "resources" not in found:
        found["resources"] = chatbot_sources.resources_for(db, message, found)
    return found


def _answer(db: Session, message: str, history: list[dict] | None = None, user=None) -> dict:
    """The assistant's answer to one message; `history` lets it go on from the question before ("send it to Fares")."""
    from app.services import chatbot_sources

    text = _plain(message)
    fr = _french(message)
    if not text:
        return {"reply": "Ask me about your candidates, payments, classes, programs or the website.", "links": [], "source": "none"}
    program = _program_in(db, text)
    from app.services import chatbot_actions as act, chatbot_data as data

    named = _candidate_in(db, text)

    if _has(text, act.ABILITY_WORDS):
        return act.abilities(fr)
    if not re.match(r"(how|comment|where|ou est|ou se|ou puis)\b", text):
        # A program, or all of them, as a PDF, ready to open or in an email to a candidate.
        found = act.document(db, message, _earlier_questions(message, history), fr)
        if found:
            return found
        # Anything else "in PDF": the answer to the rest of the question, or the answer just given.
        found = act.as_pdf(db, message, history, fr)
        if found:
            return found
    if _has(text, act.EXPORT_WORDS):
        return act.export(text, fr)
    if named is not None and program is not None and _has(text, act.REGISTER_WORDS):
        return act.register(db, named, program, fr)
    if named is not None:
        if _has(text, act.WRITE_WORDS):
            return act.write_to(db, named, fr)
        if _has(text, act.REMIND_WORDS):
            return act.remind(db, named, text, fr)
        if _has(text, data.CONTACT_WORDS):
            return data.contact(db, named)
        if _has(text, data.OWE_WORDS):
            return data.balance(db, named)
    if _has(text, data.OVERVIEW_WORDS):
        return data.overview(db)
    if _has(text, data.TODAY_WORDS):
        return data.today(db)
    if _has(text, data.REGISTERED_WORDS):
        return data.registered(db, text)
    if named is None and program is None and _has(text, data.LIST_WORDS) and not _has(text, act.SEND + "|" + act.REMIND_WORDS + "|write|ecri\\w*"):
        return data.candidates_list(db)
    if _has(text, data.NO_CLASS_WORDS):
        return data.programs_without_class(db)

    if _has(text, "facebook|fb"):
        return {"reply": ("La page Facebook de 9antra est ici. L'application n'en lit que la photo : les publications et les avis demandent la clé d'accès de la page (Paramètres)."
                          if fr else
                          "9antra's Facebook page is here. The application only reads its picture: posts and reviews need the page's access key (Settings)."),
                "links": [_link("Facebook page", FACEBOOK), _link("Settings", "/settings#identity")], "source": "system"}
    if _has(text, "backups?|sauvegardes?|data (is |are )?(safe|lost|loss|protected|secure|security)|is (my|the|our) data|securite des donnees|donnees (sont )?(protegees|en securite|perdues)|perdre (les|mes|nos) donnees"):
        return _safety(db, fr)
    if _has(text, "reminders? (planned|scheduled|to come|waiting)|(planned|scheduled|which|what) reminders?|rappels? (prevus|programmes|planifies)|quels rappels"):
        return data.planned_reminders(db)
    found = chatbot_sources.about(db, message, fr)      # who 9antra is, where, how to reach it
    if found:
        return found
    found = _how(text)
    if found:
        return found
    owing = _has(text, "unpaid|not paid|haven t paid|hasn t paid|owe\\w*|impaye\\w*|pas (encore )?paye|n ont pas paye|reste a payer")
    mails = _has(text, "e ?mails?|mails?|messages?|inbox|boite|courriers?|discussions?")
    if not owing and not mails and named is None and (_has(text, data.MONEY_WORDS) or (_has(text, "payments?|paiements?|money|argent|revenue|recettes?|income") and data.period(text))):
        return data.money(db, text)
    if _has(text, data.EMAIL_WORDS) and (named is not None or mails):
        found = data.emails(db, text, named)
        if found:
            return found
    if _has(text, "market|marche du travail|job offers?|offres?|linkedin|skills?|competences?|what (should|to) (we )?add|ajouter|recommend\\w*|trend\\w*|tendances?|data science"):
        return _market(db, fr, program)
    if _has(text, "everything work\\w*|is it working|status|tout (marche|fonctionne)|connected|connecte|problem\\w*|en panne"):
        return _status(db, fr)
    if _has(text, "unpaid|not paid|haven t paid|hasn t paid|owe\\w*|impaye\\w*|pas (encore )?paye|n ont pas paye|reste a payer|who (has|have) (not )?paid|payments? (waiting|pending|late)"):
        return _unpaid(db, fr)
    if _has(text, "on hold|cannot start|can t start|not start\\w*|waiting class\\w*|en attente|ne (demarre|commence)\\w*|bloque\\w*"):
        return _classes(db, fr, only_waiting=True)
    if _has(text, "level checks?|levels?|niveaux?|test de niveau|quiz\\w*|answered|repondu"):
        return _levels(db, fr)
    teacher = data.professor_in(db, text)
    if teacher is not None and named is None:
        return data.professor_card(db, teacher)
    if _has(text, "professors?|teachers?|teach(es)?|formateurs?|profs?|enseigne\\w*|trainers?"):
        return _professors(db, fr, program)
    if (program is not None and named is None and _has(text, data.PEOPLE_WORDS)
            and not _has(text, "price|prix|cost\\w*|tarif|how much|how long|duration|duree|weeks?|semaines?|market|marche")):
        return data.program_people(db, program)
    group = data.class_in(db, text)
    if group is not None and named is None:
        return data.class_card(db, group)
    candidate = named
    if candidate is not None:
        return _candidate(db, fr, candidate)
    if _has(text, "feedback|avis|temoignages?|reviews?|satisf\\w*|what do (the )?candidates say|opinions?"):
        return _feedback(db, fr)
    if _has(text, "sessions?|seances?|today|aujourd hui|this week|cette semaine|tomorrow|demain|next (session|class)|prochaine"):
        return _sessions(db, fr)
    if _has(text, "inbox|emails?|mails?|boite|messages? (waiting|en attente)|questions? (waiting|en attente)"):
        return _inbox(db, fr)
    if _has(text, "classes?"):
        return _classes(db, fr)
    found = chatbot_sources.program_content(db, message, program, fr)      # what a program teaches, week by week
    if found:
        return found
    if program is not None and _has(text, "price|prix|cost\\w*|combien|tarif|how much|how long|long|length|combien de temps|duration|duree|weeks?|semaines?|about|tell me|c est quoi|what is"):
        return _programs(db, fr, program)
    if program is None and _has(text, "price list|prices list|liste des prix|liste des tarifs|all (the |our )?prices|tous les (prix|tarifs)|tarifs (des|de nos) formations"):
        return _programs(db, fr, None)
    found = _knowledge(db, message)
    if found:
        return found
    if program is not None:
        return _programs(db, fr, program)
    if _has(text, "programs?|formations?|catalogue|courses?"):
        return _programs(db, fr, None)
    if _has(text, "how many|combien|number of|nombre|overview|summary|resume"):
        return _counts(db, fr)
    from app.services import howto

    task = howto.meaning_task(message, user, margin=0.08)       # a how-to said in other words or another language
    if task is not None:
        title, steps = howto.in_language(task, _CHOSEN.get() or howto.language_of(message))
        return howto.as_steps(title, steps, task["page"], _CHOSEN.get() or howto.language_of(message))
    found = data.search(db, text)          # anything the records mention, before asking the model
    if found:
        return found
    found = _model(db, message, fr)
    if found:
        return found
    return {
        "reply": ("Je n'ai pas la réponse à cela. Je réponds sur : paiements, classes, formateurs, formations et prix, tests de niveau, emails, séances, avis, marché de l'emploi, et ce que dit 9antra.tn. "
                  "Je prépare aussi les PDF des formations, les emails et les fichiers Excel."
                  if fr else
                  "I do not have the answer to that. I can answer about payments, classes, professors, programs and prices, level checks, emails, sessions, feedback, the job market, and what 9antra.tn says. "
                  "I also make the programs' PDFs, prepare emails and give files for Excel."),
        "links": [_link("How it works", "/guide")], "source": "none", "suggestions": act.EXAMPLES[:4],
    }

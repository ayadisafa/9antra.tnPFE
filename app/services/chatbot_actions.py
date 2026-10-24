"""What the assistant does, not only says.

It makes a program's PDF, or several programs in one; prepares the email that
carries it to a candidate; opens the email to write to someone; leads to the
payment reminder or a reminder of the admin's own; and says what it can do.
Nothing is sent from here: what goes to a candidate waits in the composer,
where the admin reads it and presses Send.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from contextvars import ContextVar

from sqlalchemy.orm import Session

from app.models.candidate import Candidate
from app.models.program import Program

SEND = r"send|envo\w*|share|partag\w*|mail|email|forward|transf\w*"
_POINTING = r"it|this|that|them|those|same|too|also|again|le|la|les|lui|leur|ca|cela|meme|aussi|pareil|celui ci|celle ci"


def _plain(text: str) -> str:
    from app.services.chatbot import _plain as plain

    return plain(text)


def _has(text: str, words: str) -> bool:
    return re.search(r"(?<![a-z0-9])(?:" + words + r")(?![a-z0-9])", text) is not None


def _link(label: str, url: str, tab: bool = False) -> dict:
    return {"label": label, "url": url, "tab": True} if tab else {"label": label, "url": url}


def _name(person: Candidate) -> str:
    return person.full_name or person.email


def _follows_up(message: str) -> bool:
    """A short message that goes on with the one before: "send it to Fares", "in English", "and for Python?"."""
    text = _plain(message)
    if not text or len(text.split()) > 12:
        return False
    return (_has(text, _POINTING) or _has(text, SEND) or _has(text, r"in english|en anglais|in french|en francais")
            or re.match(r"(and|et|to|a|pour|for|now|maintenant)\b", text) is not None)


def _names_list(programs: list[Program], most: int = 4) -> str:
    names = [program.name for program in programs]
    return ", ".join(names[:most]) + (f" and {len(names) - most} more" if len(names) > most else "")


# ------------------------------------------------------------------
# Programs as PDF
# ------------------------------------------------------------------

# Whether the person asking may make the assistant send (set by chatbot.answer for each question).
may_send: ContextVar[bool] = ContextVar("assistant_may_send", default=False)


def _send_document(db: Session, person: Candidate, programs: list, lang: str, short: bool, file, fr: bool) -> dict | None:
    """“Send at once” is on: the email with the program's PDF goes now. None when it cannot go (then it is prepared)."""
    from app.services import email_assistant, email_files, email_threads, program_documents as docs

    if not email_threads.smtp_configured() or not (person.email or "").strip():
        return None
    words = docs.reply_text(programs, "named", lang, summary=short).replace("Comme demandé, vous", "Vous").replace("As you asked, you", "You")
    text = f"{email_assistant.greeting_for(person, lang)}\n\n{words}"
    plain, page = email_assistant.compose_reply(db, person, text, language=lang)
    subject = ((f"Le programme de {programs[0].name}" if len(programs) == 1 else "Nos formations") if lang == "fr"
               else (f"The program of {programs[0].name}" if len(programs) == 1 else "Our programs"))
    sent = email_threads.deliver(db, person, subject, plain, page, kind="message", author="The assistant", language=lang,
                                 files=email_files.load(db, [file.id]))
    if sent is None or getattr(sent, "status", "") == "failed":
        return None
    first = _name(person)
    reply = (f"Envoyé à {first} ({person.email}), avec le PDF en pièce jointe." if fr
             else f"Sent to {first} ({person.email}), with the PDF attached.")
    return {"reply": reply, "source": "action", "links": [_link(f"Open the discussion with {first.split()[0]}", f"/inbox?candidate={person.id}")]}


def document(db: Session, message: str, earlier: list[str] | None = None, fr: bool = False) -> dict | None:
    """A program, or several, as a PDF: to open, to download, or ready in an email to a candidate.

    `earlier` are the questions asked before, oldest first: "send it to Fares",
    then "in English", go on from the last request for a PDF.
    """
    from app.services import chatbot, program_documents as docs, program_pdf
    from app.services.settings_service import get_settings

    chain: list[str] = []          # the request for a PDF, then what was said after it
    if docs.asks_for_program(message):
        request = message
    else:
        if not _follows_up(message):
            return None
        earlier = list(earlier or [])
        start = next((index for index in range(len(earlier) - 1, max(-1, len(earlier) - 5), -1) if docs.asks_for_program(earlier[index])), None)
        if start is None:
            return None
        chain = earlier[start:]
        request = chain[0]
    if not program_pdf.available():
        return {"reply": "The PDF library is not installed on this computer: run “pip install reportlab” and restart the application.",
                "links": [], "source": "system"}

    newest_first = [message] + list(reversed(chain))
    person = next((found for found in (chatbot._candidate_in(db, _plain(said)) for said in newest_first) if found is not None), None)
    # "same for DevOps" names the program again; otherwise the request says which.
    named_later = next((found for found in (docs.named_programs(db, said) for said in newest_first[:-1]) if found), []) if chain else []
    if named_later:
        programs, how = sorted(named_later, key=lambda program: program.name.lower()), "named"
    else:
        programs, how = docs.programs_meant(db, request, person)
    if not programs:
        return {"reply": "There is no program yet: add one on the Programs page first.", "links": [_link("Programs", "/programs/all")], "source": "system"}
    lang = next((found for found in (docs.language_asked(said, "") for said in newest_first) if found), "fr")
    other = "en" if lang == "fr" else "fr"
    in_lang = {"fr": "en français", "en": "en anglais"}[lang] if fr else {"fr": "in French", "en": "in English"}[lang]
    every = db.query(Program).count()
    one = programs[0] if len(programs) == 1 else None
    ids = "" if len(programs) >= every else ",".join(program.id for program in programs)
    short = any(docs.wants_summary(said) for said in newest_first)       # "in short", "a summary", "résumé"
    about = one.id if one is not None else ("summary" if short else "all")

    if one is not None:
        style = "one_page" if short else program_pdf.clean_style(getattr(get_settings(db), "program_pdf_style", None))
        address = f"/programs/{one.id}/pdf/{style}?" + urlencode({"lang": lang})
        what = f"le programme de {one.name}" if fr else f"the program of {one.name}"
    else:
        address = f"/programs/{'summary' if short else 'catalogue'}.pdf?" + urlencode({"lang": lang, **({"ids": ids} if ids else {})})
        if fr:
            what = f"toutes les {len(programs)} formations" if not ids else f"{len(programs)} formations ({_names_list(programs).replace(' and ', ' et ').replace(' more', ' autres')})"
        else:
            what = f"all {len(programs)} programs" if not ids else f"{len(programs)} programs ({_names_list(programs)})"
        if short:
            what = (f"le résumé de {what}" if fr else f"the summary of {what}")

    if person is not None:
        file = docs.make_file(db, programs, lang, person.full_name, summary=short)
        from app.services import chatbot_send

        if may_send.get() and chatbot_send.auto_send(db):
            done = _send_document(db, person, programs, lang, short, file, fr)
            if done is not None:
                return done
        query = {"candidate": person.id, "attach": file.id, "program": about, "count": len(programs)}
        first = _name(person).split()[0]
        if fr:
            reply = (f"L'email pour {_name(person)} est prêt, avec le PDF en pièce jointe ({in_lang}) : {what}. "
                     "Relisez-le et appuyez sur Envoyer : rien ne part avant.")
        else:
            reply = (f"The email to {_name(person)} is ready with the PDF attached ({in_lang}): {what}. "
                     "Read it and press Send: nothing goes before you do.")
        return {"reply": reply, "source": "action", "links": [
            _link(f"Open the email to {first}", "/inbox/compose?" + urlencode(query)),
            _link("See the PDF", address + "&" + urlencode({"for": person.id}), tab=True),
        ]}

    file = docs.make_file(db, programs, lang, summary=short)
    compose = "/inbox/compose?" + urlencode({"attach": file.id, "program": about, "count": len(programs)})
    if one is not None and short:
        reply = (f"Voici {one.name} sur une page ({in_lang}) : ce qu'elle enseigne, le rythme et les tarifs." if fr
                 else f"Here is {one.name} on one page ({in_lang}): what it teaches, its pace and its prices.")
        links = [_link("Open the PDF", address, tab=True), _link("Download it", address + "&download=1"), _link("Other looks", f"/programs/{one.id}/pdf")]
    elif short:
        reply = (f"Voici {what} dans un seul PDF ({in_lang}), en deux ou trois pages : pour chacune, ce qu'elle enseigne, sa durée, son parcours et ses tarifs." if fr
                 else f"Here is {what} in one PDF ({in_lang}), on two or three pages: for each one, what it teaches, how long it lasts, its path and its prices.")
        links = [_link("Open the PDF", address, tab=True), _link("Download it", address + "&download=1")]
    elif one is not None:
        weeks = sum(len(level.weeks) for level in one.levels)
        if fr:
            reply = (f"Voici le PDF de {one.name} ({in_lang})" + (f" : {weeks} semaines, semaine par semaine, avec le rythme et les tarifs." if weeks else ", avec le rythme et les tarifs."))
        else:
            reply = (f"Here is the PDF of {one.name} ({in_lang})" + (f": {weeks} weeks, week by week, with the pace and the prices." if weeks else ", with the pace and the prices."))
        links = [_link("Open the PDF", address, tab=True), _link("Download it", address + "&download=1"),
                 _link("Other looks", f"/programs/{one.id}/pdf")]
    else:
        if fr:
            reply = (f"Voici {what} dans un seul PDF ({in_lang}) : une page avec toutes en un coup d'œil (durée, rythme, tarifs), "
                     "puis chacune en détail, semaine par semaine.")
        else:
            reply = (f"Here are {what} with their details in one PDF ({in_lang}): a first page with all of them at a glance "
                     "(duration, pace, prices), then each program in detail, week by week.")
        links = [_link("Open the PDF", address, tab=True), _link("Download it", address + "&download=1")]
    links.append(_link("Send it by email", compose))
    links.append(_link("In English" if other == "en" else "En français",
                       address.replace(f"lang={lang}", f"lang={other}"), tab=True))
    reply += ("\nPour l'envoyer à quelqu'un, dites par exemple : « envoie-le à Fares »." if fr
              else "\nTo send it to someone, say for example: “send it to Fares”.")
    return {"reply": reply, "links": links, "source": "action"}


# ------------------------------------------------------------------
# Any answer as a PDF
# ------------------------------------------------------------------

PDF_WORDS = r"pdf|printable|imprimable|a imprimer|to print|print (it|this|that)|imprime\w*"
# What is said around "in PDF" that says nothing of what goes in it.
_AROUND = set("""make made turn put give send sent get want need can could would you me it this that these those the a an of in into as on
to for and also now again please same answer reply version format file document doc pdf printable print here there
fais fait faire mets met mettre transforme donne envoie envoyer moi le la les un une des du de en au sous forme fichier ce cet cette ca cela
ceci aussi meme maintenant svp stp pour et avec reponse imprimable imprimer imprime""".split())


def _without_pdf_words(message: str, recipient: Candidate | None) -> str:
    """The question itself: the message without "in PDF", "send it to Fares" and the like."""
    said = re.sub(r"(?i)\b(?:(?:en|in|as|au format|sous forme(?: de)?|into)\s+)?(?:a\s+|an\s+|un\s+)?pdf\b", " ", message)
    said = re.sub(r"(?i)\b(?:printable|imprimable|à imprimer|a imprimer|to print)\b", " ", said)
    if recipient is not None:
        for part in sorted((recipient.full_name or "").split(), key=len, reverse=True):
            said = re.sub(r"(?i)\b(?:to|à|a|pour|for)?\s*" + re.escape(part) + r"\b", " ", said)
    said = re.sub(r"(?i)^\s*(?:please\s+|svp\s+)?(?:send|envoie|envoyer|give|donne|make|fais|put|mets)(?:\s+(?:me|moi|us|nous))?\s+", " ", said)
    return " ".join(said.replace(" ,", ",").split()).strip(" ,.;:!?")


def _last_exchange(history: list[dict] | None, message: str) -> tuple[str, str] | None:
    """(the question asked before this message, the answer it got)."""
    items = [item for item in (history or []) if item.get("content")]
    if items and items[-1].get("role") == "user" and items[-1]["content"].strip() == (message or "").strip():
        items = items[:-1]
    for index in range(len(items) - 1, 0, -1):
        if items[index].get("role") == "assistant" and items[index - 1].get("role") == "user":
            return items[index - 1]["content"], items[index]["content"]
    return None


def as_pdf(db: Session, message: str, history: list[dict] | None = None, fr: bool = False) -> dict | None:
    """Any information as a PDF: what is asked with "in PDF", or the answer just given ("make it a PDF").
    To open, to change first, or ready in an email to a client."""
    from app.services import chatbot, document_pdf, program_pdf

    text = _plain(message)
    if not _has(text, PDF_WORDS):
        return None
    if not program_pdf.available():
        return {"reply": "The PDF library is not installed on this computer: run “pip install reportlab” and restart the application.",
                "links": [], "source": "system"}
    person = chatbot._candidate_in(db, text)
    first = _plain((person.full_name or "").split()[0]) if person is not None and person.full_name else ""
    recipient = person if first and re.search(r"(?:^|\s)(?:to|a|pour|for)\s+" + re.escape(first), text) else None
    question = _without_pdf_words(message, recipient)
    asked = [word for word in _plain(question).split() if len(word) >= 3 and word not in _AROUND]
    if asked:
        found = chatbot.answer(db, question)
        title = question
    else:
        before = _last_exchange(history, message)
        if before is None:
            reply = ("Dites-moi ce qu'il faut mettre dans le PDF, par exemple « la liste des tarifs en pdf », ou posez une question puis dites « en pdf »."
                     if fr else "Tell me what to put in the PDF, for example “the price list in pdf”, or ask something, then say “make it a pdf”.")
            return {"reply": reply, "links": [_link("PDF documents", "/programs/documents#write")], "source": "guide"}
        title, answer_text = before
        title = _without_pdf_words(title, None)
        found = {"reply": answer_text, "source": "history"}
    if found.get("source") == "none" or not (found.get("reply") or "").strip():
        reply = ("Je n'ai rien trouvé à mettre dans un PDF pour cela. Vous pouvez écrire le texte vous-même :" if fr
                 else "I found nothing to put in a PDF for that. You can write the text yourself:")
        return {"reply": reply, "links": [_link("PDF documents", "/programs/documents#write")], "source": "none"}
    title = (title[:1].upper() + title[1:]).rstrip(" ?!.")[:120] or "Document"
    body = found["reply"].strip()
    lang = "fr" if fr else "en"
    file = document_pdf.store(db, title, body, lang, recipient.full_name if recipient is not None else None)
    compose = {"attach": file.id, "program": "document", "title": title}
    change = "/programs/documents?" + urlencode({"title": title, "text": body[:3000]}) + "#write"
    if recipient is not None:
        compose["candidate"] = recipient.id
        name = _name(recipient)
        reply = (f"L'email pour {name} est prêt, avec « {title} » en PDF. Relisez-le et appuyez sur Envoyer : rien ne part avant." if fr
                 else f"The email to {name} is ready with “{title}” attached as a PDF. Read it and press Send: nothing goes before you do.")
        links = [_link(f"Open the email to {name.split()[0]}", "/inbox/compose?" + urlencode(compose)), _link("See the PDF", f"/inbox/files/{file.id}", tab=True),
                 _link("Change it first", change)]
    else:
        reply = (f"Voici « {title} » en PDF. Relisez-le avant de l'envoyer à un client : il dit exactement ce que dit la réponse." if fr
                 else f"Here is “{title}” as a PDF. Read it before sending it to a client: it says exactly what the answer says.")
        links = [_link("Open the PDF", f"/inbox/files/{file.id}", tab=True), _link("Send it by email", "/inbox/compose?" + urlencode(compose)),
                 _link("Change it first", change)]
    return {"reply": reply, "links": links, "source": "action"}


# ------------------------------------------------------------------
# Writing to someone, reminding them
# ------------------------------------------------------------------

WRITE_WORDS = (r"^(please |svp |stp )?(write|ecris|ecrire|compose|draft|contact|reply|repond\w*|send (an |a )?(e ?mail|message|mail)|"
               r"envoie (un |une )?(e ?mail|message|mail)|envoyer (un |une )?(e ?mail|message|mail))|"
               r"(i want to|i d like to|i would like to|je veux|je voudrais|help me|aide moi a|can you|could you|peux tu|pourrais tu) "
               r"(write|ecrire|contact\w*|send (an |a )?(e ?mail|message)|envoyer (un |une )?(e ?mail|message)|repondre|reply)")
REMIND_WORDS = r"remind\w*|rappel\w*|relance\w*|relancer"


def write_to(db: Session, person: Candidate, fr: bool = False) -> dict:
    name, first = _name(person), _name(person).split()[0]
    reply = (f"L'email pour {name} est ouvert : écrivez, ou partez d'un texte prêt, puis envoyez." if fr
             else f"The email to {name} is open: write it, or start from a ready text, then send.")
    return {"reply": reply, "source": "action", "links": [
        _link(f"Write to {first}", f"/inbox/compose?candidate={person.id}"),
        _link("Their discussions", f"/inbox?candidate={person.id}"),
    ]}


def remind(db: Session, person: Candidate, text: str, fr: bool = False) -> dict:
    """A reminder for one candidate: the payment one (read and changed before it goes), or one of the admin's own."""
    from app.services import chatbot_data, payment_reminders

    name, first = _name(person), _name(person).split()[0]
    if _has(text, r"pay\w*|paiement|money|argent|owe\w*|doit|reste|due|frais|fees?"):
        found = chatbot_data.balance(db, person)
        if not payment_reminders.owing(person):
            return found
        found["reply"] += ("\nLe rappel de paiement n'est envoyé qu'une fois par lui-même : relisez-le et envoyez-le d'ici." if fr
                           else "\nThe payment reminder goes out once by itself; another one is yours to send: read it, change it, send it.")
        found["source"] = "action"
        return found
    reply = (f"Préparez le rappel pour {name} : choisissez le texte (ou une idée), la date et l'heure. Il part une fois, à ce moment-là." if fr
             else f"Plan the reminder for {name}: choose the text (or one of the ideas), the day and the hour. It goes once, at that time.")
    return {"reply": reply, "source": "action", "links": [
        _link(f"Plan a reminder for {first}", "/reminders?" + urlencode({"audience": "candidate", "to": person.id})),
        _link(f"Write to {first} now", f"/inbox/compose?candidate={person.id}"),
    ]}


REGISTER_WORDS = r"register\w*|enrol\w*|sign (him |her |them )?up|inscri\w*|add (him |her |them )?(to|in|into)|ajoute\w* (a|au|dans|en)"


def register(db: Session, person: Candidate, program: Program, fr: bool = False) -> dict:
    """Registering someone in a program: their page, with the program already chosen. The admin presses Add."""
    from app.models.registration import RegistrationStatus

    name, first = _name(person), _name(person).split()[0]
    kept = [item for item in person.registrations if item.program_id == program.id and item.status != RegistrationStatus.CANCELLED]
    if kept:
        item = kept[0]
        total = item.total_amount or item.total_price or 0
        state = ("payé" if fr else "paid in full") if item.remaining_amount <= 0 else (f"{item.paid_amount or 0:.0f} DT payés sur {total:.0f}" if fr else f"paid {item.paid_amount or 0:.0f} of {total:.0f} DT")
        where = (f", {'classe' if fr else 'in'} {item.class_group.name}" if item.class_group else "")
        reply = (f"{name} est déjà inscrit(e) en {program.name} ({state}{where})." if fr
                 else f"{name} is already registered in {program.name} ({state}{where}).")
        return {"reply": reply, "source": "system", "links": [_link(f"Open {name}", f"/candidate/{person.id}#registration-{item.id}")]}
    price = f"{program.price_online or 0:.0f} DT online, {program.price_onsite or 0:.0f} DT on site"
    reply = (f"{name} n'est pas encore inscrit(e) en {program.name} ({price}). Sa page s'ouvre avec {program.name} déjà choisi : "
             "choisissez en ligne ou sur place, puis « Add »." if fr else
             f"{name} is not registered in {program.name} yet ({price}). Their page opens with {program.name} already chosen: "
             "pick online or on site, then press Add.")
    return {"reply": reply, "source": "action",
            "links": [_link(f"Register {first} in {program.name}", f"/candidate/{person.id}?" + urlencode({"add": program.id}) + "#programs")]}


# ------------------------------------------------------------------
# A file for Excel
# ------------------------------------------------------------------

EXPORT_WORDS = r"export\w*|excel|csv|spreadsheet|tableur|feuille de calcul|xlsx?"
# What can be exported, by the words that name it (the first one named wins; candidates when none is).
_EXPORTS = [("payments", r"payments?|paiements?|money|argent"), ("registrations", r"registrations?|inscriptions?"),
            ("programs", r"programs?|formations?"), ("classes", r"class\w*"), ("professors", r"professors?|teachers?|formateurs?"),
            ("sessions", r"sessions?|seances?"), ("attendance", r"presence|attendance|absences?"),
            ("candidates", r"candidates?|candidats?|students?|etudiants?|people|contacts?")]


def export(text: str, fr: bool = False) -> dict:
    from app.services import data_editor

    keys = [key for key, words in _EXPORTS if _has(text, words)] or ["candidates"]
    names = [data_editor.TABLES[key][0] for key in keys[:3]]
    reply = ((f"Voici le fichier pour Excel : {', '.join(names)}. Il s'ouvre directement dans Excel, accents compris. "
              "Chaque tableau de « Toutes les données » a le même bouton Excel.") if fr else
             (f"Here is the file for Excel: {', '.join(names)}. It opens directly in Excel, accents included. "
              "Every table in “All the data” has the same Excel button."))
    links = [_link(f"{name} for Excel", f"/data/{key}.csv") for key, name in zip(keys[:3], names)] + [_link("All the data", "/data")]
    return {"reply": reply, "links": links, "source": "action"}


# ------------------------------------------------------------------
# What the assistant can do
# ------------------------------------------------------------------

ABILITY_WORDS = (r"what can you do|what do you do|what can i ask|what are you able|how can you help|^help$|^aide$|^help me$|capabilit\w*|abilit\w*|"
                 r"que (peux|sais) tu faire|qu est ce que tu (peux|sais) faire|tu sers a quoi|que peut on te demander|comment peux tu m aider")
EXAMPLES = [
    "Send me all the programs in PDF",
    "Summarize all the programs in PDF",
    "The price list in pdf",
    "Send the DevOps program to Fares",
    "What should I do today?",
    "Who registered this week?",
    "How much does Ahmed owe?",
    "Which candidates are in Python?",
]


def abilities(fr: bool = False) -> dict:
    if fr:
        reply = ("Voici ce que je fais, demandez avec vos mots :\n"
                 "• Documents : le PDF d'une formation, de toutes en détail ou en bref, ou de n'importe laquelle de mes réponses (« … en pdf »), à ouvrir, télécharger ou envoyer à vos clients.\n"
                 "• Emails : j'ouvre l'email pour quelqu'un, avec le PDF déjà joint quand il le faut. Rien ne part sans vous.\n"
                 "• Vos données : candidats, inscriptions, paiements, ce qu'une personne doit, ses coordonnées, classes, formateurs, séances, emails, rappels.\n"
                 "• Votre journée : ce qui vous attend aujourd'hui, le plus urgent d'abord.\n"
                 "• Le marché et le site 9antra.tn : ce que demandent les offres, ce que dit le site.\n"
                 "• Comment faire : une réponse courte et la page à ouvrir.")
    else:
        reply = ("Here is what I do; ask in your own words:\n"
                 "• Documents: the PDF of a program, of all of them in detail or in short, or of any of my answers (“… in pdf”), to open, download or send to your clients.\n"
                 "• Emails: I open the email for someone, with the PDF already attached when needed. Nothing goes without you.\n"
                 "• Your data: candidates, registrations, payments, what someone owes, how to reach them, classes, professors, sessions, emails, reminders.\n"
                 "• Your day: what waits for you today, the most pressing first.\n"
                 "• The market and 9antra.tn: what job offers ask, what the website says.\n"
                 "• How to do something: a short answer and the page to open.")
    return {"reply": reply, "links": [_link("How it works", "/guide")], "source": "guide", "suggestions": EXAMPLES}

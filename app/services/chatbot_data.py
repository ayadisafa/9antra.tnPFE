"""What the assistant reads straight from the database, beyond its fixed questions.

Emails (of one candidate, of a period, waiting, or about some words), a class
or a professor named in the question, the money received in a period, the
reminders planned, every candidate, who registered in a period, what one
candidate owes and how to reach them, who follows a program, the programs
without a class, what needs the admin today, and a search across everything
when nothing else fits. Every answer is rows of the database, said as they
are, with the page that shows them. Nothing here asks a language model.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.candidate import Candidate
from app.models.candidate_email import CandidateEmail
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.payment import Payment
from app.models.professor import Professor
from app.models.program import Program
from app.models.registration import Registration, RegistrationStatus
from app.services.schedule_utils import local_now, utc_to_local

EMAIL_WORDS = (r"e ?mails?|mails?|wrote|written|writes?|ecrit|ecrivent|messages?|discussions?|repl(y|ied)|answered|repondu|reponses?|"
               r"sent|envoye\w*|received|recu\w*|inbox|boite|courriers?")
MONEY_WORDS = r"received|recu\w*|encaisse\w*|revenue|recettes?|income|collected|paid (us|in)|payments? (received|made|of)|how much (money|did we)|combien (a t on|avons nous) (recu|encaisse)"
_STOP = set("""a an the of and or to in on for with about from by is are was were be been do does did have has had i me my we our you your it its this that these those
what which who whom when where why how many much all any some every each show give tell find search list me us can could please there their them they he she his her
le la les un une des du de d et ou a au aux en dans sur pour avec par est sont etait ont a t on je moi mon ma mes nous notre nos vous votre vos il elle ils elles ce cet cette ces
que qui quoi quand ou pourquoi comment combien tout tous toute toutes quel quelle quels quelles montre donne dis trouve cherche liste svp merci y s il n ne pas plus""".split())


def plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9+#@.]+", " ", folded).split())


def _has(text: str, words: str) -> bool:
    return re.search(r"(?<![a-z0-9])(?:" + words + r")(?![a-z0-9])", text) is not None


def _link(label: str, url: str) -> dict:
    return {"label": label, "url": url}


def _when(value: datetime | None) -> str:
    local = utc_to_local(value)
    return local.strftime("%d %b %H:%M") if local else "?"


def _to_utc(local: datetime) -> datetime:
    from app.services.reminders import to_utc

    return to_utc(local)


def period(text: str) -> tuple[datetime, datetime, str] | None:
    """(start, end, name) in UTC of the period a question names, if it names one."""
    now = local_now()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if _has(text, "today|aujourd hui|ce jour"):
        start, end, name = day, day + timedelta(days=1), "today"
    elif _has(text, "yesterday|hier"):
        start, end, name = day - timedelta(days=1), day, "yesterday"
    elif _has(text, "this week|cette semaine"):
        start, end, name = day - timedelta(days=day.weekday()), day + timedelta(days=1), "this week"
    elif _has(text, "last week|la semaine derniere|semaine passee"):
        monday = day - timedelta(days=day.weekday())
        start, end, name = monday - timedelta(days=7), monday, "last week"
    elif _has(text, "this month|ce mois"):
        start, end, name = day.replace(day=1), day + timedelta(days=1), "this month"
    elif _has(text, "last month|le mois dernier|mois passe"):
        first = day.replace(day=1)
        start, end, name = (first - timedelta(days=1)).replace(day=1), first, "last month"
    elif _has(text, "this year|cette annee"):
        start, end, name = day.replace(month=1, day=1), day + timedelta(days=1), "this year"
    else:
        found = re.search(r"(?:last|past|derniers?|dernieres?) (\d{1,3}) (?:days|jours)", text)
        if not found:
            return None
        start, end, name = day - timedelta(days=int(found.group(1)) - 1), day + timedelta(days=1), f"the last {found.group(1)} days"
    return _to_utc(start), _to_utc(end), name


def _mail_line(db: Session, row: CandidateEmail, with_name: bool = True) -> str:
    person = db.get(Candidate, row.candidate_id) if with_name and row.candidate_id else None
    who = (person.full_name or person.email) + " · " if person is not None else ""
    way = "received" if row.direction == "inbound" else "sent"
    words = " ".join((row.body or "").split())
    return f"• {_when(row.received_at)} · {way} · {who}{row.subject or '(no subject)'}: {words[:130]}{'…' if len(words) > 130 else ''}"


def search_words(text: str) -> list[str]:
    return [word for word in text.split() if len(word) >= 3 and word not in _STOP and not word.isdigit()]


def emails(db: Session, text: str, candidate: Candidate | None) -> dict | None:
    """Emails: of one candidate, of a period, waiting for an answer, or about some words."""
    query = db.query(CandidateEmail)
    if candidate is not None:
        rows = query.filter(CandidateEmail.candidate_id == candidate.id).order_by(CandidateEmail.received_at.desc()).limit(6).all()
        name = candidate.full_name or candidate.email
        if not rows:
            return {"reply": f"No email with {name} is recorded yet.", "links": [_link(name, f"/candidate/{candidate.id}")], "source": "system"}
        total = query.filter(CandidateEmail.candidate_id == candidate.id).count()
        waiting = query.filter(CandidateEmail.candidate_id == candidate.id, CandidateEmail.direction == "inbound",
                               CandidateEmail.requires_admin.is_(True), CandidateEmail.replied_at.is_(None)).count()
        reply = (f"{total} email{'s' if total != 1 else ''} with {name}"
                 + (f", {waiting} waiting for your answer" if waiting else "") + ". The latest:\n"
                 + "\n".join(_mail_line(db, row, with_name=False) for row in rows))
        return {"reply": reply, "links": [_link("Their discussions", f"/inbox?candidate={candidate.id}"), _link(name, f"/candidate/{candidate.id}")], "source": "system"}

    if _has(text, "waiting|unanswered|not answered|sans reponse|en attente|need\\w* (an |a |my )?(answer|reply)|a repondre|pending"):
        rows = (query.filter(CandidateEmail.direction == "inbound", CandidateEmail.requires_admin.is_(True), CandidateEmail.replied_at.is_(None))
                .order_by(CandidateEmail.received_at.desc()).limit(8).all())
        if not rows:
            return {"reply": "No email is waiting for your answer.", "links": [_link("Inbox", "/inbox")], "source": "system"}
        return {"reply": f"{len(rows)} email{'s' if len(rows) != 1 else ''} wait for your answer:\n" + "\n".join(_mail_line(db, row) for row in rows),
                "links": [_link("Inbox", "/inbox")], "source": "system"}

    span = period(text)
    if span is not None:
        start, end, name = span
        rows = query.filter(CandidateEmail.received_at >= start, CandidateEmail.received_at < end).order_by(CandidateEmail.received_at.desc()).all()
        received = sum(1 for row in rows if row.direction == "inbound")
        reply = f"Emails {name}: {received} received, {len(rows) - received} sent."
        if rows:
            reply += "\n" + "\n".join(_mail_line(db, row) for row in rows[:6])
        return {"reply": reply, "links": [_link("Inbox", "/inbox")], "source": "system"}

    about = re.search(r"(?:about|regarding|concerning|containing|contain\w*|with the words?|au sujet de|a propos de|sur|concernant|contenant|parl\w+ de)\s+(.+)$", text)
    words = search_words(about.group(1)) if about else []
    if words:
        clauses = [or_(CandidateEmail.subject.ilike(f"%{word}%"), CandidateEmail.body.ilike(f"%{word}%")) for word in words[:4]]
        rows = query.filter(*clauses).order_by(CandidateEmail.received_at.desc()).limit(6).all()
        if not rows and len(clauses) > 1:
            rows = query.filter(or_(*clauses)).order_by(CandidateEmail.received_at.desc()).limit(6).all()
        said = "“" + " ".join(words[:4]) + "”"
        if not rows:
            return {"reply": f"No email mentions {said}.", "links": [_link("Inbox", "/inbox")], "source": "system"}
        return {"reply": f"Emails that mention {said}:\n" + "\n".join(_mail_line(db, row) for row in rows), "links": [_link("Inbox", "/inbox")], "source": "system"}
    return None


def class_in(db: Session, text: str) -> ClassGroup | None:
    for group in sorted(db.query(ClassGroup).all(), key=lambda item: -len(item.name or "")):
        name = plain(group.name or "").replace(".", " ")
        if len(name) >= 3 and _has(" ".join(text.replace(".", " ").split()), re.escape(" ".join(name.split()))):
            return group
    return None


def class_card(db: Session, group: ClassGroup) -> dict:
    from app.services import schedule_utils
    from app.services.enrollment_service import class_members

    members = class_members(db, group.id)
    names = [(item.candidate.full_name or item.candidate.email) for item in members if item.candidate is not None]
    start = schedule_utils.class_start_dt(group)
    state = {ClassStatus.NOT_STARTED: "not started", ClassStatus.ONGOING: "running", ClassStatus.COMPLETED: "completed"}.get(group.status, str(group.status))
    lines = [
        f"{group.name}: {group.program.name if group.program else 'no program'}, {state}.",
        f"• Professor: {group.professor_name or (group.professor.name if group.professor else 'none yet')}",
        f"• Hours: {schedule_utils.describe_schedule_en(group) or 'not set'}" + (f", first session {start.strftime('%d %b %Y')}" if start else ""),
        f"• {len(names)} candidate{'s' if len(names) != 1 else ''}" + (": " + ", ".join(names[:12]) + ("…" if len(names) > 12 else "") if names else ""),
    ]
    return {"reply": "\n".join(lines), "links": [_link(group.name, f"/classes/{group.id}")], "source": "system"}


def professor_in(db: Session, text: str) -> Professor | None:
    people = db.query(Professor).all()
    for person in people:
        if person.name and _has(text, re.escape(plain(person.name))):
            return person
    found = [person for person in people
             if any(len(part) >= 4 and _has(text, re.escape(part)) for part in plain(person.name or "").split())]
    return found[0] if len(found) == 1 else None


def professor_card(db: Session, person: Professor) -> dict:
    groups = db.query(ClassGroup).filter(ClassGroup.professor_id == person.id).all()
    running = [group.name for group in groups if group.status != ClassStatus.COMPLETED]
    lines = [f"{person.name}" + (f" ({person.email})" if person.email else "") + (f", {person.phone}" if person.phone else "") + ":",
             f"• Specialities: {person.specialties or 'none written yet'}",
             f"• Classes: {', '.join(running) if running else 'none at the moment'}" + (f" ({len(groups) - len(running)} completed)" if len(groups) > len(running) else ""),
             f"• {'Available' if person.is_available else 'Not available at the moment'}"]
    return {"reply": "\n".join(lines), "links": [_link("Professors", "/classes/?tab=professors")], "source": "system"}


def money(db: Session, text: str) -> dict:
    """What was received, in a period when one is named, and what is still to come."""
    span = period(text)
    query = db.query(Payment)
    if span is not None:
        query = query.filter(Payment.payment_date >= span[0], Payment.payment_date < span[1])
    rows = query.order_by(Payment.payment_date.desc()).all()
    total = sum(row.amount or 0 for row in rows)
    owed = sum(item.remaining_amount for item in db.query(Registration).filter(Registration.status != RegistrationStatus.CANCELLED).all() if item.remaining_amount > 0)
    reply = f"Received {span[2] if span else 'so far'}: {total:.0f} DT in {len(rows)} payment{'s' if len(rows) != 1 else ''}. Still to come: {owed:.0f} DT."
    for row in rows[:5]:
        registration = db.get(Registration, row.registration_id) if row.registration_id else None
        person = registration.candidate if registration is not None else None
        reply += f"\n• {_when(row.payment_date)} · {row.amount:.0f} DT · {(person.full_name or person.email) if person else '?'} · {registration.program_name if registration else ''}"
    return {"reply": reply, "links": [_link("Payments", "/payments")], "source": "system"}


def planned_reminders(db: Session) -> dict:
    from app.services import reminders

    rows = reminders.upcoming(db)
    if not rows:
        return {"reply": "No reminder of yours is planned. The automatic ones (payment, first session, level check) keep working by themselves.",
                "links": [_link("Reminders", "/reminders")], "source": "system"}
    return {"reply": f"{len(rows)} reminder{'s' if len(rows) != 1 else ''} planned:\n" + "\n".join(
        f"• {_when(row.send_at)} · {row.audience_label} · {row.subject}" for row in rows[:8]), "links": [_link("Reminders", "/reminders#planned")], "source": "system"}


def _name(person: Candidate | None) -> str:
    return (person.full_name or person.email or "?") if person is not None else "?"


def _active(person: Candidate) -> list[Registration]:
    return [item for item in person.registrations if item.status != RegistrationStatus.CANCELLED]


def _program_of(registration: Registration) -> str:
    return registration.program.name if registration.program else (registration.program_name or "a program")


LIST_WORDS = r"(list|show|see|give|display|all|every|liste|affiche|montre|voir|tous|toutes)( me)?( the| les| des| of| de)? (candidates?|students?|learners?|people|candidats?|etudiants?|apprenants?|inscrits?)|candidates? list|liste des candidats|who are (my|the|our) candidates|qui sont (les|mes|nos) candidats"
REGISTERED_WORDS = r"who (has |have )?(registered|signed up|enrolled|joined)|new (candidates?|registrations?|students?|sign ?ups?)|nouveaux (candidats?|inscrits?)|nouvelles? inscriptions?|qui s est inscrit|qui s est inscrite|qui se sont inscrits|(registered|inscrits?|registrations?|inscriptions?) (this|last|today|yesterday|cette|la|aujourd|hier|ce)"
CONTACT_WORDS = (r"phone|telephone|tel|numero|(his|her|their|phone|mobile) number|mobile|whatsapp|contact|joindre|reach|call|appeler|email address|"
                 r"adresse (e ?)?mail|(his|her|their|son|sa) (e ?mail|mail)|(e ?mail|mail) (of|de|du)|what is the (e ?mail|mail)|quel est (le |l )?(e ?mail|mail)")
OWE_WORDS = r"owe\w*|doit|doivent|due|reste a payer|left to pay|balance|solde|how much (does|did|has|is)|combien (doit|a paye|reste)|paid|paye|payment status|statut de paiement"
PEOPLE_WORDS = r"who|which candidates?|candidates?|registered|inscrits?|students?|etudiants?|apprenants?|learners?|people|members?|participants?|follow\w*|suivent|liste? (of|des|de)"
NO_CLASS_WORDS = r"(programs?|formations?) (that )?(have |has |with |without |sans )?(no |aucune |pas de )?class\w*|no class (for|yet)|sans classe|without (a )?class|pas de classe"
TODAY_WORDS = (r"what (should|must|do|have|can) i (to )?do|what to do|to ?do (list|today)|todo|que dois je faire|quoi faire|qu est ce que je dois faire|"
               r"summary of (the day|today)|today s (summary|plan|agenda|priorities)|resume (du jour|de la journee|d aujourd hui)|my day|ma journee|"
               r"priorit\w*|what needs me|what is urgent|qu est ce qui est urgent|urgences?|plan (for|of) (the day|today)|programme du jour")


def candidates_list(db: Session) -> dict:
    """Every candidate, the newest first: what they follow, what they owe, their class."""
    people = sorted(db.query(Candidate).all(), key=lambda person: person.created_at or datetime.min, reverse=True)
    if not people:
        return {"reply": "No candidate yet. They appear when they register on your form, or when you add one.",
                "links": [_link("Add a candidate", "/candidate/all?add=1")], "source": "system"}
    lines = []
    for person in people[:15]:
        active = _active(person)
        due = sum(item.remaining_amount for item in active if item.remaining_amount > 0)
        classes = sorted({item.class_group.name for item in active if item.class_group})
        money_said = f"owes {due:.0f} DT" if due > 0 else ("paid in full" if active else "no program yet")
        lines.append(f"• {_name(person)}: {len(active)} program{'s' if len(active) != 1 else ''}, {money_said}"
                     + (f", in {', '.join(classes[:3])}" if classes else ""))
    reply = f"{len(people)} candidate{'s' if len(people) != 1 else ''}, the newest first:\n" + "\n".join(lines)
    if len(people) > 15:
        reply += f"\n… and {len(people) - 15} more on the Candidates page."
    links = [_link("All candidates", "/candidate/all")] + [_link(_name(person), f"/candidate/{person.id}") for person in people[:3]]
    return {"reply": reply, "links": links, "source": "system"}


def registered(db: Session, text: str) -> dict:
    """Who registered in a period: the last 7 days when the question names none."""
    span = period(text)
    if span is None:
        end = datetime.utcnow()
        span = (end - timedelta(days=7), end + timedelta(minutes=1), "in the last 7 days")
    start, end, name = span
    rows = (db.query(Registration).filter(Registration.registration_date >= start, Registration.registration_date < end,
                                          Registration.status != RegistrationStatus.CANCELLED)
            .order_by(Registration.registration_date.desc()).all())
    if not rows:
        return {"reply": f"Nobody registered {name}.", "links": [_link("Candidates", "/candidate/all")], "source": "system"}
    people: dict[str, list[Registration]] = {}
    for row in rows:
        people.setdefault(row.candidate_id, []).append(row)
    lines = []
    for registrations in list(people.values())[:12]:
        person = registrations[0].candidate
        lines.append(f"• {_name(person)} · {_when(registrations[0].registration_date)}: " + ", ".join(dict.fromkeys(_program_of(item) for item in registrations)))
    reply = (f"{len(people)} candidate{'s' if len(people) != 1 else ''} registered {name}, "
             f"{len(rows)} registration{'s' if len(rows) != 1 else ''}:\n" + "\n".join(lines))
    first = next(iter(people.values()))[0].candidate
    return {"reply": reply, "links": [_link("Candidates", "/candidate/all?show=new"), _link(_name(first), f"/candidate/{first.id}")], "source": "system"}


def balance(db: Session, person: Candidate) -> dict:
    """What one candidate paid and still owes, program by program, and the reminder they got."""
    from app.services import payment_reminders

    active = _active(person)
    owing = [item for item in active if item.remaining_amount > 0]
    total = sum((item.total_amount or item.total_price or 0) for item in active)
    paid = sum(item.paid_amount or 0 for item in active)
    name = _name(person)
    if not active:
        reply = f"{name} has no registration, so nothing to pay."
    elif not owing:
        reply = f"{name} has paid everything: {paid:.0f} DT for {len(active)} program{'s' if len(active) != 1 else ''}."
    else:
        due = sum(item.remaining_amount for item in owing)
        reply = (f"{name} owes {due:.0f} DT (paid {paid:.0f} of {total:.0f} DT):\n"
                 + "\n".join(f"• {_program_of(item)}: paid {item.paid_amount or 0:.0f} of {(item.total_amount or item.total_price or 0):.0f} DT, {item.remaining_amount:.0f} DT left"
                             for item in owing[:8]))
        last = payment_reminders.story(db, person)["last"]
        reply += (f"\nPayment reminder: sent {_when(last.received_at)}." if last is not None
                  else "\nNo payment reminder was sent yet.")
    links = [_link(f"Open {name}", f"/candidate/{person.id}")]
    if owing:
        links.append(_link("Write the payment reminder", f"/candidate/{person.id}/payment-reminder"))
    return {"reply": reply, "links": links, "source": "system"}


def contact(db: Session, person: Candidate) -> dict:
    """How to reach one candidate."""
    from app.services import whatsapp

    name = _name(person)
    parts = [f"Email: {person.email}" if person.email else "No email", f"Phone: {person.phone}" if person.phone else "No phone given"]
    where = ", ".join(part for part in (person.university, person.city) if part)
    reply = f"{name}\n• " + "\n• ".join(parts) + (f"\n• {where}" if where else "")
    links = [_link(f"Write to {name.split()[0]}", f"/inbox/compose?candidate={person.id}"), _link(f"Open {name}", f"/candidate/{person.id}")]
    number = whatsapp.normalise(person.phone) if person.phone else None
    if number:
        links.append(_link("WhatsApp", f"https://wa.me/{number.lstrip('+')}"))
    return {"reply": reply, "links": links, "source": "system"}


def program_people(db: Session, program: Program) -> dict:
    """Who follows a program: paid or not, and in which class."""
    rows = (db.query(Registration).filter(Registration.program_id == program.id, Registration.status != RegistrationStatus.CANCELLED)
            .order_by(Registration.registration_date.desc()).all())
    if not rows:
        return {"reply": f"Nobody is registered in {program.name} yet.", "links": [_link(program.name, f"/programs/{program.id}")], "source": "system"}
    lines = []
    for row in rows[:15]:
        money_said = "paid" if row.remaining_amount <= 0 else f"owes {row.remaining_amount:.0f} DT"
        lines.append(f"• {_name(row.candidate)}: {money_said}, {('in ' + row.class_group.name) if row.class_group else 'no class yet'}")
    paid = sum(1 for row in rows if row.remaining_amount <= 0)
    reply = f"{len(rows)} registration{'s' if len(rows) != 1 else ''} in {program.name}, {paid} paid:\n" + "\n".join(lines)
    return {"reply": reply, "links": [_link(program.name, f"/programs/{program.id}"), _link("Candidates", "/candidate/all")], "source": "system"}


def programs_without_class(db: Session) -> dict:
    """Programs that have no class running or to come, and who waits for one."""
    lines = []
    for program in db.query(Program).order_by(Program.name.asc()).all():
        if db.query(ClassGroup).filter(ClassGroup.program_id == program.id, ClassGroup.status != ClassStatus.COMPLETED).count():
            continue
        waiting = (db.query(Registration).filter(Registration.program_id == program.id, Registration.status != RegistrationStatus.CANCELLED,
                                                 Registration.class_group_id.is_(None)).all())
        paid = sum(1 for row in waiting if row.remaining_amount <= 0)
        lines.append(f"• {program.name}: " + (f"{paid} paid candidate{'s' if paid != 1 else ''} waiting for one" if paid
                                              else f"{len(waiting)} registered, none paid yet" if waiting else "nobody registered"))
    if not lines:
        return {"reply": "Every program has a class running or to come.", "links": [_link("Classes", "/classes/")], "source": "system"}
    reply = (f"{len(lines)} program{'s have' if len(lines) != 1 else ' has'} no class running or to come:\n" + "\n".join(lines)
             + "\nA class is created by itself when a candidate pays; by hand, only for paid candidates who wait.")
    return {"reply": reply, "links": [_link("Classes", "/classes/"), _link("Programs", "/programs/all")], "source": "system"}


OVERVIEW_WORDS = (r"everything about (9antra|the cent\w+|us|our cent\w+)|overview|where (do|does) (we|the cent\w+|9antra) stand|"
                  r"state of the cent\w+|summary of (the cent\w+|9antra)|tout sur 9antra|resume (du centre|de 9antra)|etat du centre|"
                  r"ou en est (le centre|9antra)|vue d ensemble|bilan (du centre|general|de 9antra)|koll chay|kol chay|kolchay")


def overview(db: Session) -> dict:
    """The whole centre in a few lines: people, money, classes, professors, programs, messages and learning."""
    from app.models import Professor, Program
    from app.services import overview as centre

    now = datetime.utcnow()
    figures = centre.figures(db, now)
    board = centre.week_board(db, local_now())
    professors = db.query(Professor).all()
    available = [person for person in professors if person.is_available and not (person.name or "").startswith("À nommer")]
    nobody = centre.programs_without_teacher(db)
    lines = ["9antra at a glance:",
             f"• Candidates: {figures['candidates']} ({figures['new_candidates']} new this week), {figures['registrations']} registrations, "
             f"{figures['paid_registrations']} paid in full.",
             f"• Money: {figures['collected']:.0f} DT received, {figures['due']:.0f} DT still due from {figures['owing_candidates']} candidate(s) "
             f"({figures['collection_rate']}% received).",
             f"• Classes: {figures['classes_running']} running, {figures['classes_upcoming']} about to start, {board['sessions']} session(s) a week.",
             f"• Professors: {len(professors)} ({len(available)} available)."
             + (f" Professors booked twice: {', '.join(item['professor'] for item in board['clashes'])}." if board["clashes"] else ""),
             f"• Programs: {db.query(Program).count()}" + (f"; without a teacher: {', '.join(program.name for program in nobody)}." if nobody else "."),
             f"• Emails waiting for your answer: {figures['emails_waiting']}."]
    if figures["understanding"] is not None:
        lines.append(f"• Weekly questions: {figures['understanding']:.0f}% understood over {figures['answers']} answer(s).")
    for card in board["cards"][:6]:
        lines.append(f"  – {card['name']}: {card['program']}, {card['professor'] or 'no professor'}, "
                     f"{card['enrolled']}/{card['capacity']}" + (f", week {card['week']}/{card['weeks']}" if card["running"] else ""))
    return {"reply": "\n".join(lines), "links": [_link("Dashboard", "/"), _link("Candidates", "/candidate/all"), _link("Payments", "/payments"),
                                                 _link("Classes", "/classes/"), _link("What the assistant knows", "/programs/expertise")],
            "source": "system"}


def today(db: Session) -> dict:
    """What needs the admin today: the dashboard's first recommendations, today's sessions, the emails that wait."""
    from app.models.classsession import ClassSession
    from app.services import advisor

    now = local_now()
    items = [item for item in advisor.for_dashboard(db) if not item.set_aside][:5]
    lines = [f"Today, {now:%A %d %b}:"]
    if items:
        lines.append("To do, most pressing first:")
        lines += [f"• {item.title}" for item in items]
    else:
        lines.append("• Nothing needs you right now.")
    sessions = []
    for session in db.query(ClassSession).filter(ClassSession.session_date == now.date()).all():
        group = db.get(ClassGroup, session.class_group_id)
        if group is None or group.status == ClassStatus.COMPLETED:
            continue
        hour = session.start_time or group.start_time
        sessions.append(f"• {hour.strftime('%H:%M') + ' ' if hour else ''}{group.name}: {session.topic or 'session'}")
    lines.append("Sessions today:" if sessions else "No session today.")
    lines += sessions[:6]
    waiting = (db.query(CandidateEmail).filter(CandidateEmail.direction == "inbound", CandidateEmail.requires_admin.is_(True),
                                               CandidateEmail.replied_at.is_(None)).count())
    lines.append(f"{waiting} email{'s wait' if waiting != 1 else ' waits'} for your answer." if waiting else "No email waits for your answer.")
    links = [_link("Dashboard", "/")]
    if items and items[0].action and items[0].action.get("method") == "get":
        links.append(_link(items[0].action["label"], items[0].action["url"]))
    if waiting:
        links.append(_link("Inbox", "/inbox"))
    return {"reply": "\n".join(lines), "links": links, "source": "system"}


def search(db: Session, text: str) -> dict | None:
    """Everything that mentions the words of the question: people, classes, programs, professors, emails, the website."""
    from app.models.website import WebsiteItem

    words = search_words(text)[:5]
    if not words:
        return None
    lines, links = [], []

    def like(*columns):
        return or_(*[column.ilike(f"%{word}%") for column in columns for word in words])

    for person in db.query(Candidate).filter(like(Candidate.full_name, Candidate.email, Candidate.phone, Candidate.university, Candidate.city, Candidate.admin_notes)).limit(4).all():
        details = ", ".join(part for part in (person.email, person.phone, person.university, person.city) if part)
        lines.append(f"• Candidate: {person.full_name or person.email} ({details})")
        links.append(_link(person.full_name or person.email, f"/candidate/{person.id}"))
    for group in db.query(ClassGroup).filter(like(ClassGroup.name, ClassGroup.professor_name)).limit(3).all():
        lines.append(f"• Class: {group.name}, professor {group.professor_name or 'none yet'}")
        links.append(_link(group.name, f"/classes/{group.id}"))
    for person in db.query(Professor).filter(like(Professor.name, Professor.specialties, Professor.email)).limit(3).all():
        lines.append(f"• Professor: {person.name} ({person.specialties or 'no speciality written'})")
    for program in db.query(Program).filter(like(Program.name, Program.description)).limit(3).all():
        lines.append(f"• Program: {program.name}, {program.price_online or 0:.0f} DT online / {program.price_onsite or 0:.0f} DT on site")
        links.append(_link(program.name, f"/programs/{program.id}"))
    for row in db.query(CandidateEmail).filter(like(CandidateEmail.subject, CandidateEmail.body)).order_by(CandidateEmail.received_at.desc()).limit(3).all():
        lines.append(_mail_line(db, row).replace("• ", "• Email: ", 1))
    for item in db.query(WebsiteItem).filter(WebsiteItem.removed_at.is_(None), like(WebsiteItem.title, WebsiteItem.summary)).limit(3).all():
        lines.append(f"• On the website ({item.kind}): {item.title}")
    if not lines:
        return None
    return {"reply": "Here is what the records say about “" + " ".join(words) + "”:\n" + "\n".join(lines[:12]), "links": links[:4], "source": "system"}

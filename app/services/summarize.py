"""Yasmine sums things up ("résume…", "summarize…", "lkhoulasa…", "لخّص…"): a candidate, their discussion, a class, a
program, the day or the week, the page that is open, a text pasted after the word, or her own last answer.

Everything is read from the records and said in a few lines, with the place to open. A pasted text or a long answer is
summed up by keeping its sentences that say the most (the words that come back most often, the small words left out):
the sentences are the text's own, never rewritten, so a summary can never say something the text did not. No model.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

_ASKS = re.compile(r"\b(r[ée]sum[ée]e?s?|r[ée]sumer|r[ée]sume[sz]?|r[ée]capitul\w*|en bref|summari[sz]\w*|summary|sum (?:it |this |that )?up|tl;?dr|"
                   r"in short|briefly|lkhoulsa|lkhoulasa|5oulasa|khoulasa|khalasni|la5asli|lakhasli)\b|لخّص|لخص|ملخص|اختصر", re.IGNORECASE)
_DISCUSSION = re.compile(r"\b(discussions?|conversations?|[ée]changes?|emails?|e-mails?|messages?|mails?|whatsapp|thread)\b", re.IGNORECASE)
_DAY = re.compile(r"\b(journ[ée]e|aujourd'?hui|today|the day|lyoum|ليوم|اليوم)\b", re.IGNORECASE)
_WEEK = re.compile(r"\b(semaine|week|jem3a|الأسبوع|الاسبوع)\b", re.IGNORECASE)
_PAGE = re.compile(r"\b(cette page|la page|this page|the page|el page|page hedhi|هذه الصفحة)\b", re.IGNORECASE)
_SMALL = set("""le la les un une des du de d l et ou en au aux a à pour par sur dans avec sans ce cet cette ces qui que quoi
dont est sont été être avoir ont a il elle ils elles on nous vous je tu se sa son ses leur leurs plus moins très tout tous
toute toutes mais donc car ne pas n y the a an and or of to in on for with by is are was were be been it this that these
those as at from not no you your we our they their he she his her its""".split())
SUMMARY_MOST = 3                       # sentences kept from a text
_FRENCH_ASK = re.compile(r"r[ée]sum|r[ée]capitul|en bref|journ[ée]e|semaine|cette page|discussion avec|formation", re.IGNORECASE)
_ENGLISH_ASK = re.compile(r"summari|summary|sum (?:it |this |that )?up|in short|briefly", re.IGNORECASE)


def wants(message: str) -> bool:
    return bool(_ASKS.search(message or ""))


def _fr(lang: str | None) -> bool:
    return lang != "en"


def _money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " DT"


# ------------------------------------------------------------------
# A text: its sentences that say the most
# ------------------------------------------------------------------

def sentences(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", (text or "").replace("\r", ""))
    parts = []
    for line in text.split("\n"):
        line = line.strip(" -•*·\t")
        if line:
            parts += [piece.strip() for piece in re.split(r"(?<=[.!?؟])\s+(?=[A-ZÀ-ÖØ-Þ0-9«“\"؀-ۿ])", line) if piece.strip()]
    return parts


def gist(text: str, most: int = SUMMARY_MOST) -> str:
    """The sentences of a text that say the most, in their order."""
    found = sentences(text)
    if len(found) <= most:
        return " ".join(found)
    words = [word for word in re.findall(r"[^\W\d_]{3,}", text.lower()) if word not in _SMALL]
    counts = Counter(words)
    scored = []
    for index, sentence in enumerate(found):
        own = [word for word in re.findall(r"[^\W\d_]{3,}", sentence.lower()) if word not in _SMALL]
        weight = sum(counts[word] for word in set(own)) / (1 + len(own) ** 0.5)
        weight += 0.6 if index == 0 else 0                 # the first sentence often says what it is about
        weight += 0.3 if re.search(r"\d", sentence) else 0  # figures matter
        scored.append((weight, index))
    kept = sorted(index for _, index in sorted(scored, reverse=True)[:most])
    return " ".join(found[index] for index in kept)


def _pasted(message: str) -> str:
    """The text given with the request ("résume : …", or a long text around the word)."""
    after = re.split(r"[:：]\s*", message, maxsplit=1)
    if len(after) == 2 and len(after[1]) >= 120:
        return after[1].strip()
    rest = _ASKS.sub(" ", message).strip(" :,-\n")
    return rest if len(rest) >= 280 else ""


# ------------------------------------------------------------------
# From the records
# ------------------------------------------------------------------

def _candidate(db: Session, candidate, fr: bool) -> dict:
    from app.models.candidate_email import CandidateEmail
    from app.models.registration import PaymentStatus, RegistrationStatus
    from app.models.user import Attendance
    from app.services import program_feedback

    name = candidate.full_name or candidate.email
    lines = []
    owed = 0.0
    for registration in candidate.registrations:
        if registration.status == RegistrationStatus.CANCELLED:
            continue
        total = registration.total_amount or registration.total_price or 0
        paid = registration.paid_amount or 0
        owed += max(0.0, total - paid) if registration.payment_status != PaymentStatus.PAID else 0
        where = registration.class_group.name if registration.class_group else ("pas encore de classe" if fr else "no class yet")
        lines.append(f"{registration.program_name or (registration.program.name if registration.program else '?')} ({where})")
    marks = [row.status for row in db.query(Attendance).filter(Attendance.candidate_id == candidate.id).all()]
    came = sum(1 for mark in marks if mark in ("present", "late"))
    last = (db.query(CandidateEmail).filter(CandidateEmail.candidate_id == candidate.id, CandidateEmail.direction == "inbound")
            .order_by(CandidateEmail.received_at.desc()).first())
    reviews = [row for row in (program_feedback.of_registration(db, registration.id) for registration in candidate.registrations) if row and row.rating]
    parts = []
    if fr:
        parts.append(f"{name} : {', '.join(lines) if lines else 'aucune inscription'}.")
        parts.append(f"Reste à payer : {_money(owed)}." if owed else "Tout est payé.")
        if marks:
            parts.append(f"Présent(e) à {came} séance(s) sur {len(marks)}.")
        if last is not None:
            parts.append(f"Dernier message le {last.received_at:%d/%m} : « {(last.question or last.body or '')[:90].strip()} »"
                         + (", en attente de votre réponse." if last.requires_admin else "."))
        if reviews:
            parts.append(f"Son avis : {reviews[0].rating}★.")
    else:
        parts.append(f"{name}: {', '.join(lines) if lines else 'no registration'}.")
        parts.append(f"Still to pay: {_money(owed)}." if owed else "Everything is paid.")
        if marks:
            parts.append(f"Came to {came} of {len(marks)} session(s).")
        if last is not None:
            parts.append(f"Last message on {last.received_at:%d/%m}: “{(last.question or last.body or '')[:90].strip()}”"
                         + (", waiting for your answer." if last.requires_admin else "."))
        if reviews:
            parts.append(f"Their review: {reviews[0].rating}★.")
    return {"reply": " ".join(parts), "links": [{"label": "Profil" if fr else "Profile", "url": f"/candidate/{candidate.id}"}]}


def _discussion(db: Session, candidate, fr: bool) -> dict:
    from app.models.candidate_email import CandidateEmail

    rows = (db.query(CandidateEmail).filter(CandidateEmail.candidate_id == candidate.id).order_by(CandidateEmail.received_at.asc()).all())
    name = candidate.full_name or candidate.email
    if not rows:
        return {"reply": (f"Aucun message avec {name} pour l'instant." if fr else f"No message with {name} yet."), "links": []}
    came = [row for row in rows if row.direction == "inbound"]
    went = [row for row in rows if row.direction == "outbound"]
    asked = [(row.question or "").strip() for row in came if (row.question or "").strip()]
    asked = list(dict.fromkeys(asked))[-3:]
    waiting = sum(1 for row in came if row.requires_admin)
    first = rows[0].received_at
    if fr:
        text = (f"{len(rows)} messages avec {name} depuis le {first:%d/%m} ({len(came)} reçus, {len(went)} envoyés)."
                + (f" Ils ont demandé : {' ; '.join(asked)}." if asked else "")
                + (f" {waiting} attend(ent) votre réponse." if waiting else " Rien n'attend de réponse."))
    else:
        text = (f"{len(rows)} messages with {name} since {first:%d/%m} ({len(came)} received, {len(went)} sent)."
                + (f" They asked: {'; '.join(asked)}." if asked else "")
                + (f" {waiting} wait(s) for your answer." if waiting else " Nothing waits for an answer."))
    thread = rows[-1].thread_id
    return {"reply": text, "links": [{"label": "La discussion" if fr else "The discussion", "url": f"/inbox?thread={thread}" if thread else f"/inbox?candidate={candidate.id}"}]}


def _class(db: Session, group, fr: bool) -> dict:
    from app.models.user import Attendance
    from app.services import overview, program_feedback
    from app.services.enrollment_service import class_members
    from app.services.schedule_utils import local_now

    members = class_members(db, group.id)
    marks = [row.status for row in db.query(Attendance).filter(Attendance.class_group_id == group.id).all()]
    rate = round(100 * sum(1 for mark in marks if mark in ("present", "late")) / len(marks)) if marks else None
    coming = next((item for item in overview.coming_sessions(db, local_now(), days=14, limit=20) if item["class"].id == group.id), None)
    opinions = program_feedback.of_class(db, group.id)
    program = group.program.name if group.program else ""
    state = {"not_started": ("pas commencée", "not started"), "ongoing": ("en cours", "running"), "completed": ("terminée", "completed")}.get(
        getattr(group.status, "value", str(group.status)), ("", ""))[0 if fr else 1]
    if fr:
        parts = [f"{group.name} ({program}) : {len(members)} candidat(s), {state}, semaine {group.current_week or 0}/{group.total_weeks or '?'}"
                 + (f", formateur {group.professor_name}." if group.professor_name else ", sans formateur.")]
        if coming:
            parts.append(f"Prochaine séance le {coming['date']:%d/%m}" + (f" à {coming['start_time']:%H:%M}." if coming.get("start_time") else "."))
        if rate is not None:
            parts.append(f"Présence : {rate} %.")
        if opinions["stars"]:
            parts.append(f"Avis : {opinions['stars']}★ ({opinions['answered']}).")
    else:
        parts = [f"{group.name} ({program}): {len(members)} candidate(s), {state}, week {group.current_week or 0}/{group.total_weeks or '?'}"
                 + (f", professor {group.professor_name}." if group.professor_name else ", no professor.")]
        if coming:
            parts.append(f"Next session on {coming['date']:%d/%m}" + (f" at {coming['start_time']:%H:%M}." if coming.get("start_time") else "."))
        if rate is not None:
            parts.append(f"Presence: {rate}%.")
        if opinions["stars"]:
            parts.append(f"Reviews: {opinions['stars']}★ ({opinions['answered']}).")
    return {"reply": " ".join(parts), "links": [{"label": "La classe" if fr else "The class", "url": f"/classes/{group.id}"}]}


def _program(db: Session, program, fr: bool) -> dict:
    from app.models import ClassGroup, ClassStatus
    from app.models.registration import Registration, RegistrationStatus
    from app.services import program_feedback

    weeks = [week for level in program.levels for week in level.weeks]
    topics = [week.topic_title for week in weeks if week.topic_title][:4]
    running = db.query(ClassGroup).filter(ClassGroup.program_id == program.id, ClassGroup.status == ClassStatus.ONGOING).count()
    people = db.query(Registration).filter(Registration.program_id == program.id, Registration.status != RegistrationStatus.CANCELLED).count()
    price = program.price_online
    stars = program_feedback.of_program(db, program.id)["stars"]
    if fr:
        parts = [f"{program.name} : {len(weeks) or '?'} semaine(s) en {len(program.levels) or 1} niveau(x)" + (f", {_money(price)} en ligne." if price else ".")]
        if topics:
            parts.append("On y voit : " + ", ".join(topics) + ("…" if len(weeks) > len(topics) else "."))
        parts.append(f"{running} classe(s) en cours, {people} inscrit(s)." + (f" Avis : {stars}★." if stars else ""))
    else:
        parts = [f"{program.name}: {len(weeks) or '?'} week(s) in {len(program.levels) or 1} level(s)" + (f", {_money(price)} online." if price else ".")]
        if topics:
            parts.append("It covers: " + ", ".join(topics) + ("…" if len(weeks) > len(topics) else "."))
        parts.append(f"{running} class(es) running, {people} registered." + (f" Reviews: {stars}★." if stars else ""))
    return {"reply": " ".join(parts), "links": [{"label": "La formation" if fr else "The program", "url": f"/programs/{program.id}"}]}


def _period(db: Session, days: int, fr: bool, lang: str) -> dict:
    from app.services import advisor, digest, overview
    from app.services.schedule_utils import _tz, local_now

    start = local_now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    since = start.replace(tzinfo=_tz()).astimezone(timezone.utc).replace(tzinfo=None)
    said = digest.sentence(db, since, "fr" if fr else "en", heading="today")
    if days > 1:
        said = said.replace("Aujourd'hui", "Ces 7 jours", 1).replace("Today", "These 7 days", 1)
    waiting = overview.figures(db, datetime.utcnow())["emails_waiting"]
    todo = len(advisor.for_dashboard(db))
    tail = (f" À faire : {waiting} message(s) à répondre, {todo} chose(s) dans « Ce qui vous attend »." if fr
            else f" To do: {waiting} message(s) to answer, {todo} thing(s) in “What needs you today”.")
    found = digest.lines(db, since, "fr" if fr else "en")
    return {"reply": said + tail, "links": [{"label": item["text"], "url": item["url"]} for item in found[:3]]}


def _page(path: str | None, fr: bool) -> dict | None:
    from app.services import guides, page_tours

    if not path:
        return None
    tour = page_tours.for_path(path)
    guide = guides.for_path(path)
    if tour is None and guide is None:
        return None
    title = (tour or guide)["title"]
    purpose = tour["purpose"] if tour else guide["text"]
    parts = [step["name"] for step in (tour["steps"] if tour else [])][:5]
    if fr:
        text = f"Cette page, {title} : {gist(purpose, 2)}" + (f" On y trouve : {', '.join(parts)}." if parts else "")
    else:
        text = f"This page, {title}: {gist(purpose, 2)}" + (f" You find: {', '.join(parts)}." if parts else "")
    return {"reply": text, "links": []}


# ------------------------------------------------------------------
# The answer
# ------------------------------------------------------------------

def answer(db: Session, message: str, history: list[dict] | None = None, *, lang: str | None = None, path: str | None = None) -> dict:
    """What was asked to be summed up, summed up: {"reply", "links", "source", "sources", "suggestions"}."""
    from app.services import chatbot, chatbot_data

    # The words of the request say its language first ("résume la journée" is French whatever the window guessed).
    if _FRENCH_ASK.search(message):
        fr = True
    elif _ENGLISH_ASK.search(message):
        fr = False
    else:
        fr = _fr(lang) if lang else chatbot._french(message)
    plain = chatbot._plain(message)
    sources = [{"label": "Les fiches de l'application" if fr else "The application's records"}]
    pasted = _pasted(message)
    if pasted:
        return {"reply": ("En bref : " if fr else "In short: ") + gist(pasted), "links": [], "source": "system", "scene": "data",
                "sources": [{"label": "Le texte donné, ses phrases qui en disent le plus" if fr else "The text given, its sentences that say the most"}]}
    if _PAGE.search(message):
        found = _page(path, fr)
        if found:
            return {**found, "source": "system", "scene": "look", "sources": [{"label": "Le guide de la page" if fr else "The page's guide"}]}
    if _DAY.search(message) or _WEEK.search(message):
        return {**_period(db, 7 if _WEEK.search(message) else 1, fr, lang or ""), "source": "system", "scene": "dashboard", "sources": sources}
    candidate = chatbot._candidate_in(db, plain)
    if candidate is not None:
        found = _discussion(db, candidate, fr) if _DISCUSSION.search(message) else _candidate(db, candidate, fr)
        return {**found, "source": "system", "scene": "inbox" if _DISCUSSION.search(message) else "candidates", "sources": sources}
    group = chatbot_data.class_in(db, plain)
    if group is not None:
        return {**_class(db, group, fr), "source": "system", "scene": "classes", "sources": sources}
    program = chatbot._program_in(db, plain)
    if program is not None:
        return {**_program(db, program, fr), "source": "system", "scene": "programs", "sources": sources}
    last = next((item.get("content") or "" for item in reversed(history or []) if item.get("role") == "assistant"), "")
    if last and len(last) >= 140:
        return {"reply": ("En bref : " if fr else "In short: ") + gist(last, 2), "links": [], "source": "system", "scene": "data",
                "sources": [{"label": "Ma réponse précédente" if fr else "My previous answer"}]}
    return {"reply": ("Que voulez-vous que je résume ? Un candidat, sa discussion, une classe, une formation, la journée, la semaine, cette page, "
                      "ou un texte collé après « résume : »." if fr else
                      "What should I sum up? A candidate, their discussion, a class, a program, the day, the week, this page, or a text "
                      "pasted after “summarize:”."),
            "links": [], "source": "system", "sources": [],
            "suggestions": (["Résume la journée", "Résume la semaine", "Résume cette page"] if fr
                            else ["Summarize the day", "Summarize the week", "Summarize this page"])}

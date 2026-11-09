"""What to do when something happens: "a professor is absent, what do I do?", "un candidat n'a pas payé, que faire ?",
"what can I do if the meeting link is missing?".

Each case says what the application already does by itself and what is left to the admin, with how things stand right
now: written here from what the application does (enrollment_service, replacements, meetings, reach, program_feedback),
never guessed. Asked as a case ("what can I do when…", "…je fais quoi ?", "if…"), not as a how-to ("how do I record an
absence?" stays the guide's).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from app.services.ui_words import t as _t

# Asked as a case: what to do (anywhere in the question), or the case first ("if a professor is absent?").
_ASKED = re.compile(r"\bwhat (?:can|should|could|must|do|shall|would) (?:i|we) do\b|\bwhat to do\b|\bwhat now\b|\bwhat do you (?:suggest|advise|recommend)\b|"
                    r"\bque faire\b|\b(?:je|on) fai[ts] quoi\b|\bquoi faire\b|\bqu est ce que (?:je|on) (?:dois|doit|peux|peut|fais|fait)\b|"
                    r"\bque (?:dois|peux) je faire\b|\bcomment (?:reagir|faire)\b|\b(?:chna3mel|chnaamel|chnowa na3mel|chnoua na3mel|na3mel chnowa)\b")
_ASKED_AR = re.compile(r"ماذا أفعل|ماذا نفعل|شنوة نعمل|شنعمل|كيف أتصرف|كيف نتصرف")
_CASE_FIRST = re.compile(r"^(?:and |et )?(?:when|whenever|if|in case|quand|lorsqu\w*|si|en cas|au cas|ken|ki|kan)\b")


@dataclass
class Case:
    key: str
    words: str                              # on the plain question
    title: str
    short: str                              # what to say first, in a line or two
    steps: list[str]
    link: tuple[str, str]
    now: Callable | None = None             # how things stand right now: a sentence, or None
    more: Callable | None = field(default=None)   # steps that depend on the settings


def _owing(db: Session) -> str | None:
    from app.models.registration import PaymentStatus, Registration, RegistrationStatus

    rows = [row for row in db.query(Registration).filter(Registration.payment_status != PaymentStatus.PAID).all()
            if row.status != RegistrationStatus.CANCELLED and row.candidate is not None]
    if not rows:
        return _t("Right now everybody has paid in full.")
    owed = sum(max(0.0, (row.total_amount or row.total_price or 0) - (row.paid_amount or 0)) for row in rows)
    names = sorted({row.candidate.full_name or row.candidate.email for row in rows})
    return _t("Right now {0} still owe {1} DT in all: {2}.", len(names), f"{owed:,.0f}".replace(",", " "), ", ".join(names[:6]) + ("…" if len(names) > 6 else ""))


def _without_professor(db: Session) -> str | None:
    from app.models.classgroup import ClassGroup, ClassStatus

    rows = (db.query(ClassGroup).filter(ClassGroup.professor_id.is_(None), ClassGroup.status != ClassStatus.COMPLETED)
            .order_by(ClassGroup.name).all())
    if not rows:
        return _t("Right now every class has its professor.")
    return _t("Right now waiting for a professor: {0}.", ", ".join(row.name for row in rows[:6]))


def _auto_professor(db: Session) -> list[str]:
    from app.services.settings_service import get_settings

    if get_settings(db).auto_assign_professor:
        return [_t(AUTO_PROFESSOR)]
    return [_t(AUTO_OFF)]


def _absences_ahead(db: Session) -> str | None:
    from app.services import replacements

    rows = [row for row in db.query(replacements.SessionReplacement).all() if row.status in ("searching", "asked", "none")]
    if not rows:
        return None
    return _t("Right now {0} replacement(s) are not settled yet: open the class to see who was asked.", len(rows))


def _not_reached(db: Session) -> str | None:
    from app.services import reach

    missed = reach.not_reached(db)
    if not missed:
        return _t("Right now every message reached its candidate.")
    counted = reach.summary(missed)
    return _t("Right now {0} message(s) did not reach {1} candidate(s).", counted["messages"], counted["people"])


def _links_missing(db: Session) -> str | None:
    from datetime import date, timedelta

    from app.models.classgroup import ClassGroup, ClassStatus
    from app.services import meetings

    soon = []
    for group in db.query(ClassGroup).filter(ClassGroup.status != ClassStatus.COMPLETED, ClassGroup.meeting_url.is_(None)).all():
        start = group.start_date.date() if hasattr(group.start_date, "date") else group.start_date
        if start and date.today() <= start <= date.today() + timedelta(days=7) and meetings.mode_of(db, group) != "onsite":
            soon.append(group.name)
    return _t("Starting within a week without a meeting link: {0}.", ", ".join(soon)) if soon else None


AUTO_PROFESSOR = ("The moment a professor of its program is free at its hours from its start date (no other class then, not absent), "
                  "they take it by themselves and are told in the app, by email, WhatsApp and SMS.")
AUTO_OFF = "“Assign an available professor to new classes” is off (Settings › Classes): nobody takes it by themselves, so choose one."
_PROFESSOR = r"(?:professor|prof|teacher|formateur|formatrice|enseignant\w*|trainer)s?"
CASES = [
    Case("professor-absent",
         _PROFESSOR + r" (?:\w+ ){0,3}(?:absent\w*|absence|sick|ill|malade|can t come|cannot come|won t come|isn t coming|ne (?:peut|pourra|vient) pas(?: venir)?|"
         r"ghayeb|ghayba|mridh|mrith|ma ynajjamch yji)|(?:absence|absent\w*) (?:\w+ ){0,3}(?:of |d un |du |de |mte3 )?" + _PROFESSOR,
         "When a professor is absent",
         "Record their absence (Classes › Professors › their card › Absence): each of their sessions that day then asks a free professor of the program to replace them, by a private link.",
         ["Record their absence: Classes › Professors › their card › Absence (the dates, the hours if not the whole day, the reason).",
          "Each of their sessions on those days then asks a professor of the program who is free then, one at a time, by a private link to confirm.",
          "Yes: they replace them for that session only. No, or no answer within 6 hours (1 hour when it is close): the next one is asked.",
          "Nobody left: you are told, and the class page shows it; then find someone or move the session."],
         ("Professors", "/classes/?tab=professors"), now=_absences_ahead),
    Case("unpaid",
         r"(?:not|hasn t|haven t|has not|have not|didn t|did not|doesn t|n a pas|n ont pas|pas encore|mazal ma|ma) ?(?:paid|pay|paye\w*|regle\w*|khallas\w*|5allas\w*)|"
         r"unpaid|impaye\w*|owes?|owing|doit encore|reste a payer|ne pa(?:ie|ye) pas|retard de paiement|late (?:payment|to pay)|ma ?khallasch|ma5allasch",
         "When a candidate has not paid",
         "One reminder goes by itself 24 hours after registering; after that, call them or send it again from their file. No class until they have paid in full.",
         ["One payment reminder goes by itself, 24 hours after registering; the application never sends a second one by itself.",
          "After that, call them, or send it again yourself: their file › Payment reminder.",
          "Until they have paid in full they are placed in no class; once they have, a class is found or created for them at once."],
         ("Payments", "/payments"), now=_owing),
    Case("no-professor",
         r"(?:class|classe|groupe)\w* (?:\w+ ){0,4}(?:no|without|sans|pas de|aucun|missing|needs? a) " + _PROFESSOR + r"|"
         r"(?:no|without|sans|pas de|aucun) " + _PROFESSOR + r" (?:\w+ ){0,4}(?:class|classe)|(?:nobody|no one|personne) (?:\w+ ){0,3}(?:teach\w*|enseign\w*)|"
         r"ma famech (?:prof|formateur)",
         "When a class has no professor",
         "It waits: a class does not start without a professor. Choose one in the class's parameters, or add a professor to its program.",
         ["It waits: a class does not start without a professor of its program.",
          "To choose one yourself: open the class › Class parameters › Professor.",
          "No professor teaches its program yet: add one to it in Classes › Professors › Who teaches each program."],
         ("Who teaches each program", "/classes/?tab=professors#coverage"), now=_without_professor, more=_auto_professor),
    Case("unhappy",
         r"unhappy|not happy|angry|upset|frustrated|disappointed|complain\w*|plainte|se plaint|mecontent\w*|pas content\w*|fache\w*|en colere|decu\w*|"
         r"reclamation|ychaki|m(?:ou)?ch farhan|ghadhban|bad review|mauvais avis|low (?:stars?|rating|review)|(?:1|one|une) (?:star|etoile)",
         "When a candidate is unhappy",
         "Their message comes to you, never answered alone: answer them yourself soon, a call often does better. A review of 1 to 3 stars stays private and you are told to call them.",
         ["Their message is never answered alone: it comes to you in the inbox, with what was read in it.",
          "Answer them yourself, soon; a call often does better than a message.",
          "A review of 1 to 3 stars stays private: they are thanked, and you are told to call them.",
          "If they wrote they are frustrated or worried, they also appear under “Who may drop out” on the dashboard."],
         ("Inbox", "/inbox")),
    Case("refund",
         r"refund\w*|rembours\w*|money back|(?:yraja3|raja3li|ta3tih) (?:\w+ )?flous",
         "When a candidate asks for a refund",
         "The assistant never answers a refund alone: it comes to you in the inbox. You decide, then answer them yourself.",
         ["The assistant never answers a refund alone: the message comes to you in the inbox.",
          "You decide; then answer them yourself, from the inbox or by phone.",
          "Keep “refund” in Settings › Assistant's rules so that it never talks about one by itself."],
         ("Inbox", "/inbox")),
    Case("meeting-link",
         r"(?:meeting|meet|zoom|teams|session|seance|class|classe|online|en ligne|reunion)\w* (?:\w+ ){0,3}(?:link|lien)\w*|(?:link|lien)\w* (?:\w+ ){0,3}(?:meeting|reunion|meet|zoom|teams|session|seance|online|en ligne)",
         "When a class has no meeting link",
         "Its professor is asked for it 3 days before the start; you can also paste it in the class's parameters. Still none the day before: you are told.",
         ["Three days before an online or mixed class starts, its professor is asked for the private meeting link, in the app, by email, WhatsApp and SMS.",
          "You can paste it yourself: open the class › Class parameters › Meeting link (online).",
          "Once given, every session uses it and each online candidate gets their own link; those on site get none.",
          "Still none the day before: you are told; at the start, a private room is created."],
         ("Classes", "/classes/"), now=_links_missing),
    Case("not-reached",
         r"(?:message|email|mail|sms|whatsapp)\w* (?:\w+ ){0,4}(?:not|didn t|did not|n a pas|n est pas|never|pas|ma) (?:\w+ ){0,2}(?:reach\w*|arriv\w*|received|recu\w*|delivered|wsel\w*|wsol\w*)|"
         r"(?:not|never|didn t|did not|n a pas|pas) (?:\w+ ){0,2}(?:received|recu|got|get) (?:\w+ ){0,3}(?:message|email|mail|sms)|bounced|failed to send|echec d envoi|ma wsel\w*",
         "When a message did not reach a candidate",
         "Everything that did not reach someone is in Inbox › Didn't reach them, with why; an email that failed is tried again once by itself.",
         ["Everything that did not reach someone is in one list, with why in plain words: Inbox › Didn't reach them.",
          "An email that failed is tried again once by itself, within 48 hours.",
          "From the list: send it again, open WhatsApp, call, or mark it “not needed any more”.",
          "When the reason is a wrong address or number, correct it in their file."],
         ("Didn't reach them", "/inbox/not-reached"), now=_not_reached),
]


def case_of(message: str) -> Case | None:
    from app.services import howto
    from app.services.chatbot import _plain

    plain = _plain(howto.understood(message or ""))
    return next((case for case in CASES if re.search(r"(?<![a-z0-9])(?:" + case.words + r")(?![a-z0-9])", plain)), None)


def wants(message: str) -> bool:
    from app.services import howto
    from app.services.chatbot import _plain

    plain = _plain(howto.understood(message or ""))
    asked = _ASKED.search(plain) or _ASKED_AR.search(message or "") or _CASE_FIRST.search(plain)
    return bool(asked) and case_of(message) is not None


def answer(db: Session, message: str, *, lang: str | None = None) -> dict:
    """The case: what the application does by itself, what is left to you, and how things stand now."""
    from app.services import howto, ui_words

    case = case_of(message)
    spoken = lang if lang in ("en", "fr", "ar", "tn") else howto.language_of(message or "")
    token = ui_words.use({"tn": "fr"}.get(spoken, spoken))
    try:
        steps = [_t(step) for step in case.steps]
        if case.more is not None:
            steps[1:1] = case.more(db)
        lines = [_t(case.title) + " :" if ui_words.language() == "fr" else _t(case.title) + ":"]
        lines += [f"{number}. {step}" for number, step in enumerate(steps, 1)]
        now = case.now(db) if case.now is not None else None
        if now:
            lines += ["", now]
        short = _t(case.short) + (f" {now}" if now else "")
        found = {"reply": "\n".join(lines), "short": short, "links": [{"label": _t(case.link[0]), "url": case.link[1]}],
                 "source": "guide", "sources": [{"label": _t("What the application does by itself"), "url": "/system#automatic"}],
                 "resources": [], "case": case.key}
    finally:
        ui_words.current.reset(token)
    return found

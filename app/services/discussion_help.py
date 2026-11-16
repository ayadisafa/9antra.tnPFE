"""When a discussion is open: who the candidate is, in a few facts from the records ("About"), or only a few things to say
to them ("What to say"), worked out by rules from the same facts and from their last message. The admin chooses which
of the two to see (remembered in their browser).

A suggestion goes into the answer box in one tap, next to what is already written; the admin adds their own words and
the rewording writes the whole as one message. The same suggestions are offered by Yasmine when she is asked to write to
someone without being told what ("écris à Ahmed").

Nothing here is written by a model, nothing is guessed (a date, an amount, a name is the record's) and nothing is sent
from here. Money is said only as a fact in the open discussion: the one automatic payment reminder stays the only one.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.services.schedule_utils import DAY_LABELS_FR, MONTHS_FR, local_now
from app.services.ui_words import t as _t

SHOWN = 5
_SAD = {"frustrated", "disappointed", "worried", "confused", "urgent"}
_DAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _naive(moment):
    from app.services.dropout import _naive as read

    return read(moment)


def _first(candidate) -> str:
    name = (candidate.full_name or "").split()
    return name[0].capitalize() if name else ""


def _when(moment: datetime, lang: str) -> str:
    """“samedi 21 novembre à 09:00” / “Saturday 21 November at 09:00” (no hour at midnight: none was set)."""
    hour = moment.strftime("%H:%M") if (moment.hour, moment.minute) != (0, 0) else ""
    if lang == "en":
        return f"{_DAYS_EN[moment.weekday()]} {moment.day} {moment:%B}" + (f" at {hour}" if hour else "")
    day = "1er" if moment.day == 1 else str(moment.day)
    return f"{DAY_LABELS_FR[moment.weekday()].lower()} {day} {MONTHS_FR[moment.month - 1]}" + (f" à {hour}" if hour else "")


def _active(candidate) -> list:
    from app.models.registration import RegistrationStatus

    return [item for item in candidate.registrations if item.status != RegistrationStatus.CANCELLED]


def _main(registrations: list):
    """The registration the discussion is most likely about: a running class first, then the latest."""
    from app.models import ClassStatus

    def rank(item):
        group = item.class_group
        running = group is not None and group.status == ClassStatus.ONGOING
        waiting = group is not None and group.status == ClassStatus.NOT_STARTED
        return (running, waiting, _naive(item.registration_date) or datetime.min)

    return max(registrations, key=rank) if registrations else None


def _sessions(db: Session, registration, now: datetime) -> tuple:
    """(the next session as (start, session), the last session gone as (start, session)) of their class."""
    from app.models.classsession import ClassSession
    from app.services import meetings

    group = registration.class_group if registration is not None else None
    if group is None:
        return None, None
    upcoming = past = None
    for session in (db.query(ClassSession).filter(ClassSession.class_group_id == group.id).order_by(ClassSession.session_date).all()):
        start, end = meetings.hours_of(session, group)
        if start > now and upcoming is None:
            upcoming = (start, session)
        if end <= now:
            past = (start, session)
    return upcoming, past


def _program_name(registration) -> str:
    return (registration.program.name if registration.program else None) or registration.program_name or _t("Program")


def about(db: Session, candidate, now: datetime | None = None, smart: dict | None = None) -> dict:
    """What to know before answering: {"first", "programs": [...], "tiles": [...], "notes": [...]}."""
    from app.models import ClassStatus
    from app.models.candidate_email import CandidateEmail
    from app.models.registration import PaymentStatus
    from app.models.user import Attendance
    from app.services import candidate_insights, dropout, level_check, smart_reminders

    now = now or local_now()
    registrations = _active(candidate)
    programs = []
    for registration in registrations:
        group = registration.class_group
        paid = registration.payment_status == PaymentStatus.PAID
        state, when = "no_class", ""
        if group is not None:
            if group.status == ClassStatus.ONGOING:
                state = "running"
                when = _t("week {0} of {1}", max(1, group.current_week or 1), group.total_weeks or 12)
            elif group.status == ClassStatus.COMPLETED:
                state = "ended"
            else:
                state = "starts"
                start = _naive(group.start_date)
                when = start.strftime("%d/%m") if start else ""
        programs.append({"name": _program_name(registration), "paid": paid, "left": round(registration.remaining_amount or 0),
                         "class": group.name if group is not None else None, "state": state, "when": when,
                         "url": f"/classes/registration/{registration.id}"})
    main = _main(registrations)
    tiles = []
    upcoming, _ = _sessions(db, main, now)
    if upcoming is not None:
        start, session = upcoming
        tiles.append({"key": "next", "icon": "bi-calendar-event", "label": _t("Next session"),
                      "value": f"{start:%d/%m}" + (f" · {start:%H:%M}" if (start.hour, start.minute) != (0, 0) else ""),
                      "note": session.topic or "", "tone": "info"})
    if main is not None:
        marks = [status for (status,) in db.query(Attendance.status).filter(Attendance.registration_id == main.id).all()]
        if marks:
            came = sum(1 for status in marks if status in ("present", "late"))
            tiles.append({"key": "presence", "icon": "bi-person-check", "label": _t("Presence"), "value": f"{came}/{len(marks)}",
                          "tone": "ok" if came >= len(marks) * 0.75 else "bad"})
        check = level_check.current(db, main.id)
        result = level_check.result_of(check) if check is not None else None
        if result is not None:
            tiles.append({"key": "level", "icon": "bi-bar-chart-steps", "label": _t("Level"), "value": _t(result["info"]["label"]), "tone": "info"})
        elif check is not None and check.sent_at is not None:
            tiles.append({"key": "level", "icon": "bi-bar-chart-steps", "label": _t("Level"), "value": _t("Not answered yet"), "tone": "warn"})
    smart = smart or smart_reminders.for_candidate(db, candidate)
    for metric in smart["metrics"]:
        if metric["key"] in ("questions", "score", "mood", "answers"):
            tiles.append({"key": metric["key"], "icon": metric["icon"], "label": metric["label"], "value": metric["value"],
                          "tone": {"ok": "ok", "bad": "bad"}.get(metric["tone"], "warn")})
    tiles.append({"key": "way", "icon": smart["channel"]["icon"], "label": _t("Answers best by"), "value": smart["channel"]["label"],
                  "note": smart["hour_why"], "tone": "info"})
    notes = []
    feelings = [item for item in candidate_insights.feelings_of(db, candidate)["emotions"]
                if item["key"] in _SAD and item["last"] is not None and _naive(item["last"]) >= datetime.utcnow() - timedelta(days=30)]
    if feelings:
        notes.append({"icon": "bi-emoji-frown", "tone": "bad",
                      "text": _t("Wrote they felt: {0}", ", ".join(_t(item["label"]).lower() for item in feelings[:3]))})
    if main is not None:
        risk = dropout.of_registration(db, main)
        if risk["score"] >= dropout.SHOWN:
            notes.append({"icon": "bi-exclamation-triangle", "tone": "bad" if risk["score"] >= dropout.HIGH else "warn",
                          "text": _t("May drop out ({0}/100): {1}", risk["score"], "; ".join(risk["reasons"][:2]))})
    last_in = (db.query(CandidateEmail.received_at).filter(CandidateEmail.candidate_id == candidate.id, CandidateEmail.direction == "inbound")
               .order_by(CandidateEmail.received_at.desc()).first())
    since = min((_naive(item.registration_date) for item in registrations if item.registration_date), default=None)
    if since is not None:
        notes.append({"icon": "bi-clock-history", "tone": "muted",
                      "text": _t("With us since {0}", since.strftime("%d/%m/%Y"))
                      + (" · " + _t("last message {0}", _naive(last_in[0]).strftime("%d/%m")) if last_in and last_in[0] else "")})
    return {"first": _first(candidate), "programs": programs, "tiles": tiles, "notes": notes}


def language_of(db: Session, candidate) -> str:
    """The language to write to them in: English when they write in English, else French (as the centre writes)."""
    from app.models.candidate_email import CandidateEmail

    last = (db.query(CandidateEmail.language).filter(CandidateEmail.candidate_id == candidate.id, CandidateEmail.direction == "inbound",
                                                     CandidateEmail.language.isnot(None))
            .order_by(CandidateEmail.received_at.desc()).first())
    return "en" if last and last[0] == "en" else "fr"


def to_say(db: Session, candidate, last=None, *, lang: str | None = None, now: datetime | None = None, most: int = SHOWN,
           smart: dict | None = None) -> list[dict]:
    """A few things to say to them, the likeliest first: [{"key", "label" (for the admin), "text" (for the candidate)}].
    `last` is their last message, when the discussion has one."""
    from app.models import ClassStatus
    from app.models.registration import PaymentStatus
    from app.models.user import Attendance
    from app.services import registration_form, reply_rules, smart_reminders
    from app.services.settings_service import get_settings

    now = now or local_now()
    lang = lang if lang in ("fr", "en") else language_of(db, candidate)
    en = lang == "en"
    first = _first(candidate)
    found: list[dict] = []

    def add(key: str, label: str, french: str, english: str):
        if all(item["key"] != key for item in found):
            found.append({"key": key, "label": label, "text": english if en else french})

    said = " ".join(((last.body if last is not None else "") or "").split())
    if last is not None and said:
        try:
            felt = [item["key"] for item in (__import__("json").loads(last.emotions or "{}").get("emotions") or [])]
        except ValueError:
            felt = []
        if set(felt) & {"frustrated", "disappointed", "worried", "confused"}:
            add("sorry", _t("Say you are sorry"),
                "Je suis vraiment désolé(e) pour ce désagrément. Nous regardons cela tout de suite et nous revenons vers vous.",
                "I am really sorry about this. We are looking into it right away and will come back to you.")
        if reply_rules.pure_thanks(said, reply_rules.rules(db), candidate.full_name or ""):
            add("welcome", _t("Answer their thanks"),
                f"Avec plaisir{', ' + first if first else ''} ! N'hésitez pas si vous avez une autre question.",
                f"You are welcome{', ' + first if first else ''}! Feel free to ask if you have another question.")

    registrations = _active(candidate)
    main = _main(registrations)
    if main is not None:
        program = _program_name(main)
        group = main.class_group
        upcoming, past = _sessions(db, main, now)
        if past is not None:
            start, session = past
            mark = db.query(Attendance.status).filter(Attendance.registration_id == main.id, Attendance.session_id == session.id).first()
            if mark and mark[0] == "absent" and start >= now - timedelta(days=14):
                add("missed", _t("We missed them"),
                    f"Nous ne vous avons pas vu à la séance de {_when(start, 'fr')} : tout va bien ? Dites-nous si nous pouvons vous aider.",
                    f"We did not see you at the session on {_when(start, 'en')}: is everything all right? Tell us if we can help.")
        if group is not None and group.status == ClassStatus.NOT_STARTED and upcoming is not None and upcoming[0] <= now + timedelta(days=21):
            add("start", _t("When it starts"),
                f"Votre formation {program} commence le {_when(upcoming[0], 'fr')}.",
                f"Your {program} training starts on {_when(upcoming[0], 'en')}.")
        elif group is not None and group.status == ClassStatus.ONGOING and upcoming is not None:
            start, session = upcoming
            add("next", _t("Their next session"),
                f"Votre prochaine séance est le {_when(start, 'fr')}" + (f" : {session.topic}." if session.topic else "."),
                f"Your next session is on {_when(start, 'en')}" + (f": {session.topic}." if session.topic else "."))
        smart = smart or smart_reminders.for_candidate(db, candidate)
        keys = {item["key"] for item in smart["suggestions"]}
        if "questions" in keys:
            add("questions", _t("Their questions wait"),
                "Les questions de la semaine vous attendent : cinq minutes suffisent, et vos réponses nous aident à adapter les séances.",
                "This week's questions are waiting for you: five minutes are enough, and your answers help us adapt the sessions.")
        if "catch_up" in keys:
            add("catch_up", _t("Offer help with the hard parts"),
                "Si certaines notions sont difficiles, nous pouvons prévoir un moment avec votre formateur pour les reprendre ensemble.",
                "If some parts are hard, we can plan a moment with your trainer to go over them together.")
        if "bravo" in keys:
            add("bravo", _t("Say bravo"),
                f"Bravo pour votre assiduité en {program}, continuez ainsi !",
                f"Well done for your commitment in {program}, keep it up!")
        unplaced = next((item for item in registrations if item.payment_status == PaymentStatus.PAID and not item.class_group_id), None)
        if unplaced is not None:
            add("placed", _t("Their payment is received"),
                f"Votre paiement pour {_program_name(unplaced)} est bien reçu : nous vous envoyons la date de début dès que votre classe est fixée.",
                f"Your payment for {_program_name(unplaced)} is received: we send you the start date as soon as your class is set.")
        owed = next((item for item in registrations if (item.remaining_amount or 0) > 0 and item.payment_status != PaymentStatus.PAID), None)
        if owed is not None:
            left = f"{owed.remaining_amount:g}"
            add("owes", _t("What is left to pay"),
                f"Il vous reste {left} DT à régler pour {_program_name(owed)}.",
                f"You still have {left} DT to pay for {_program_name(owed)}.")
    else:
        form = registration_form.url(db)
        if form:
            add("register", _t("How to register"),
                f"Pour vous inscrire, il suffit de remplir ce formulaire : {form}",
                f"To register, simply fill in this form: {form}")
    phone = (getattr(get_settings(db), "centre_phone", None) or "").strip()
    if phone:
        add("call", _t("Offer a call"), f"Vous pouvez aussi nous appeler au {phone}.", f"You can also call us on {phone}.")
    add("soon", _t("We come back to you"),
        "Merci pour votre message, nous revenons vers vous très vite.", "Thank you for your message, we will come back to you very soon.")
    return found[:most]


def for_discussion(db: Session, candidate, last=None) -> dict:
    """Both, for the open discussion: {"about", "say", "lang"} (the candidate's behaviour is counted once)."""
    from app.services import smart_reminders

    smart = smart_reminders.for_candidate(db, candidate)
    lang = language_of(db, candidate)
    return {"about": about(db, candidate, smart=smart), "say": to_say(db, candidate, last, lang=lang, smart=smart), "lang": lang}

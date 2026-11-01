"""What is smart to remind each candidate, counted from what they do (never guessed by a model): their presence, the
weekly questions answered and their scores, how they said their week went, what they still owe, and how they answer
the centre (which way, how fast, at what hour).

Each suggestion is SMART:
- Specific: what to send, to whom, which way;
- Measurable: the figure to watch afterwards;
- Achievable and relevant: the facts it rests on;
- Time-bound: when it goes (the hour they usually write), and when to look again.

Nothing is sent from here. "Prepare it" fills the reminder form (`/reminders?smart=<candidate>:<key>`), or WhatsApp
opens with the text written. Money is never reminded from here: the payment reminder goes by itself, once.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.services.schedule_utils import local_now, utc_to_local
from app.services.ui_words import t as _t

WAYS = {"email": ("Email", "bi-envelope"), "whatsapp": ("WhatsApp", "bi-whatsapp"), "sms": ("SMS", "bi-phone")}
ANSWER_WITHIN = timedelta(hours=72)        # an answer counts when it comes within three days
LOOK_BACK = timedelta(days=120)
DEFAULT_HOUR = 18

# What each suggestion sends (in French, like the reminder ideas; {first_name} and {program_name} are filled).
TEXTS = {
    "questions": {"style": "brand", "subject": "Vos questions de la semaine vous attendent", "title": "Vos questions de la semaine",
                  "body": "Bonjour {first_name},\n\nLes questions de la semaine de **{program_name}** vous attendent : cinq minutes suffisent, "
                          "et vos réponses nous aident à adapter les séances.\n\n[box]\nRépondez à l'email des questions, ou à ce message si vous ne le "
                          "trouvez plus : nous vous le renvoyons.\n\nMerci !"},
    "catch_up": {"style": "letter", "subject": "Reprenons ensemble quelques notions", "title": "Un moment pour reprendre",
                 "body": "Bonjour {first_name},\n\nLes dernières questions ont montré quelques notions de **{program_name}** à revoir : c'est normal "
                         "à ce stade.\n\n[box green]\nNous pouvons vous proposer un moment avec votre professeur pour les reprendre ensemble : "
                         "répondez à ce message avec vos disponibilités.\n\nCourage, vous avancez !"},
    "feeling": {"style": "letter", "subject": "Comment se passe votre formation ?", "title": "Merci de nous l'avoir dit",
                "body": "Bonjour {first_name},\n\nVous nous avez dit que la semaine était difficile. Merci de nous l'avoir dit !\n\n[box]\n"
                        "Qu'est-ce qui vous aiderait le plus : un résumé de la séance, des exercices en plus, ou un moment avec le professeur ?\n\n"
                        "Répondez simplement à ce message."},
    "bravo": {"style": "announcement", "subject": "Bravo pour votre parcours", "title": "Bravo !",
              "body": "Bonjour {first_name},\n\nUn petit mot pour vous dire bravo : votre assiduité et vos réponses en **{program_name}** "
                      "sont remarquables.\n\n[box green]\nContinuez ainsi, la suite va vous plaire !\n\nÀ très bientôt."},
}


def _naive(moment):
    return moment.replace(tzinfo=None) if moment is not None and moment.tzinfo else moment


def _registration(db: Session, candidate):
    """Their training of now: assigned to a class that has not ended, else the latest one not cancelled."""
    from app.models.classgroup import ClassStatus
    from app.models.registration import RegistrationStatus

    rows = [item for item in candidate.registrations if item.status not in (RegistrationStatus.CANCELLED,)]
    rows.sort(key=lambda item: _naive(item.registration_date) or datetime.min, reverse=True)
    for item in rows:
        if item.class_group is not None and item.class_group.status != ClassStatus.COMPLETED and item.status != RegistrationStatus.COMPLETED:
            return item
    return None


def _how_they_answer(db: Session, candidate, now: datetime) -> dict:
    """For each way (email, WhatsApp, SMS): messages sent to them, how many they answered within three days, and how
    fast; and the hour (local) they usually write."""
    from app.models.candidate_email import CandidateEmail

    rows = (db.query(CandidateEmail).filter(CandidateEmail.candidate_id == candidate.id, CandidateEmail.received_at >= now - LOOK_BACK)
            .order_by(CandidateEmail.received_at.asc()).all())
    ways: dict[str, dict] = {}
    hours: Counter = Counter()
    for index, row in enumerate(rows):
        way = row.channel or "email"
        if row.direction == "inbound":
            local = utc_to_local(_naive(row.received_at))
            if local is not None:
                hours[local.hour] += 1
            continue
        if row.status not in ("sent", "to_send", "delivered", "test") and row.status is not None:
            continue
        found = ways.setdefault(way, {"sent": 0, "answered": 0, "delays": []})
        found["sent"] += 1
        sent_at = _naive(row.received_at)
        for later in rows[index + 1:]:
            if later.direction == "inbound" and (later.channel or "email") == way:
                gap = _naive(later.received_at) - sent_at
                if timedelta(0) <= gap <= ANSWER_WITHIN:
                    found["answered"] += 1
                    found["delays"].append(gap.total_seconds() / 3600)
                break
    week = [row for row in rows if row.direction == "outbound" and _naive(row.received_at) >= now - timedelta(days=7)]
    answered_since = any(row.direction == "inbound" and _naive(row.received_at) >= now - timedelta(days=7) for row in rows)
    return {"ways": ways, "hours": hours, "this_week": len(week), "answered_this_week": answered_since}


def _way(candidate, contact: dict) -> dict:
    """Which way to send: the one they answer best, with why."""
    from app.services import whatsapp

    ways = contact["ways"]

    def rate(key):
        found = ways.get(key) or {}
        return (found.get("answered", 0) / found["sent"]) if found.get("sent") else None

    phone = bool(whatsapp.normalise(getattr(candidate, "phone", None)))
    mail, chat = rate("email"), rate("whatsapp")
    if phone and chat is not None and ways["whatsapp"]["answered"] and (mail is None or chat > mail):
        found = ways["whatsapp"]
        return {"key": "whatsapp", "why": _t("They answered {0} of {1} WhatsApp messages (email: {2}).", found["answered"], found["sent"],
                                             f"{ways['email']['answered']}/{ways['email']['sent']}" if ways.get("email") else "—")}
    if mail is not None and ways["email"]["answered"]:
        found = ways["email"]
        fast = statistics.median(found["delays"]) if found["delays"] else None
        if fast is not None and fast < 1:
            return {"key": "email", "why": _t("They answered {0} of {1} emails, within the hour.", found["answered"], found["sent"])}
        return {"key": "email", "why": _t("They answered {0} of {1} emails, in about {2} h.", found["answered"], found["sent"], round(fast or 0))}
    if phone and ways.get("email", {}).get("sent", 0) >= 2:
        return {"key": "whatsapp", "why": _t("None of the {0} emails sent was answered: try WhatsApp.", ways["email"]["sent"])}
    return {"key": "email", "why": _t("Not enough messages yet to compare the ways: email.")}


def _hour(contact: dict, registration, db: Session) -> tuple[int, str]:
    """The hour they usually write (or answer their questions), else 18 h."""
    from app.models.quiz import Quiz

    hours = Counter(contact["hours"])
    if registration is not None:
        for (done,) in db.query(Quiz.completed_at).filter(Quiz.registration_id == registration.id, Quiz.completed_at.isnot(None)).all():
            local = utc_to_local(_naive(done))
            if local is not None:
                hours[local.hour] += 1
    hour, times = hours.most_common(1)[0] if hours else (DEFAULT_HOUR, 0)
    if times >= 2:                              # a habit: the same hour at least twice
        hour = min(max(hour, 8), 21)            # never at night
        return hour, _t("They usually write around {0} h ({1} times).", hour, times)
    return DEFAULT_HOUR, _t("No habit known yet: {0} h.", DEFAULT_HOUR)


def _next_session(db: Session, registration, now: datetime):
    from app.models.classsession import ClassSession
    from app.services import meetings

    if registration is None or registration.class_group is None:
        return None
    for session in (db.query(ClassSession).filter(ClassSession.class_group_id == registration.class_group_id,
                                                  ClassSession.session_date >= now.date()).order_by(ClassSession.session_date).limit(3).all()):
        start, _ = meetings.hours_of(session, registration.class_group)
        if start > now:
            return start
    return None


def _send_at(now: datetime, hour: int, before: datetime | None = None) -> datetime:
    """The next time at that hour, at least an hour from now; the day before the session when there is one ahead."""
    moment = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if moment < now + timedelta(hours=1):
        moment += timedelta(days=1)
    if before is not None:
        day_before = (before - timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
        if day_before >= now + timedelta(hours=1):
            moment = min(moment, day_before) if moment < before - timedelta(hours=2) else day_before
    return moment


def _when_text(moment: datetime) -> str:
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return _t("{0} {1} at {2}", _t(days[moment.weekday()]), moment.strftime("%d/%m"), moment.strftime("%H:%M"))


def for_candidate(db: Session, candidate, now: datetime | None = None) -> dict:
    """{"name", "metrics", "channel", "hour", "hour_why", "suggestions", "score"} for one candidate."""
    from app.models.quiz import Quiz
    from app.models.classsession import ClassSession
    from app.models.user import Attendance
    from app.services import dropout, whatsapp

    now = now or local_now()
    utc_now = datetime.utcnow()
    registration = _registration(db, candidate)
    contact = _how_they_answer(db, candidate, utc_now)
    way = _way(candidate, contact)
    hour, hour_why = _hour(contact, registration, db)
    upcoming = _next_session(db, registration, now)
    metrics, suggestions = [], []
    program = (registration.program_name if registration is not None else "") or ""

    marks = []
    if registration is not None:
        marks = [status for (status,) in db.query(Attendance.status).join(ClassSession, ClassSession.id == Attendance.session_id)
                 .filter(Attendance.registration_id == registration.id).order_by(ClassSession.session_date.desc()).limit(6).all()]
    in_a_row = 0
    for status in marks:
        if status != "absent":
            break
        in_a_row += 1
    present = sum(1 for status in marks[:4] if status in ("present", "late"))
    if marks:
        metrics.append({"key": "presence", "icon": "bi-person-check", "label": _t("Presence"), "value": f"{present}/{len(marks[:4])}",
                        "tone": "ok" if present >= max(1, len(marks[:4]) - 1) else "bad"})

    quizzes = []
    if registration is not None:
        quizzes = (db.query(Quiz).filter(Quiz.registration_id == registration.id, Quiz.kind.in_(("weekly", "session")), Quiz.sent_at.isnot(None))
                   .order_by(Quiz.sent_at.desc()).limit(6).all())
    settled = [quiz for quiz in quizzes if _naive(quiz.sent_at) <= utc_now - timedelta(days=2)][:3]
    waiting = [quiz for quiz in settled if quiz.completed_at is None]
    scored = [quiz.score for quiz in quizzes if quiz.completed_at is not None and quiz.score is not None][:3]
    average = round(sum(scored) / len(scored)) if scored else None
    if settled:
        metrics.append({"key": "questions", "icon": "bi-patch-question", "label": _t("Questions answered"), "value": f"{len(settled) - len(waiting)}/{len(settled)}",
                        "tone": "ok" if not waiting else "warn"})
    if average is not None:
        metrics.append({"key": "score", "icon": "bi-graph-up", "label": _t("Score"), "value": f"{average}%", "tone": "ok" if average >= 60 else "bad"})
    feeling = None
    for quiz in quizzes:
        try:
            said = json.loads(quiz.feedback or "{}") if quiz.feedback else {}
        except ValueError:
            said = {}
        if isinstance(said.get("mood"), int):
            feeling = said
            break
    if feeling:
        metrics.append({"key": "mood", "icon": "bi-emoji-smile", "label": _t("Their week"), "value": f"{feeling['mood']}/5",
                        "tone": "ok" if feeling["mood"] >= 3 else "bad"})
    owed = (registration.remaining_amount or 0) if registration is not None else 0
    if owed > 0:
        from app.services.dropout import _naive as _read_date

        reminded = utc_to_local(_read_date(registration.reminder_sent_at))
        metrics.append({"key": "owes", "icon": "bi-cash-coin", "label": _t("Still owes"), "value": f"{owed:g} DT", "tone": "warn",
                        "note": (_t("The payment reminder went on {0} (only one is sent).", reminded.strftime("%d/%m")) if reminded
                                 else _t("The payment reminder goes by itself, once."))})
    ways = contact["ways"]
    if ways:
        sent = sum(item["sent"] for item in ways.values())
        answered = sum(item["answered"] for item in ways.values())
        metrics.append({"key": "answers", "icon": "bi-reply", "label": _t("Answers to our messages"), "value": f"{answered}/{sent}",
                        "tone": "ok" if sent and answered / sent >= 0.5 else "warn"})

    first_name = (candidate.full_name or "").split(" ")[0]
    phone = getattr(candidate, "phone", None)

    def add(key: str, title: str, measure: str, why: list[str], look_again, text_key: str | None = None, idea: str | None = None,
            before: datetime | None = None):
        moment = _send_at(now, hour, before)
        # When to look again: a moment (after the next session), or a time after the sending.
        look_again = look_again if isinstance(look_again, datetime) else moment + look_again
        text = TEXTS.get(text_key) if text_key else None
        if idea:
            from app.services import reminders

            text = reminders.idea(idea)
        body = (text or {}).get("body", "").replace("{first_name}", first_name).replace("{program_name}", program)
        specific = (_t("Send “{0}” to {1}, by {2}.", text["subject"], candidate.full_name or candidate.email, _t(WAYS[way["key"]][0]).lower())
                    if text else _t("{0} to {1}, by {2}.", title, candidate.full_name or candidate.email, _t(WAYS[way["key"]][0])))
        suggestions.append({
            "key": key, "title": title, "specific": specific, "measure": measure, "why": why,
            "when": moment, "when_text": _t("Send {0}; look again {1}.", _when_text(moment), _when_text(look_again)),
            "href": f"/reminders?smart={candidate.id}:{key}#new" if text else None,
            "whatsapp": whatsapp.link(phone, body) if text and way["key"] == "whatsapp" and phone else None,
            "text": text, "way": way["key"],
        })

    session_text = _when_text(upcoming) if upcoming else None
    after_session = (upcoming + timedelta(hours=4)).replace(minute=0) if upcoming else timedelta(days=7)
    if in_a_row >= 2 or (len(marks) >= 4 and present / len(marks[:4]) < 0.6):
        why = [_t("Absent from the last {0} sessions.", in_a_row) if in_a_row >= 2 else _t("Present at {0} of the last {1} sessions.", present, len(marks[:4]))]
        add("missed", _t("“We missed you”"), _t("Present at the next session ({0}).", session_text) if session_text else _t("Present at the next session."),
            why, after_session, idea="missed", before=upcoming)
    if waiting:
        week = waiting[0].week_number
        add("questions", _t("Their questions are waiting"),
            _t("The questions of week {0} answered within two days.", week) if week else _t("The waiting questions answered within two days."),
            [_t("{0} of their last {1} questions not answered.", len(waiting), len(settled))], timedelta(days=2), text_key="questions")
    if average is not None and len(scored) >= 2 and average < 50:
        add("catch_up", _t("A moment to catch up"), _t("Their next score above 60% (now {0}%).", average),
            [_t("{0}% on average on their last {1} answers.", average, len(scored))], timedelta(days=8), text_key="catch_up")
    if feeling and (feeling["mood"] <= 2 or feeling.get("pace") == "fast"):
        why = [_t("Gave their last week {0}/5.", feeling["mood"])] + ([_t("Said the pace is too fast.")] if feeling.get("pace") == "fast" else [])
        add("feeling", _t("Ask what would help"), _t("Their next week at 3/5 or more (now {0}/5).", feeling["mood"]), why, timedelta(days=8), text_key="feeling")
    worried = len(suggestions) > 0
    if not worried and len(marks) >= 3 and present == len(marks[:4]) and (average is None or average >= 70) and not waiting:
        why = [_t("Present at every one of the last {0} sessions.", len(marks[:4]))] + ([_t("{0}% on average on their answers.", average)] if average is not None else [])
        add("bravo", _t("A word of “bravo”"), _t("Still present at every session next month."), why, timedelta(days=30), text_key="bravo")
    if contact["this_week"] >= 3 and not contact["answered_this_week"] and suggestions:
        # They already got several messages this week and answered none: one more would be one too many.
        for item in suggestions:
            item["why"].append(_t("Already {0} messages this week without an answer: a call may do better.", contact["this_week"]))
            item["call"] = f"tel:{phone}" if phone else None

    risk = dropout.of_registration(db, registration) if registration is not None else {"score": 0, "reasons": []}
    if risk["score"]:
        metrics.append({"key": "risk", "icon": "bi-exclamation-triangle", "label": _t("May drop out"), "value": f"{risk['score']}/100",
                        "tone": "bad" if risk["score"] >= 40 else "warn"})
    return {"candidate": candidate, "name": candidate.full_name or candidate.email, "program": program, "metrics": metrics,
            "channel": {**way, "label": _t(WAYS[way["key"]][0]), "icon": WAYS[way["key"]][1]}, "hour": hour, "hour_why": hour_why,
            "suggestions": suggestions, "score": risk["score"] + 10 * sum(1 for item in suggestions if item["key"] != "bravo"),
            "next_session": session_text}


def board(db: Session, most: int = 8, now: datetime | None = None) -> list[dict]:
    """The candidates following a training now, those who need a reminder most first (then a “bravo”)."""
    from app.models.candidate import Candidate
    from app.models.registration import Registration, RegistrationStatus

    ids = {row[0] for row in db.query(Registration.candidate_id).filter(Registration.class_group_id.isnot(None),
                                                                         Registration.status == RegistrationStatus.ASSIGNED).all()}
    rows = []
    for candidate in db.query(Candidate).filter(Candidate.id.in_(ids)).all() if ids else []:
        if not (candidate.email or candidate.phone):
            continue
        row = for_candidate(db, candidate, now)
        if row["suggestions"]:
            rows.append(row)
    rows.sort(key=lambda row: (-row["score"], row["name"].lower()))
    return rows[:most]


def prefill(db: Session, wanted: str) -> dict | None:
    """The reminder form filled from one suggestion ("<candidate id>:<key>"): who, words, look, day and hour."""
    from app.models.candidate import Candidate

    candidate_id, _, key = (wanted or "").partition(":")
    candidate = db.get(Candidate, candidate_id) if candidate_id else None
    if candidate is None:
        return None
    found = next((item for item in for_candidate(db, candidate)["suggestions"] if item["key"] == key and item["text"]), None)
    if found is None:
        return None
    text = found["text"]
    return {"key": key, "candidate_id": candidate.id, "subject": text["subject"], "title": text["title"], "body": text["body"],
            "style": text["style"], "date": found["when"].strftime("%Y-%m-%d"), "time": found["when"].strftime("%H:%M"),
            "why": found["why"], "measure": found["measure"]}

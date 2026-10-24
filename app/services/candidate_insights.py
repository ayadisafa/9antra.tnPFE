"""A candidate seen whole: their motivation, how much they use their programs, their loyalty, and what they felt in
their messages. Each score is the weighted average of counted parameters; each parameter shows its value, its rule,
its weight and where it was read (the presence sheets, the level check, the payments, their messages...). Nothing is
guessed by a model, and a score needs at least two parameters to be given.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session


def _param(key: str, label: str, value: str, score, rule: str, source: str, weight: int) -> dict:
    return {"key": key, "label": label, "value": value, "score": None if score is None else max(0, min(100, round(score))),
            "rule": rule, "source": source, "weight": weight}


def _score(params: list[dict], words: tuple[str, str, str, str]) -> dict:
    used = [param for param in params if param["score"] is not None]
    weight = sum(param["weight"] for param in used)
    score = round(sum(param["score"] * param["weight"] for param in used) / weight) if len(used) >= 2 and weight else None
    for param in params:
        param["share"] = round(param["weight"] / weight * 100) if param["score"] is not None and weight else 0
    if score is None:
        headline, tone = "Not enough to say yet", "muted"
    elif score >= 75:
        headline, tone = words[0], "ok"
    elif score >= 55:
        headline, tone = words[1], "info"
    elif score >= 35:
        headline, tone = words[2], "warn"
    else:
        headline, tone = words[3], "bad"
    return {"score": score, "headline": headline, "tone": tone, "params": params, "used": len(used), "of": len(params)}


def _naive(moment):
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def feelings_of(db: Session, candidate) -> dict:
    """What the candidate felt in the messages they sent: each emotion, how often, the last words that showed it."""
    from app.models.candidate_email import CandidateEmail

    counts: dict[str, dict] = {}
    rows = (db.query(CandidateEmail).filter(CandidateEmail.candidate_id == candidate.id, CandidateEmail.direction == "inbound",
                                            CandidateEmail.emotions.isnot(None)).order_by(CandidateEmail.received_at.asc()).all())
    for row in rows:
        try:
            read = json.loads(row.emotions)
        except (TypeError, ValueError):
            continue
        for item in read.get("emotions") or []:
            entry = counts.setdefault(item["key"], {"key": item["key"], "label": item["label"], "count": 0, "words": [], "last": None})
            entry["count"] += 1
            entry["words"] = item["words"][:3]
            entry["last"] = row.received_at
    found = sorted(counts.values(), key=lambda item: -item["count"])
    return {"emotions": found, "messages": len(rows),
            "how": "From each message they sent (email, WhatsApp, SMS), read with word lists in French, English, Tunisian and Arabic."}


def motivation(db: Session, candidate, behaviour: dict, feelings: dict) -> dict:
    from app.models.quiz import Quiz
    from app.services import level_check

    by_key = {param["key"]: param for param in behaviour.get("params") or []}
    params = []
    checks = [quiz for quiz in db.query(Quiz).filter(Quiz.candidate_id == candidate.id, Quiz.kind == level_check.KIND).all() if quiz.sent_at]
    if checks:
        done = [quiz for quiz in checks if quiz.completed_at]
        fast = [quiz for quiz in done if (quiz.completed_at - quiz.sent_at).total_seconds() <= 3 * 86400]
        score = 100 if fast else 75 if done else 25
        params.append(_param("level", "Level check", f"{len(done)} of {len(checks)} answered" + (", within 3 days" if fast else ""), score,
                             "Answered within 3 days: 100; later: 75; not answered: 25.", "The level check", 3))
    else:
        params.append(_param("level", "Level check", "None sent yet", None, "Answered within 3 days: 100; later: 75; not answered: 25.", "The level check", 3))
    for key, label, source in (("answers", "Weekly questions answered", "The weekly questions"),
                               ("participation", "Taking part in sessions", "The presence sheets (the professor's notes)"),
                               ("questions", "Questions asked", "Their emails and the presence sheets")):
        param = by_key.get(key)
        params.append(_param(key, label, param["value"] if param else "Nothing yet", param["score"] if param else None,
                             (param or {}).get("rule", ""), source, 3 if key != "questions" else 2))
    days = []
    for registration in candidate.registrations:
        payments = sorted((payment for payment in registration.payments or []), key=lambda payment: payment.payment_date or datetime.min)
        if payments and registration.registration_date and (registration.paid_amount or 0) >= (registration.total_price or registration.total_amount or 0) > 0:
            days.append(max(0, ((payments[-1].payment_date or payments[-1].created_at) - _naive(registration.registration_date)).days))
    if days:
        fastest = min(days)
        score = 100 if fastest <= 3 else 80 if fastest <= 7 else 60 if fastest <= 14 else 40
        params.append(_param("paying", "Paid quickly", f"{fastest} day{'s' if fastest != 1 else ''} from registering to paying in full", score,
                             "Paid in full within 3 days: 100; 7 days: 80; 14 days: 60; later: 40.", "The payments", 2))
    else:
        params.append(_param("paying", "Paid quickly", "Not paid in full yet", None, "Paid in full within 3 days: 100; 7 days: 80; 14 days: 60; later: 40.",
                             "The payments", 2))
    keys = {item["key"]: item["count"] for item in feelings["emotions"]}
    good = keys.get("eager", 0) + keys.get("happy", 0) + keys.get("grateful", 0)
    bad = keys.get("frustrated", 0) + keys.get("disappointed", 0)
    if good or bad:
        score = 100 if good and not bad else 70 if good >= bad else 40
        params.append(_param("feelings", "What they feel when writing", f"{good} message{'s' if good != 1 else ''} motivated or happy, {bad} frustrated or disappointed",
                             score, "Only good feelings: 100; more good than bad: 70; more bad: 40.", "Their messages (emotions read with word lists)", 2))
    else:
        params.append(_param("feelings", "What they feel when writing", "No feeling shown in their messages", None,
                             "Only good feelings: 100; more good than bad: 70; more bad: 40.", "Their messages (emotions read with word lists)", 2))
    found = _score(params, ("Very motivated", "Motivated", "Motivation is fading", "Seems demotivated"))
    found["how"] = "The weighted average of the parameters below that can be measured (at least two). Each says its rule and where it was read."
    return found


def usage(db: Session, candidate, behaviour: dict) -> dict:
    from app.models.quiz import Quiz
    from app.models.session_join import SessionJoin
    from app.services import level_check

    by_key = {param["key"]: param for param in behaviour.get("params") or []}
    params = []
    presence = by_key.get("presence")
    params.append(_param("presence", "Sessions attended", presence["value"] if presence else "No session marked yet",
                         presence["score"] if presence else None, "Sessions attended (present or late), out of the sessions marked.",
                         "The presence sheets", 5))
    links = db.query(SessionJoin).filter(SessionJoin.candidate_id == candidate.id, SessionJoin.sent_at.isnot(None)).all()
    if links:
        opened = sum(1 for link in links if (link.joins or 0) > 0)
        params.append(_param("links", "Meeting links used", f"{opened} of {len(links)} sessions joined with their link", opened / len(links) * 100,
                             "Sessions joined with their personal link, out of the links sent.", "The meeting links", 3))
    else:
        params.append(_param("links", "Meeting links used", "No link sent yet", None, "Sessions joined with their personal link, out of the links sent.",
                             "The meeting links", 3))
    answers = by_key.get("answers")
    params.append(_param("answers", "Weekly questions answered", answers["value"] if answers else "None sent yet",
                         answers["score"] if answers else None, "Weekly questions answered, out of those sent.", "The weekly questions", 3))
    checks = [quiz for quiz in db.query(Quiz).filter(Quiz.candidate_id == candidate.id, Quiz.kind == level_check.KIND).all() if quiz.sent_at]
    if checks:
        done = sum(1 for quiz in checks if quiz.completed_at)
        params.append(_param("level", "Level check done", f"{done} of {len(checks)}", done / len(checks) * 100,
                             "Level checks answered, out of those sent.", "The level check", 1))
    found = _score(params, ("Uses the program fully", "Uses it well", "Uses it partly", "Hardly uses it"))
    found["how"] = "The weighted average of the parameters below that can be measured (at least two). Each says its rule and where it was read."
    return found


def loyalty(db: Session, candidate) -> dict:
    from app.models.candidate_email import CandidateEmail
    from app.models.registration import RegistrationStatus
    from app.services import program_feedback

    registrations = list(candidate.registrations)
    kept = [item for item in registrations if item.status != RegistrationStatus.CANCELLED]
    cancelled = len(registrations) - len(kept)
    completed = [item for item in kept if item.status == RegistrationStatus.COMPLETED]
    params = []
    count = len(kept)
    params.append(_param("programs", "Programs chosen", f"{count} program{'s' if count != 1 else ''}", 40 if count == 1 else 75 if count == 2 else 100 if count else None,
                         "One program: 40; two: 75; three or more: 100.", "The registrations", 3))
    params.append(_param("completed", "Programs finished", f"{len(completed)} finished", 70 if len(completed) == 1 else 100 if len(completed) >= 2 else None,
                         "One finished: 70; two or more: 100 (none yet: not counted).", "The classes", 3))
    if completed:
        first_end = min(_naive(item.training_end_date) or datetime.max for item in completed)
        back = [item for item in kept if _naive(item.registration_date) and first_end != datetime.max and _naive(item.registration_date) > first_end]
        params.append(_param("back", "Came back after a program", "Yes: registered again after finishing" if back else "Not yet",
                             100 if back else 40, "Registered again after finishing a program: 100; not yet: 40.", "The registrations", 3))
    first = min((_naive(item.registration_date) for item in registrations if item.registration_date), default=None)
    if first:
        months = max(0, (datetime.utcnow() - first).days // 30)
        params.append(_param("time", "Time with 9antra", f"{months} month{'s' if months != 1 else ''}", 40 if months < 1 else 60 if months < 6 else 80 if months < 12 else 100,
                             "Under a month: 40; under 6: 60; under 12: 80; a year or more: 100.", "The first registration", 2))
    reminders = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == candidate.id, CandidateEmail.direction == "outbound",
                                                CandidateEmail.intent == "payment_reminder").count()
    paid_any = any((item.paid_amount or 0) > 0 for item in kept)
    if paid_any:
        params.append(_param("punctual", "Pays without being reminded", f"{reminders} payment reminder{'s' if reminders != 1 else ''} needed",
                             100 if reminders == 0 else 70 if reminders == 1 else 40, "No reminder: 100; one: 70; more: 40.", "The payment reminders", 2))
    params.append(_param("cancelled", "Kept their registrations", f"{cancelled} cancelled" if cancelled else "None cancelled",
                         100 if not cancelled else 30, "None cancelled: 100; one or more: 30.", "The registrations", 2))
    review = next((item["review"] for item in program_feedback.of_candidate(db, candidate.id) if item["review"] and item["review"]["answered"]), None)
    if review is not None:
        tone = review["row"].sentiment
        params.append(_param("review", "Their review", f"{review['stars'] or '–'} stars, {tone or 'no tone'}",
                             100 if tone == "positive" else 60 if tone in ("mixed", "neutral") else 30,
                             "A happy review: 100; mixed: 60; unhappy: 30.", "Their reviews at the end of a program", 3))
    found = _score(params, ("Very loyal", "Loyal", "Could drift away", "At risk of leaving"))
    found["how"] = "The weighted average of the parameters below that can be measured (at least two). Each says its rule and where it was read."
    return found


def of_candidate(db: Session, candidate, behaviour: dict | None = None) -> dict:
    from app.services import behaviour as behaviour_service

    behaviour = behaviour or behaviour_service.profile(db, candidate)
    feelings = feelings_of(db, candidate)
    return {"motivation": motivation(db, candidate, behaviour, feelings), "usage": usage(db, candidate, behaviour),
            "loyalty": loyalty(db, candidate), "feelings": feelings}

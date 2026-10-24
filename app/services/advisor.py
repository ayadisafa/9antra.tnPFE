"""What the admin should do next, and why.

Every recommendation is worked out from the records (payments, reminders
already sent, emails, classes, course files, weekly results), never guessed
by a model, so each one can state the facts it rests on. One line per
subject: a candidate who owes money on three programs is one
recommendation, not three, and it takes into account what was already tried
("three reminders without an answer: call instead of writing again").

A recommendation the admin sets aside ("not now") stays hidden for a few
days, or until the situation changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy.orm import Session, joinedload

from app.models.candidate import Candidate
from app.models.candidate_email import CandidateEmail
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.course_material import AdviceSnooze, ClassMaterial, ClassWeek, WeekQuestion
from app.models.email_thread import EmailThread
from app.models.program import Program
from app.models.registration import PaymentStatus, Registration, RegistrationStatus
from app.services import attendance, enrollment_service, payment_reminders, program_assistant, week_insights, weekly_questions
from app.services.email_templates import money
from app.services.schedule_utils import class_start_dt, format_date_en, local_now, utc_to_local
from app.services.ui_words import plural as _counted, t as _t      # the sentences below come out in the page's language

SNOOZE_DAYS = 3
REMIND_AGAIN_AFTER = payment_reminders.SUGGEST_AGAIN_AFTER   # of silence, before proposing another reminder
FIRST_REMINDER_AFTER = timedelta(hours=24)
CALL_AFTER_REMINDERS = 3
ANSWER_WAIT = timedelta(days=3)             # before chasing unanswered weekly questions

TONES = {"urgent": 0, "important": 1, "info": 2, "good": 3}


@dataclass
class Advice:
    key: str                    # changes when the situation does, so "not now" does not hide news
    priority: int               # 0-100, highest first
    tone: str                   # urgent | important | info | good
    icon: str
    title: str                  # what to do
    why: str                    # the facts behind it
    facts: list[str] = field(default_factory=list)
    action: dict | None = None  # {"label", "url", "method", "fields", "confirm"}
    link: dict | None = None    # {"label", "url"}: where to look first
    can_snooze: bool = True
    set_aside: bool = False     # the admin said "not now": still true, no longer pressing


# ------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------

def _ago(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return _t("never")
    seconds = max(0, (now - moment).total_seconds())
    if seconds < 3600:
        return _t('less than an hour ago')
    if seconds < 86400:
        hours = int(seconds // 3600)
        return _t('{0} hour{1} ago', hours, 's' if hours != 1 else '')
    days = int(seconds // 86400)
    return _t("yesterday") if days == 1 else _t('{0} days ago', days)


def _naive_utc(moment: datetime | None) -> datetime | None:
    """Stored times are UTC, with or without saying so."""
    if moment is None or moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _plural(count: int, word: str) -> str:
    return _counted(count, word)


def _name(candidate: Candidate) -> str:
    return candidate.full_name or candidate.email or _t('This candidate')


def _short_name(candidate: Candidate) -> str:
    """How a recommendation calls someone: the full name when it is short (two
    candidates can share a first name), else the first name."""
    name = _name(candidate)
    if "@" in name:
        return name
    # Names typed in lower case get their capital; the others are left as written.
    words = [word[:1].upper() + word[1:] if word.islower() else word for word in name.split()]
    return " ".join(words) if len(words) <= 2 and len(name) <= 22 else words[0]


def _program(registration: Registration) -> str:
    return registration.program.name if registration.program else (registration.program_name or _t('a program'))


def _owing(candidate: Candidate) -> list[Registration]:
    return [
        item for item in candidate.registrations
        if item.status != RegistrationStatus.CANCELLED and item.remaining_amount > 0
    ]


def _post(label: str, url: str, fields: dict | None = None, confirm: str | None = None) -> dict:
    return {"label": label, "url": url, "method": "post", "fields": fields or {}, "confirm": confirm}


def _go(label: str, url: str) -> dict:
    return {"label": label, "url": url, "method": "get", "fields": {}, "confirm": None}


# ------------------------------------------------------------------
# Payments: one recommendation per candidate
# ------------------------------------------------------------------

def _payment_history(db: Session, candidate_ids: list[str]) -> dict[str, dict]:
    """Per candidate: payment reminders delivered, the last one, whether they wrote
    since, and the registrations a reminder already covered."""
    history = {
        candidate_id: {"sent": 0, "last": None, "failed": 0, "wrote_since": False, "covered": set()}
        for candidate_id in candidate_ids
    }
    if not candidate_ids:
        return history
    for candidate_id, covered in payment_reminders.covered_ids(db, candidate_ids).items():
        history[candidate_id]["covered"] = covered
    emails = (
        db.query(CandidateEmail)
        .filter(CandidateEmail.candidate_id.in_(candidate_ids))
        .order_by(CandidateEmail.received_at.asc())
        .all()
    )
    for email in emails:
        entry = history[email.candidate_id]
        if email.direction == "outbound" and email.intent in ("payment_reminder", "payment_partial"):
            if email.status == "sent":
                entry["sent"] += 1
                entry["last"] = email.received_at
                entry["wrote_since"] = False
            elif email.status == "failed":
                entry["failed"] += 1
        elif email.direction == "inbound" and entry["last"] is not None:
            entry["wrote_since"] = True
    return history


def payment_advice(candidate: Candidate, history: dict, now_utc: datetime, now_local: datetime) -> Advice | None:
    """The next sensible step about what a candidate owes."""
    owing = sorted(_owing(candidate), key=lambda item: item.registration_date or datetime.min)
    if not owing:
        return None
    total = sum(item.remaining_amount for item in owing)
    paid_part = sum(item.paid_amount or 0 for item in owing)
    oldest = owing[0]
    registered = _naive_utc(oldest.registration_date)
    age = (now_utc - registered) if registered else timedelta(days=99)
    sent, last = history["sent"], _naive_utc(history["last"])
    programs = ", ".join(dict.fromkeys(_program(item) for item in owing))
    name, first = _name(candidate), _short_name(candidate)

    # The class of an unpaid registration starting soon makes it pressing.
    starts = [class_start_dt(item.class_group) for item in owing if item.class_group and class_start_dt(item.class_group)]
    soon = min((start for start in starts if start >= now_local), default=None)
    pressing = soon is not None and soon - now_local <= timedelta(days=3)

    facts = [_t('{0} due', money(total)), _plural(len(owing), "program")]
    if paid_part:
        facts.append(_t('{0} already paid', money(paid_part)))
    facts.append(_t('{0} sent', _plural(sent, 'reminder')) if sent else _t('no reminder yet'))
    owes = _t('{0} owes {1}', name, money(total)) + (_t(' on {0} programs ({1})', len(owing), programs) if len(owing) > 1 else _t(' for {0}', programs))
    profile = f"/candidate/{candidate.id}"
    # Nothing goes out from a recommendation: the admin reads the message first.
    write = _go(_t('Read and send'), f"{profile}/payment-reminder")
    key = f"pay:{candidate.id}:{sent}:{len(owing)}"
    base = 55 + min(20, int(total // 100)) + (15 if pressing else 0)
    newer = [item for item in owing if item.id not in history.get("covered", set())] if sent else []
    also = (
        _t(' Since then they also registered for {0}.', ', '.join(dict.fromkeys(_program(item) for item in newer)))
        if newer and len(newer) < len(owing) else ""
    )

    if history["wrote_since"]:
        return Advice(
            key=key + ":wrote", priority=base - 5, tone="important", icon="bi-chat-left-text",
            title=_t("Read {0}'s message before reminding again", first),
            why=_t('{0}. They wrote to you after the last reminder ({1}).', owes, _ago(last, now_utc)),
            facts=facts, action=_go(_t('Open the discussion'), f"/inbox?candidate={candidate.id}"),
            link={"label": "Profile", "url": profile},
        )
    if sent == 0:
        if age < FIRST_REMINDER_AFTER:
            return Advice(
                key=key + ":new", priority=20, tone="info", icon="bi-hourglass-split",
                title=_t("Nothing to do yet for {0}'s payment", first),
                why=_t('{0}, registered {1}. One reminder goes out by itself 24 hours after the registration.', owes, _ago(registered, now_utc)),
                facts=facts, link={"label": "Profile", "url": profile}, can_snooze=False,
            )
        return Advice(
            key=key, priority=base + 5, tone="urgent" if pressing else "important", icon="bi-cash-coin",
            title=_t('Send {0} a payment reminder', first),
            why=_t('{0} and has not been reminded since registering {1}.', owes, _ago(registered, now_utc))
                + (_t(' The class starts {0}.', format_date_en(soon)) if pressing else ""),
            facts=facts, action=write, link={"label": "Profile", "url": profile},
        )
    if last is not None and now_utc - last < REMIND_AGAIN_AFTER:
        again = utc_to_local(last + REMIND_AGAIN_AFTER)
        return Advice(
            key=key + ":wait", priority=15, tone="info", icon="bi-hourglass-split",
            title=_t('Give {0} time to answer', first),
            why=_t('{0}. The reminder went out {1}. Nothing more is sent by itself; if there is still no payment and no answer on {2:%A %d %b}, you will be offered to send it again.{3}', owes, _ago(last, now_utc), again, also),
            facts=facts, link={"label": "Profile", "url": profile}, can_snooze=False,
        )
    silent_days = max(REMIND_AGAIN_AFTER.days, (now_utc - last).days) if last is not None else REMIND_AGAIN_AFTER.days
    if sent >= CALL_AFTER_REMINDERS:
        phone = (candidate.phone or "").strip()
        return Advice(
            key=key + ":call", priority=base + 10, tone="urgent" if pressing else "important", icon="bi-telephone-outbound",
            title=_t('Call {0}', first) + (_t(' on {0}', phone) if phone else "") + _t(': the reminders are not working'),
            why=_t('{0}. {1} went out, the last {2}, with no payment and no answer. Another email is unlikely to change that.{3}', owes, _plural(sent, 'reminder'), _ago(last, now_utc), also),
            facts=facts,
            action=_go("Call", f"tel:{quote(phone)}") if phone else write,
            link={"label": _t('Write anyway'), "url": f"{profile}/payment-reminder"} if phone else {"label": "Profile", "url": profile},
        )
    return Advice(
        key=key, priority=base, tone="urgent" if pressing else "important", icon="bi-cash-coin",
        title=_t('{0} has not answered for {1} days: send the payment reminder again?', first, silent_days),
        why=_t('{0}. The reminder of {1:%d %b} brought no payment and no answer.{2} Nothing is sent unless you decide: read the message, change it if you want, then send.', owes, utc_to_local(last), also)
            + (_t(' The class starts {0}.', format_date_en(soon)) if pressing else ""),
        facts=facts, action=write, link={"label": "Profile", "url": profile},
    )


# ------------------------------------------------------------------
# Emails
# ------------------------------------------------------------------

def inbox_advice(db: Session, now_utc: datetime, candidate_id: str | None = None) -> list[Advice]:
    found = []
    query = db.query(EmailThread).options(joinedload(EmailThread.candidate)).filter(EmailThread.needs_admin.is_(True))
    if candidate_id:
        query = query.filter(EmailThread.candidate_id == candidate_id)
    waiting = query.order_by(EmailThread.last_message_at.asc()).all()
    if waiting:
        oldest = waiting[0]
        since = _naive_utc(oldest.last_message_at)
        hours = (now_utc - since).total_seconds() / 3600 if since else 0
        who = _name(oldest.candidate) if oldest.candidate else _t('A candidate')
        if len(waiting) == 1:
            title = _t("Answer {0}'s email", who.split()[0].capitalize())
            why = _t('“{0}” has waited since {1}. The system was not sure enough to answer alone.', oldest.subject or _t('No subject'), _ago(since, now_utc))
            url = f"/inbox?filter=todo&thread={oldest.id}"
        else:
            title = _t('Answer {0} candidate emails', len(waiting))
            why = _t('The oldest, from {0}, has waited since {1}. The system was not sure enough to answer them alone.', who, _ago(since, now_utc))
            url = "/inbox?filter=todo"
        found.append(Advice(
            key=f"inbox:{candidate_id or 'all'}:{len(waiting)}:{oldest.id}",
            # A candidate waiting for an answer comes before everything else.
            priority=min(98, 86 + int(hours // 4)), tone="urgent" if hours >= 24 else "important",
            icon="bi-envelope-exclamation", title=title, why=why,
            facts=[_t('{0} waiting', _plural(len(waiting), 'email'))], action=_go(_t('Open the inbox'), url), can_snooze=False,
        ))

    failed_query = db.query(CandidateEmail).filter(
        CandidateEmail.direction == "outbound", CandidateEmail.status == "failed",
        CandidateEmail.received_at >= now_utc - timedelta(days=14),
    )
    if candidate_id:
        failed_query = failed_query.filter(CandidateEmail.candidate_id == candidate_id)
    failed = failed_query.order_by(CandidateEmail.received_at.desc()).all()
    if failed:
        newest = failed[0]
        found.append(Advice(
            key=f"failed:{candidate_id or 'all'}:{len(failed)}:{newest.id}",
            priority=70, tone="important", icon="bi-send-x",
            title=_t('{0} did not reach {1}', _plural(len(failed), 'email'), _t('this candidate') if candidate_id else _t('the candidates')),
            why=_t('The latest is “{0}”, {1}. Open it and use “Send again” once the email settings are right.', newest.subject or _t('No subject'), _ago(_naive_utc(newest.received_at), now_utc)),
            action=_go(_t('Open it'), f"/inbox?filter=all&thread={newest.thread_id}" if newest.thread_id else "/inbox?filter=all"),
        ))
    return found


# ------------------------------------------------------------------
# Classes, course files and weekly questions
# ------------------------------------------------------------------

def _members(class_group: ClassGroup) -> list[Registration]:
    return [item for item in class_group.registrations if item.status != RegistrationStatus.CANCELLED]


def class_advice(db: Session, class_group: ClassGroup, now_local: datetime, now_utc: datetime) -> list[Advice]:
    """What one class needs."""
    if class_group.status == ClassStatus.COMPLETED:
        return []
    found = []
    url = f"/classes/{class_group.id}"
    members = _members(class_group)
    start = class_start_dt(class_group)
    days_to_start = (start - now_local).days if start and start >= now_local else None
    not_started = class_group.status == ClassStatus.NOT_STARTED
    # Its date has come but it waits: nobody in it, or no professor of its speciality.
    waiting = not_started and start is not None and start < now_local
    starting = waiting or (not_started and days_to_start is not None and days_to_start <= 7)
    soon = waiting or (starting and days_to_start <= 3)

    if members and (starting or class_group.status == ClassStatus.ONGOING):
        when = (_t('was due to start {0} and waits', format_date_en(start)) if waiting
                else _t('starts {0}', format_date_en(start)) if starting else _t('is running'))
        professor = class_group.professor
        if professor is None and not class_group.professor_name:
            found.append(Advice(
                key=f"class:{class_group.id}:professor", priority=88 if soon else 72,
                tone="urgent" if soon else "important", icon="bi-person-exclamation",
                title=_t('Give {0} a professor', class_group.name),
                why=_t('The class {0} with {1} and nobody is assigned to teach it. A class starts only with a professor of its speciality.', when, _plural(len(members), 'candidate')),
                action=_go(_t('Choose a professor'), url + "#parameters"),
            ))
        elif professor is not None and class_group.program is not None and not enrollment_service.teaches(professor, class_group.program):
            found.append(Advice(
                key=f"class:{class_group.id}:speciality:{professor.id}", priority=86 if soon and not_started else 60,
                tone="urgent" if soon and not_started else "important", icon="bi-mortarboard",
                title=_t("{0} is not one of {1}'s specialities", class_group.program.name, professor.name),
                why=_t('{0} {1} with {2}. Only a professor of the speciality teaches a class: add it to their profile if they do teach it, or choose another professor.', class_group.name, when, _plural(len(members), 'candidate'))
                    + (_t(' Until then the class does not start.') if not_started else ""),
                action=_go(_t('Open the class'), url),
            ))
        if not class_group.start_time:
            found.append(Advice(
                key=f"class:{class_group.id}:time", priority=66, tone="important", icon="bi-clock",
                title=_t('Set the session time of {0}', class_group.name),
                why=_t('The class {0}, but its emails and reminders cannot tell candidates what time to come.', when),
                facts=[class_group.name],
                action=_go(_t('Set the time'), url + "#parameters"),
            ))

    if not members and (waiting or class_group.status == ClassStatus.ONGOING):
        found.append(Advice(
            key=f"class:{class_group.id}:empty", priority=38, tone="info", icon="bi-people",
            title=_t('{0} has no candidate, so it does not start', class_group.name),
            why=_t('A class runs only with somebody in it. This one keeps a place on the timetable: remove it, or move candidates into it.'),
            facts=[class_group.name],
            action=_go(_t('Open the class'), url),
        ))

    if class_group.status != ClassStatus.ONGOING or not members:
        return found

    # Candidates who stopped coming.
    missing = [person for person in attendance.class_summary(db, class_group)["people"] if person["absent_in_a_row"] >= 2]
    if missing:
        names = ", ".join(person["name"] for person in missing)
        most = max(person["absent_in_a_row"] for person in missing)
        found.append(Advice(
            key=f"class:{class_group.id}:absent:" + ",".join(sorted(f"{person['registration'].id}:{person['absent_in_a_row']}" for person in missing)),
            priority=66, tone="important", icon="bi-person-x",
            title=(_t('{0} missed the last {1} sessions of {2}', missing[0]['name'], missing[0]['absent_in_a_row'], class_group.name) if len(missing) == 1
                   else _t('{0} candidates stopped coming to {1}', len(missing), class_group.name)),
            why=_t('{0}: absent {1} in a row or more. A call now is the best chance to keep them in the class.', names, 'twice' if most == 2 else str(most) + ' times'),
            action=_go(_t('See the presence sheet'), f"{url}/attendance"),
            link={"label": "Profile", "url": f"/candidate/{missing[0]['registration'].candidate_id}"} if len(missing) == 1 else None,
        ))

    course = f"{url}/course"
    current = max(1, class_group.current_week or 1)
    program = class_group.program
    if not class_group.sessions and program is not None and not program_assistant.has_curriculum(program):
        found.append(Advice(
            key=f"class:{class_group.id}:plan", priority=58, tone="important", icon="bi-list-check",
            title=_t('{0} has no weekly plan', class_group.name),
            why=_t('Its program, {0}, has no week-by-week content yet, so candidates were told nothing about what they will study.', program.name),
            facts=[class_group.name],
            action=_go(_t('Review the program'), f"/programs/all?review={program.id}"),
        ))

    weeks = {row.week_number: row for row in db.query(ClassWeek).filter(ClassWeek.class_group_id == class_group.id).all()}
    files = {}
    for (week_number,) in db.query(ClassMaterial.week_number).filter(ClassMaterial.class_group_id == class_group.id).all():
        files[week_number] = files.get(week_number, 0) + 1
    questions: dict[int, dict[str, int]] = {}
    for week_number, status in db.query(WeekQuestion.week_number, WeekQuestion.status).filter(
        WeekQuestion.class_group_id == class_group.id
    ).all():
        questions.setdefault(week_number, {}).setdefault(status, 0)
        questions[week_number][status] += 1

    # This week's course: nothing to learn from yet.
    week_row = weeks.get(current)
    if not files.get(current) and not (week_row and (week_row.notes or "").strip()) and not questions.get(current):
        found.append(Advice(
            key=f"class:{class_group.id}:files:{current}", priority=46, tone="info", icon="bi-folder-plus",
            title=_t("Add week {0}'s course to {1}", current, class_group.name),
            why=_t('The class is in week {0} of {1}. With the course files (or a few lines about what was covered) the system writes the questions that measure what candidates understood.', current, class_group.total_weeks or 12),
            facts=[_t('{0} · week {1}', class_group.name, current)],
            action=_go(_t('Add the course'), f"{course}?week={current}"),
        ))

    for week_number in sorted(questions):
        counts = questions[week_number]
        row = weeks.get(week_number)
        ready, review = counts.get("ready", 0), counts.get("review", 0)
        sent = row is not None and row.sent_at is not None
        if not sent and ready and weekly_questions.week_is_over(db, class_group, week_number, now_local):
            found.append(Advice(
                key=f"class:{class_group.id}:send:{week_number}:{ready}", priority=64, tone="important", icon="bi-send-check",
                title=_t("Send week {0}'s questions to {1}", week_number, class_group.name),
                why=_t('The week is over and {0} are ready', _plural(ready, 'question'))
                    + (_t(' ({0} more wait for your review)', review) if review else "")
                    + _t('. {0} will each get a few of them.', _plural(len(members), 'candidate')),
                action=_go(_t('Check and send'), f"{course}?week={week_number}"),
            ))
        elif not sent and review and not ready:
            found.append(Advice(
                key=f"class:{class_group.id}:review:{week_number}:{review}", priority=52, tone="important", icon="bi-patch-question",
                title=_t("Review week {0}'s questions for {1}", week_number, class_group.name),
                why=_t('The system wrote {0} but could not confirm any of them against the course, so none will be sent until you check them.', _plural(review, 'question')),
                action=_go("Review", f"{course}?week={week_number}"),
            ))

    # Results of the weeks already sent.
    for week_number, row in sorted(weeks.items()):
        if row.sent_at is None:
            continue
        results = weekly_questions.week_results(db, class_group, week_number)
        if results["answered"] and results["average"] is not None and results["average"] < weekly_questions.PARTIAL:
            hardest = results["hardest"][0]["question"] if results["hardest"] else None
            found.append(Advice(
                key=f"class:{class_group.id}:low:{week_number}:{results['answered']}", priority=74, tone="important", icon="bi-graph-down-arrow",
                title=_t('Go over week {0} again with {1}', week_number, class_group.name),
                why=_t('{0} of {1} answered and the average is {2:.0f}%.', results['answered'], results['sent'], results['average'])
                    + (_t(' Most missed: “{0}”', hardest) if hardest else ""),
                action=_go(_t('See the answers'), f"{course}?week={week_number}"),
            ))
        if results["answered"] and week_number == max(weeks):
            found += feeling_advice(db, class_group, week_number, results)
        silent = results["sent"] - results["answered"]
        sent_at = _naive_utc(row.sent_at)
        if silent and sent_at and now_utc - sent_at >= ANSWER_WAIT:
            found.append(Advice(
                key=f"class:{class_group.id}:silent:{week_number}:{silent}", priority=44, tone="info", icon="bi-bell",
                title=_t("{0} of {1} have not answered week {2}'s questions in {3}", silent, results['sent'], week_number, class_group.name),
                why=_t('They were sent {0}. A reminder sends each of them their own questions again.', _ago(sent_at, now_utc)),
                action=_post(_t('Remind them'), f"{course}/{week_number}/send", {"remind": "1"},
                             confirm=_t('Send the questions again to the {0} candidate(s) who have not answered?', silent)),
                link={"label": _t('See who'), "url": f"{course}?week={week_number}"},
            ))
    return found


def feeling_advice(db: Session, class_group: ClassGroup, week_number: int, results: dict) -> list[Advice]:
    """What the candidates said about the latest week: who needs a word, and what to change in class."""
    feelings = week_insights.week_feelings(db, class_group, week_number, hardest=results["hardest"])
    course = f"/classes/{class_group.id}/course?week={week_number}#feelings"
    found = []
    for row in feelings["people"][:3]:
        insight = row["insight"]
        if insight["attention"]["level"] != "act" or row["candidate"] is None:
            continue
        candidate = row["candidate"]
        first = _short_name(candidate)
        words = _t(' They wrote: “{0}”', insight['words'][:160]) if insight.get("words") else ""
        found.append(Advice(
            key=f"feel:{candidate.id}:{week_number}:{insight['attention']['points']}",
            priority=82 if insight.get("giving_up") else 72, tone="urgent" if insight.get("giving_up") else "important",
            icon="bi-heart-pulse",
            title=_t('Talk to {0}: week {1} of {2} went badly', first, week_number, class_group.name),
            why=(", ".join(insight["attention"]["reasons"]).capitalize() + "." + words),
            facts=[fact for fact in (
                _t('{0:.0f}% on the questions', insight['score']) if insight.get("score") is not None else "",
                insight["mood_view"]["label"] if insight.get("mood_view") else "",
            ) if fact],
            action=_go(_t('Write to them'), f"/inbox/compose?candidate={candidate.id}"),
            link={"label": _t('What they said'), "url": f"/candidate/{candidate.id}#feelings"},
        ))
    if feelings["advice"]:
        first_line = feelings["advice"][0]["text"]
        more = len(feelings["advice"]) - 1
        found.append(Advice(
            key=f"class:{class_group.id}:feel:{week_number}:{feelings['answers']}:{len(feelings['advice'])}",
            priority=58, tone="info", icon="bi-emoji-smile",
            title=_t('Before the next session of {0}', class_group.name),
            why=first_line + (_t(' And {0} more point{1} from what the candidates said.', more, 's' if more != 1 else '') if more else ""),
            facts=[_t('{0} said how the week went', feelings['felt'])] + ([_t('{0} out of 5', feelings['mood']['average'])] if feelings["mood"]["average"] else []),
            action=_go(_t('See how they feel'), course),
        ))
    return found


def placement_advice(db: Session) -> list[Advice]:
    waiting = (
        db.query(Registration)
        .options(joinedload(Registration.candidate))
        .filter(
            Registration.payment_status == PaymentStatus.PAID,
            Registration.class_group_id.is_(None),
            Registration.status != RegistrationStatus.CANCELLED,
        )
        .order_by(Registration.registration_date.asc())
        .all()
    )
    if not waiting:
        return []
    names = ", ".join(dict.fromkeys(_name(item.candidate) for item in waiting if item.candidate))
    return [Advice(
        key=f"placement:{len(waiting)}:{waiting[0].id}", priority=82, tone="important", icon="bi-person-plus",
        title=_t('Place {0} in a class', _plural(len(waiting), 'paid candidate')),
        why=_t('{0} paid in full and {1} no class yet, so no date was announced to them.', names, 'has' if len(waiting) == 1 else 'have'),
        action=_go("Decide", "/classes/?tab=decisions"),
    )]


# ------------------------------------------------------------------
# Programs
# ------------------------------------------------------------------

def program_advice(db: Session) -> list[Advice]:
    found = []
    programs = {program.id: program for program in db.query(Program).all()}
    proposals = program_assistant.open_proposals(db)
    ready = [programs[item.program_id] for item in proposals.values() if item.status == "ready" and item.program_id in programs]
    if ready:
        first = ready[0]
        found.append(Advice(
            key=f"proposals:{len(ready)}:{first.id}", priority=50, tone="important", icon="bi-magic",
            title=(_t('Confirm the proposal for {0}', first.name) if len(ready) == 1
                   else _t('Confirm the proposals for {0} programs', len(ready))),
            why=(_t('Their description, hours or weekly plan were missing. The system prepared them; nothing is saved until you accept: ')
                 + ", ".join(program.name for program in ready) + "."),
            action=_go("Review", f"/programs/all?review={first.id}"),
        ))
    pending = [program for program in programs.values() if not program.is_approved]
    if pending:
        found.append(Advice(
            key=f"approve:{len(pending)}", priority=34, tone="info", icon="bi-check2-square",
            title=_t('Approve {0}', _plural(len(pending), 'program')),
            why=_t('Waiting for your approval: ') + ", ".join(program.name for program in pending[:4]) + ("…" if len(pending) > 4 else "."),
            action=_go(_t('Open the programs'), "/programs/all?status=pending"),
        ))
    return found


# ------------------------------------------------------------------
# Putting it together
# ------------------------------------------------------------------

def _visible(db: Session, items: list[Advice], now_utc: datetime) -> list[Advice]:
    """Without what the admin set aside, most pressing first."""
    snoozed = {row.key for row in db.query(AdviceSnooze).filter(AdviceSnooze.until > now_utc).all()}
    kept = [item for item in items if item.key not in snoozed]
    kept.sort(key=lambda item: (-item.priority, TONES.get(item.tone, 9), item.title))
    return kept


def snooze(db: Session, key: str, days: int = SNOOZE_DAYS) -> None:
    until = datetime.utcnow() + timedelta(days=max(1, min(days, 30)))
    row = db.get(AdviceSnooze, key)
    if row is None:
        db.add(AdviceSnooze(key=key[:300], until=until))
    else:
        row.until = until
    db.query(AdviceSnooze).filter(AdviceSnooze.until < datetime.utcnow() - timedelta(days=30)).delete(synchronize_session=False)
    db.commit()


def for_dashboard(db: Session) -> list[Advice]:
    """Everything that needs the admin, most pressing first."""
    now_utc, now_local = datetime.utcnow(), local_now()
    items: list[Advice] = []
    items += inbox_advice(db, now_utc)
    items += placement_advice(db)

    candidates = (
        db.query(Candidate)
        .options(
            joinedload(Candidate.registrations).joinedload(Registration.program),
            joinedload(Candidate.registrations).joinedload(Registration.class_group),
        )
        .all()
    )
    history = _payment_history(db, [candidate.id for candidate in candidates])
    for candidate in candidates:
        advice = payment_advice(candidate, history[candidate.id], now_utc, now_local)
        # "Wait" and "nothing yet" belong on the candidate's own page.
        if advice is not None and advice.tone != "info":
            items.append(advice)

    classes = (
        db.query(ClassGroup)
        .options(joinedload(ClassGroup.registrations), joinedload(ClassGroup.sessions), joinedload(ClassGroup.program))
        .all()
    )
    for class_group in classes:
        items += class_advice(db, class_group, now_local, now_utc)
    items = _one_line_per_need(items)
    items += program_advice(db)
    return _visible(db, items, now_utc)


# The same need in several classes is one recommendation naming them, not one line each:
# kind -> (title, what it means, icon).
_SAME_NEED = {
    "files": (_t("Add this week's course to {{count}} classes"),
              _t('With the course files (or a few lines about what was covered) the system writes the questions that measure what candidates understood. Nothing was added yet for: {{names}}.'), "bi-folder-plus"),
    "time": (_t('Set the session time of {{count}} classes'),
             _t('Their emails and reminders cannot tell candidates what time to come: {{names}}.'), "bi-clock"),
    "plan": (_t('{{count}} classes have no weekly plan'),
             _t('Their programs have no week-by-week content yet, so candidates were told nothing about what they will study: {{names}}.'), "bi-list-check"),
    "empty": (_t('{{count}} classes have no candidate, so they do not start'),
              _t('A class runs only with somebody in it. These keep a place on the timetable: delete them, or move candidates into them: {{names}}.'),
              "bi-people"),
}


def _one_line_per_need(items: list[Advice]) -> list[Advice]:
    """Group class recommendations of the same kind when several classes share them."""
    kept = list(items)
    for kind, (title, why, icon) in _SAME_NEED.items():
        same = [item for item in kept if item.key.startswith("class:") and item.key.split(":")[2] == kind]
        if len(same) < 2:
            continue
        names = [fact for item in same for fact in item.facts]
        kept = [item for item in kept if item not in same]
        kept.append(Advice(
            key=f"{kind}:" + "|".join(sorted(item.key for item in same)),
            priority=max(item.priority for item in same),
            tone=min((item.tone for item in same), key=lambda tone: TONES.get(tone, 9)),
            icon=icon,
            title=title.format(count=len(same)),
            why=why.format(names=", ".join(names)),
            action=_go(_t('Start with ') + names[0].split(" · ")[0], same[0].action["url"]),
            link={"label": _t('All classes'), "url": "/classes/"},
        ))
    return kept


def for_candidate(db: Session, candidate: Candidate) -> list[Advice]:
    """What to do next about one candidate. Never empty: says so when all is fine."""
    now_utc, now_local = datetime.utcnow(), local_now()
    items: list[Advice] = []
    profile = f"/candidate/{candidate.id}"
    first = _short_name(candidate)
    items += inbox_advice(db, now_utc, candidate.id)

    history = _payment_history(db, [candidate.id])
    payment = payment_advice(candidate, history[candidate.id], now_utc, now_local)
    if payment is not None:
        items.append(payment)

    active = [item for item in candidate.registrations if item.status != RegistrationStatus.CANCELLED]
    unplaced = [item for item in active if item.payment_status == PaymentStatus.PAID and not item.class_group_id]
    if unplaced:
        items.append(Advice(
            key=f"place:{candidate.id}:{len(unplaced)}", priority=80, tone="important", icon="bi-person-plus",
            title=_t('Place {0} in a class', first),
            why=_t('Paid in full for {0} and still without a class, so no start date was announced.', ', '.join(_program(item) for item in unplaced)),
            action=_go("Decide", f"/classes/registration/{unplaced[0].id}"),
        ))

    learning = weekly_questions.candidate_weeks(db, candidate.id)
    answered = [week for week in learning["weeks"] if week["done"]]
    if answered:
        last = answered[-1]
        if last["score"] is not None and last["score"] < weekly_questions.PARTIAL:
            missed = last["missed"][0] if last["missed"] else None
            items.append(Advice(
                key=f"learn:{candidate.id}:{last['quiz'].id}", priority=68, tone="important", icon="bi-life-preserver",
                title=_t('Help {0} with week {1} of {2}', first, last['week'], last['class_name']),
                why=_t("Scored {0:.0f}% on the week's questions", last['score'])
                    + (_t('; missed “{0}”', missed) if missed else "")
                    + (_t('. The previous weeks went better, so this topic is the problem.') if learning["trend"] == "down" else "."),
                action=_go(_t('Write to them'), f"/inbox/compose?candidate={candidate.id}"),
                link={"label": _t('See the answers'), "url": f"/classes/{last['class_id']}/course?week={last['week']}"},
            ))
    feelings = week_insights.candidate_feelings(db, candidate.id)
    latest = feelings["latest"]
    if latest is not None and latest["insight"]["attention"]["level"] == "act":
        insight = latest["insight"]
        items.append(Advice(
            key=f"feel:{candidate.id}:{latest['week']}:{insight['attention']['points']}",
            priority=82 if insight.get("giving_up") else 72, tone="urgent" if insight.get("giving_up") else "important",
            icon="bi-heart-pulse", title=_t('Talk to {0}: week {1} went badly', first, latest['week']),
            why=", ".join(insight["attention"]["reasons"]).capitalize() + "."
                + (_t(' They wrote: “{0}”', insight['words'][:160]) if insight.get("words") else ""),
            action=_go(_t('Write to them'), f"/inbox/compose?candidate={candidate.id}"),
            link={"label": _t('What they said'), "url": f"{profile}#feelings"},
        ))
    waiting = [week for week in learning["weeks"] if week["sent"] and not week["done"]]
    if len(waiting) >= 2:
        items.append(Advice(
            key=f"silent:{candidate.id}:{len(waiting)}", priority=48, tone="info", icon="bi-bell",
            title=_t('{0} has not answered {1} sets of weekly questions', first, len(waiting)),
            why="Weeks " + ", ".join(str(week["week"]) for week in waiting) + _t('. A candidate who stops answering often stopped following.'),
            action=_go(_t('Write to them'), f"/inbox/compose?candidate={candidate.id}"),
        ))

    if not candidate.registrations:
        items.append(Advice(
            key=f"noreg:{candidate.id}", priority=40, tone="info", icon="bi-journal-plus",
            title=_t('{0} is not registered to any program', first),
            why=_t('Add a program below to start their file.'), can_snooze=False,
        ))

    # On the candidate's own page every step is shown: "not now" only quiets the
    # dashboard and the list, it must never make this page say all is well.
    snoozed = {row.key for row in db.query(AdviceSnooze).filter(AdviceSnooze.until > now_utc).all()}
    for item in items:
        if item.key in snoozed:
            item.set_aside = True
            item.why += _t(' (You set this aside on the dashboard for a few days.)')
        item.can_snooze = False
    items.sort(key=lambda item: (-item.priority, TONES.get(item.tone, 9), item.title))
    if not items:
        items = [Advice(
            key=f"fine:{candidate.id}", priority=0, tone="good", icon="bi-check2-circle",
            title=_t('Nothing needs you for {0}', first),
            why=_t('Payments are settled, no email is waiting and no result is worrying.'), can_snooze=False,
        )]
    return items


def for_candidates(db: Session, candidates: list[Candidate]) -> dict[str, Advice | None]:
    """The single most pressing step for each candidate of a list (None: nothing to do).

    Works on the whole list at once, so the candidates page stays fast.
    """
    now_utc, now_local = datetime.utcnow(), local_now()
    ids = [candidate.id for candidate in candidates]
    history = _payment_history(db, ids)
    waiting: dict[str, EmailThread] = {}
    if ids:
        for thread in (
            db.query(EmailThread)
            .filter(EmailThread.needs_admin.is_(True), EmailThread.candidate_id.in_(ids))
            .order_by(EmailThread.last_message_at.desc())
            .all()
        ):
            waiting[thread.candidate_id] = thread
    snoozed = {row.key for row in db.query(AdviceSnooze).filter(AdviceSnooze.until > now_utc).all()}

    chosen: dict[str, Advice | None] = {}
    for candidate in candidates:
        options: list[Advice] = []
        first = _short_name(candidate)
        thread = waiting.get(candidate.id)
        if thread is not None:
            options.append(Advice(
                key=f"inbox:{candidate.id}:{thread.id}", priority=90, tone="important", icon="bi-envelope-exclamation",
                title=_t("Answer {0}'s email", first),
                why=_t('“{0}” is waiting for your answer.', thread.subject or _t('No subject')),
                action=_go("Answer", f"/inbox?filter=todo&thread={thread.id}"), can_snooze=False,
            ))
        unplaced = [
            item for item in candidate.registrations
            if item.status != RegistrationStatus.CANCELLED
            and item.payment_status == PaymentStatus.PAID and not item.class_group_id
        ]
        if unplaced:
            options.append(Advice(
                key=f"place:{candidate.id}:{len(unplaced)}", priority=80, tone="important", icon="bi-person-plus",
                title=_t('Place in a class'), why=_t('Paid for {0} and has no class yet.', _program(unplaced[0])),
                action=_go("Decide", f"/classes/registration/{unplaced[0].id}"),
            ))
        payment = payment_advice(candidate, history[candidate.id], now_utc, now_local)
        if payment is not None:
            options.append(payment)
        # What was set aside stays visible on the row, without asking for attention.
        for item in options:
            item.set_aside = item.key in snoozed
        options.sort(key=lambda item: (item.set_aside, -item.priority))
        chosen[candidate.id] = options[0] if options else None
    return chosen


def summary(items: list[Advice]) -> dict:
    return {
        "total": len(items),
        "urgent": sum(1 for item in items if item.tone == "urgent"),
        "important": sum(1 for item in items if item.tone == "important"),
    }

"""Prices are for one month: each month of a program is paid to continue it.

A program's price (online or on site) is the price of ONE month. A program lasts its duration in months (Duration in
days / 30, at least one: 60 days = 2 months, 180 days = 6). A registration owes, at any time, the months begun so far:

    registering                -> the first month is owed (total_amount = one month); paying it places them in a class
    month k starts (class start + k-1 months)
                               -> month k is added to what they owe (total_amount += one month); not paid -> PARTIAL
    N days before month k      -> ONE reminder (email template "month_due", Settings › Classes › Monthly payments)
    G days after month k began and still not paid
                               -> their access pauses (paused_at): no session link, their questions and course
                                  files wait, they are told once (template "month_paused") and the admin gets one
                                  notification (MONTH_UNPAID) until they pay
    they pay                   -> it resumes, and they are told (template "month_received")

A candidate may pay several months ahead: the payment is accepted up to the whole program. A month that started before
monthly payments were switched on (system_settings.monthly_since, set on the first run), or so soon after that its
reminder could not go on time, is never added by itself: the Payments page lists it once it started, for the admin, who
adds it or leaves it free. The price of a month follows the
registration's way (online or on site) when the month starts: a candidate who changes way pays the new price from the
next month on (services/mode_change.py). Everything is counted from the records, never by a model.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.notification import Notification
from app.models.registration import PaymentStatus, Registration, RegistrationStatus
from app.services.ui_words import t as _t

logger = logging.getLogger(__name__)

DAYS_PER_MONTH = 30.4


def money(value) -> str:
    amount = round(float(value or 0), 2)
    return f"{amount:.0f} DT" if amount == int(amount) else f"{amount:.2f} DT"


def program_months(program) -> int:
    """How many months a program lasts: its duration in days, in months (at least one)."""
    days = getattr(program, "total_duration_days", None) or 0
    return max(1, round(days / DAYS_PER_MONTH)) if days else 1


def months_of(registration: Registration) -> int:
    return registration.months or (program_months(registration.program) if registration.program else 1)


def price_now(registration: Registration) -> float:
    """One month's price for this registration today: its program's price for its way (online or on site)."""
    from app.services.program_names import price_of

    if registration.program is not None:
        value = price_of(registration.program, registration.delivery_mode)
        if value:
            return float(value)
    return float(registration.month_price or registration.total_price or registration.total_amount or 0.0)


def month_price(registration: Registration) -> float:
    """The price of the next month to count: the one kept on the registration, else today's."""
    return float(registration.month_price or price_now(registration))


def counted(registration: Registration) -> int:
    return registration.months_counted or 1


def program_total(registration: Registration) -> float:
    """What the whole program costs this candidate: the months counted so far, and the others at today's month price."""
    rest = max(0, months_of(registration) - counted(registration))
    return round(float(registration.total_amount or 0.0) + rest * month_price(registration), 2)


def add_months(day: date, count: int) -> date:
    month = day.month - 1 + count
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def start_of(registration: Registration) -> date | None:
    """The first day of their class, when it has one."""
    from app.services.schedule_utils import class_start_dt

    group = registration.class_group
    start = class_start_dt(group) if group is not None else None
    return start.date() if start else None


def month_start(registration: Registration, number: int) -> date | None:
    start = start_of(registration)
    return add_months(start, number - 1) if start else None


def month_now(registration: Registration, today: date) -> int:
    """The month of the program running today (0 before it starts, at most the last month)."""
    start = start_of(registration)
    if start is None or today < start:
        return 0
    number = 1
    while number < months_of(registration) and add_months(start, number) <= today:
        number += 1
    return number


def months_paid(registration: Registration) -> int:
    """How many months what they paid covers (the counted months first, then the coming ones at their price)."""
    paid = float(registration.paid_amount or 0.0) + 0.01
    owed = float(registration.total_amount or 0.0)
    if owed and paid >= owed:
        extra = int((paid - owed) // month_price(registration)) if month_price(registration) else 0
        return min(months_of(registration), counted(registration) + extra)
    price = month_price(registration) or owed or 1.0
    return min(counted(registration), int(paid // price))


def answer_for(registration: Registration, number: int) -> str | None:
    """Their answer to "do you continue with month `number`?": "yes", "no" or None."""
    said, _, choice = (registration.month_answer or "").partition(":")
    return choice if said == str(number) and choice in ("yes", "no") else None


def last_month_day(registration: Registration) -> date | None:
    """The day their chosen months end (the next month's first day), when they chose fewer months than the program."""
    if not registration.months or registration.program is None or registration.months >= program_months(registration.program):
        return None
    return month_start(registration, registration.months + 1)


def ended_before(registration: Registration, day: date | None) -> bool:
    """Their chosen months are over on that day: the sessions from then on are not theirs."""
    end = last_month_day(registration)
    return bool(end is not None and day is not None and day >= end)


def stopped(registration: Registration | None, today: date | None = None) -> bool:
    from app.services.schedule_utils import local_now

    return registration is not None and ended_before(registration, today or local_now().date())


def no_access(registration: Registration | None) -> str | None:
    """"paused" (a month not paid), "ended" (they stopped after their months), or None."""
    if paused(registration):
        return "paused"
    if stopped(registration):
        return "ended"
    return None


def ensure_token(db: Session, registration: Registration) -> str:
    import secrets

    if not registration.month_token:
        registration.month_token = secrets.token_urlsafe(18)
        db.flush()
    return registration.month_token


def by_token(db: Session, token: str) -> Registration | None:
    if not token or len(token) < 16:
        return None
    return db.query(Registration).filter(Registration.month_token == token).first()


def upcoming(registration: Registration) -> int | None:
    """The month they can still say yes or no to: the next one, while it has not started."""
    from app.services.schedule_utils import local_now

    number = counted(registration) + 1
    if registration.program is None or number > program_months(registration.program):
        return None
    begins = month_start(registration, number)
    if begins is None or begins <= local_now().date():
        return None
    return number


def answer(db: Session, registration: Registration, yes: bool, *, by: str = "the candidate") -> dict | None:
    """They continue with the next month (it is owed when it starts) or stop after this one (nothing more is owed, the
    later sessions are not theirs). Can be changed until that month starts. Returns what to tell them, or None."""
    number = upcoming(registration)
    if number is None:
        return None
    begins = month_start(registration, number)
    who = registration.candidate.full_name or registration.candidate.email
    registration.month_answer, registration.month_answer_at = f"{number}:{'yes' if yes else 'no'}", datetime.utcnow()
    if yes:
        if registration.months and registration.months < program_months(registration.program):
            registration.months = None                   # they had said no: the whole program again
        title, text = f"{who} continues with month {number}", _t("{0} continues with month {1} of {2} ({3}, from {4}).", who, number,
                                                                   registration.program_name or "", money(price_now(registration)), begins.strftime("%d/%m"))
    else:
        registration.months = counted(registration)
        title, text = f"{who} stops after month {number - 1}", _t("{0} stops after month {1} of {2}: nothing more is owed; their sessions end on {3}.",
                                                                    who, number - 1, registration.program_name or "", begins.strftime("%d/%m"))
    db.add(Activity(candidate_id=registration.candidate_id, event_type="MONTH_CONTINUES" if yes else "MONTH_STOPS",
                    details=f"{registration.program_name or 'Program'}: month {number} " + ("taken" if yes else "not taken") + f" ({by})."))
    db.add(Notification(recipient_role="admin", category="MONTH_CHOICE", candidate_id=registration.candidate_id, registration_id=registration.id,
                        program_id=registration.program_id, title=title, message=text[:500], action_url=f"/candidate/{registration.candidate_id}#programs",
                        is_read=True, status="told"))
    db.commit()
    return {"yes": yes, "number": number, "begins": begins, "price": price_now(registration), "program": registration.program_name or ""}


YES_WORDS = r"(oui|yes|ok|okay|d accord|bien sur|je continue|continue|continuer|ey+|eyh|ih|nkammel|nkamel|na3am|نعم|ايه|إيه|اي|أكمل|نكمل)"
NO_WORDS = r"(non|no|nope|je m arrete|j arrete|stop|arreter|la|le|ma nkammelch|ma nkamelch|nwa9ef|لا|لن أكمل|ما نكملش|نوقف)"


def reply_answer(text: str | None) -> bool | None:
    """A short reply that is only yes or no (with a greeting or thanks around it): True, False, or None."""
    import re
    import unicodedata

    plain = unicodedata.normalize("NFKD", text or "")
    plain = "".join(char for char in plain if not unicodedata.combining(char)).lower()
    plain = re.sub(r"[’'`]", " ", plain)
    plain = re.split(r"(?m)^\s*(le|on|el) .{0,120}(a ecrit|wrote)\s*:", plain)[0]       # their mail's quote of our email
    lines = [line for line in plain.splitlines() if line.strip() and not line.lstrip().startswith(">")]
    plain = " ".join(" ".join(lines).split()[:14])
    if "?" in plain or "؟" in plain:
        return None
    plain = re.sub(r"\b(bonjour|bonsoir|salut|slm|salam|aslema|hello|hi|merci|thanks|thank you|cordialement|bien a vous|mr|mme|svp|s il vous plait|"
                   r"شكرا|مرحبا|السلام عليكم)\b", " ", plain)
    plain = " ".join(re.sub(r"[!.,;:]+", " ", plain).split())
    words = plain.split()
    if not words or len(words) > 6:
        return None
    if re.fullmatch(YES_WORDS + r"( " + r"(je continue|pour le mois \d+|mois \d+|for month \d+|please|svp))?", plain):
        return True
    if re.fullmatch(NO_WORDS + r"( " + r"(merci|je m arrete|pour le moment|for now|thanks))?", plain):
        return False
    return None


def owes_now(registration: Registration) -> bool:
    return registration.payment_status != PaymentStatus.PAID and registration.remaining_amount > 0.01


def paused(registration: Registration | None) -> bool:
    """Their access waits for a month's payment (links, questions to the professor, course files)."""
    return bool(registration is not None and registration.paused_at is not None and owes_now(registration))


def plan(registration: Registration, today: date | None = None) -> dict:
    """The months of one registration, for the candidate's file and the Payments page."""
    from app.services.schedule_utils import local_now

    today = today or local_now().date()
    total = months_of(registration)
    paid = months_paid(registration)
    rows = []
    for number in range(1, max(total, program_months(registration.program) if registration.program else total) + 1):
        begins = month_start(registration, number)
        state = ("paid" if number <= paid else "due" if number <= counted(registration)
                 else "coming")
        if number > months_of(registration):
            state = "not taken"                          # they chose to stop before it
        rows.append({"number": number, "starts": begins, "state": state, "now": begins is not None and month_now(registration, today) == number})
    return {"months": rows, "count": total, "paid": paid, "counted": counted(registration), "price": month_price(registration),
            "total": program_total(registration), "paused": paused(registration), "running": month_now(registration, today),
            "ended": stopped(registration, today), "program_months": program_months(registration.program) if registration.program else total,
            "answer": answer_for(registration, counted(registration) + 1)}


# ----------------------------------------------------------------------
# Counting a month, pausing, resuming
# ----------------------------------------------------------------------

def _recompute(registration: Registration) -> None:
    from app.services.payment_service import _payment_status_from_amount

    registration.total_price = registration.total_amount
    registration.payment_status = _payment_status_from_amount(float(registration.paid_amount or 0.0), float(registration.total_amount or 0.0))


def charge(db: Session, registration: Registration, number: int, *, by: str = "the application") -> float:
    """Month `number` is added to what they owe, at its price now. Returns the price."""
    if number <= counted(registration):
        return 0.0
    price = price_now(registration)
    registration.month_price = price
    registration.total_amount = round(float(registration.total_amount or 0.0) + price, 2)
    registration.months_counted = number
    if not registration.months:
        registration.months = months_of(registration)
    _recompute(registration)
    db.add(Activity(candidate_id=registration.candidate_id, event_type="MONTH_COUNTED",
                    details=f"Month {number} of {months_of(registration)} of {registration.program_name or 'the program'}: "
                            f"{money(price)} added ({by}). Status: {registration.payment_status.value}."))
    db.flush()
    return price


def leave_free(db: Session, registration: Registration, number: int, *, by: str = "Admin") -> None:
    """The admin does not charge month `number` (it started before monthly payments, or it is offered)."""
    if number <= counted(registration):
        return
    registration.months_counted = number
    if not registration.months:
        registration.months = months_of(registration)
    db.add(Activity(candidate_id=registration.candidate_id, event_type="MONTH_LEFT_FREE",
                    details=f"Month {number} of {registration.program_name or 'the program'} not charged ({by})."))
    db.flush()


def resume_if_paid(db: Session, registration: Registration) -> bool:
    """After a payment: a paused access starts again (and they are told). Returns whether it resumed."""
    if registration.paused_at is None or owes_now(registration):
        return False
    registration.paused_at = None
    db.add(Activity(candidate_id=registration.candidate_id, event_type="MONTH_RESUMED",
                    details=f"Paid: {registration.program_name or 'the program'} continues."))
    for note in db.query(Notification).filter(Notification.category == "MONTH_UNPAID", Notification.registration_id == registration.id).all():
        note.is_read, note.status, note.read_at = True, "settled", datetime.utcnow()
    db.flush()
    return True


def _since(db: Session) -> datetime:
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    if settings.monthly_since is None:
        settings.monthly_since = datetime.utcnow()
        db.commit()
    return settings.monthly_since


def _followed(db: Session) -> list[Registration]:
    return (db.query(Registration).filter(Registration.class_group_id.isnot(None),
                                          Registration.status.notin_((RegistrationStatus.CANCELLED, RegistrationStatus.COMPLETED))).all())


def _send(db: Session, key: str, registration: Registration) -> bool:
    from app.services import email_templates

    try:
        return email_templates.delivered(email_templates.send(db, key, registration=registration))
    except Exception:
        db.rollback()
        logger.exception("Monthly email %s for registration %s failed.", key, registration.id)
        return False


def apply_everywhere(db: Session) -> dict | None:
    """Once (system_settings.rules_applied_at): every class not started follows the next-month rule (its candidates are
    told), and every month already begun counts (monthly_since far back): a month begun and not paid pauses the access
    until they pay, they are told, the admin sees it on Payments. Returns what was done, or None when already done."""
    from app.services import enrollment_service
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    if settings.rules_applied_at is not None:
        return None
    settings.rules_applied_at = datetime.utcnow()
    settings.class_start_mode = "next_month"
    settings.monthly_since = datetime(2000, 1, 1)
    db.commit()
    moved = enrollment_service.move_to_next_month_rule(db)
    db.add(Activity(event_type="RULES_APPLIED", details=f"Next-month rule and monthly payments applied to what existed; classes moved: {', '.join(moved) or 'none'}."))
    db.commit()
    return {"moved": moved}


def tick(db: Session, today: date | None = None) -> dict:
    """Every few minutes (reminders_tick): the reminders before a month, the months that start, the accesses to pause."""
    from app.services.schedule_utils import local_now
    from app.services.settings_service import get_settings

    today = today or local_now().date()
    settings = get_settings(db)
    remind_days = max(0, min(30, settings.month_remind_days if settings.month_remind_days is not None else 5))
    since = _since(db).date() + timedelta(days=remind_days)     # the first month whose reminder could go on time
    grace = max(0, min(30, settings.month_grace_days if settings.month_grace_days is not None else 3))
    done = {"reminded": 0, "counted": 0, "paused": 0, "resumed": 0}
    for registration in _followed(db):
        total = months_of(registration)
        if total <= 1 or start_of(registration) is None:
            continue
        upcoming = counted(registration) + 1
        if upcoming <= total:
            begins = month_start(registration, upcoming)
            # Near the end of the month, once: "do you continue with the next one?" (unless already paid, or already answered).
            if ((registration.month_reminded or 0) < upcoming and begins >= since and begins - timedelta(days=remind_days) <= today < begins
                    and answer_for(registration, upcoming) is None):
                registration.month_reminded = upcoming
                db.commit()                          # kept first: never sent twice
                ahead = float(registration.paid_amount or 0.0) >= float(registration.total_amount or 0.0) + price_now(registration) - 0.01
                if not ahead:
                    ensure_token(db, registration)
                    db.commit()
                    if _send(db, "month_due", registration):
                        done["reminded"] += 1
            # The month starts: it is owed.
            if today >= begins and begins >= since:
                charge(db, registration, upcoming)
                db.commit()
                done["counted"] += 1
        # Not paid some days into the month: their access waits for it.
        if settings.month_pause_on is not False and registration.paused_at is None and owes_now(registration) and counted(registration) >= 2:
            begins = month_start(registration, counted(registration))
            if begins is not None and begins >= since and today >= begins + timedelta(days=grace):
                registration.paused_at = datetime.utcnow()
                db.add(Notification(recipient_role="admin", category="MONTH_UNPAID", candidate_id=registration.candidate_id,
                                    registration_id=registration.id, program_id=registration.program_id,
                                    title=f"Month {counted(registration)} not paid",
                                    message=_t("{0}: month {1} of {2} ({3}) is not paid; their access is paused until they pay.",
                                               registration.candidate.full_name or registration.candidate.email, counted(registration),
                                               registration.program_name or "", money(registration.remaining_amount)),
                                    action_url=f"/candidate/{registration.candidate_id}#programs"))
                db.add(Activity(candidate_id=registration.candidate_id, event_type="MONTH_PAUSED",
                                details=f"Month {counted(registration)} not paid: access paused."))
                db.commit()
                _send(db, "month_paused", registration)
                done["paused"] += 1
        elif registration.paused_at is not None and not owes_now(registration):
            resume_if_paid(db, registration)
            db.commit()
            done["resumed"] += 1
    db.commit()
    return done


# ----------------------------------------------------------------------
# What the pages show
# ----------------------------------------------------------------------

def before_switch(db: Session) -> list[dict]:
    """Months that started before monthly payments were switched on and were never counted: the admin decides each."""
    from app.services.schedule_utils import local_now

    from app.services.settings_service import get_settings

    today = local_now().date()
    settings = get_settings(db)
    since = _since(db).date() + timedelta(days=max(0, min(30, settings.month_remind_days if settings.month_remind_days is not None else 5)))
    found = []
    for registration in _followed(db):
        upcoming = counted(registration) + 1
        if upcoming > months_of(registration):
            continue
        begins = month_start(registration, upcoming)
        if begins is not None and begins < since and begins <= today:
            found.append({"registration": registration, "candidate": registration.candidate, "number": upcoming,
                          "starts": begins, "price": price_now(registration), "months": months_of(registration)})
    found.sort(key=lambda item: item["starts"])
    return found


def board(db: Session) -> dict:
    """The Payments page's monthly part: months starting soon, months owed, accesses paused, months to decide."""
    from app.services.schedule_utils import local_now
    from app.services.settings_service import get_settings

    today = local_now().date()
    settings = get_settings(db)
    soon_days = max(7, settings.month_remind_days or 5)
    soon, owed, paused_rows = [], [], []
    for registration in _followed(db):
        if months_of(registration) <= 1 or start_of(registration) is None:
            continue
        row = {"registration": registration, "candidate": registration.candidate, "months": months_of(registration),
               "counted": counted(registration), "price": price_now(registration), "remaining": registration.remaining_amount}
        if paused(registration):
            paused_rows.append({**row, "since": registration.paused_at})
        elif owes_now(registration):
            owed.append({**row, "starts": month_start(registration, counted(registration))})
        upcoming = counted(registration) + 1
        if upcoming <= months_of(registration):
            begins = month_start(registration, upcoming)
            if begins is not None and today <= begins <= today + timedelta(days=soon_days):
                soon.append({**row, "number": upcoming, "starts": begins, "reminded": (registration.month_reminded or 0) >= upcoming,
                             "answer": answer_for(registration, upcoming),
                             "ahead": float(registration.paid_amount or 0.0) >= float(registration.total_amount or 0.0) + price_now(registration) - 0.01})
    soon.sort(key=lambda item: item["starts"])
    stopping = []
    for registration in _followed(db):
        end = last_month_day(registration)
        if end is not None and end >= today - timedelta(days=31):
            stopping.append({"registration": registration, "candidate": registration.candidate, "months": registration.months, "ends": end,
                             "of": program_months(registration.program)})
    stopping.sort(key=lambda item: item["ends"])
    rows = follow_rows(db)
    counts = {state: sum(1 for row in rows if row["state"] == state) for state in STATES}
    return {"rows": rows, "counts": counts, "due_total": sum(row["due"] for row in rows if row["state"] in ("paused", "owed")),
            "soon": soon, "owed": owed, "paused": paused_rows, "before": before_switch(db), "stopping": stopping,
            "remind_days": settings.month_remind_days if settings.month_remind_days is not None else 5,
            "grace_days": settings.month_grace_days if settings.month_grace_days is not None else 3,
            "pause_on": settings.month_pause_on is not False}


STATES = {"paused": 0, "owed": 1, "asked": 2, "stopping": 3, "continues": 4, "ahead": 5, "ok": 6, "ended": 7}


def state_of(registration: Registration, today: date | None = None) -> str:
    """paused | owed (in its days of grace) | asked (no answer yet) | stopping | continues | ahead | ok | ended."""
    from app.services.schedule_utils import local_now

    today = today or local_now().date()
    upcoming_month = counted(registration) + 1
    if paused(registration):
        return "paused"
    if owes_now(registration):
        return "owed"
    if stopped(registration, today):
        return "ended"
    said = answer_for(registration, upcoming_month)
    if said == "no":
        return "stopping"
    if said == "yes":
        return "continues"
    if months_paid(registration) > counted(registration):
        return "ahead"
    if upcoming(registration) and (registration.month_reminded or 0) >= upcoming_month:
        return "asked"
    return "ok"


def follow_rows(db: Session) -> list[dict]:
    """Everyone followed month by month (in a class, a program of more than a month), the most pressing first."""
    from app.services.schedule_utils import local_now

    today = local_now().date()
    found = []
    for registration in _followed(db):
        if start_of(registration) is None or registration.program is None:
            continue
        if program_months(registration.program) <= 1 and not owes_now(registration):
            continue
        state = state_of(registration, today)
        next_month = counted(registration) + 1
        found.append({"registration": registration, "candidate": registration.candidate, "plan": plan(registration, today), "state": state,
                      "due": registration.remaining_amount, "next": next_month if next_month <= months_of(registration) else None,
                      "next_starts": month_start(registration, next_month), "class": registration.class_group,
                      "since": month_start(registration, counted(registration))})
    found.sort(key=lambda item: (STATES[item["state"]], -(item["due"] or 0), (item["candidate"].full_name or "").lower()))
    return found


def longer_than_program(class_group) -> int | None:
    """A class that runs into more months than its program says: how many months it runs (None when it fits)."""
    from app.services.schedule_utils import class_end_dt, class_start_dt

    if class_group is None or class_group.program is None:
        return None
    start, end = class_start_dt(class_group), class_end_dt(class_group)
    if not start or not end:
        return None
    runs, day = 1, start.date()
    while add_months(day, runs) <= end.date():
        runs += 1
    return runs if runs > program_months(class_group.program) else None


def values(registration: Registration | None, key: str = "month_due") -> dict:
    """The {variables} of the monthly emails: the month coming (month_due), owed (month_paused) or just paid (month_received)."""
    from app.services.schedule_utils import format_date_fr

    if registration is None:
        return {}
    if key == "month_received":
        number = max(1, min(months_of(registration), months_paid(registration)))
    elif key == "month_paused" or owes_now(registration):
        number = counted(registration)
    else:
        number = min(months_of(registration), counted(registration) + 1)
    begins = month_start(registration, number)
    from sqlalchemy.orm import object_session

    from app.services.settings_service import public_base_url

    session = object_session(registration)
    link = f"{public_base_url(session).rstrip('/')}/month/{registration.month_token}" if session is not None and registration.month_token else ""
    return {"continue_link": link, "previous_month": str(max(1, number - 1)),
            "month_number": str(number), "months_total": str(months_of(registration)),
            "month_start": format_date_fr(begins) if begins else "à confirmer", "month_price": money(price_now(registration)),
            "program_total": money(program_total(registration)), "months_paid": str(months_paid(registration))}

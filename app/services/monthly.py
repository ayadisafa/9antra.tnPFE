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
    for number in range(1, total + 1):
        begins = month_start(registration, number)
        state = ("paid" if number <= paid else "due" if number <= counted(registration)
                 else "coming")
        rows.append({"number": number, "starts": begins, "state": state, "now": begins is not None and month_now(registration, today) == number})
    return {"months": rows, "count": total, "paid": paid, "counted": counted(registration), "price": month_price(registration),
            "total": program_total(registration), "paused": paused(registration), "running": month_now(registration, today)}


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
            # One reminder a few days before the month, unless it is already paid ahead.
            if ((registration.month_reminded or 0) < upcoming and begins >= since and begins - timedelta(days=remind_days) <= today < begins):
                registration.month_reminded = upcoming
                db.commit()                          # kept first: never sent twice
                ahead = float(registration.paid_amount or 0.0) >= float(registration.total_amount or 0.0) + price_now(registration) - 0.01
                if not ahead and _send(db, "month_due", registration):
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
                             "ahead": float(registration.paid_amount or 0.0) >= float(registration.total_amount or 0.0) + price_now(registration) - 0.01})
    soon.sort(key=lambda item: item["starts"])
    return {"soon": soon, "owed": owed, "paused": paused_rows, "before": before_switch(db),
            "remind_days": settings.month_remind_days if settings.month_remind_days is not None else 5,
            "grace_days": settings.month_grace_days if settings.month_grace_days is not None else 3,
            "pause_on": settings.month_pause_on is not False}


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
    return {"month_number": str(number), "months_total": str(months_of(registration)),
            "month_start": format_date_fr(begins) if begins else "à confirmer", "month_price": money(price_now(registration)),
            "program_total": money(program_total(registration)), "months_paid": str(months_paid(registration))}

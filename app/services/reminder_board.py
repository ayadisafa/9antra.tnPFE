"""Every reminder the application sends by itself, on the Reminders page: to candidates (the payment, the countdown
before the first session, the reminder before each session, the session link, the level check, the next month, the
month not paid) and to professors (the week's resources, the unanswered questions), then the ones the admin set up
(from the suggestions or by hand) that go again and again. For each: whether it is on, its timer in any unit (seconds
to months: services/durations.py), how many went lately, where its words and its design are changed, and the original
again.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.services import durations

ORDER = ["payment_reminder", "training_reminder", "session_reminder", "session_link", "level_reminder", "month_due", "month_paused",
         "resources", "course_questions"]
DATE_UNITS = list(durations.DATE_UNITS)


def _sent(db: Session, kind: str, days: int = 30) -> int:
    from app.models.candidate_email import CandidateEmail

    since = datetime.utcnow() - timedelta(days=days)
    return (db.query(CandidateEmail).filter(CandidateEmail.direction == "outbound", CandidateEmail.intent == kind,
                                            CandidateEmail.received_at >= since).count())


def _resources_sent(db: Session, days: int = 30) -> int:
    from app.models.resource_reminder import ResourceReminder

    return db.query(ResourceReminder).filter(ResourceReminder.sent_at >= datetime.utcnow() - timedelta(days=days)).count()


def _row(duration, label: str = "", field: str = "") -> dict:
    return {"value": duration.value, "unit": duration.unit, "label": label, "field": field}


def items(db: Session) -> list[dict]:
    from app.services import email_templates, email_threads, resource_reminders, timings
    from app.services.settings_service import get_settings, parse_reminder_offsets

    settings = get_settings(db)
    mail_ready = email_threads.smtp_configured()

    def automatic(key, *, icon, motion, who, timing, when):
        template = email_templates.get(db, key)
        return {"key": key, "icon": icon, "motion": motion, "who": who, "name": template.spec.name, "what": template.spec.when,
                "on": template.sends_itself, "switchable": template.spec.switchable, "timing": timing, "when": when,
                "edit": f"/inbox/templates?key={key}", "changed": template.customized, "designed": bool(template.design),
                "sent": _sent(db, key), "needs_mail": not mail_ready, "rule": None}

    def timer(key, after):
        duration = timings.get(db, key)
        return {"rows": [_row(duration)], "after": after, "units": durations.NAMES}, duration

    payment, pay_after = timer("payment_reminder", "after registering")
    offsets = parse_reminder_offsets(settings.training_reminder_days or "7,3,1")
    before, before_after = timer("session_reminder", "before each session")
    link, link_before = timer("session_link", "before each session")
    level, level_after = timer("level_reminder", "without an answer")
    remind_days = settings.month_remind_days if settings.month_remind_days is not None else 5
    grace_days = settings.month_grace_days if settings.month_grace_days is not None else 3
    resources = resource_reminders.settings_of(db)
    res_timer, res_before = timer("resources", "before the week")
    ask_after, ask_admin = timings.get(db, "question_remind"), timings.get(db, "question_admin")
    found = [
        automatic("payment_reminder", icon="bi-cash-coin", motion="pulse", who="Candidates", timing=payment,
                  when=f"{pay_after.label()} after registering, once"),
        automatic("training_reminder", icon="bi-hourglass-split", motion="flip", who="Candidates",
                  timing={"rows": [_row(item) for item in offsets] + [{"value": "", "unit": "days", "label": "", "field": ""}],
                          "after": "before the first session", "units": durations.NAMES, "multi": True},
                  when=", ".join(item.label() for item in offsets) + " before the first session"),
        automatic("session_reminder", icon="bi-bell-fill", motion="ring", who="Candidates", timing=before,
                  when=f"{before_after.label()} before each session"),
        automatic("session_link", icon="bi-link-45deg", motion="swing", who="Candidates", timing=link,
                  when=f"{link_before.label()} before each session"),
        automatic("level_reminder", icon="bi-signpost-split", motion="bounce", who="Candidates", timing=level,
                  when=f"Once, {level_after.label()} without an answer"),
        automatic("month_due", icon="bi-calendar2-month", motion="flip", who="Candidates",
                  timing={"rows": [_row(_in_date_units(remind_days))], "after": "before the next month", "units": DATE_UNITS},
                  when=f"{_in_date_units(remind_days).label()} before the next month"),
        automatic("month_paused", icon="bi-pause-circle", motion="pulse", who="Candidates",
                  timing={"rows": [_row(_in_date_units(grace_days))], "after": "after a month starts, not paid", "units": DATE_UNITS},
                  when=f"{_in_date_units(grace_days).label()} after a month starts, not paid"),
        {"key": "resources", "icon": "bi-journal-arrow-up", "motion": "bounce", "who": "Professors", "name": "The week's resources",
         "what": "A week with no resource yet: its professor is reminded to add them, in your words.", "on": resources["on"],
         "switchable": True, "timing": res_timer, "when": f"{res_before.label()} before the week", "edit": "/professors#resources",
         "changed": False, "designed": False, "sent": _resources_sent(db), "needs_mail": not mail_ready, "rule": None},
        {"key": "course_questions", "icon": "bi-patch-question", "motion": "pulse", "who": "Professors", "name": "Candidates' questions",
         "what": "A paid candidate's question about the course goes to their professor; reminded if not answered, then you are told.",
         "on": True, "switchable": False,
         "timing": {"rows": [_row(ask_after, "Their professor, after", "remind"), _row(ask_admin, "You, after", "admin")],
                    "after": "without an answer", "units": durations.NAMES},
         "when": f"After {ask_after.label()}, then you after {ask_admin.label()}",
         "edit": "/inbox/team", "changed": False, "designed": False, "sent": None, "needs_mail": False, "rule": None},
    ]
    return found + rule_items(db)


def _in_date_units(days: int):
    """Days as weeks or months when they divide evenly (a timer kept in days)."""
    if days and days % 30 == 0:
        return durations.Duration(days // 30, "months")
    if days and days % 7 == 0:
        return durations.Duration(days // 7, "weeks")
    return durations.Duration(days, "days")


def _readable(text: str) -> str:
    """A subject as people read it: its variables ({first_name}…) as "…"."""
    import re

    return re.sub(r"[{]\w+[}]", "…", text or "")


def rule_items(db: Session) -> list[dict]:
    """The admin's own reminders that go again and again, with their picture (from their kind, else their trigger)."""
    from app.services import reminder_kinds, reminders

    found = []
    for reminder in reminders.rules(db):
        kind = reminder_kinds.get(reminder.kind)
        trigger_words, trigger_icon = reminder_kinds.TRIGGERS.get(reminder.repeat, reminder_kinds.TRIGGERS["every"])
        if reminder.repeat == "every" or reminder.repeat in reminders.OLD_REPEATS:
            duration = reminders.every_of(reminder)
            timing = {"rows": [_row(duration)], "after": "", "lead": "Every", "units": durations.NAMES}
            when = f"Every {duration.label()}" + (f" · next {reminder.send_at:%d/%m %H:%M}" if reminder.status == "scheduled" else "")
        else:
            duration = reminders.offset_of(reminder)
            timing = {"rows": [_row(duration)], "after": trigger_words, "units": durations.NAMES}
            when = f"{duration.label()} {trigger_words}" if duration.value else trigger_words.replace("after ", "right after ", 1)
        found.append({"key": f"rule-{reminder.id}", "rule": reminder, "icon": (kind or {}).get("icon") or trigger_icon,
                      "motion": (kind or {}).get("motion") or "pulse",
                      "who": "Professors" if reminder.audience in reminders.FOR_PROFESSORS else "Candidates",
                      "name": (kind or {}).get("name") or reminders.REPEATS.get(reminder.repeat) or "Again and again",
                      "what": f"“{_readable(reminder.subject)}” · {reminder.audience_label}",
                      "on": reminder.status != "paused", "switchable": True, "timing": timing, "when": when,
                      "edit": None, "changed": False, "designed": bool(reminder.design), "sent": reminder.sent_count or 0,
                      "needs_mail": False})
    return found


def switch(db: Session, key: str, on: bool) -> str:
    from app.services import email_templates, resource_reminders

    if key.startswith("rule-"):
        from app.models.admin_reminder import AdminReminder
        from app.services import reminders

        reminder = db.get(AdminReminder, key[5:])
        if reminder is None:
            raise ValueError("Unknown reminder.")
        state = reminders.pause(db, reminder, on)
        return f"“{reminder.subject}” is on: it goes by itself." if state != "paused" else f"“{reminder.subject}” is off: it waits."
    if key == "resources":
        current = resource_reminders.settings_of(db)
        resource_reminders.save_settings(db, on=on, days=current["days"], text=current["text"])
        db.commit()
        return "The professors' reminder is on." if on else "The professors' reminder is off: nothing more is sent."
    if key not in email_templates.AUTO_EMAILS or not email_templates.AUTO_EMAILS[key].switchable:
        raise ValueError("This reminder cannot be switched off.")
    email_templates.set_active(db, key, on)
    db.commit()
    name = email_templates.AUTO_EMAILS[key].name
    return f"“{name}” is on: it goes by itself." if on else f"“{name}” is off: the application no longer sends it by itself."


def _durations_of(values, units, *, allow_empty: bool = False) -> list:
    found = []
    for value, unit in zip(values, units):
        if str(value).strip() == "":
            if allow_empty:
                continue
            raise ValueError("Write the time as a whole number.")
        found.append(durations.of(value, unit))
    if not found:
        raise ValueError("Write the time as a whole number.")
    return found


def set_timing(db: Session, key: str, values: list[str], units: list[str], fields: list[str] | None = None) -> str:
    """A reminder's timer, in any unit (the units a reminder counted in dates takes: days, weeks, months)."""
    from app.models.classgroup import ClassGroup, ClassStatus
    from app.services import timings
    from app.services.settings_service import format_reminder_days, get_settings

    if key.startswith("rule-"):
        from app.models.admin_reminder import AdminReminder
        from app.services import reminders

        reminder = db.get(AdminReminder, key[5:])
        if reminder is None:
            raise ValueError("Unknown reminder.")
        duration = _durations_of(values, units)[0]
        reminders.set_offset(db, reminder, duration)
        return f"“{reminder.subject}”: {duration.label()}."
    simple = {"payment_reminder": "after registering", "session_reminder": "before each session", "session_link": "before each session",
              "level_reminder": "without an answer", "resources": "before the week"}
    if key in simple:
        duration = timings.save(db, key, _durations_of(values, units)[0])
        return f"It now goes {duration.label()} {simple[key]}."
    if key == "course_questions":
        given = dict(zip(fields or ["remind", "admin"], _durations_of(values, units)))
        first = given.get("remind") or timings.get(db, "question_remind")
        second = given.get("admin") or timings.get(db, "question_admin")
        if second.seconds <= first.seconds:
            raise ValueError("You are told after their professor is reminded: choose a longer time for you.")
        first, second = timings.save(db, "question_remind", first), timings.save(db, "question_admin", second)
        return f"Their professor is reminded after {first.label()}, you are told after {second.label()}."
    if key in ("month_due", "month_paused"):
        duration = durations.checked(_durations_of(values, units)[0], units=DATE_UNITS, most=durations.Duration(2, "months"),
                                     zero=key == "month_paused")
        settings = get_settings(db)
        if key == "month_due":
            settings.month_remind_days = max(1, duration.days)
        else:
            settings.month_grace_days = duration.days
        db.commit()
        return f"It now goes {duration.label()} " + ("before the next month." if key == "month_due" else "after a month starts, when not paid.")
    if key == "training_reminder":
        # One box may still hold the whole list ("7, 3, 12h"): each piece in the box's unit unless it says its own.
        pieces = [(piece, unit) for value, unit in zip(values, units) for piece in str(value).replace(";", ",").split(",")]
        offsets = [durations.parse(piece, unit) or durations.of(piece, unit) for piece, unit in pieces if piece.strip()]             or _durations_of(values, units)
        for item in offsets:
            durations.checked(item, least=durations.Duration(1, "minutes"), most=durations.Duration(2, "months"))
        settings = get_settings(db)
        old, new = settings.training_reminder_days or "7,3,1", format_reminder_days(offsets)
        settings.training_reminder_days = new
        # The classes not started yet that kept the usual times follow; one that set its own keeps them.
        moved = 0
        for group in db.query(ClassGroup).filter(ClassGroup.status == ClassStatus.NOT_STARTED).all():
            if (group.reminder_days or "7,3,1") == old:
                group.reminder_days = new
                moved += 1
        db.commit()
        return (f"The countdown now goes {durations.label_list(durations.parse_list(new))} before the first session"
                + (f", also in {moved} class(es) not started yet." if moved else "."))
    raise ValueError("This reminder has no timing to change.")


def reset(db: Session, key: str) -> str:
    """The original words and design, and the original timing."""
    from app.services import email_templates, resource_reminders, timings

    if key == "resources":
        resource_reminders.save_settings(db, on=True, days=resource_reminders.DEFAULT_DAYS, text=resource_reminders.DEFAULT_TEXT)
        timings.reset(db, "resources")
        return "The professors' reminder is back as at first."
    if key == "course_questions":
        timings.reset(db, "question_remind")
        timings.reset(db, "question_admin")
        return "Back as at first: their professor after 24 hours, you after 48 hours."
    if key not in email_templates.AUTO_EMAILS:
        raise ValueError("Unknown reminder.")
    email_templates.reset(db, key)
    if key in ("payment_reminder", "session_reminder", "session_link", "level_reminder"):
        timings.reset(db, key)
    elif key == "training_reminder":
        set_timing(db, key, ["7", "3", "1"], ["days", "days", "days"])
    elif key in ("month_due", "month_paused"):
        from app.services.settings_service import get_settings

        settings = get_settings(db)
        if key == "month_due":
            settings.month_remind_days = 5
        else:
            settings.month_grace_days = 3
    db.commit()
    return f"“{email_templates.AUTO_EMAILS[key].name}” is back as at first: its words, its design and its timing."

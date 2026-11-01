"""Every reminder the application sends by itself, on the Reminders page: to candidates (the payment, the countdown
before the first session, the reminder before each session, the session link, the level check) and to professors (the
week's resources, the unanswered questions). For each: whether it is on, when it goes, how many went lately, where its
words and its design are changed, and the original again (its words and its timing).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

ORDER = ["payment_reminder", "training_reminder", "session_reminder", "session_link", "level_reminder", "resources", "course_questions"]


def _sent(db: Session, kind: str, days: int = 30) -> int:
    from app.models.candidate_email import CandidateEmail

    since = datetime.utcnow() - timedelta(days=days)
    return (db.query(CandidateEmail).filter(CandidateEmail.direction == "outbound", CandidateEmail.intent == kind,
                                            CandidateEmail.received_at >= since).count())


def _resources_sent(db: Session, days: int = 30) -> int:
    from app.models.resource_reminder import ResourceReminder

    return db.query(ResourceReminder).filter(ResourceReminder.sent_at >= datetime.utcnow() - timedelta(days=days)).count()


def items(db: Session) -> list[dict]:
    from app.services import email_templates, email_threads, resource_reminders, session_reminders, welcome
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    mail_ready = email_threads.smtp_configured()

    def automatic(key, *, icon, motion, who, timing, when):
        template = email_templates.get(db, key)
        return {"key": key, "icon": icon, "motion": motion, "who": who, "name": template.spec.name, "what": template.spec.when,
                "on": template.sends_itself, "switchable": template.spec.switchable, "timing": timing, "when": when,
                "edit": f"/inbox/templates?key={key}", "changed": template.customized, "designed": bool(template.design),
                "sent": _sent(db, key), "needs_mail": not mail_ready}

    hours = welcome.reminder_hours(db)
    days = settings.training_reminder_days or "7,3,1"
    before = session_reminders.hours_before(db)
    resources = resource_reminders.settings_of(db)
    found = [
        automatic("payment_reminder", icon="bi-cash-coin", motion="pulse", who="Candidates",
                  timing={"kind": "hours", "value": hours, "least": 1, "most": welcome.MOST_HOURS, "after": "hours after registering"},
                  when=f"{hours} h after registering, once"),
        automatic("training_reminder", icon="bi-hourglass-split", motion="flip", who="Candidates",
                  timing={"kind": "days", "value": days.replace(",", ", "), "after": "days before the first session"},
                  when=f"J-{days.replace(',', ', J-')}"),
        automatic("session_reminder", icon="bi-bell-fill", motion="ring", who="Candidates",
                  timing={"kind": "hours", "value": before, "least": session_reminders.LEAST, "most": session_reminders.MOST,
                          "after": "hours before each session"},
                  when=f"{before} h before each session"),
        automatic("session_link", icon="bi-link-45deg", motion="swing", who="Candidates", timing=None,
                  when="Within the hour before each session"),
        automatic("level_reminder", icon="bi-signpost-split", motion="bounce", who="Candidates", timing=None,
                  when="Once, when the level check is not answered"),
        {"key": "resources", "icon": "bi-journal-arrow-up", "motion": "bounce", "who": "Professors", "name": "The week's resources",
         "what": "A week with no resource yet: its professor is reminded to add them, in your words.", "on": resources["on"],
         "switchable": True, "timing": {"kind": "days", "single": True, "value": resources["days"], "least": 1, "most": 14,
                                        "after": "days before the week"},
         "when": f"{resources['days']} days before the week", "edit": "/classes/?tab=professors#resources",
         "changed": False, "designed": False, "sent": _resources_sent(db), "needs_mail": not mail_ready},
        {"key": "course_questions", "icon": "bi-patch-question", "motion": "pulse", "who": "Professors", "name": "Candidates' questions",
         "what": "A paid candidate's question about the course goes to their professor; reminded after a day, you after two.",
         "on": True, "switchable": False, "timing": None, "when": "After one day, then you after two",
         "edit": "/inbox/team", "changed": False, "designed": False, "sent": None, "needs_mail": False},
    ]
    return found


def switch(db: Session, key: str, on: bool) -> str:
    from app.services import email_templates, resource_reminders

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


def set_timing(db: Session, key: str, value: str) -> str:
    from app.models.classgroup import ClassGroup, ClassStatus
    from app.services import resource_reminders, session_reminders, welcome
    from app.services.settings_service import format_reminder_days, get_settings, parse_reminder_days

    if key == "payment_reminder":
        hours = welcome.set_reminder_hours(db, value)
        db.commit()
        return f"The payment reminder now goes {hours} h after registering."
    if key == "session_reminder":
        hours = session_reminders.set_hours(db, value)
        return f"The reminder before each session now goes {hours} h before it."
    if key == "training_reminder":
        days = parse_reminder_days(value)
        if not days:
            raise ValueError("Write the days before the first session, like 7, 3, 1.")
        settings = get_settings(db)
        old, new = settings.training_reminder_days or "7,3,1", format_reminder_days(days)
        settings.training_reminder_days = new
        # The classes not started yet that kept the usual days follow; one that set its own keeps them.
        moved = 0
        for group in db.query(ClassGroup).filter(ClassGroup.status == ClassStatus.NOT_STARTED).all():
            if (group.reminder_days or "7,3,1") == old:
                group.reminder_days = new
                moved += 1
        db.commit()
        return f"The countdown now goes J-{new.replace(',', ', J-')}" + (f", also in {moved} class(es) not started yet." if moved else ".")
    if key == "resources":
        current = resource_reminders.settings_of(db)
        try:
            days = int(value)
        except (TypeError, ValueError):
            raise ValueError("The days before the week must be a number.")
        resource_reminders.save_settings(db, on=current["on"], days=days, text=current["text"])
        db.commit()
        return f"Professors are reminded {resource_reminders.settings_of(db)['days']} days before each week."
    raise ValueError("This reminder has no timing to change.")


def reset(db: Session, key: str) -> str:
    """The original words and design, and the original timing."""
    from app.services import email_templates, resource_reminders, session_reminders, welcome

    if key == "resources":
        resource_reminders.save_settings(db, on=True, days=resource_reminders.DEFAULT_DAYS, text=resource_reminders.DEFAULT_TEXT)
        db.commit()
        return "The professors' reminder is back as at first."
    if key not in email_templates.AUTO_EMAILS:
        raise ValueError("Unknown reminder.")
    email_templates.reset(db, key)
    if key == "payment_reminder":
        welcome.set_reminder_hours(db, welcome.REMINDER_HOURS)
    elif key == "session_reminder":
        session_reminders.set_hours(db, session_reminders.HOURS)
    elif key == "training_reminder":
        set_timing(db, key, "7,3,1")
    db.commit()
    return f"“{email_templates.AUTO_EMAILS[key].name}” is back as at first: its words, its design and its timing."

"""Everything the application does for the admin by itself, in one list: what, when, whether it is on, and where to
change it. Each one has its moving emoji (System page, "Everything it does for you"). Read from the settings and the
switches, so the list always says the truth of today."""
from __future__ import annotations

from sqlalchemy.orm import Session


def _item(key, emoji, motion, title, what, when, on, change, label=None):
    return {"key": key, "emoji": emoji, "motion": motion, "title": title, "what": what, "when": when,
            "state": "on" if on is True else "off" if on is False else "setup", "label": label or ("On" if on is True else "Off" if on is False else "To set up"),
            "change": change}


def listing(db: Session) -> list[dict]:
    from app.models import ClassGroup
    from app.services import (channels, email_assistant, email_templates, email_threads, level_check, mailbox_service, proof_reader,
                              report_email, resource_reminders, sms, welcome, whatsapp)
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    plan = level_check.timing(db)
    days = settings.training_reminder_days or "7,3,1"
    hours = welcome.reminder_hours(db)
    resources = resource_reminders.settings_of(db)
    weekly_auto = db.query(ClassGroup).filter(ClassGroup.weekly_questions_mode == "auto").count()
    from app.services import session_check

    session_plan = session_check.settings_of(db)
    way = channels.choice(db)
    mode = email_assistant.mode_of(settings)
    wa_mode = email_assistant.whatsapp_mode_of(settings)
    found = [
        _item("welcome", "👋", "wave", "Welcome message", "Someone registers: they get your welcome message (Summer Camp, contact, the link).",
              "At once, once per person", email_templates.get(db, "welcome").sends_itself, "/inbox/templates?key=welcome"),
        _item("payment", "💳", "pulse", "One payment reminder", "Still unpaid after registering: one reminder for everything they owe, never repeated by itself.",
              f"{hours} hours after registering", email_templates.get(db, "payment_reminder").sends_itself, "/inbox/templates?key=payment_reminder"),
        _item("proofs", "🧾", "bounce", "Payment proofs read", "A receipt or transfer arrives (email, WhatsApp): amount, bank, RIB, date read and checked; you confirm.",
              "As soon as it arrives", proof_reader.ocr_available() or None, "/payments#proofs"),
        _item("classes", "🏫", "float", "Classes made and started", "Someone pays in full: placed in a class (or a new class is made); classes start and finish on their dates.",
              "At payment, then every day", bool(getattr(settings, "auto_create_class_on_payment", True)), "/settings#classes"),
        _item("level", "🧭", "tilt", "Level check", "Paid candidates get their level check, and one reminder if they do not answer.",
              f"{plan['days']} days before the first session" if plan["when"] == "before" else "As soon as they pay",
              email_templates.is_on(db, "quiz_invitation"), "/quiz/all#timing"),
        _item("countdown", "⏰", "ring", "Countdown before the first session", "J-7, J-3, J-1: livelier reminders with the date, the hour and what to bring.",
              f"{days.replace(',', ', ')} days before", email_templates.get(db, "training_reminder").sends_itself, "/settings#classes"),
        _item("links", "🔗", "pulse", "Session links", "Each candidate gets their own link to the session's meeting.",
              "Within the hour before each session", email_templates.get(db, "session_link").sends_itself, "/inbox/templates?key=session_link"),
        _item("weekly", "📝", "bounce", "Weekly questions", "After each week, the candidates of the classes set to automatic get the week's questions.",
              f"After each week ({weekly_auto} class{'es' if weekly_auto != 1 else ''} automatic)", weekly_auto > 0 or None, "/classes/"),
        _item("session", "🎯", "pulse", "After each session: what was learnt",
              "Each candidate gets a few questions about the session (from what the system knows of the course); their answers show next to their presence.",
              f"{session_plan['hours']} h after each session", session_plan["on"], "/settings#session-check"),
        _item("resources", "📚", "bounce", "Professors' resources", "A week with no resource yet: its professor is reminded to add them (your words).",
              f"{resources['days']} day{'s' if resources['days'] != 1 else ''} before the week", resources["on"], "/classes/?tab=professors#resources"),
        _item("questions", "🙋", "wave", "Candidates' course questions", "A paid candidate's question about the course goes to their professor; reminded after a day, you after two.",
              "When it arrives", True, "/inbox/team"),
        _item("reviews", "⭐", "twinkle", "Reviews at the end", "A class finishes: each candidate is asked their review (program, professor, what to improve), then Facebook.",
              "When the class finishes", email_templates.get(db, "feedback_request").sends_itself, "/inbox/templates?key=feedback_request"),
        _item("answers", "🤖", "float", "Emails answered", "Candidates' emails are read; answered alone when sure (with the source), otherwise prepared for you.",
              "Every minute", (mode != "off") if mailbox_service.configured() else None, "/settings#assistant",
              label={"auto": "Answers alone when sure", "review": "Prepares, you send", "off": "Off"}.get(mode) if mailbox_service.configured() else "To set up"),
        _item("whatsapp", "💬", "pulse", "WhatsApp answered", "Messages on your WhatsApp Business number: answered like emails, in French first.",
              "As they arrive", (wa_mode != "off") if whatsapp.receives() else None, "/settings#whatsapp"),
        _item("sms", "📱", "shake", "SMS answered", "SMS sent to the Android phone: kept in the inbox (SMS) and answered like WhatsApp messages.",
              "As they arrive", True if sms.receives() and sms.configured() else None, "/settings#sms"),
        _item("ways", "🔀", "spin", "The next way when one fails", "A message that cannot go by the way chosen (email, WhatsApp, SMS) tries the next one.",
              "Every message", way["fallback"], "/inbox#ways", label=f"{channels.NAMES[way['way']]}{', then the next' if way['fallback'] else ''}"),
        _item("recommendations", "🚀", "launch", "Program recommendations", "Job offers, Hacker News, GitHub and dev.to read: what to add, change, strengthen, reduce.",
              "Every 24 hours", True, "/programs/market"),
        _item("website", "🌐", "spin", "9antra.tn followed", "The website's programs, events, offers and videos are read and kept up to date.", "Every 6 hours", True, "/website"),
        _item("report", "📊", "bounce", "Progress report by email", "A summary of everything (candidates, payments, classes, what to improve) with its PDFs.",
              f"Every {report_email.every_days(settings)} days", report_email.is_on(settings), "/settings#report"),
        _item("backup", "💾", "pulse", "A copy of the data", "The database and the files are copied and checked; two weeks are kept.", "Every day", True, "/system#safety"),
        _item("friend", "🧕", "wave", "Yasmine says hi", "Each morning: what waits for you today, the facts of the page, and a hand to guide you.", "Once a day", True, "/"),
    ]
    if not email_threads.smtp_configured():
        for item in found:
            if item["key"] in ("welcome", "payment", "level", "countdown", "links", "reviews", "report"):
                item.update(state="setup", label="Email to set up", change="/settings#email")
    return found

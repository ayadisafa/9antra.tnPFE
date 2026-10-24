"""The settings in plain words: what is chosen now, said as sentences.

The Settings page lists them at the top (each with the place to change it), and
its PDF prints them. Every sentence is worked out from the saved values, so it
always says what the application really does.
"""
from __future__ import annotations

import os

from sqlalchemy.orm import Session

from app.services.settings_service import get_settings, parse_reminder_days, public_base_url

_MODES = {"soft": "Day, soft", "colours": "Day, with colours", "night": "Night, black", "night-blue": "Night, blue"}


def _days(numbers: list[int]) -> str:
    if not numbers:
        return ""
    words = [str(number) for number in numbers]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _time(value) -> str:
    """09:00 from a time or from the text the settings keep."""
    if not value:
        return ""
    return value.strftime("%H:%M") if hasattr(value, "strftime") else str(value)[:5]


def plain_words(db: Session, request=None, model_ready: bool | None = None) -> list[dict]:
    """[{"key", "title", "icon", "change", "lines": [{"text", "tone"}]}]: tone is ok, warn or off."""
    from app.services import answer_sync, brand, class_emails, email_assistant, mailbox_service, program_pdf, theme, weekly_questions, whatsapp
    from app.services.enrollment_service import START_MIN_ENROLLMENT
    from app.services.schedule_utils import DAY_LABELS_EN, parse_days

    settings = get_settings(db)
    groups = []

    # ---- Classes ----
    lines = []
    if settings.auto_create_class_on_payment:
        lines.append(("When someone pays and no class of their program is open, a new class is created by itself.", "ok"))
    else:
        lines.append(("When someone pays and no class of their program is open, they wait in “Pending decisions” until you choose.", "warn"))
    if settings.auto_assign_professor:
        lines.append(("Each new class gets a professor of its speciality who is free at those hours, when there is one.", "ok"))
    else:
        lines.append(("New classes are made without a professor: you choose one.", "warn"))
    if settings.class_start_mode == START_MIN_ENROLLMENT:
        lines.append((f"A new class waits until {settings.min_students_to_start} candidates are in it, then starts "
                      f"{settings.min_start_notice_days} days later, on its first session day.", "ok"))
    else:
        lines.append((f"A new class starts {settings.default_class_start_days} days after it is created, on its first session day.", "ok"))
    chosen = parse_days(settings.default_session_days)
    days = ("every " + _days([DAY_LABELS_EN[day] for day in chosen])) if chosen else ""
    hours = _time(settings.default_session_start_time) + (f" to {_time(settings.default_session_end_time)}" if settings.default_session_end_time else "")
    lines.append((f"A new class holds {settings.default_class_capacity} candidates and lasts {settings.default_program_weeks} weeks"
                  + (f", {days}" if days else "") + (f", {hours}" if hours else "")
                  + ", unless its program gives its own hours.", "ok"))
    reminder_days = parse_reminder_days(settings.training_reminder_days)
    if settings.enable_automated_reminders and reminder_days:
        lines.append((f"Candidates get a reminder {_days(reminder_days)} day{'s' if reminder_days != [1] else ''} before their first session.", "ok"))
    else:
        lines.append(("No reminder is sent before a first session.", "warn"))
    from app.services import level_check

    plan = level_check.timing(db)
    if plan["when"] == "paid":
        lines.append(("The level check goes to a candidate as soon as they have paid in full.", "ok"))
    else:
        lines.append((f"The level check goes by itself {plan['days']} day{'s' if plan['days'] != 1 else ''} before the first session "
                      "(change it on the Level checks page).", "ok"))
    groups.append({"key": "classes", "title": "Classes", "icon": "bi-easel2-fill", "change": "/settings#classes", "lines": lines})

    # ---- Emails ----
    lines = []
    sender = os.getenv("SMTP_EMAIL") or ""
    if class_emails.smtp_configured():
        lines.append((f"Emails are sent from {sender}.", "ok"))
    else:
        lines.append(("No email can leave: the sending address and its password are missing.", "off"))
    if mailbox_service.configured():
        lines.append(("Candidates' emails are read every minute, without changing anything in your mailbox.", "ok"))
    else:
        lines.append(("Candidates' emails are not read: the mailbox is not connected.", "off"))
    from app.services import channels, welcome

    hours = welcome.reminder_hours(db)
    lines.append((f"Only one payment reminder goes by itself, {hours} hour{'s' if hours != 1 else ''} after registering "
                  "(change it on its email template). The next ones only when you decide.", "ok"))
    lines.append(("Someone who registers gets the welcome message at once (Inbox, Email templates).", "ok"))
    from app.services import payment_proofs, proof_reader

    if not proof_reader.ocr_available():
        lines.append(("Payment proofs cannot be read: the reading tool is not installed (pip install rapidocr_onnxruntime).", "warn"))
    elif payment_proofs.centre_ribs(db):
        lines.append(("Payment proofs (photos, PDFs) are read and checked against the centre's RIB; you confirm each one.", "ok"))
    else:
        lines.append(("Payment proofs are read and checked; write the centre's RIB (WhatsApp card) so they are checked against it.", "warn"))
    chosen = channels.choice(db)
    lines.append((f"Messages to candidates go: {channels.NAMES[chosen['way']].lower() if chosen['way'] == 'auto' else 'by ' + channels.NAMES[chosen['way']]}"
                  + (", then the next way when one does not work." if chosen["fallback"] else ", and only that way.")
                  + " (Top of the inbox.)", "ok"))
    groups.append({"key": "email", "title": "Emails", "icon": "bi-envelope-at-fill", "change": "/settings#email", "lines": lines})

    # ---- The email assistant ----
    lines = []
    mode, threshold = email_assistant.mode_of(settings), round(email_assistant.threshold_of(settings) * 100)
    if mode == "auto":
        lines.append((f"When a candidate writes, the assistant answers alone when it is at least {threshold}% sure. Below that, it asks you.", "ok"))
        lines.append(("Cancellations, refunds and complaints always come to you, however sure it is.", "ok"))
    elif mode == "review":
        lines.append(("The assistant writes a draft for every email; nothing leaves before you approve it.", "warn"))
    else:
        lines.append(("The assistant is off: you answer every email yourself.", "off"))
    if email_assistant.rewrites_taught(settings):
        lines.append(("Answers you taught are written afresh for each candidate, and kept only if every figure, name and link stays the same.", "ok"))
    else:
        lines.append(("Answers you taught go out in your own words.", "ok"))
    never = [line.strip() for line in (getattr(settings, "assistant_never", None) or "").splitlines() if line.strip()]
    if never:
        lines.append((f"It never answers alone about: {', '.join(never[:6])}{'…' if len(never) > 6 else ''}.", "ok"))
    if (getattr(settings, "assistant_notes", None) or "").strip():
        lines.append(("It follows the notes you wrote about what to know and say.", "ok"))
    ready = email_assistant.model_available() if model_ready is None else model_ready
    lines.append(("The assistant's language model is running on this computer." if ready
                  else "The assistant's language model is not running: emails wait for you and nothing is written by itself.", "ok" if ready else "warn"))
    if getattr(settings, "team_auto_reply", False):
        lines.append(("In the team chat, the assistant answers a professor or a team member alone with an answer you taught.", "ok"))
    else:
        lines.append(("In the team chat, the assistant prepares answers for the professors and the team; you send them.", "ok"))
    groups.append({"key": "assistant", "title": "Email assistant", "icon": "bi-robot", "change": "/settings#assistant", "lines": lines})

    # ---- Candidates' links, WhatsApp, weekly answers ----
    lines = []
    address = public_base_url(db, request)
    if weekly_questions.is_local_address(address):
        lines.append((f"Links in emails start with {address}: that only opens on this computer, so candidates cannot open them yet.", "warn"))
    else:
        lines.append((f"Links in emails open {address}.", "ok"))
    if whatsapp.receives():
        lines.append(("WhatsApp Business is connected: messages go by themselves, and candidates' messages come into the inbox.", "ok"))
    elif whatsapp.configured():
        lines.append(("WhatsApp Business sends by itself; candidates' messages do not come in yet (app secret or Meta's webhook missing).", "warn"))
    else:
        lines.append(("WhatsApp: messages wait in the inbox with a button that opens WhatsApp with the text written; you press send.", "ok"))
    wa_mode = email_assistant.whatsapp_mode_of(settings)
    lines.append(({"auto": "On WhatsApp the assistant answers alone when it is sure, in French first, then in the candidate's language.",
                   "review": "On WhatsApp the assistant writes drafts; nothing leaves before you approve it.",
                   "off": "On WhatsApp the assistant is off: you answer every message."}[wa_mode], "ok" if wa_mode != "off" else "warn"))
    lines.append(("Phone numbers get +216 in front, unless the candidate wrote another country code.", "ok"))
    road = answer_sync.status()
    if road.get("ready"):
        lines.append(("Weekly questions can be answered on a Google Form; the answers come back here by themselves.", "ok"))
    else:
        lines.append(("Weekly questions are answered on this application's own private page (Google Forms is not switched on).", "warn"))
    groups.append({"key": "candidates", "title": "Reaching candidates", "icon": "bi-link-45deg", "change": "/settings#address", "lines": lines})

    # ---- The report by email ----
    from app.services import report_email
    from app.services.schedule_utils import utc_to_local

    if report_email.is_on(settings):
        coming = report_email.next_at(settings)
        to = ", ".join(report_email.recipients(db, settings)) or "nobody yet"
        when = f" The next one: {utc_to_local(coming):%A %d %B, %H:%M}." if coming else " The first one goes a full period after it was switched on."
        report_lines = [(f"A progress report (the analysis and the candidates' progress, as PDFs) goes {report_email.rhythm_label(report_email.every_days(settings))} to {to}.{when}",
                         "ok" if class_emails.smtp_configured() else "warn")]
    else:
        report_lines = [("No progress report is sent by email.", "warn")]
    groups.append({"key": "report", "title": "Report by email", "icon": "bi-envelope-paper-heart-fill", "change": "/settings#report", "lines": report_lines})

    # ---- Look and documents ----
    colour = theme.current_colour(settings)
    named = next((name for name, value in theme.PRESETS if value.lower() == colour.lower()), "your own colour")
    look = [
        (f"The application is called “{brand.view(settings)['name']}”.", "ok"),
        (f"The main colour is {named} ({colour}); the menu is on the {'right' if getattr(settings, 'ui_menu_side', None) == 'right' else 'left'}.", "ok"),
        (f"Pages are shown “{_MODES.get(getattr(settings, 'ui_mode', None) or 'soft', 'Day, soft')}”, with "
         + ("a still background." if getattr(settings, "ui_background", None) == "plain" else "a softly moving background."), "ok"),
        (f"Program PDFs use the “{program_pdf.STYLES[program_pdf.clean_style(getattr(settings, 'program_pdf_style', None))]['name']}” look unless you choose another.", "ok"),
    ]
    groups.append({"key": "look", "title": "Look and documents", "icon": "bi-palette-fill", "change": "/settings#appearance", "lines": look})

    for group in groups:
        group["lines"] = [{"text": text, "tone": tone} for text, tone in group["lines"]]
    return groups

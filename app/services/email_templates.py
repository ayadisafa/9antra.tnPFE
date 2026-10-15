"""The emails the system sends by itself, as templates the admin can change.

Every automatic email (registration confirmed, schedule change, reminder
before the first session, payment messages, quiz invitation) has an original
wording defined here. In "Email templates" the admin can change the text, add
a photo, attach documents or switch the email off. Their version is stored in
`email_templates` under the email's key; restoring the original removes it.

This module also holds what personalized emails share with automatic ones:
the {variables} and the code that turns a template into one candidate's email.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.candidate import Candidate
from app.models.candidate_email import CandidateEmail
from app.models.email_file import EmailFile
from app.models.email_template import EmailTemplate
from app.models.registration import Registration, RegistrationStatus
from app.services import email_files, email_threads
from app.services.email_layout import STYLES, branding, render_email, render_plain, strip_marks, text_to_html
from app.services.email_service import EmailConfigurationError, EmailDeliveryError, send_email
from app.services.schedule_utils import class_start_dt, describe_schedule_fr, format_date_fr, local_now
from app.services.settings_service import get_settings, parse_reminder_days

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# {variables}
# ------------------------------------------------------------------

# (variable, what it shows, sample used when there is no candidate to preview)
VARIABLES = [
    ("first_name", "First name", "Amira"),
    ("candidate_name", "Full name", "Amira Ben Salem"),
    ("program_name", "Program", "DevOps"),
    ("class_name", "Class", "DEVOPS-20261024-1"),
    ("start_date", "First session date", "samedi 24 octobre 2026"),
    ("start_time", "Session time", "18:00"),
    ("schedule", "Weekly schedule", "chaque samedi, 18:00–20:00"),
    ("professor", "Professor", "M. Trabelsi"),
    ("paid_amount", "Amount paid", "390 DT"),
    ("remaining_amount", "Amount left to pay", "0 DT"),
]
SAMPLE_CONTEXT = {name: sample for name, _, sample in VARIABLES}

SAMPLE_ROWS = [
    ("Formation", "DevOps"),
    ("Mode", "En ligne"),
    ("Classe", "DEVOPS-20261024-1"),
    ("Date de démarrage", "samedi 24 octobre 2026"),
    ("Heure de la première séance", "18:00"),
    ("Horaire", "chaque samedi, 18:00–20:00"),
    ("Durée", "12 semaines"),
    ("Formateur", "M. Trabelsi"),
]
SAMPLE_TOPICS = ["Introduction à DevOps", "Git et intégration continue", "Conteneurs avec Docker"]

_VARIABLE = re.compile(r"\{([a-z_]+)\}")
# "Bonjour ," when the candidate has no name on file.
_LONELY_COMMA = re.compile(r"(?m)^([^\W\d_]+)[ \t]+,")
_SPACES = re.compile(r"(?<=\S)[ \t]{2,}(?=\S)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LINK = re.compile(r"^(https?://\S+|\{[a-z_]+\})$", re.IGNORECASE)


def money(value) -> str:
    amount = round(float(value or 0), 2)
    return f"{amount:.0f} DT" if amount == int(amount) else f"{amount:.2f} DT"


def main_registration(candidate: Candidate, registration_id: str | None = None) -> Registration | None:
    """The registration an email is about: the chosen one, else the most relevant."""
    active = [item for item in candidate.registrations if item.status != RegistrationStatus.CANCELLED]
    if registration_id:
        chosen = next((item for item in active if item.id == registration_id), None)
        if chosen:
            return chosen
    active.sort(
        key=lambda item: (item.class_group_id is not None, item.registration_date is not None, item.registration_date),
        reverse=True,
    )
    return active[0] if active else None


def program_name_of(registration: Registration) -> str:
    if registration.program:
        return registration.program.name
    return registration.program_name or "votre formation"


def context_for(candidate: Candidate, registration: Registration | None = None) -> dict:
    """The value of every {variable} for one candidate."""
    full_name = (candidate.full_name or "").strip()
    context = {
        "candidate_name": full_name,
        "first_name": full_name.split(" ")[0] if full_name else "",
        "program_name": "votre formation",
        "class_name": "votre classe",
        "start_date": "à confirmer",
        "start_time": "à confirmer",
        "schedule": "à confirmer",
        "professor": "votre formateur",
        "paid_amount": money(candidate.overall_paid_amount),
        "remaining_amount": money(candidate.overall_remaining_amount),
    }
    if registration is None:
        return context
    context["program_name"] = program_name_of(registration)
    context["paid_amount"] = money(registration.paid_amount)
    context["remaining_amount"] = money(registration.remaining_amount)
    class_group = registration.class_group
    if class_group:
        start = class_start_dt(class_group)
        context["class_name"] = class_group.name
        context["schedule"] = describe_schedule_fr(class_group)
        context["professor"] = class_group.professor_name or "votre formateur"
        if start:
            context["start_date"] = format_date_fr(start)
        if class_group.start_time:
            context["start_time"] = f"{class_group.start_time:%H:%M}"
    return context


def personalize(text: str | None, context: dict) -> str:
    """Replace {variables}; unknown ones are left as written so typos stay visible."""
    text = (text or "").replace("\r\n", "\n")   # text typed in a browser comes with Windows line ends
    return _VARIABLE.sub(lambda match: str(context.get(match.group(1), match.group(0))), text)


def unknown_variables(*texts: str, extra: tuple | list = ()) -> list[str]:
    known = {name for name, _, _ in VARIABLES} | {name for name, _, _ in extra}
    found = {name for text in texts for name in _VARIABLE.findall(text or "")}
    return sorted(found - known)


def _one_line(text: str) -> str:
    value = " ".join(text.split())
    stripped = value.lstrip(" ,")
    # A subject that began with a name the candidate does not have.
    return stripped[:1].upper() + stripped[1:] if stripped != value else value


# ------------------------------------------------------------------
# What every email is built from
# ------------------------------------------------------------------

def build(db: Session, *, subject: str, body: str, context: dict, title: str | None = None,
          closing: str | None = None, style: str = "brand", rows=None, topics=None,
          image: EmailFile | None = None, image_position: str = "top", attachments=(),
          cta_label: str | None = None, cta_url: str | None = None,
          lang: str = "fr", browser: bool = False) -> dict:
    """One candidate's email from a template: subject, plain text, HTML and files.

    `browser=True` makes the photo point at this server, for previews; an
    email that is sent carries the photo inside itself instead.
    """
    brand = branding(get_settings(db), lang)
    final_subject = _one_line(personalize(subject, context))
    # A detail with no value must not leave "Bonjour ," or a double space behind.
    final_body = _SPACES.sub(" ", _LONELY_COMMA.sub(r"\1,", personalize(body, context))).strip()
    final_closing = _SPACES.sub(" ", personalize(closing, context)).strip()
    final_title = _one_line(personalize(title, context)) or final_subject or "9antra"
    label, url = personalize(cta_label, context).strip(), personalize(cta_url, context).strip()
    cta = (label, url) if label and url else None
    image_src = None
    if image is not None:
        image_src = email_files.url_of(image.id) if browser else email_files.cid_src(image)

    # The text version (also what the discussion shows) has no use for the bold marks.
    text = render_plain(
        text=_BOLD.sub(r"\1", strip_marks(final_body)), rows=rows, bullets=topics,
        bullets_title="Au programme :" if topics else None,
        cta=cta, closing=_BOLD.sub(r"\1", final_closing) or None, signature=brand["signature"],
    )
    html_body = render_email(
        title=final_title, body_html=text_to_html(final_body, brand["accent"]),
        rows=rows, bullets=topics, bullets_title="Au programme :" if topics else None,
        closing_html=text_to_html(final_closing, brand["accent"]) if final_closing else "",
        cta=cta, style=style if style in STYLES else "brand", lang=lang,
        preheader=email_threads.snippet(strip_marks(final_body), 110),
        image_src=image_src, image_width=image.width if image is not None else None,
        image_position=image_position or "top", **brand,
    )
    return {
        "subject": final_subject,
        "text": text,
        "html": html_body,
        "inline": [image] if image is not None else [],
        "files": list(attachments or []),
    }


def find_image(db: Session, image_id: str | None) -> EmailFile | None:
    if not image_id:
        return None
    image = db.get(EmailFile, image_id)
    return image if image is not None and image.is_image else None


def check_extras(db: Session, *, image_id: str | None, attachment_ids, cta_label: str | None,
                 cta_url: str | None) -> tuple[EmailFile | None, list[EmailFile]]:
    """Validate the photo, the attached files and the button of an email."""
    image = find_image(db, image_id)
    if image_id and image is None:
        raise ValueError("The photo could not be found. Add it again.")
    wanted = email_files.parse_ids(attachment_ids)
    attachments = email_files.load(db, wanted)
    if len(attachments) != len(wanted):
        raise ValueError("One of the attached files could not be found. Attach it again.")
    email_files.check_total(attachments + ([image] if image else []))
    label, url = (cta_label or "").strip(), (cta_url or "").strip()
    if bool(label) != bool(url):
        raise ValueError("A button needs both a text and a link.")
    if url and not _LINK.match(url):
        raise ValueError("The button link must start with http:// or https://.")
    return image, attachments


# ------------------------------------------------------------------
# The automatic emails
# ------------------------------------------------------------------

@dataclass(frozen=True)
class AutoEmail:
    """One email the system sends by itself, with its original wording."""
    key: str
    name: str
    when: str
    group: str
    subject: str
    title: str
    body: str
    closing: str = ""
    style: str = "brand"
    details: bool = False       # can show the table describing the program and the class
    topics: bool = False        # lists what the program covers under that table
    switchable: bool = True     # sent without anyone clicking, so it can be switched off
    cta_label: str = ""
    cta_url: str = ""
    extra: tuple = ()           # (variable, what it shows, sample) known only to this email


_CLASS_CLOSING = (
    "Si ce créneau ne vous convient pas, répondez à cet email et notre équipe vous proposera une autre classe."
)

AUTO_EMAILS: dict[str, AutoEmail] = {item.key: item for item in [
    AutoEmail(
        key="enrollment", group="Registration and class",
        name="Registration confirmed",
        when="Sent as soon as a candidate has paid in full and has a place in a class.",
        subject="Inscription confirmée — {program_name}",
        title="Votre inscription est confirmée",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Nous vous confirmons la réception de votre paiement pour la formation « {program_name} ».\n\n"
            "Votre place est réservée. Voici les détails de votre programme :"
        ),
        closing="{next_step}",
        details=True, topics=True,
        extra=(("next_step", "What happens next (reminders, or the date to come)",
                "Vous recevrez un rappel J-7, J-3, J-1 avant la première séance."),),
    ),
    AutoEmail(
        key="class_scheduled", group="Registration and class",
        name="Start date confirmed",
        when="Sent to the whole class when it has enough participants and its start date is fixed.",
        subject="La date de démarrage est confirmée — {program_name}",
        title="La date de démarrage est confirmée",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Bonne nouvelle : votre classe est complète et la date de démarrage est maintenant fixée."
        ),
        closing=_CLASS_CLOSING, details=True,
    ),
    AutoEmail(
        key="class_rescheduled", group="Registration and class",
        name="Schedule update",
        when="Sent to the whole class when you change its date, its time or its trainer.",
        subject="Mise à jour de votre planning — {program_name}",
        title="Mise à jour de votre planning",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Le planning de votre classe a été mis à jour. Merci de noter les nouvelles informations ci-dessous."
        ),
        closing=_CLASS_CLOSING, details=True,
    ),
    AutoEmail(
        key="class_moved", group="Registration and class",
        name="Class change",
        when="Sent to a candidate when you move them to another class.",
        subject="Changement de classe — {program_name}",
        title="Changement de classe",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Vous avez été affecté(e) à une nouvelle classe. Voici vos nouvelles informations :"
        ),
        closing=_CLASS_CLOSING, details=True,
    ),
    AutoEmail(
        key="training_reminder", group="Registration and class",
        name="Reminder before the first session",
        when="Sent 7, 3 and 1 day before the first session (each class sets its own days), "
             "and when you click “Remind now”.",
        subject="Rappel J-{days_left} : « {program_name} » démarre {when}",
        title="Votre formation démarre {countdown}",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Votre formation « {program_name} » démarre {countdown}.\n\n"
            "Votre première séance a lieu {when}. Votre présence est attendue."
        ),
        closing="Merci de vous connecter ou de vous présenter 10 minutes avant le début de la séance.",
        details=True,
        extra=(
            ("days_left", "Days left before the first session", "3"),
            ("countdown", "The same, in words", "dans 3 jours"),
            ("when", "Date and time of the first session, in words", "le samedi 24 octobre 2026 à 18:00"),
        ),
    ),
    AutoEmail(
        key="payment_reminder", group="Payment",
        name="Payment reminder",
        when="Sent by itself once, 24 hours after a registration that is still unpaid: one email for "
             "everything the candidate owes. It is never repeated by itself. After three days without "
             "payment or answer, the dashboard suggests sending it again, and you can change the text first.",
        subject="Rappel de paiement — {program_name}",
        title="Finalisez votre inscription",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Nous vous rappelons que votre inscription à la formation « {program_name} » est en attente de paiement.\n\n"
            "Montant restant : **{remaining_amount}**\n\n"
            "Dès réception du paiement, vous serez affecté(e) à une classe et vous recevrez la date "
            "et l'horaire de votre première séance."
        ),
        closing="Si vous avez déjà effectué le règlement, répondez simplement à cet email "
                "afin que notre équipe vérifie votre dossier.",
    ),
    AutoEmail(
        key="payment_partial", group="Payment",
        name="Partial payment recorded",
        when="Sent when you record a payment and a balance is still due.",
        subject="Paiement partiel enregistré — {program_name}",
        title="Votre paiement est enregistré",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Nous avons bien enregistré votre paiement partiel pour la formation « {program_name} ».\n\n"
            "Montant réglé : **{paid_amount}**\n"
            "Montant restant : **{remaining_amount}**\n\n"
            "L'affectation à une classe pourra être finalisée après le règlement complet du montant restant."
        ),
        closing="Si vous avez déjà effectué le règlement, vous pouvez répondre à cet email "
                "afin que notre équipe vérifie votre dossier.",
    ),
    AutoEmail(
        key="payment_received", group="Payment",
        name="Payment received, class to come",
        when="Sent when a candidate has paid in full but could not be placed in a class yet.",
        subject="Confirmation de paiement — {program_name}",
        title="Votre paiement est confirmé",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Nous vous confirmons la réception du paiement complet pour votre formation « {program_name} ».\n\n"
            "Notre équipe finalise votre affectation à une classe. Vous recevrez la date et l'horaire "
            "de votre première séance par email."
        ),
        details=True, topics=True,
    ),
    AutoEmail(
        key="quiz_invitation", group="Quiz",
        name="Level check",
        when="Sent to a candidate who has just paid in full, to know where they start from before their "
             "program begins, and when you send it from the Level checks page. Switching it off only stops "
             "the automatic sending.",
        subject="{first_name}, où en êtes-vous avant de commencer {program_name} ?",
        title="Votre point de départ",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Avant le début de votre formation « {program_name} », nous aimerions savoir où vous en êtes "
            "aujourd'hui.\n\n"
            "Quelques questions, cinq minutes : d'abord vous, puis les sujets du programme, puis quelques "
            "questions du plus simple au plus avancé. Il n'y a pas de note, et « Je ne sais pas encore » "
            "est une vraie réponse."
        ),
        closing="Vos réponses sont lues par votre formateur : elles lui permettent d'adapter le rythme "
                "dès la première séance.",
        cta_label="Commencer", cta_url="{quiz_link}",
        extra=(
            ("quiz_link", "The candidate's private link to their level check", "https://9antra.tn/level"),
        ),
    ),
    AutoEmail(
        key="level_reminder", group="Quiz",
        name="Level check: the reminder",
        when="Sent once, by itself, to a candidate who received their level check two days ago and has not "
             "answered. Never a second time. Switch it off to stop these reminders.",
        subject="{first_name}, cinq minutes pour nous dire où vous en êtes",
        title="Il nous manque vos réponses",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Il y a quelques jours, nous vous avons envoyé un court questionnaire avant le début de votre "
            "formation « {program_name} ». Nous n'avons pas encore vos réponses.\n\n"
            "Il prend cinq minutes, il n'y a pas de note, et « Je ne sais pas encore » est une vraie réponse. "
            "Vos réponses permettent à votre formateur d'adapter le rythme dès la première séance."
        ),
        closing="C'est le seul rappel que vous recevrez à ce sujet.",
        cta_label="Répondre maintenant", cta_url="{quiz_link}",
        extra=(
            ("quiz_link", "The candidate's private link to their level check", "https://9antra.tn/level"),
        ),
    ),
    AutoEmail(
        key="session_link", group="Registration and class",
        name="Your link to the session",
        when="Sent to each candidate one hour before a session of their class, and when you press “Send each "
             "candidate their own link” on the presence sheet. The link is theirs alone: it lets them into the "
             "meeting and notes when they arrived. Switching it off only stops the automatic sending.",
        subject="{first_name}, votre lien pour la séance de {session_date}",
        title="Votre séance, {session_date}",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Votre prochaine séance de « {program_name} » a lieu **{session_date}**, {session_hours}.\n\n"
            "Au programme : {session_topic}.\n\n"
            "Le bouton ci-dessous vous fait entrer dans la réunion. La salle ouvre trente minutes avant le début."
        ),
        closing="Ce lien est personnel : il note votre arrivée. Merci de ne pas le transmettre.",
        cta_label="Entrer dans la séance", cta_url="{join_link}",
        extra=(
            ("join_link", "The candidate's own link to the meeting", "https://9antra.tn/join"),
            ("session_date", "Day of the session", "samedi 17 octobre"),
            ("session_hours", "Hours of the session", "09:00 – 13:00"),
            ("session_topic", "What the session is about", "Conteneurs avec Docker"),
        ),
    ),
    AutoEmail(
        key="next_program_wait", group="Registration and class",
        name="A second program, after the first",
        when="Sent once to a candidate who pays for a program while they follow another class on the same "
             "hours: they start the new program when their class ends, and are asked whether that suits them. "
             "Their answer arrives in the Inbox.",
        subject="{first_name}, votre formation {program_name} après {current_program} ?",
        title="Une formation après l'autre",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Merci pour votre inscription à « {program_name} ». Vous suivez déjà « {current_program} » "
            "{current_end}, et les deux formations ont lieu au même horaire.\n\n"
            "Pour que vous puissiez suivre chacune entièrement, nous vous proposons de commencer "
            "« {program_name} » dès la fin de « {current_program} ».\n\n"
            "Cela vous convient-il ? Répondez simplement à cet email : oui, ou dites-nous ce que vous préférez."
        ),
        closing="Sans réponse de votre part, votre place est réservée pour la prochaine classe après la fin de votre formation en cours.",
        extra=(
            ("current_program", "The program they follow now", "DevOps"),
            ("current_end", "Until when", "jusqu'au 28 novembre"),
        ),
    ),
    AutoEmail(
        key="weekly_questions", group="Quiz",
        name="Weekly questions",
        when="Sent to each candidate of a class with their questions about the week's course: when you "
             "send them from the class's course page, or by itself after the week's session for classes "
             "set to automatic. Switching it off only stops the automatic sending.",
        subject="{first_name}, vos questions de la semaine {week_number} — {program_name}",
        title="Qu'avez-vous retenu cette semaine ?",
        body=(
            "Bonjour {candidate_name},\n\n"
            "La semaine {week_number} de votre formation « {program_name} » portait sur : **{week_topic}**.\n\n"
            "Nous vous avons préparé {questions_count} questions courtes sur ce qui a été vu en cours. "
            "Elles prennent quelques minutes et nous aident à savoir ce qui est acquis et ce qu'il faut revoir ensemble."
        ),
        closing="Ce n'est pas un examen : répondez sans vos notes, le résultat et les explications "
                "s'affichent dès l'envoi de vos réponses.",
        cta_label="Répondre aux questions", cta_url="{questions_link}",
        extra=(
            ("week_number", "Number of the week", "3"),
            ("week_topic", "What the week was about", "Conteneurs avec Docker"),
            ("questions_count", "How many questions the candidate gets", "5"),
            ("questions_link", "The candidate's private link to the questions", "https://9antra.tn/learn"),
        ),
    ),
    AutoEmail(
        key="feedback_request", group="Registration and class",
        name="Your opinion at the end",
        when="Sent to each candidate when their class is completed, or when you ask from the class's page: "
             "how many stars they give the program, and a few words. What they answer is read and shown "
             "on the class's page.",
        subject="{first_name}, votre avis sur la formation {program_name}",
        title="Comment s'est passée votre formation ?",
        body=(
            "Bonjour {candidate_name},\n\n"
            "Votre formation « {program_name} » est terminée : bravo pour le chemin parcouru !\n\n"
            "Votre avis compte beaucoup pour nous. En une minute, dites-nous combien d'étoiles vous donnez "
            "à cette formation et ce que vous en retenez."
        ),
        closing="Vous pouvez aussi laisser votre avis sur notre page Facebook : {facebook_link}",
        style="letter",
        cta_label="Donner mon avis", cta_url="{feedback_link}",
        extra=(
            ("feedback_link", "The candidate's private link to give their opinion", "https://9antra.tn/feedback"),
            ("facebook_link", "Where reviews are left on your Facebook page", "https://www.facebook.com/9antra.tn/reviews"),
        ),
    ),
]}


@dataclass
class Template:
    """An automatic email as it is sent today: the admin's version, or the original."""
    spec: AutoEmail
    subject: str
    title: str
    body: str
    closing: str
    style: str
    show_details: bool = True
    is_active: bool = True
    image_id: str | None = None
    image_position: str = "top"
    attachment_ids: list[str] = field(default_factory=list)
    cta_label: str = ""
    cta_url: str = ""
    customized: bool = False
    updated_at: object = None

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def sends_itself(self) -> bool:
        return self.is_active or not self.spec.switchable


def _from_row(spec: AutoEmail, row: EmailTemplate | None) -> Template:
    if row is None:
        return Template(
            spec=spec, subject=spec.subject, title=spec.title, body=spec.body, closing=spec.closing,
            style=spec.style, cta_label=spec.cta_label, cta_url=spec.cta_url,
        )
    return Template(
        spec=spec, subject=row.subject, title=row.title or "", body=row.body, closing=row.closing or "",
        style=row.style if row.style in STYLES else spec.style,
        show_details=row.show_details is not False, is_active=row.is_active is not False,
        image_id=row.image_id, image_position=row.image_position or "top",
        attachment_ids=email_files.parse_ids(row.attachment_ids),
        cta_label=row.cta_label or "", cta_url=row.cta_url or "",
        customized=True, updated_at=row.updated_at,
    )


def get(db: Session, key: str) -> Template:
    spec = AUTO_EMAILS[key]
    return _from_row(spec, db.query(EmailTemplate).filter(EmailTemplate.key == key).first())


def all_templates(db: Session) -> list[Template]:
    rows = {row.key: row for row in db.query(EmailTemplate).filter(EmailTemplate.key.isnot(None)).all()}
    return [_from_row(spec, rows.get(key)) for key, spec in AUTO_EMAILS.items()]


def is_on(db: Session, key: str) -> bool:
    """Whether the system still sends this email by itself."""
    return get(db, key).sends_itself


def variables_for(key: str) -> list[tuple[str, str, str]]:
    return VARIABLES + list(AUTO_EMAILS[key].extra)


def save(db: Session, key: str, *, subject: str, body: str, title: str = "", closing: str = "",
         style: str = "brand", show_details: bool = True, is_active: bool = True,
         image_id: str | None = None, image_position: str = "top", attachment_ids=None,
         cta_label: str = "", cta_url: str = "") -> EmailTemplate:
    """Keep the admin's version of an automatic email."""
    spec = AUTO_EMAILS[key]
    if not (subject or "").strip() or not (body or "").strip():
        raise ValueError("An email needs a subject and a text.")
    unknown = unknown_variables(subject, title, body, closing, cta_label, cta_url, extra=spec.extra)
    if unknown:
        raise ValueError(
            "Unknown detail: " + ", ".join("{" + name + "}" for name in unknown)
            + ". Use the buttons under the text to insert valid ones."
        )
    image, attachments = check_extras(
        db, image_id=image_id, attachment_ids=attachment_ids, cta_label=cta_label, cta_url=cta_url,
    )

    row = db.query(EmailTemplate).filter(EmailTemplate.key == key).first()
    if row is None:
        row = EmailTemplate(key=key)
        db.add(row)
    row.name = spec.name
    row.subject = " ".join(subject.split())[:200]
    row.title = " ".join((title or "").split())[:120] or None
    row.body = body.strip()
    row.closing = (closing or "").strip() or None
    row.style = style if style in STYLES else spec.style
    row.show_details = bool(show_details)
    row.is_active = bool(is_active) or not spec.switchable
    row.image_id = image.id if image else None
    row.image_position = "bottom" if image_position == "bottom" else "top"
    row.attachment_ids = json.dumps([file.id for file in attachments]) if attachments else None
    row.cta_label = (cta_label or "").strip()[:80] or None
    row.cta_url = (cta_url or "").strip()[:500] or None
    db.flush()
    return row


def reset(db: Session, key: str) -> bool:
    """Go back to the original wording. Returns whether there was anything to remove."""
    rows = db.query(EmailTemplate).filter(EmailTemplate.key == key).all()
    for row in rows:
        db.delete(row)
    db.flush()
    return bool(rows)


# ------------------------------------------------------------------
# Details computed for each candidate
# ------------------------------------------------------------------

_MODE_LABELS_FR = {
    "online": "En ligne",
    "a distance": "En ligne",
    "à distance": "En ligne",
    "onsite": "Présentiel",
    "presentiel": "Présentiel",
    "présentiel": "Présentiel",
    "recording": "Enregistrements",
}


def topics_of(registration: Registration, limit: int = 8) -> list[str]:
    class_group = registration.class_group
    topics = []
    if class_group and class_group.sessions:
        ordered = sorted(class_group.sessions, key=lambda item: (item.session_date, item.week_number or 0))
        topics = [session.topic for session in ordered if session.topic]
    elif registration.program:
        for level in registration.program.levels:
            topics.extend(week.topic_title for week in level.weeks if week.topic_title)
    return topics[:limit]


def detail_rows(db: Session, registration: Registration) -> list[tuple[str, str]]:
    """Label/value pairs describing the program and the class, in French."""
    class_group = registration.class_group
    mode = _MODE_LABELS_FR.get((registration.delivery_mode or "online").strip().lower(), "En ligne")
    rows = [("Formation", program_name_of(registration)), ("Mode", mode)]
    if not class_group:
        rows.append(("Classe", "en cours d'affectation"))
        return rows

    start = class_start_dt(class_group)
    rows.append(("Classe", class_group.name))
    if start:
        rows.append(("Date de démarrage", format_date_fr(start)))
        rows.append((
            "Heure de la première séance",
            f"{class_group.start_time:%H:%M}" if class_group.start_time else "à confirmer",
        ))
    else:
        enrolled = (
            db.query(Registration)
            .filter(
                Registration.class_group_id == class_group.id,
                Registration.status != RegistrationStatus.CANCELLED,
            )
            .count()
        )
        rows.append((
            "Date de démarrage",
            f"dès que le groupe atteint {class_group.min_students_to_start} participants "
            f"({enrolled} inscrit(s) actuellement) — la date et l'heure exactes vous seront envoyées par email",
        ))
    rows.append(("Horaire", describe_schedule_fr(class_group)))
    rows.append(("Durée", f"{class_group.total_weeks or 12} semaines"))
    rows.append(("Formateur", class_group.professor_name or "sera communiqué prochainement"))
    if class_group.room and mode == "Présentiel":
        rows.append(("Salle", class_group.room))
    return rows


def _next_step(class_group) -> str:
    if class_group is None:
        return "Nous vous enverrons tous les détails de votre classe par email."
    start = class_start_dt(class_group)
    offsets = parse_reminder_days(class_group.reminder_days)
    if start and offsets:
        return f"Vous recevrez un rappel {', '.join(f'J-{day}' for day in offsets)} avant la première séance."
    if start:
        return "Nous vous attendons à la première séance."
    return "Nous vous enverrons la date et l'heure exactes dès que le démarrage est confirmé."


def _countdown(class_group) -> dict | None:
    start = class_start_dt(class_group) if class_group else None
    if not start:
        return None
    days_left = max(0, (start.date() - local_now().date()).days)
    at_time = f" à {class_group.start_time:%H:%M}" if class_group.start_time else ""
    if days_left == 0:
        when, countdown = f"aujourd'hui{at_time}", "aujourd'hui"
    elif days_left == 1:
        when, countdown = f"demain{at_time}", "demain"
    else:
        when, countdown = f"le {format_date_fr(start)}{at_time}", f"dans {days_left} jours"
    return {"days_left": str(days_left), "when": when, "countdown": countdown}


def _likely_registration(key: str, candidate: Candidate) -> Registration | None:
    """For a preview, where no registration is given: the one this email would be about."""
    if key in ("payment_reminder", "payment_partial"):
        owing = [
            item for item in candidate.registrations
            if item.status != RegistrationStatus.CANCELLED and item.remaining_amount > 0
        ]
        if key == "payment_partial":
            owing = [item for item in owing if item.paid_amount] or owing
        if owing:
            return owing[0]
    return main_registration(candidate)


def computed_values(key: str, registration: Registration | None) -> dict | None:
    """Values only this email knows. None means the email cannot be written yet
    (a reminder for a class that has no start date)."""
    class_group = registration.class_group if registration else None
    if key == "enrollment":
        return {"next_step": _next_step(class_group)}
    if key == "training_reminder":
        return _countdown(class_group)
    return {}


# ------------------------------------------------------------------
# Rendering and sending
# ------------------------------------------------------------------

def render_auto(db: Session, key: str, *, candidate: Candidate | None = None,
                registration: Registration | None = None, values: dict | None = None,
                template: Template | None = None, overrides: dict | None = None,
                browser: bool = False, preview: bool | None = None) -> dict | None:
    """One automatic email, for a candidate or (without one) with sample details.

    `values` gives the details the caller knows (the quiz link), `overrides`
    replaces the subject or the text for this one email. `preview` (on by
    default with `browser`) fills what a candidate does not have yet with
    samples; without it this returns None when a real email cannot be
    written, see `computed_values`.
    """
    template = template or get(db, key)
    spec = template.spec
    samples = {name: sample for name, _, sample in spec.extra}
    preview = browser if preview is None else preview

    if candidate is not None:
        registration = registration or _likely_registration(key, candidate)
        context = context_for(candidate, registration)
        computed = computed_values(key, registration)
        if computed is None:
            if not preview:
                return None
            computed = {}
        if preview:
            context.update(samples)
        context.update(computed)
        rows = detail_rows(db, registration) if registration else []
        topics = topics_of(registration) if registration else []
    else:
        context = {**SAMPLE_CONTEXT, **samples}
        rows, topics = list(SAMPLE_ROWS), list(SAMPLE_TOPICS)
    context.update(values or {})

    show_details = spec.details and template.show_details
    if show_details and spec.topics:
        paid = registration.paid_amount if registration else 390
        if paid:
            rows.append(("Montant réglé", money(paid)))

    fields = {
        "subject": template.subject, "title": template.title, "body": template.body,
        "cta_label": template.cta_label, "cta_url": template.cta_url,
    }
    fields.update({name: value for name, value in (overrides or {}).items() if name in fields})
    return build(
        db, context=context, closing=template.closing, style=template.style,
        rows=rows if show_details else None,
        topics=topics if show_details and spec.topics else None,
        image=find_image(db, template.image_id), image_position=template.image_position,
        attachments=email_files.load(db, template.attachment_ids),
        browser=browser, **fields,
    )


def send(db: Session, key: str, *, registration: Registration | None = None,
         candidate: Candidate | None = None, values: dict | None = None,
         overrides: dict | None = None, manual: bool = False) -> CandidateEmail | None:
    """Send an automatic email and record it in the candidate's discussions.

    Returns the stored message (its status says whether it was delivered), or
    None when nothing was sent: the email is switched off and nobody asked for
    it (`manual=False`), or it cannot be written yet.
    """
    template = get(db, key)
    if not manual and not template.sends_itself:
        logger.info("The automatic email %r is switched off; nothing was sent.", key)
        return None
    candidate = candidate or (registration.candidate if registration else None)
    if candidate is None:
        return None
    rendered = render_auto(
        db, key, candidate=candidate, registration=registration, values=values,
        template=template, overrides=overrides,
    )
    if rendered is None:
        return None
    message = email_threads.deliver(
        db, candidate, rendered["subject"], rendered["text"], rendered["html"],
        kind=key, author="system", registration=registration,
        files=rendered["files"], inline=rendered["inline"],
    )
    if delivered(message):
        from app.services import whatsapp       # here: whatsapp is not needed to write an email

        whatsapp.after_email(db, candidate, key, rendered["text"])
    return message


def delivered(message: CandidateEmail | None) -> bool:
    return bool(message is not None and message.status == "sent")


def send_test(to_email: str, rendered: dict) -> str | None:
    """Send a rendered email to the admin's own address. Returns the error, if any."""
    parts, unreadable = email_files.as_mime(rendered["files"], rendered["inline"])
    if unreadable:
        return f"The attached file “{unreadable}” could not be read."
    try:
        send_email(
            to_email=to_email, subject=f"[Test] {rendered['subject']}", body=rendered["html"], is_html=True,
            text_body=rendered["text"], journal=False, attachments=parts or None,
        )
    except (EmailConfigurationError, EmailDeliveryError, ValueError) as exc:
        return str(exc)
    return None

"""Personalized emails written by the admin.

The admin writes plain text with {variables}; each recipient gets their own
values, inside the shared 9antra design. Templates can be saved and reused,
and the local model can write a first draft from a short instruction.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.candidate import Candidate
from app.models.email_template import EmailTemplate
from app.services import email_assistant, email_files, email_templates, email_threads
from app.services.email_layout import STYLES
# The {variables} are shared with the automatic emails.
from app.services.email_templates import (  # noqa: F401 (re-exported for the pages)
    SAMPLE_CONTEXT,
    VARIABLES,
    context_for,
    main_registration,
    personalize,
    unknown_variables,
)

logger = logging.getLogger(__name__)

STARTER_TEMPLATES = [
    {
        "name": "Welcome before the first session",
        "subject": "Bienvenue dans {program_name} !",
        "title": "Bienvenue parmi nous",
        "style": "brand",
        "body": (
            "Bonjour {first_name},\n\n"
            "Toute l'équipe est ravie de vous compter parmi les participants de la formation **{program_name}**.\n\n"
            "Votre première séance a lieu le {start_date} à {start_time}, avec {professor}.\n\n"
            "Pour bien démarrer :\n"
            "- préparez votre ordinateur et une bonne connexion ;\n"
            "- connectez-vous 10 minutes avant le début ;\n"
            "- notez vos questions, nous y répondrons pendant la séance.\n\n"
            "À très bientôt !"
        ),
    },
    {
        "name": "Friendly payment follow-up",
        "subject": "Votre inscription à {program_name}",
        "title": "Finalisez votre inscription",
        "style": "letter",
        "body": (
            "Bonjour {first_name},\n\n"
            "Nous avons bien reçu votre inscription à la formation **{program_name}** et nous vous en remercions.\n\n"
            "Il reste {remaining_amount} à régler pour confirmer votre place. Dès réception du paiement, "
            "vous serez affecté(e) à une classe et vous recevrez la date et l'horaire de votre première séance.\n\n"
            "Si vous avez la moindre question, répondez simplement à cet email."
        ),
    },
    {
        "name": "News for a whole class",
        "subject": "Information importante — {class_name}",
        "title": "Une information pour votre classe",
        "style": "announcement",
        "body": (
            "Bonjour {first_name},\n\n"
            "Nous avons une information à partager avec tous les participants de la classe {class_name}.\n\n"
            "[Écrivez votre annonce ici.]\n\n"
            "Pour rappel, vos séances ont lieu {schedule}."
        ),
    },
]

def render(db: Session, *, subject: str, body: str, title: str | None = None, style: str = "brand",
           candidate: Candidate | None = None, registration_id: str | None = None,
           cta_label: str | None = None, cta_url: str | None = None,
           image_id: str | None = None, image_position: str = "top", attachment_ids=None,
           browser: bool = False) -> dict:
    """One recipient's email: subject, plain text, HTML and the files that go with it.

    `browser=True` is for previews: the photo is then read from this server.
    """
    if candidate is not None:
        context = context_for(candidate, main_registration(candidate, registration_id))
    else:
        context = dict(SAMPLE_CONTEXT)
    return email_templates.build(
        db, subject=subject, title=title, body=body, style=style, context=context,
        cta_label=cta_label, cta_url=cta_url,
        image=email_templates.find_image(db, image_id), image_position=image_position,
        attachments=email_files.load(db, attachment_ids), browser=browser,
    )


def send_one(db: Session, candidate: Candidate, **fields) -> bool:
    """Send a personalized email to one candidate and record it as a discussion."""
    rendered = render(db, candidate=candidate, **fields)
    message = email_threads.deliver(
        db, candidate, rendered["subject"], rendered["text"], rendered["html"],
        kind="personal", author="admin",
        registration=main_registration(candidate, fields.get("registration_id")),
        files=rendered["files"], inline=rendered["inline"],
    )
    return bool(message and message.status == "sent")


def send_many_task(candidate_ids: list[str], fields: dict) -> None:
    """Background entry point: one email per candidate, each with their own values."""
    db = SessionLocal()
    try:
        for candidate_id in candidate_ids:
            candidate = db.get(Candidate, candidate_id)
            if candidate is None or not candidate.email:
                continue
            try:
                send_one(db, candidate, **fields)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Personalized email to candidate %s failed.", candidate_id)
    finally:
        db.close()


# ------------------------------------------------------------------
# Drafting with the local model
# ------------------------------------------------------------------

_DRAFT_SYSTEM = (
    "You write emails for 9antra, a training centre in Tunisia, to its candidates. "
    "Reply with one JSON object and nothing else."
)
TONES = {
    "warm": "warm and encouraging",
    "formal": "formal and respectful",
    "short": "very short and direct",
    "motivating": "energetic and motivating",
}


def draft_with_ai(instruction: str, *, tone: str = "warm", language: str = "fr") -> dict | None:
    """A first draft from a short instruction, using {variables} for personal details."""
    language_name = "English" if language == "en" else "French"
    data = email_assistant.llm_json(_DRAFT_SYSTEM, f"""Write an email in {language_name}. Tone: {TONES.get(tone, TONES["warm"])}.

What the email must say:
\"\"\"{instruction[:1500]}\"\"\"

Rules:
- Start with a greeting that uses the placeholder {{first_name}}, written exactly like that.
- You may also use {{program_name}} and {{class_name}} where the program or the class is named. Use no other placeholder.
- Keep every day, date, time, place and amount exactly as written in the instruction.
- Do not invent facts, dates, prices or links that are not in the instruction.
- Do not write a signature or a sender name: they are added automatically.
- Keep it under 120 words. Separate paragraphs with an empty line.

Return a JSON object with exactly these keys:
"subject": a short subject line
"title": a headline of at most 6 words
"body": the email text""", max_tokens=500)
    if not data or not isinstance(data.get("body"), str) or not data["body"].strip():
        return None
    body = data["body"].replace("\\n", "\n").strip()
    lines = body.split("\n")
    while lines and (not lines[-1].strip() or email_assistant._SIGNOFF_LINE.match(lines[-1])):
        lines.pop()
    return {
        "subject": " ".join(str(data.get("subject") or "").split())[:160],
        "title": " ".join(str(data.get("title") or "").split())[:80],
        "body": "\n".join(lines).strip(),
    }


# ------------------------------------------------------------------
# Saved templates
# ------------------------------------------------------------------

def own_templates(db: Session) -> list[EmailTemplate]:
    """The templates the admin wrote (not their versions of the automatic emails)."""
    return db.query(EmailTemplate).filter(EmailTemplate.key.is_(None)).order_by(EmailTemplate.name).all()


def template_extras(db: Session, template: EmailTemplate) -> dict:
    """The photo, files and button saved with a template, as the pages show them."""
    image = email_templates.find_image(db, template.image_id)
    return {
        "image": email_files.describe(image, inline=True) if image else None,
        "image_position": template.image_position or "top",
        "files": [email_files.describe(file) for file in email_files.load(db, template.attachment_ids)],
        "cta_label": template.cta_label or "",
        "cta_url": template.cta_url or "",
    }


def seed_templates(db: Session) -> int:
    """Add the starter templates the first time, so the page is never empty."""
    if db.query(EmailTemplate.id).filter(EmailTemplate.key.is_(None)).first():
        return 0
    for template in STARTER_TEMPLATES:
        db.add(EmailTemplate(**template))
    db.commit()
    return len(STARTER_TEMPLATES)


def save_template(db: Session, *, name: str, subject: str, body: str, title: str | None = None,
                  style: str = "brand", template_id: str | None = None,
                  image_id: str | None = None, image_position: str = "top", attachment_ids=None,
                  cta_label: str | None = None, cta_url: str | None = None) -> EmailTemplate:
    name = " ".join((name or "").split())
    if not name or not (subject or "").strip() or not (body or "").strip():
        raise ValueError("A template needs a name, a subject and a message.")
    image, attachments = email_templates.check_extras(
        db, image_id=image_id, attachment_ids=attachment_ids, cta_label=cta_label, cta_url=cta_url,
    )
    own = db.query(EmailTemplate).filter(EmailTemplate.key.is_(None))
    template = own.filter(EmailTemplate.id == template_id).first() if template_id else None
    if template is None:
        template = own.filter(EmailTemplate.name == name).first()
    if template is None:
        template = EmailTemplate()
        db.add(template)
    template.name = name[:120]
    template.subject = " ".join(subject.split())[:200]
    template.title = " ".join((title or "").split())[:120] or None
    template.body = body.strip()
    template.style = style if style in STYLES else "brand"
    template.image_id = image.id if image else None
    template.image_position = "bottom" if image_position == "bottom" else "top"
    template.attachment_ids = json.dumps([file.id for file in attachments]) if attachments else None
    template.cta_label = (cta_label or "").strip()[:80] or None
    template.cta_url = (cta_url or "").strip()[:500] or None
    db.flush()
    return template

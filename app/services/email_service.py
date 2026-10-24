from __future__ import annotations

import logging
import os
import smtplib
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Final

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SMTP_HOST: Final[str] = os.getenv(
    "SMTP_HOST",
    "smtp.gmail.com",
)

SMTP_PORT: Final[int] = int(
    os.getenv("SMTP_PORT", "465")
)

SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

SMTP_TIMEOUT: Final[int] = int(
    os.getenv("SMTP_TIMEOUT", "30")
)


class EmailConfigurationError(RuntimeError):
    """Raised when SMTP configuration is missing or invalid."""


class EmailDeliveryError(RuntimeError):
    """Raised when email delivery fails."""


def _get_smtp_credentials() -> tuple[str, str]:
    email = (SMTP_EMAIL or "").strip()
    password = SMTP_PASSWORD or ""

    if not email:
        raise EmailConfigurationError(
            "SMTP_EMAIL is not configured."
        )

    if not password:
        raise EmailConfigurationError(
            "SMTP_PASSWORD is not configured."
        )

    return email, password


def _validate_recipient(to_email: str) -> str:
    recipient = (to_email or "").strip()

    if not recipient or "@" not in recipient:
        raise ValueError(
            "A valid recipient email address is required."
        )

    return recipient


SENDER_NAME: Final[str] = os.getenv("SMTP_SENDER_NAME", "9antra")

# Called after a successful send that was not already recorded in a discussion,
# so emails sent by older code paths (payment, quiz...) are kept too.
_journal = None


def set_journal(callback) -> None:
    global _journal
    _journal = callback


def new_message_id() -> str:
    """A Message-ID for an outgoing email, so replies can be matched to it."""
    domain = (SMTP_EMAIL or "").partition("@")[2].strip() or "9antra.tn"
    return make_msgid(domain=domain)


def _file_part(item: dict) -> MIMEBase:
    """One photo or document. With a `cid` it is shown inside the email body."""
    maintype, _, subtype = (item.get("content_type") or "application/octet-stream").partition("/")
    part = MIMEBase(maintype or "application", subtype or "octet-stream")
    part.set_payload(item["data"])
    encoders.encode_base64(part)
    name = item.get("filename") or "file"
    if item.get("cid"):
        part.add_header("Content-ID", f"<{item['cid']}>")
        part.add_header("Content-Disposition", "inline", filename=name)
    else:
        part.add_header("Content-Disposition", "attachment", filename=name)
    return part


def _build_message(
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    is_html: bool,
    text_body: str | None = None,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: list[dict] | None = None,
) -> MIMEMultipart:
    clean_subject = " ".join(str(subject or "").split())
    clean_body = str(body or "")

    if not clean_subject:
        raise ValueError("Email subject is required.")

    if not clean_body.strip():
        raise ValueError("Email body is required.")

    content = MIMEMultipart("alternative")

    if is_html:
        content.attach(
            MIMEText(
                text_body
                or (
                    "Veuillez consulter cet email dans un client "
                    "compatible HTML."
                ),
                "plain",
                "utf-8",
            )
        )
        content.attach(
            MIMEText(
                clean_body,
                "html",
                "utf-8",
            )
        )
    else:
        content.attach(
            MIMEText(
                clean_body,
                "plain",
                "utf-8",
            )
        )

    # Photos shown in the body travel with it; documents are attached around it.
    embedded = [item for item in attachments or [] if item.get("cid") and is_html]
    attached = [item for item in attachments or [] if not (item.get("cid") and is_html)]
    message = content
    if embedded:
        message = MIMEMultipart("related")
        message.attach(content)
        for item in embedded:
            message.attach(_file_part(item))
    if attached:
        body_part = message
        message = MIMEMultipart("mixed")
        message.attach(body_part)
        for item in attached:
            message.attach(_file_part({**item, "cid": None}))

    message["Subject"] = clean_subject
    message["From"] = formataddr((SENDER_NAME, sender))
    message["To"] = recipient
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = message_id or new_message_id()
    # Threading headers keep a reply inside the candidate's conversation.
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = references or in_reply_to

    return message


def send_email(
    to_email: str,
    subject: str,
    body: str,
    is_html: bool = False,
    text_body: str | None = None,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    journal: bool = True,
    attachments: list[dict] | None = None,
) -> bool:
    """
    Send an email using Gmail SMTP.

    SMTP_PORT=465 uses SMTP_SSL.
    SMTP_PORT=587 uses STARTTLS.

    `text_body` is the plain-text version shown by clients that do not render
    HTML. `message_id`, `in_reply_to` and `references` thread the email.
    `journal=False` is for callers that record the email themselves.
    `attachments` are dicts with `filename`, `content_type`, `data` (bytes)
    and, for a photo shown inside the HTML body as `cid:<cid>`, `cid`.
    """
    from app.database import let_others_write

    let_others_write()                  # the mail server may take seconds: nobody waits for the database meanwhile
    recipient = _validate_recipient(to_email)
    sender, password = _get_smtp_credentials()

    message = _build_message(
        sender=sender,
        recipient=recipient,
        subject=subject,
        body=body,
        is_html=is_html,
        text_body=text_body,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        attachments=attachments,
    )

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(
                host=SMTP_HOST,
                port=SMTP_PORT,
                timeout=SMTP_TIMEOUT,
            ) as server:
                server.login(sender, password)
                server.sendmail(
                    sender,
                    [recipient],
                    message.as_string(),
                )

        elif SMTP_PORT == 587:
            with smtplib.SMTP(
                host=SMTP_HOST,
                port=SMTP_PORT,
                timeout=SMTP_TIMEOUT,
            ) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(sender, password)
                server.sendmail(
                    sender,
                    [recipient],
                    message.as_string(),
                )

        else:
            raise EmailConfigurationError(
                "SMTP_PORT must be 465 or 587."
            )

        logger.info(
            "Email sent successfully to %s with subject %r.",
            recipient,
            subject,
        )

        if journal and _journal is not None:
            try:
                _journal(
                    recipient=recipient,
                    subject=str(message["Subject"]),
                    body=body,
                    is_html=is_html,
                    text_body=text_body,
                    message_id=str(message["Message-ID"]),
                )
            except Exception:
                logger.exception(
                    "Could not record the email to %s in its discussion.",
                    recipient,
                )

        return True

    except smtplib.SMTPAuthenticationError as exc:
        logger.error(
            "SMTP authentication failed. Check SMTP_EMAIL and "
            "SMTP_PASSWORD."
        )
        raise EmailDeliveryError(
            "Email authentication failed."
        ) from exc

    except smtplib.SMTPException as exc:
        logger.exception(
            "SMTP error while sending email to %s.",
            recipient,
        )
        raise EmailDeliveryError(
            "The email could not be delivered."
        ) from exc

    except OSError as exc:
        logger.exception(
            "Network error while sending email to %s.",
            recipient,
        )
        raise EmailDeliveryError(
            "The email server could not be reached."
        ) from exc


def send_email_safely(
    to_email: str,
    subject: str,
    body: str,
    is_html: bool = False,
    journal: bool = True,
) -> bool:
    """
    Send an email without raising delivery/configuration exceptions.
    """
    try:
        return send_email(
            to_email=to_email,
            subject=subject,
            body=body,
            is_html=is_html,
            journal=journal,
        )

    except (
        EmailConfigurationError,
        EmailDeliveryError,
        ValueError,
    ):
        logger.exception(
            "Safe email delivery failed for %s.",
            to_email,
        )
        return False


def safe_send_email_with_retry(
    recipient: str,
    subject: str,
    body: str,
    is_html: bool = False,
    retries: int = 3,
    retry_delay: int = 2,
) -> bool:
    """
    Compatibility wrapper used by the reminder and candidate-email services.

    Returns False after all attempts fail instead of raising an exception.
    """
    if retries < 1:
        retries = 1

    for attempt in range(1, retries + 1):
        try:
            return send_email(
                to_email=recipient,
                subject=subject,
                body=body,
                is_html=is_html,
            )

        except (
            EmailConfigurationError,
            EmailDeliveryError,
            ValueError,
        ) as exc:
            logger.warning(
                "Email attempt %s/%s failed for %s: %s",
                attempt,
                retries,
                recipient,
                exc,
            )

            if attempt < retries:
                time.sleep(retry_delay)

    logger.error(
        "Email delivery failed after %s attempts for %s.",
        retries,
        recipient,
    )

    return False
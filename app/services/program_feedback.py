"""What candidates think at the end of their program.

When a class is completed, each of its candidates is asked once: how many
stars, and a few words. They answer on a private page (the link is in the
email, and on WhatsApp when a business number is connected); a reply to that
email counts too. Their words go through the same rules as the published
feedback (see feedback.analyse), so satisfaction is read, not guessed.

An opinion is a row of `candidate_feedback`: asked ("to_ask", then "asked"),
then answered ("received").
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.models.candidate import Candidate
from app.models.classgroup import ClassGroup
from app.models.feedback import CandidateFeedback
from app.models.notification import Notification
from app.models.registration import Registration, RegistrationStatus
from app.services import email_templates, feedback, settings_service

logger = logging.getLogger(__name__)

SOURCE = "opinion"                  # asked by the application at the end of a program
WORDS_LIMIT = 2000
_STARS = re.compile(r"\b([1-5])\s*(?:/\s*5|sur\s*5|out of\s*5|étoiles?|etoiles?|stars?|نجوم|نجمة)|([★⭐]{1,5})", re.IGNORECASE)


def _payload(row: CandidateFeedback) -> dict:
    try:
        data = json.loads(row.raw_payload or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _keep(row: CandidateFeedback, **values) -> None:
    row.raw_payload = json.dumps({**_payload(row), **values}, ensure_ascii=False)


def facebook_reviews() -> str:
    from app.services import website_sync

    return f"https://www.facebook.com/{website_sync.FACEBOOK_PAGE}/reviews"


def of_registration(db: Session, registration_id: str) -> CandidateFeedback | None:
    return (
        db.query(CandidateFeedback)
        .filter(CandidateFeedback.source == SOURCE, CandidateFeedback.registration_id == registration_id)
        .first()
    )


def by_token(db: Session, token: str) -> CandidateFeedback | None:
    if not token or len(token) < 16:
        return None
    return (
        db.query(CandidateFeedback)
        .options(joinedload(CandidateFeedback.candidate), joinedload(CandidateFeedback.registration))
        .filter(CandidateFeedback.source == SOURCE, CandidateFeedback.external_id == token)
        .first()
    )


def _row_for(db: Session, registration: Registration, class_group: ClassGroup | None) -> CandidateFeedback:
    row = of_registration(db, registration.id)
    if row is None:
        candidate = registration.candidate
        row = CandidateFeedback(
            source=SOURCE, external_id=secrets.token_urlsafe(24), status="to_ask",
            candidate_id=registration.candidate_id, registration_id=registration.id,
            author_name=(candidate.full_name or candidate.email) if candidate else None,
            author_email=candidate.email if candidate else None,
            program_name=email_templates.program_name_of(registration),
        )
        db.add(row)
        _keep(row, class_id=class_group.id if class_group else registration.class_group_id, asks=0)
        db.flush()
    return row


def queue_class(db: Session, class_group: ClassGroup, registrations: list[Registration]) -> int:
    """A class has just finished: its candidates will be asked. Returns how many are waiting."""
    waiting = 0
    for registration in registrations:
        if registration.status == RegistrationStatus.CANCELLED or registration.candidate is None:
            continue
        if _row_for(db, registration, class_group).status == "to_ask":
            waiting += 1
    return waiting


def ask(db: Session, registration: Registration, *, manual: bool = False) -> CandidateFeedback | None:
    """Ask one candidate what they thought. Returns the row when the email went out."""
    row = _row_for(db, registration, registration.class_group)
    if row.status == "received":
        return None                 # they already answered
    link = f"{settings_service.public_base_url(db)}/opinion/{row.external_id}"
    message = email_templates.send(
        db, "feedback_request", registration=registration,
        values={"feedback_link": link, "facebook_link": facebook_reviews()}, manual=manual,
    )
    if not email_templates.delivered(message):
        if message is None and not manual:
            row.status = "off"       # the email is switched off: nothing waits
        return None
    row.status = "asked"
    _keep(row, asked_at=datetime.now(timezone.utc).isoformat(), asks=(_payload(row).get("asks") or 0) + 1)
    db.flush()
    return row


def send_due(db: Session, limit: int = 20) -> int:
    """Send the requests that are waiting (classes that just finished). Returns how many went out."""
    rows = (
        db.query(CandidateFeedback)
        .filter(CandidateFeedback.source == SOURCE, CandidateFeedback.status == "to_ask")
        .limit(limit)
        .all()
    )
    sent = 0
    for row in rows:
        registration = db.get(Registration, row.registration_id) if row.registration_id else None
        if registration is None or registration.candidate is None or not registration.candidate.email:
            row.status = "off"
            continue
        if ask(db, registration) is not None:
            sent += 1
        db.commit()
    db.commit()
    return sent


def ask_class(db: Session, class_group: ClassGroup) -> dict:
    """The admin asks a whole class now. {"sent", "answered", "failed"}."""
    outcome = {"sent": 0, "answered": 0, "failed": 0}
    registrations = (
        db.query(Registration)
        .options(joinedload(Registration.candidate), joinedload(Registration.program))
        .filter(Registration.class_group_id == class_group.id, Registration.status != RegistrationStatus.CANCELLED)
        .all()
    )
    for registration in registrations:
        existing = of_registration(db, registration.id)
        if existing is not None and existing.status == "received":
            outcome["answered"] += 1
        elif registration.candidate is None or not registration.candidate.email:
            outcome["failed"] += 1
        elif ask(db, registration, manual=True) is not None:
            outcome["sent"] += 1
        else:
            outcome["failed"] += 1
        db.commit()
    return outcome


def stars_in(text: str) -> int | None:
    """The stars someone gave in their own words ("5 étoiles", "4/5", "★★★★")."""
    found = _STARS.search(text or "")
    if not found:
        return None
    return int(found.group(1)) if found.group(1) else len(found.group(2))


def answer(db: Session, row: CandidateFeedback, *, stars: int | None, words: str = "",
           may_publish: bool = False, channel: str = "page") -> CandidateFeedback:
    """Record what a candidate answered, and read it."""
    words = (words or "").strip()[:WORDS_LIMIT]
    stars = stars if stars in (1, 2, 3, 4, 5) else None
    if stars is None and not words:
        raise ValueError("Choisissez un nombre d'étoiles ou écrivez quelques mots.")
    reading = feedback.analyse(words, stars=stars)
    first = row.status != "received"
    row.rating, row.comment = stars, words or None
    row.sentiment, row.language = reading["sentiment"], reading["language"]
    row.sentiment_score = {"positive": 100, "mixed": 50, "neutral": 50, "negative": 0}.get(reading["sentiment"])
    row.status = "received"
    row.submitted_at = datetime.now(timezone.utc)
    _keep(row, themes=reading["themes"], positive=reading["positive"], negative=reading["negative"],
          may_publish=bool(may_publish), channel=channel)
    if first:
        happy = reading["sentiment"] == "positive"
        db.add(Notification(
            recipient_role="admin", category="FEEDBACK", candidate_id=row.candidate_id,
            title=f"{row.author_name or 'A candidate'} gave {stars} star{'s' if stars != 1 else ''}" if stars else f"{row.author_name or 'A candidate'} gave their opinion",
            message=(words[:200] or row.program_name or "") if happy else ("Not fully happy: " + (words[:180] or "no words, only the stars")),
            action_url=f"/classes/{_payload(row).get('class_id')}#opinions" if _payload(row).get("class_id") else "/website#feedback",
        ))
    db.flush()
    return row


def from_reply(db: Session, registration_id: str | None, body: str) -> CandidateFeedback | None:
    """A candidate answered the request by email: their reply is their opinion."""
    row = of_registration(db, registration_id) if registration_id else None
    if row is None or row.status == "received" or not (body or "").strip():
        return None
    return answer(db, row, stars=stars_in(body), words=body, channel="email")


# ------------------------------------------------------------------
# What the pages show
# ------------------------------------------------------------------

def _view(row: CandidateFeedback) -> dict:
    data = _payload(row)
    return {
        "row": row, "name": row.author_name or "Candidate", "stars": row.rating, "words": row.comment or "",
        "view": feedback.view(row.sentiment if row.status == "received" else None),
        "themes": [feedback.THEMES[key] for key in data.get("themes") or [] if key in feedback.THEMES],
        "answered": row.status == "received", "asked": row.status in ("asked", "received"),
        "waiting": row.status == "to_ask", "may_publish": bool(data.get("may_publish")),
        "channel": data.get("channel"), "program": row.program_name, "when": row.submitted_at,
    }


def summary(rows: list[CandidateFeedback]) -> dict:
    views = [_view(row) for row in rows]
    answered = [item for item in views if item["answered"]]
    rated = [item["stars"] for item in answered if item["stars"]]
    counts = {key: sum(1 for item in answered if item["row"].sentiment == key) for key in ("positive", "mixed", "negative")}
    judged = sum(counts.values())
    themes: dict[str, int] = {}
    for item in answered:
        for theme in item["themes"]:
            themes[theme["label"]] = themes.get(theme["label"], 0) + 1
    return {
        "rows": sorted(views, key=lambda item: (not item["answered"], -(item["stars"] or 0), item["name"])),
        "asked": sum(1 for item in views if item["asked"]), "answered": len(answered),
        "waiting": sum(1 for item in views if item["waiting"]),
        "stars": round(sum(rated) / len(rated), 1) if rated else None,
        "counts": counts, "happy_share": round(100 * counts["positive"] / judged) if judged else None,
        "themes": sorted(themes.items(), key=lambda pair: pair[1], reverse=True)[:5],
        "to_answer": [item for item in answered if item["row"].sentiment in ("negative", "mixed")],
    }


def of_class(db: Session, class_id: str) -> dict:
    """The opinions of one class's candidates."""
    rows = (
        db.query(CandidateFeedback)
        .join(Registration, Registration.id == CandidateFeedback.registration_id)
        .filter(CandidateFeedback.source == SOURCE, Registration.class_group_id == class_id)
        .all()
    )
    return summary(rows)


def overview(db: Session) -> dict:
    """Every opinion candidates gave at the end of a program."""
    return summary(db.query(CandidateFeedback).filter(CandidateFeedback.source == SOURCE).all())

"""What candidates think at the end of their program.

When a class is completed, each of its candidates is asked once: how many
stars, and a few words. They answer on a private page (the link is in the
email, and on WhatsApp when a business number is connected); a reply to that
email counts too. Their words go through the same rules as the published
feedback (see feedback.analyse), so satisfaction is read, not guessed.

An opinion is a row of `candidate_feedback`: asked ("to_ask", then "asked"),
then answered ("received").

It takes ten seconds: each star of the email or WhatsApp is a link, so one tap
gives the rating; a few words, then their professor, come after, only if they
want. Who gives 1 to 3 stars is thanked privately and the admin is told to call
them (it rings until someone wrote to them); who gives 4 or 5 and accepts
publication is offered to say it again on Google or Facebook, their words
copied for them. A class is asked on its last session day (with a QR code to
show in the room), and a candidate who succeeds (an internship, a PFE, a
certification) can be congratulated and asked then.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.models.candidate import Candidate
from app.models.candidate_email import CandidateEmail
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


def google_reviews(db: Session) -> str:
    """Where a happy candidate leaves a review on Google (Settings, Your centre); empty when it was not given."""
    return (getattr(settings_service.get_settings(db), "google_review_url", None) or "").strip()


def star_links(db: Session, row: CandidateFeedback) -> list[tuple[int, str]]:
    """The five stars of the email or WhatsApp, each a link: one tap opens their page with that rating, which keeps it."""
    page = f"{settings_service.public_base_url(db)}/opinion/{row.external_id}"
    return [(stars, f"{page}?stars={stars}") for stars in (1, 2, 3, 4, 5)]


def rating(db: Session) -> dict:
    """The stars candidates gave at the end of their program: {"stars": 4.8, "count": 120}, stars None when there is none."""
    given = [value for (value,) in db.query(CandidateFeedback.rating).filter(
        CandidateFeedback.source == SOURCE, CandidateFeedback.status == "received", CandidateFeedback.rating.isnot(None)).all()]
    return {"stars": round(sum(given) / len(given), 1) if given else None, "count": len(given)}


RATING_SHOWN_FROM = 3               # fewer reviews than this: the rating is not said (one 5 is not "5/5")


def rating_line(db: Session) -> str:
    """"4,8/5 d'après 120 candidats", for the emails ({rating_line}); empty while there are too few reviews."""
    found = rating(db)
    if found["stars"] is None or found["count"] < RATING_SHOWN_FROM:
        return ""
    return f"{str(found['stars']).replace('.', ',')}/5 d'après {found['count']} candidats"


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
        values={"feedback_link": link, "facebook_link": facebook_reviews(), "star_links": star_links(db, row)},
        manual=manual, first=first_way(db),
    )
    # By the way chosen above the inbox: an email sent, or a WhatsApp / SMS waiting for the admin's phone, both count.
    if not (message is not None and message.status in ("sent", "to_send", "in_letter")):
        if message is None and not manual:
            row.status = "off"       # the email is switched off: nothing waits
        return None
    row.status = "asked"
    _keep(row, asked_at=datetime.now(timezone.utc).isoformat(), asks=(_payload(row).get("asks") or 0) + 1)
    db.flush()
    return row


def first_way(db: Session) -> str | None:
    """WhatsApp first, when a Business number sends by itself and the way above the inbox is left to the system:
    a review is given where people already are. Otherwise None (the usual way)."""
    from app.services import channels, whatsapp

    return "whatsapp" if whatsapp.configured() and channels.choice(db)["way"] == "auto" else None


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
        candidate = registration.candidate if registration else None
        if candidate is None or not (candidate.email or (candidate.phone or "").strip()):
            row.status = "off"          # no email and no phone: nobody to ask
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
        elif registration.candidate is None or not (registration.candidate.email or (registration.candidate.phone or "").strip()):
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


def professor_of(db: Session, row: CandidateFeedback) -> str:
    """The name of the professor of the class the review is about."""
    class_id = _payload(row).get("class_id")
    group = db.get(ClassGroup, class_id) if class_id else None
    if group is None and row.registration_id:
        registration = db.get(Registration, row.registration_id)
        group = registration.class_group if registration else None
    return (group.professor_name or "").strip() if group else ""


RECOMMEND = {"yes": "Yes", "maybe": "Maybe", "no": "No"}


def answer(db: Session, row: CandidateFeedback, *, stars: int | None, words: str = "",
           may_publish: bool = False, channel: str = "page", happy: int | None = None, improve: str = "",
           prof_stars: int | None = None, prof_words: str = "", recommend: str = "") -> CandidateFeedback:
    """Record what a candidate answered (the program, how happy they are, what to improve, their professor, whether
    they recommend us), and read it."""
    words = (words or "").strip()[:WORDS_LIMIT]
    improve = (improve or "").strip()[:WORDS_LIMIT]
    prof_words = (prof_words or "").strip()[:WORDS_LIMIT]
    stars = stars if stars in (1, 2, 3, 4, 5) else None
    happy = happy if happy in (1, 2, 3, 4, 5) else None
    prof_stars = prof_stars if prof_stars in (1, 2, 3, 4, 5) else None
    if stars is None and not words and not improve:
        raise ValueError("Choisissez un nombre d'étoiles ou écrivez quelques mots.")
    reading = feedback.analyse(" ".join(part for part in (words, improve) if part), stars=stars)
    first = row.status != "received"
    row.rating, row.comment = stars, words or None
    row.sentiment, row.language = reading["sentiment"], reading["language"]
    row.sentiment_score = {"positive": 100, "mixed": 50, "neutral": 50, "negative": 0}.get(reading["sentiment"])
    row.status = "received"
    row.submitted_at = datetime.now(timezone.utc)
    _keep(row, themes=reading["themes"], positive=reading["positive"], negative=reading["negative"],
          may_publish=bool(may_publish), channel=channel, happy=happy, improve=improve or None,
          improve_themes=feedback.analyse(improve)["themes"] if improve else [],
          professor=professor_of(db, row) or None, prof_stars=prof_stars, prof_words=prof_words or None,
          prof_tone=feedback.analyse(prof_words, stars=prof_stars)["sentiment"] if (prof_words or prof_stars) else None,
          recommend=recommend if recommend in RECOMMEND else None)
    _keep(row, low=is_low(row))
    _tell_admin(db, row, first)
    db.flush()
    return row


def is_low(row: CandidateFeedback) -> bool:
    """1 to 3 stars, or (without stars) words that are not happy: thanked privately, and the team contacts them."""
    if row.rating:
        return row.rating <= 3
    return row.sentiment in ("negative", "mixed")


def _tell_admin(db: Session, row: CandidateFeedback, first: bool) -> None:
    """The admin hears of each review once. A low one asks them to contact the person and rings until someone wrote to
    them (see notification_rules); the words added after the stars, or a rating changed, change that same notification."""
    data = _payload(row)
    name, stars, words = row.author_name or "A candidate", row.rating, row.comment or ""
    low = is_low(row)
    title = f"{name} gave {stars} star{'s' if stars != 1 else ''}" if stars else f"{name} gave their opinion"
    if low:
        message = "Not fully happy: " + (words[:180] or "no words, only the stars") + ". They were told the team would contact them."
    else:
        message = words[:200] or row.program_name or ""
    category = "REVIEW_LOW" if low else "FEEDBACK"
    url = (f"/candidate/{row.candidate_id}#reviews" if low and row.candidate_id
           else f"/classes/{data.get('class_id')}#opinions" if data.get("class_id") else "/website#feedback")
    known = db.get(Notification, data["notified"]) if data.get("notified") else None
    if known is None and not first:
        return                  # told before this was kept (an older review): not again
    if known is not None:
        if low and known.category != "REVIEW_LOW":
            known.is_read, known.status, known.read_at = False, "unread", None       # it became low: it needs them now
        known.title, known.message, known.category, known.action_url = title, message, category, url
        return
    note = Notification(recipient_role="admin", category=category, candidate_id=row.candidate_id, title=title,
                        message=message, action_url=url, registration_id=row.registration_id)
    db.add(note)
    db.flush()
    _keep(row, notified=note.id)


def change(db: Session, row: CandidateFeedback, **changes) -> CandidateFeedback:
    """One step of their page (the stars, then a few words, then their professor): what they gave before is kept."""
    data = _payload(row) if row.status == "received" else {}
    values = {
        "stars": row.rating if row.status == "received" else None, "words": (row.comment or "") if row.status == "received" else "",
        "may_publish": bool(data.get("may_publish")), "channel": data.get("channel") or "page", "happy": data.get("happy"),
        "improve": data.get("improve") or "", "prof_stars": data.get("prof_stars"), "prof_words": data.get("prof_words") or "",
        "recommend": data.get("recommend") or "",
    }
    return answer(db, row, **{**values, **changes})


def contacted(db: Session, row: CandidateFeedback, by: str = "") -> None:
    """The team contacted someone who was not fully happy (a call, a visit): their notification closes."""
    _keep(row, contacted={"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": by or None})
    note = db.get(Notification, _payload(row).get("notified")) if _payload(row).get("notified") else None
    if note is not None and not note.is_read:
        note.is_read, note.status, note.read_at = True, "settled", datetime.utcnow()
    db.flush()


def was_contacted(db: Session, row: CandidateFeedback) -> bool:
    """Someone of the team wrote to them after their review, or said they contacted them."""
    from app.models.candidate_email import CandidateEmail

    if _payload(row).get("contacted"):
        return True
    if row.submitted_at is None or not row.candidate_id:
        return False
    since = row.submitted_at.astimezone(timezone.utc).replace(tzinfo=None) if row.submitted_at.tzinfo else row.submitted_at
    return db.query(CandidateEmail.id).filter(
        CandidateEmail.candidate_id == row.candidate_id, CandidateEmail.direction == "outbound",
        CandidateEmail.author == "admin", CandidateEmail.received_at > since).first() is not None


def from_reply(db: Session, registration_id: str | None, body: str, message_id: str | None = None) -> CandidateFeedback | None:
    """A candidate answered the request by email: their reply is their opinion (the message it came from is kept, so a
    reading the admin corrects can be taken back)."""
    row = of_registration(db, registration_id) if registration_id else None
    if row is None or row.status == "received" or not (body or "").strip():
        return None
    kept = answer(db, row, stars=stars_in(body), words=body, channel="email")
    if message_id:
        _keep(kept, from_message=message_id)
    return kept


# A reply to "what did you think?" may be an opinion, a question, or both. Read with words (never the model):
# what it asks (a question mark, question words, a subject the assistant answers) against what it says of the program
# (stars, praise or complaint words, what it talks about). The admin's corrections are examples the reading learns from.
_ASKING = re.compile(r"\?|؟|\b(comment|quand|combien|o[uù]|pourquoi|quel(?:le)?s?|est-ce|puis-je|pouvez|pourriez|peux|how|when|where|what|which|why|can i|could you|"
                     r"is there|do you|kifech|kifach|win|9adech|wa9tech|chnowa|chnoua)\b|كيف|متى|أين|كم|ماذا|هل", re.I)
_SAYING = re.compile(r"\b(j'ai (?:bien |beaucoup |vraiment )?(?:aim|ador|appr[ée]ci)\w*|merci (?:pour|de) (?:la|cette|votre) formation|satisfait\w*|recommande\w*|"
                     r"(?:super|bonne|excellente|tr[eè]s bonne|belle) (?:formation|exp[ée]rience|ambiance)|d[ée]çu\w*|great (?:course|training)|"
                     r"i (?:liked|loved|enjoyed)|highly recommend|ma (?:fhemt|7abbit)|7abbit|3jebni)\b", re.I)
_ASKED_SUBJECTS = ("registration", "pricing", "schedule", "payment", "certificate", "location", "course_content", "refund", "cancellation")


def reading_of(db: Session | None, text: str) -> str:
    """"opinion", "question" (it asks something and says nothing of the program), or "both"."""
    from app.services import email_assistant

    text = (text or "").strip()
    if not text:
        return "opinion"
    analysis = email_assistant.analyse("", text)
    tone = feedback.analyse(text, stars=stars_in(text))
    says = bool(stars_in(text) or _SAYING.search(text) or tone["positive"] or tone["negative"] or tone["themes"])
    # Asking: a question mark or a question word; a subject the assistant answers ("une attestation") only when nothing
    # in it is an opinion ("les séances trop longues" speaks of the sessions without asking about them).
    asks = bool(_ASKING.search(text)) or (analysis["intent"] in _ASKED_SUBJECTS and not says)
    if db is not None and asks:
        from app.services import reply_rules

        # A reply like one the admin said was a question, not an opinion: a question.
        words = set(re.findall(r"[^\W\d_]{3,}", text.lower()))
        for example in reply_rules.rules(db)["not_opinions"]:
            known = set(re.findall(r"[^\W\d_]{3,}", example.lower()))
            if words and known and len(words & known) / max(1, len(words | known)) >= 0.5:
                return "question"
    if asks and not says:
        return "question"
    return "both" if asks else "opinion"


def withdraw(db: Session, message=None, row: CandidateFeedback | None = None) -> CandidateFeedback | None:
    """An opinion that was not one (a question, read as an opinion): taken back. The request goes back to waiting for
    their real opinion, and nothing of it is counted any more (stars, mood, themes, the dashboard)."""
    if row is None and message is not None:
        rows = db.query(CandidateFeedback).filter(CandidateFeedback.source == SOURCE, CandidateFeedback.status == "received",
                                                  CandidateFeedback.candidate_id == message.candidate_id).all()
        body = (message.body or "").strip()
        row = next((item for item in rows if _payload(item).get("from_message") == message.id), None) \
            or next((item for item in rows if (item.comment or "").strip() == body[:WORDS_LIMIT].strip()), None)
    if row is None or row.status != "received":
        return None
    _keep(row, withdrawn={"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "words": row.comment, "stars": row.rating,
                          "why": "A question, read as an opinion"}, themes=[], positive=[], negative=[], from_message=None)
    row.status, row.rating, row.comment, row.sentiment, row.sentiment_score = "asked", None, None, None, None
    db.flush()
    return row


# ------------------------------------------------------------------
# The last session: asked in the room, with a QR code
# ------------------------------------------------------------------

def is_last_session(db: Session, session) -> bool:
    """No session of its class comes after this one."""
    from app.models.classsession import ClassSession

    return db.query(ClassSession.id).filter(ClassSession.class_group_id == session.class_group_id,
                                            ClassSession.session_date > session.session_date).first() is None


def class_token(db: Session, class_group: ClassGroup) -> str:
    """The class's QR code leads to /opinion/class/<this>: who scans it finds their own review there."""
    if not class_group.opinion_token:
        class_group.opinion_token = secrets.token_urlsafe(18)
        db.flush()
    return class_group.opinion_token


def by_class_token(db: Session, token: str) -> ClassGroup | None:
    if not token or len(token) < 16:
        return None
    return db.query(ClassGroup).filter(ClassGroup.opinion_token == token).first()


def find_in_class(db: Session, class_group: ClassGroup, said: str) -> CandidateFeedback | None:
    """Someone scanned the class's QR code and gave their email or phone number: their own review, when they are in
    this class (the QR code alone never shows who is in it)."""
    said = (said or "").strip().lower()
    digits = re.sub(r"\D", "", said)
    if not said:
        return None
    for registration in (db.query(Registration).options(joinedload(Registration.candidate))
                         .filter(Registration.class_group_id == class_group.id, Registration.status != RegistrationStatus.CANCELLED).all()):
        candidate = registration.candidate
        if candidate is None:
            continue
        phone = re.sub(r"\D", "", candidate.phone or "")
        if (candidate.email and candidate.email.strip().lower() == said) or (len(digits) >= 8 and len(phone) >= 8 and phone[-8:] == digits[-8:]):
            row = _row_for(db, registration, class_group)
            if row.status == "to_ask":
                row.status = "asked"
                _keep(row, asked_at=datetime.now(timezone.utc).isoformat(), channel="qr")
            return row
    return None


# ------------------------------------------------------------------
# A success: congratulated, and asked then
# ------------------------------------------------------------------

SUCCESSES = {
    "internship": ("an internship", "un stage"),
    "pfe": ("a PFE", "un PFE"),
    "certification": ("a certification", "une certification"),
    "job": ("a job", "un emploi"),
}


def celebrate(db: Session, registration: Registration, kind: str, words: str = "") -> CandidateEmail | None:
    """A candidate got an internship, a PFE, a certification or a job: they are congratulated (WhatsApp first) and asked,
    at that happy moment, to tell it on Google or Facebook. Kept on their review, and given to the Social media page as a
    success story to tell (with their first name only once they accepted)."""
    if kind not in SUCCESSES:
        raise ValueError("Choose what they succeeded in.")
    words = " ".join((words or "").split())[:200]
    row = _row_for(db, registration, registration.class_group)
    link = f"{settings_service.public_base_url(db)}/opinion/{row.external_id}"
    message = email_templates.send(
        db, "success_review", registration=registration, manual=True, first=first_way(db),
        values={"success": SUCCESSES[kind][1] + (f" ({words})" if words else ""), "feedback_link": link,
                "facebook_link": facebook_reviews(), "google_link": google_reviews(db) or facebook_reviews()},
    )
    successes = list(_payload(row).get("successes") or [])
    successes.append({"kind": kind, "words": words or None, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "sent": bool(message is not None and message.status in ("sent", "to_send", "in_letter"))})
    _keep(row, successes=successes[-5:])
    if row.status == "to_ask" and successes[-1]["sent"]:
        row.status = "asked"
    db.flush()
    return message


def successes(db: Session, limit: int = 6) -> list[dict]:
    """The latest successes, for the Social media page: {"first_name", "kind", "words", "program", "at", "may_publish"}."""
    found = []
    for row in db.query(CandidateFeedback).filter(CandidateFeedback.source == SOURCE).all():
        data = _payload(row)
        for item in data.get("successes") or []:
            found.append({"first_name": ((row.author_name or "").split() or ["Un(e) candidat(e)"])[0].capitalize(),
                          "kind": item.get("kind"), "label": SUCCESSES.get(item.get("kind"), ("", ""))[0], "words": item.get("words") or "",
                          "program": row.program_name or "", "at": item.get("at") or "", "may_publish": bool(data.get("may_publish"))})
    return sorted(found, key=lambda item: item["at"], reverse=True)[:limit]


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
        "happy": data.get("happy"), "improve": data.get("improve") or "", "professor": data.get("professor") or "",
        "prof_stars": data.get("prof_stars"), "prof_words": data.get("prof_words") or "", "recommend": data.get("recommend"),
        "improve_themes": [feedback.THEMES[key] for key in data.get("improve_themes") or [] if key in feedback.THEMES],
        "low": row.status == "received" and is_low(row), "contacted": bool(data.get("contacted")),
        "successes": [{**item, "label": SUCCESSES.get(item.get("kind"), ("", ""))[0]} for item in data.get("successes") or []],
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
    happy = [item["happy"] for item in answered if item["happy"]]
    recommend = [item["recommend"] for item in answered if item["recommend"]]
    to_improve: dict[str, int] = {}
    for item in answered:
        for theme in item["improve_themes"]:
            to_improve[theme["label"]] = to_improve.get(theme["label"], 0) + 1
    professors: dict[str, list] = {}
    for item in answered:
        if item["professor"] and item["prof_stars"]:
            professors.setdefault(item["professor"], []).append(item["prof_stars"])
    return {
        "happy_pct": round(sum(happy) / len(happy) / 5 * 100) if happy else None,
        "recommend_pct": round(100 * sum(1 for value in recommend if value == "yes") / len(recommend)) if recommend else None,
        "to_improve": sorted(to_improve.items(), key=lambda pair: pair[1], reverse=True)[:5],
        "improve_words": [item["improve"] for item in answered if item["improve"]][:6],
        "professors": [{"name": name, "stars": round(sum(values) / len(values), 1), "count": len(values)}
                       for name, values in sorted(professors.items(), key=lambda pair: -sum(pair[1]) / len(pair[1]))],
        "how": ("Counted from the reviews candidates gave at the end of the program: stars for the program (1 to 5), how happy they "
                "are (1 to 5, shown in percent), stars for their professor, whether they recommend 9antra, and what they wrote. "
                "What they wrote is read with fixed word lists (the subjects: the trainer, the content, the rhythm…; the tone: "
                "positive or negative words), never by a model."),
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


def of_program(db: Session, program_id: str) -> dict:
    """The opinions of everyone who finished this program."""
    rows = (
        db.query(CandidateFeedback)
        .join(Registration, Registration.id == CandidateFeedback.registration_id)
        .filter(CandidateFeedback.source == SOURCE, Registration.program_id == program_id)
        .all()
    )
    return summary(rows)


def of_candidate(db: Session, candidate_id: str) -> list[dict]:
    """A candidate's programs that ended (or whose review was asked, on the last session day): their opinion (or that it
    was asked), and whether it can be asked."""
    from app.models import Candidate

    candidate = db.get(Candidate, candidate_id)
    found = []
    for registration in (candidate.registrations if candidate else []):
        row = of_registration(db, registration.id)
        if registration.status != RegistrationStatus.COMPLETED and row is None:
            continue
        found.append({"registration": registration, "review": _view(row) if row is not None else None})
    return found


def of_professor(db: Session, name: str) -> dict:
    """The reviews of the classes a professor taught."""
    rows = [row for row in db.query(CandidateFeedback).filter(CandidateFeedback.source == SOURCE).all()
            if (_payload(row).get("professor") or professor_of(db, row)) == name]
    return summary(rows)


def overview(db: Session) -> dict:
    """Every opinion candidates gave at the end of a program."""
    return summary(db.query(CandidateFeedback).filter(CandidateFeedback.source == SOURCE).all())

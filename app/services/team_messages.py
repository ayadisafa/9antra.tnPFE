"""Messages between the admin and the team: the professors and the team members.

One conversation per person. The admin writes once and chooses how it goes: in the
application (the person reads it on their own page, on a computer or a phone), by email,
on WhatsApp, or by SMS. What they answer comes back into the same conversation: an email
reply (the mailbox is read every few minutes), a WhatsApp message (Meta's webhook), or a
message written in the application.

WhatsApp without a Business number, and SMS, are not sent by the application: the message
waits with a button that opens WhatsApp or the phone's messages, already written, on the
admin's phone (an SMS then goes from the admin's own number). Nothing goes by itself to the
wrong place.

When someone of the team asks something, the assistant looks for the answer in what it
knows (answers the admin taught, the website, the application's own figures). The answer
waits for the admin, who sends it with one click or changes it. Only when the admin allowed
it, and only an answer the admin taught, matched surely, goes alone.
"""
from __future__ import annotations

import html
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.models.team_message import TeamMessage

CHANNELS = ("app", "email", "whatsapp", "sms")
CHANNEL_NAMES = {"app": "In the application", "email": "Email", "whatsapp": "WhatsApp", "sms": "SMS"}
ASSISTANT = "The assistant"
SURE = 0.8               # a taught answer this close to the question may go alone, when the admin allowed it
WORTH = 0.45             # below this, what the assistant found is not shown at all


def utcnow() -> datetime:
    return datetime.utcnow()


# ------------------------------------------------------------------
# The team
# ------------------------------------------------------------------

def key_of_user(user) -> str:
    """The conversation of someone signed in: a professor's account talks as that professor."""
    professor_id = getattr(user, "professor_id", None)
    return f"professor:{professor_id}" if professor_id else f"user:{user.id}"


def _number(phone) -> str | None:
    from app.services import whatsapp

    return whatsapp.normalise(phone) if phone else None


def _of_professor(professor, account=None) -> dict:
    return {
        "key": f"professor:{professor.id}", "kind": "professor", "id": professor.id, "name": professor.name,
        "role": "Professor", "detail": (professor.specialties or "").strip(),
        "email": professor.email or (account.email if account else None), "phone": professor.phone,
        "user_id": account.id if account else None,
    }


def _of_user(user) -> dict:
    return {
        "key": f"user:{user.id}", "kind": "user", "id": user.id, "name": user.name,
        "role": "Admin" if user.role == "admin" else "Professor" if user.role == "professor" else "Team",
        "detail": "", "email": user.email, "phone": getattr(user, "phone", None), "user_id": user.id,
    }


def _ways(person: dict) -> list[str]:
    """How this person can be reached."""
    number = _number(person.get("phone"))
    found = []
    if person.get("user_id"):
        found.append("app")
    if person.get("email"):
        found.append("email")
    if number:
        found += ["whatsapp", "sms"]
    return found


def person(db: Session, key: str | None) -> dict | None:
    from app.models import Professor, User

    kind, _, ident = (key or "").partition(":")
    if kind == "professor":
        professor = db.get(Professor, ident)
        if professor is None:
            return None
        account = db.query(User).filter(User.professor_id == professor.id, User.is_active.is_(True)).first()
        found = _of_professor(professor, account)
    elif kind == "user":
        user = db.get(User, ident)
        if user is None or user.professor_id:
            return None
        found = _of_user(user)
    else:
        return None
    found["ways"] = _ways(found)
    found["number"] = _number(found.get("phone"))
    return found


def people(db: Session, me_id: str | None = None) -> list[dict]:
    """Everyone the admin can write to, those who wrote and were not read first, then the latest conversations."""
    from app.models import Professor, User

    accounts = {user.professor_id: user for user in db.query(User).filter(User.professor_id.isnot(None), User.is_active.is_(True)).all()}
    found = [_of_professor(professor, accounts.get(professor.id)) for professor in db.query(Professor).order_by(Professor.name).all()]
    found += [_of_user(user) for user in db.query(User).filter(User.professor_id.is_(None), User.is_active.is_(True)).order_by(User.name).all()
              if user.id != me_id]
    last, unread = {}, {}
    for row in db.query(TeamMessage).order_by(TeamMessage.created_at.asc()).all():
        last[row.person] = row
        if row.direction == "in" and row.read_at is None:
            unread[row.person] = unread.get(row.person, 0) + 1
    for item in found:
        item["ways"] = _ways(item)
        item["last"] = last.get(item["key"])
        item["unread"] = unread.get(item["key"], 0)
    found.sort(key=lambda item: (-item["unread"], -(item["last"].created_at.timestamp() if item["last"] else 0), item["name"].lower()))
    return found


def find_by_email(db: Session, address: str) -> dict | None:
    from app.models import Professor, User

    address = (address or "").strip().lower()
    if not address:
        return None
    professor = db.query(Professor).filter(Professor.email.ilike(address)).first()
    if professor is not None:
        return person(db, f"professor:{professor.id}")
    user = db.query(User).filter(User.email.ilike(address), User.is_active.is_(True)).first()
    if user is not None:
        return person(db, key_of_user(user))
    return None


def find_by_phone(db: Session, phone) -> dict | None:
    from app.models import Professor, User

    number = _number(phone if str(phone).startswith("+") else "+" + str(phone).lstrip("+"))
    if number is None:
        return None
    for professor in db.query(Professor).filter(Professor.phone.isnot(None)).all():
        if _number(professor.phone) == number:
            return person(db, f"professor:{professor.id}")
    for user in db.query(User).filter(User.phone.isnot(None), User.is_active.is_(True)).all():
        if _number(user.phone) == number:
            return person(db, key_of_user(user))
    return None


def save_contact(db: Session, key: str, *, email: str | None = None, phone: str | None = None) -> str | None:
    """Change someone's email or number from their conversation. Returns the problem, or None."""
    from app.models import Professor, User
    from app.services import whatsapp

    found = person(db, key)
    if found is None:
        return "This person is not in the team any more."
    phone = (phone or "").strip()
    if phone and whatsapp.normalise(phone) is None:
        return "This phone number cannot be read: write it like +216 98 123 456."
    stored = whatsapp.pretty(phone) if phone else None
    if found["kind"] == "professor":
        professor = db.get(Professor, found["id"])
        email = (email or "").strip().lower()
        if email and ("@" not in email or " " in email):
            return "This email address cannot be used."
        professor.email = email or None
        professor.phone = stored
    else:
        db.get(User, found["id"]).phone = stored      # their email is how they sign in: changed on the team page only
    db.commit()
    return None


# ------------------------------------------------------------------
# One conversation
# ------------------------------------------------------------------

def link_of(row: TeamMessage, number: str | None) -> str | None:
    """For a message that waits for the admin's phone: what opens WhatsApp or the messages, written."""
    from app.services import whatsapp

    if row.status != "to_send" or not number:
        return None
    if row.channel == "whatsapp":
        return whatsapp.link(number, row.body)
    if row.channel == "sms":
        return f"sms:{number}?body={quote(row.body)}"
    return None


def conversation(db: Session, key: str, limit: int = 300) -> list[dict]:
    """The messages, oldest first. One message sent several ways is one bubble with one chip per way."""
    found = person(db, key)
    number = found["number"] if found else None
    rows = (db.query(TeamMessage).filter(TeamMessage.person == key)
            .order_by(TeamMessage.created_at.desc()).limit(limit).all())[::-1]
    bubbles, by_batch = [], {}
    for row in rows:
        way = {"id": row.id, "channel": row.channel, "name": CHANNEL_NAMES.get(row.channel, row.channel), "status": row.status,
               "error": row.error, "link": link_of(row, number), "read_at": row.read_at}
        if row.direction == "out" and row.batch and row.batch in by_batch:
            by_batch[row.batch]["ways"].append(way)
            continue
        bubble = {"id": row.id, "batch": row.batch, "direction": row.direction, "body": row.body, "author": row.author, "created_at": row.created_at,
                  "ways": [way], "suggestion": row.suggestion, "suggestion_source": row.suggestion_source, "read_at": row.read_at}
        bubbles.append(bubble)
        if row.direction == "out" and row.batch:
            by_batch[row.batch] = bubble
    return bubbles


def mark_read(db: Session, key: str) -> int:
    """The admin opened the conversation: what they wrote is read."""
    count = (db.query(TeamMessage).filter(TeamMessage.person == key, TeamMessage.direction == "in", TeamMessage.read_at.is_(None))
             .update({TeamMessage.read_at: utcnow()}, synchronize_session=False))
    if count:
        db.commit()
    return count


def unread_count(db: Session) -> int:
    return db.query(TeamMessage).filter(TeamMessage.direction == "in", TeamMessage.read_at.is_(None)).count()


def waiting_phone(db: Session) -> int:
    """Messages that wait for the admin's phone (WhatsApp or SMS)."""
    return db.query(TeamMessage).filter(TeamMessage.status == "to_send").count()


def mark_sent(db: Session, message_id: str, *, cancelled: bool = False) -> TeamMessage | None:
    row = db.get(TeamMessage, message_id)
    if row is None or row.status != "to_send":
        return None
    if cancelled:
        db.delete(row)
    else:
        row.status = "sent"
    db.commit()
    return row


# ------------------------------------------------------------------
# Sending
# ------------------------------------------------------------------

def _email(db: Session, found: dict, text: str, author: str) -> tuple[str | None, str | None]:
    """(problem, message id)."""
    from app.services import email_threads
    from app.services.email_service import new_message_id
    from app.services.settings_service import get_settings
    from app.services import brand

    if not email_threads.smtp_configured():
        return "No email account is connected (Settings, Email).", None
    centre = brand.view(get_settings(db))["name"]
    message_id = new_message_id()
    body = f"{text}\n\n— {author}, {centre}"
    page = ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55;color:#24212b">'
            + html.escape(body).replace("\n", "<br>") + "</div>")
    problem, _ = email_threads._attempt(found["email"], f"Message — {centre}", body, page, message_id, None, None)
    return problem, message_id


def _within_window(db: Session, key: str) -> bool:
    """WhatsApp lets a business write freely for 24 hours after the person last wrote on WhatsApp."""
    last = (db.query(TeamMessage.created_at).filter(TeamMessage.person == key, TeamMessage.direction == "in", TeamMessage.channel == "whatsapp")
            .order_by(TeamMessage.created_at.desc()).first())
    return bool(last and last[0] >= utcnow() - timedelta(hours=23, minutes=50))


def send(db: Session, key: str, text: str, channels, *, author: str) -> list[TeamMessage]:
    """Write to someone of the team, by each chosen way they can be reached. Returns the copies kept."""
    from app.services import whatsapp

    found = person(db, key)
    text = (text or "").strip()
    if found is None or not text:
        return []
    wanted = [channel for channel in CHANNELS if channel in set(channels or []) and channel in found["ways"]]
    if not wanted:
        wanted = [found["ways"][0]] if found["ways"] else []
    batch, kept = str(uuid.uuid4()), []
    for channel in wanted:
        row = TeamMessage(person=key, direction="out", channel=channel, body=text[:4000], author=author, batch=batch, status="sent")
        if channel == "email":
            problem, row.message_id = _email(db, found, text, author)
            if problem:
                row.status, row.error = "failed", problem[:500]
        elif channel == "whatsapp":
            if whatsapp.configured():
                problem = whatsapp.send(found["number"], text, free=_within_window(db, key))
                if problem:
                    row.status, row.error = "failed", problem[:500]
            else:
                row.status = "to_send"
        elif channel == "sms":
            from app.services import sms

            if sms.configured():
                problem = sms.send(found["number"], text)
                if problem:
                    row.status, row.error = "failed", problem[:500]
            else:
                row.status = "to_send"         # sent from the admin's phone: no Android phone is connected
        db.add(row)
        kept.append(row)
    db.commit()
    return kept


# ------------------------------------------------------------------
# Receiving, and the assistant's answer
# ------------------------------------------------------------------

def suggest(db: Session, text: str) -> dict | None:
    """What the assistant knows that answers this: {"text", "source", "quality", "title"}, or None."""
    from app.models.knowledge import KnowledgeEntry
    from app.services import knowledge_service

    if len((text or "").split()) < 2:
        return None
    try:
        hits = knowledge_service.search(db, text, limit=3)
    except Exception:
        return None
    for hit in hits:
        quality = hit.get("quality") or 0
        if quality < WORTH:
            continue
        entry = db.get(KnowledgeEntry, hit["id"])
        if entry is None or not (entry.content or "").strip():
            continue
        content = entry.content.strip()
        content = content if len(content) <= 900 else content[:900].rsplit(" ", 1)[0] + "…"
        taught = entry.origin != "system"
        website = (entry.source_key or "").startswith("website:")
        return {"text": content, "source": "taught" if taught else "website" if website else "knowledge",
                "quality": round(quality, 2), "title": entry.title}
    return None


def answers_alone(db: Session) -> bool:
    from app.services.settings_service import get_settings

    return bool(getattr(get_settings(db), "team_auto_reply", False))


def receive(db: Session, key: str, text: str, channel: str, *, message_id: str | None = None, name: str = "",
            when: datetime | None = None) -> TeamMessage | None:
    """Someone of the team wrote. Kept in their conversation, the admin is told, and the assistant prepares an answer
    (sent alone only when the admin allowed it and the answer is one the admin taught, matched surely)."""
    from app.services.notification_service import create_notification

    text = (text or "").strip()
    if not text:
        return None
    if message_id and db.query(TeamMessage.id).filter(TeamMessage.message_id == message_id).first() is not None:
        return None
    found = person(db, key)
    if found is None:
        return None
    if when is not None and when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    row = TeamMessage(person=key, direction="in", channel=channel if channel in CHANNELS else "app", body=text[:4000],
                      author=name or found["name"], status="received", message_id=message_id, created_at=when or utcnow())
    answer = suggest(db, text)
    if answer:
        row.suggestion, row.suggestion_source = answer["text"], answer["source"]
    db.add(row)
    db.commit()
    alone = bool(answer and answer["source"] == "taught" and answer["quality"] >= SURE and answers_alone(db))
    if alone:
        back = row.channel if row.channel in found["ways"] else (found["ways"][0] if found["ways"] else None)
        if back:
            send(db, key, answer["text"], [back], author=ASSISTANT)
    create_notification(
        db, title=f"{found['name']} wrote to you" + (" · the assistant answered" if alone else ""),
        message=text[:300], category="TEAM_MESSAGE", action_url=f"/inbox/team?person={quote(key, safe=':')}",
    )
    return row


def from_email(db: Session, *, sender: str, subject: str | None, body: str, message_id: str | None = None,
               received_at: datetime | None = None) -> TeamMessage | None:
    """An email from a professor or a team member (the mailbox reads every email that arrives)."""
    found = find_by_email(db, sender)
    if found is None:
        return None
    text = (body or "").strip()
    subject = re.sub(r"^((re|tr|fw|fwd)\s*:\s*)+", "", (subject or "").strip(), flags=re.I)
    if subject and not subject.lower().startswith("message"):       # "Message — 9antra" is the centre's own subject
        text = f"{subject}\n{text}" if text else subject
    return receive(db, found["key"], text, "email", message_id=message_id, when=received_at)


def from_whatsapp(db: Session, *, phone: str, text: str, wa_id: str, name: str = "") -> TeamMessage | None:
    found = find_by_phone(db, phone)
    if found is None:
        return None
    return receive(db, found["key"], text or "(a message without text: a photo, a voice note or a file)", "whatsapp",
                   message_id=f"wa:{wa_id}", name=name)


# ------------------------------------------------------------------
# The person's own side (a professor or a team member signed in)
# ------------------------------------------------------------------

def for_member(db: Session, user) -> dict:
    key = key_of_user(user)
    return {"key": key, "messages": conversation(db, key)}


def member_unread(db: Session, user) -> int:
    return (db.query(TeamMessage).filter(TeamMessage.person == key_of_user(user), TeamMessage.direction == "out",
                                         TeamMessage.channel == "app", TeamMessage.read_at.is_(None)).count())


def member_read(db: Session, user) -> int:
    count = (db.query(TeamMessage).filter(TeamMessage.person == key_of_user(user), TeamMessage.direction == "out",
                                          TeamMessage.channel == "app", TeamMessage.read_at.is_(None))
             .update({TeamMessage.read_at: utcnow()}, synchronize_session=False))
    if count:
        db.commit()
    return count


def member_write(db: Session, user, text: str) -> TeamMessage | None:
    return receive(db, key_of_user(user), text, "app", name=getattr(user, "name", ""))

"""Yasmine remembers the conversation: what each person asked her and what she answered, so it goes on from page to page
(she can take you to another page and keep talking) and from one day to the next ("where were we?").

Each person sees only their own conversation. "New conversation" in her window starts a fresh one (the earlier lines
stay, for "where were we?", until they are KEEP_DAYS old). What she answered is kept with what her window draws
around it (links, sources, the cards of figures), never with a move to another page: an old answer never moves you.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.chat_line import ChatLine

KEEP_DAYS = 60
MOST = 400                # lines kept per person
SHOWN = 40                # lines her window shows again
CONTEXT = 8               # lines given to the agents as what was said before
_KEPT = ("links", "sources", "resources", "short", "cards", "options", "schema", "consulted", "understood", "went")


def _id(user) -> str | None:
    return getattr(user, "id", None) or None


def _data_of(found: dict) -> dict:
    """What her window draws with the answer, without the move to another page (an old answer never moves you)."""
    data = {key: found[key] for key in _KEPT if found.get(key)}
    agent = found.get("agent") or {}
    if agent.get("key"):
        data["agent"] = {key: agent.get(key) for key in ("key", "name", "icon", "colour")}
    category = found.get("category") or {}
    if category.get("key"):
        data["category"] = {key: category.get(key) for key in ("key", "label")}
    picture = found.get("picture") or {}
    if picture.get("photo"):
        data["picture"] = {key: picture.get(key) for key in ("photo", "caption", "url")}
    elif picture.get("scene"):
        data["picture"] = {"scene": picture["scene"]}           # drawn again when shown (the drawing is long)
    return data


def save(db: Session, user, question: str, found: dict, path: str | None = None) -> None:
    """The question and her answer, at the end of the person's conversation (never stops the answer when it cannot)."""
    who = _id(user)
    if not who or not (question or "").strip():
        return
    try:
        data = json.dumps(_data_of(found), ensure_ascii=False, default=str)
        db.add(ChatLine(user_id=who, role="user", text=question.strip()[:4000], path=(path or "")[:200] or None))
        db.add(ChatLine(user_id=who, role="bot", text=(found.get("reply") or "")[:12000], data=data if len(data) < 60000 else None,
                        path=(path or "")[:200] or None))
        db.commit()
    except Exception:
        db.rollback()


def start_new(db: Session, user) -> None:
    who = _id(user)
    if who:
        db.add(ChatLine(user_id=who, role="new", text=""))
        db.commit()


def _current(db: Session, who: str, most: int) -> list[ChatLine]:
    """The person's lines since their last "new conversation", the latest `most`, oldest first."""
    since = datetime.utcnow() - timedelta(days=KEEP_DAYS)
    rows = (db.query(ChatLine).filter(ChatLine.user_id == who, ChatLine.created_at >= since)
            .order_by(ChatLine.id.desc()).limit(most + 1).all())
    kept = []
    for row in rows:
        if row.role == "new":
            break
        kept.append(row)
    return list(reversed(kept[:most]))


def lines(db: Session, user, most: int = SHOWN) -> list[dict]:
    """[{"role": "user" | "bot", "text", "at", ...what the window draws}] of the conversation going on."""
    from app.services import pictures

    who = _id(user)
    if not who:
        return []
    found = []
    for row in _current(db, who, most):
        item = {"role": row.role, "text": row.text, "at": row.created_at.isoformat() + "Z" if row.created_at else None}
        if row.role == "bot" and row.data:
            try:
                item.update(json.loads(row.data))
            except ValueError:
                pass
            picture = item.get("picture") or {}
            if picture.get("scene"):
                try:
                    item["picture"] = {"svg": pictures.scene(picture["scene"]), "scene": picture["scene"]}
                except Exception:
                    item.pop("picture", None)
        found.append(item)
    return found


def history(db: Session, user, most: int = CONTEXT) -> list[dict]:
    """What was said before, as the agents read it: [{"role": "user" | "assistant", "content"}]."""
    who = _id(user)
    if not who:
        return []
    return [{"role": "user" if row.role == "user" else "assistant", "content": row.text} for row in _current(db, who, most) if row.text]


def forget_old(db: Session, now: datetime | None = None) -> int:
    """Lines older than KEEP_DAYS go, and each person keeps at most MOST."""
    limit = (now or datetime.utcnow()) - timedelta(days=KEEP_DAYS)
    gone = db.query(ChatLine).filter(ChatLine.created_at < limit).delete(synchronize_session=False)
    for (who,) in db.query(ChatLine.user_id).distinct().all():
        edge = db.query(ChatLine.id).filter(ChatLine.user_id == who).order_by(ChatLine.id.desc()).offset(MOST).first()
        if edge is not None:
            gone += db.query(ChatLine).filter(ChatLine.user_id == who, ChatLine.id <= edge[0]).delete(synchronize_session=False)
    db.commit()
    return gone


_SHOWN = ("reply", "links", "source", "sources", "resources", "suggestions", "schema", "agent", "category", "consulted", "understood",
          "path", "short", "picture", "cards", "options", "navigate", "back", "tour", "went", "page_tour")


def response(found: dict) -> dict:
    """What her window needs of an answer (the same for the whole assistant and the guide window)."""
    shown = {key: found.get(key) for key in _SHOWN}
    for key in ("links", "sources", "resources", "suggestions", "consulted", "understood", "path", "options", "cards"):
        shown[key] = shown[key] or []
    return shown


# ------------------------------------------------------------------
# "Where were we?"
# ------------------------------------------------------------------

RECALL = re.compile(
    r"\b(o[uù] en (?:[ée]tions|[ée]tait|sommes)[- ]nous|on en [ée]tait o[uù]|de quoi (?:on |nous )?(?:parlait|parlions)|on reprend|"
    r"reprenons|continu(?:e|ons) (?:notre|la) (?:conversation|discussion)|notre (?:derni[èe]re )?conversation|"
    r"where were we|what were we (?:talking|saying)|continue (?:our|the) (?:conversation|chat)|our (?:last|latest) conversation|"
    r"pick up where|what did i (?:just )?ask|9a3din nahkiw 3la chnowa|chnowa konna nahkiw|nkamlou (?:el )?(?:hdith|7dith|conversation)|"
    r"wselna l ?win)\b|أين كنا|عن ماذا كنا نتحدث|نكمل (?:حديثنا|المحادثة)", re.IGNORECASE)

_RECALL_SAY = {
    "fr": ("Nous parlions de : « {q} ».", "Ma réponse : {a}", "On continue : posez la suite, ou demandez-moi autre chose.",
           "Nous n'avons pas encore parlé : posez-moi votre première question 😊"),
    "en": ("We were talking about: “{q}”.", "My answer: {a}", "Let's go on: ask the next thing, or anything else.",
           "We haven't talked yet: ask me your first question 😊"),
    "ar": ("كنا نتحدث عن: «{q}».", "إجابتي: {a}", "لنكمل: اطرح السؤال التالي، أو أي شيء آخر.",
           "لم نتحدث بعد: اطرح سؤالك الأول 😊"),
    "tn": ("Konna nahkiw 3la: « {q} ».", "Jwebi: {a}", "Nkamlou: es2elni el ba9i, wala 7aja okhra.",
           "Mazelna ma 7kinech: es2elni awel sou2el 😊"),
}


def wants_recall(message: str) -> bool:
    return bool(RECALL.search(message or ""))


def recall(db: Session, user, lang: str | None) -> dict:
    """What we were talking about: the last question asked before this one (earlier conversations too) and her answer."""
    from app.services import brief

    lang = lang if lang in _RECALL_SAY else "fr"
    asked, answered, going_on, nothing = _RECALL_SAY[lang]
    who = _id(user)
    rows = (db.query(ChatLine).filter(ChatLine.user_id == who, ChatLine.role.in_(("user", "bot")))
            .order_by(ChatLine.id.desc()).limit(40).all()) if who else []
    question = answer = None
    for index, row in enumerate(rows):
        if row.role == "user" and not wants_recall(row.text):
            question = row.text
            later = [other for other in rows[:index] if other.role == "bot"]
            answer = later[-1].text if later else None
            break
    if not question:
        return {"reply": nothing, "links": [], "source": "talk", "sources": [], "resources": []}
    said = [asked.format(q=question.strip()[:160])]
    if answer:
        said.append(answered.format(a=brief.shorten(answer) or answer.strip()[:260]))
    said.append(going_on)
    return {"reply": "\n\n".join(said), "links": [], "source": "talk", "sources": [], "resources": [], "options": [question.strip()[:160]]}

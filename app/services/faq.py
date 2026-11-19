"""The centre's frequent questions, kept in the database and kept up to date by themselves:
- from the centre's own posts: the Summer Camp FAQ carousel, written in from its slides; other carousels are read from
  their pictures (the question on the white card, the answer on the pink lines) and checked by the admin;
- from what candidates really ask: every 30 minutes, the questions of the last 90 days are grouped and counted, the most
  frequent kept, with the taught answer that answers them (or none yet: the admin is shown which ones to teach).

Each answer is a taught answer (knowledge_entries), so the email assistant and Yasmine answer with it. An answer that
belongs to an event (the Summer Camp) is used only when the question names that event: a question about the schedule in
November does not get the Summer Camp's Saturdays.
"""
from __future__ import annotations

import io
import re
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.faq_item import FaqItem

ASKED_DAYS = 90
KEPT = 20
# What names an event: an answer of that event is used only when the question says it.
THEMES = {
    "summer_camp": {"name": "Summer Camp", "words": re.compile(r"(?<![\w])(summer|camp|été|ete|estival\w*|صيف\w*|السمر|سمر|sif|sayf)(?![\w])", re.IGNORECASE)},
}
# The Summer Camp FAQ, as the centre's carousel says it (Tunisian and French), with its answer in French for the emails
# and other ways of asking it.
SUMMER_CAMP = [
    ("chnowa howa Summer Camp mta3 9antra",
     "Opportunité leli y7eb yt3alm skills f web, mobile, IA, IoT, BI, DevOps, Cybersecurity, Marketing digital…\nFormation Certifié + attestation de stage",
     "Qu'est-ce que le Summer Camp de 9antra ?",
     "Le Summer Camp de 9antra est une opportunité pour apprendre des compétences en web, mobile, IA, IoT, BI, DevOps, cybersécurité et "
     "marketing digital. La formation est certifiée, avec une attestation de stage.",
     ["C'est quoi le Summer Camp ?", "What is the 9antra Summer Camp?", "chnowa summer camp"]),
    ("Chkoun inajem i9ayed fel Summer Camp ?",
     "étudiants, jeunes diplômés et passionnés du digital i7eb yet3alem des compétences pratiques w i7adher lel PFE.",
     "Qui peut participer au Summer Camp ?",
     "Les étudiants, les jeunes diplômés et les passionnés du digital qui veulent apprendre des compétences pratiques et préparer leur PFE.",
     ["Pour quel public est le Summer Camp ?", "Who can join the Summer Camp?", "chkoun ynajem ychareek fel summer camp"]),
    ("Les formations sont-elles certifiantes ?",
     "bien sur w idha andk stage takhou attestation de stage",
     "Les formations du Summer Camp sont-elles certifiantes ?",
     "Bien sûr. Et si vous faites un stage, vous recevez aussi une attestation de stage.",
     ["Est-ce que le Summer Camp donne un certificat ?", "Is the Summer Camp certified?", "summer camp fih certificat"]),
    ("Kifesh elwa9t?",
     "chaque samedi 4h formation tet3lm fihom tkhdem étape par étape\nchaque mercredi 2h encadrement nchoufou maak chnoi 3mlt win wslt w ch3andk mechekel",
     "Comment se passe l'emploi du temps du Summer Camp ?",
     "Chaque samedi, 4 heures de formation où vous apprenez et travaillez étape par étape. Chaque mercredi, 2 heures d'encadrement : nous voyons "
     "avec vous ce que vous avez fait, où vous en êtes et les difficultés que vous rencontrez.",
     ["Quels sont les horaires du Summer Camp ?", "What is the Summer Camp schedule?", "kifech el wa9t fel summer camp"]),
    ("Fama des projets pratiques prévus ?",
     "Oui, kol wehd ykhtar projet w yo93od ykhdem 3lih tout au long summer camp tappliqui fih eli tet3lmou w ysirlk 3lih encadrement w suivi",
     "Y a-t-il des projets pratiques au Summer Camp ?",
     "Oui. Chacun choisit un projet et y travaille tout au long du Summer Camp : vous y appliquez ce que vous apprenez, avec un encadrement et un suivi.",
     ["Est-ce qu'il y a des projets au Summer Camp ?", "Are there practical projects in the Summer Camp?", "fama projet fel summer camp"]),
    ("Kifech najem n9ayed ?",
     "3amer formulaire w otlobna 3le numéro hedha 58 840 064",
     "Comment s'inscrire au Summer Camp ?",
     "Remplissez le formulaire d'inscription et appelez-nous au 58 840 064.",
     ["Comment je peux m'inscrire au Summer Camp ?", "How do I register for the Summer Camp?", "kifech nsajel fel summer camp"]),
    ("Win bch yet3mal Summer Camp",
     "Lac 1 - Tunis\nSahloul - Sousse\nA distance",
     "Où se déroule le Summer Camp ?",
     "Le Summer Camp a lieu au Lac 1 (Tunis), à Sahloul (Sousse), ou à distance.",
     ["Où se passe le Summer Camp ?", "Where does the Summer Camp take place?", "win ya3mlou summer camp"]),
]


def theme_of(entry) -> str | None:
    """The event a taught answer belongs to (its source_key "faq:<theme>:…"), or None."""
    key = getattr(entry, "source_key", None) or ""
    parts = key.split(":")
    return parts[1] if len(parts) >= 3 and parts[0] == "faq" and parts[1] in THEMES else None


def fits(entry, *texts: str) -> bool:
    """Whether a taught answer may answer these words: one of an event only when they name the event."""
    theme = theme_of(entry)
    if theme is None:
        return True
    return any(THEMES[theme]["words"].search(text or "") for text in texts)


def _key(text: str) -> str:
    from app.services.knowledge_service import question_key

    return question_key(text)[:200] or " ".join((text or "").lower().split())[:200]


def save(db: Session, *, question: str, answer: str, post_answer: str = "", post_question: str = "", theme: str | None = None,
         source: str = "admin", wordings: list[str] | None = None, position: int | None = None, item_id: str | None = None) -> FaqItem:
    """One frequent question: its taught answer (for the assistants) and its line in the FAQ."""
    from app.services import knowledge_service

    question = " ".join((question or "").split())[:300]
    answer = (answer or "").strip()
    if len(question) < 3 or len(answer) < 2:
        raise ValueError("Both the question and the answer are required.")
    theme = theme if theme in THEMES else None
    item = db.get(FaqItem, item_id) if item_id else db.query(FaqItem).filter(FaqItem.key == _key(question)).first()
    entry = knowledge_service.save_entry(db, title=question, content=answer, entry_id=item.entry_id if item is not None and item.entry_id else None,
                                         origin="admin")
    for wording in wordings or []:
        knowledge_service.add_wording(entry, wording, sure=True)
    if theme and not (entry.source_key or "").startswith("faq:"):
        entry.source_key = f"faq:{theme}:{entry.id[:8]}"
    elif not theme and (entry.source_key or "").startswith("faq:"):
        entry.source_key = None
    if item is None:
        item = FaqItem(key=_key(question), question=question, source=source)
        db.add(item)
    item.question, item.post_answer, item.entry_id, item.theme = question, (post_answer or "").strip() or None, entry.id, theme
    item.post_question = " ".join((post_question or "").split())[:300] or item.post_question
    item.position = position if position is not None else item.position
    item.is_active, item.updated_at = True, datetime.utcnow()
    db.flush()
    return item


def ensure_seed(db: Session) -> int:
    """The Summer Camp FAQ of the centre's carousel, written in once (an admin's later change stays)."""
    added = 0
    for position, (post_question, post_answer, question, answer, wordings) in enumerate(SUMMER_CAMP, start=1):
        if db.query(FaqItem).filter(FaqItem.key == _key(question)).first() is not None:
            continue
        save(db, question=question, answer=answer, post_answer=post_answer, post_question=post_question, theme="summer_camp", source="post",
             wordings=[post_question] + wordings, position=position)
        added += 1
    if added:
        db.commit()
    return added


# ------------------------------------------------------------------
# What candidates ask most
# ------------------------------------------------------------------

def _asked_of(message) -> str:
    text = " ".join((message.question or "").split())
    if not text:
        body = " ".join((message.body or "").split())
        sentences = [part for part in re.split(r"(?<=[?؟])\s+|\n", body) if "?" in part or "؟" in part]
        text = sentences[0] if sentences else ""
    return text[:300]


def frequent(db: Session, days: int = ASKED_DAYS, most: int = KEPT) -> list[dict]:
    """The questions asked most in the last days, grouped by meaning of their words: [{"question", "count", "last",
    "entry": the taught answer that answers them or None}]."""
    from app.models.candidate_email import CandidateEmail
    from app.services import knowledge_service

    since = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(CandidateEmail).filter(CandidateEmail.direction == "inbound", CandidateEmail.received_at >= since)
            .order_by(CandidateEmail.received_at.desc()).limit(2000).all())
    groups: list[dict] = []
    for row in rows:
        asked = _asked_of(row)
        tokens = knowledge_service.tokenize(asked)
        if len(tokens) < 2:
            continue
        home = next((group for group in groups if knowledge_service.sameness(tokens, group["tokens"]) >= 0.5), None)
        if home is None:
            home = {"tokens": tokens, "wordings": Counter(), "count": 0, "last": row.received_at}
            groups.append(home)
        home["wordings"][asked] += 1
        home["count"] += 1
        home["last"] = max(home["last"], row.received_at) if home["last"] else row.received_at
    groups.sort(key=lambda group: -group["count"])
    found = []
    for group in groups[:most]:
        if group["count"] < 2:
            break
        question = group["wordings"].most_common(1)[0][0]
        entry = knowledge_service.same_question(db, question)
        found.append({"question": question, "count": group["count"], "last": group["last"], "entry": entry})
    return found


def refresh(db: Session) -> dict:
    """Kept up to date by itself (every 30 minutes): the counts of every frequent question, and new frequent ones."""
    ensure_seed(db)
    counted = frequent(db)
    known = {item.entry_id: item for item in db.query(FaqItem).filter(FaqItem.entry_id.isnot(None)).all()}
    by_key = {item.key: item for item in db.query(FaqItem).all()}
    added = 0
    for item in by_key.values():
        if item.source == "messages":
            item.asked = 0
    for found in counted:
        item = (known.get(found["entry"].id) if found["entry"] is not None else None) or by_key.get(_key(found["question"]))
        if item is None:
            item = FaqItem(key=_key(found["question"]), question=found["question"], source="messages",
                           entry_id=found["entry"].id if found["entry"] is not None else None)
            db.add(item)
            by_key[item.key] = item
            added += 1
        item.asked, item.last_asked = found["count"], found["last"]
        if item.entry_id is None and found["entry"] is not None:
            item.entry_id = found["entry"].id
        item.updated_at = datetime.utcnow()
    db.commit()
    return {"counted": len(counted), "added": added}


def listing(db: Session) -> list[dict]:
    """Every frequent question for the page: the most asked first, then the posts' order."""
    from app.models.knowledge import KnowledgeEntry

    rows = db.query(FaqItem).filter(FaqItem.is_active.is_(True)).all()
    entries = {entry.id: entry for entry in db.query(KnowledgeEntry).filter(KnowledgeEntry.id.in_([row.entry_id for row in rows if row.entry_id])).all()}
    found = []
    for row in rows:
        entry = entries.get(row.entry_id)
        found.append({"item": row, "answer": entry.content if entry is not None and entry.is_active else None,
                      "theme": THEMES[row.theme]["name"] if row.theme in THEMES else None})
    found.sort(key=lambda item: (-(item["item"].asked or 0), item["item"].theme or "", item["item"].position or 99, item["item"].question))
    return found


def remove(db: Session, item_id: str) -> None:
    from app.models.knowledge import KnowledgeEntry

    item = db.get(FaqItem, item_id)
    if item is None:
        return
    entry = db.get(KnowledgeEntry, item.entry_id) if item.entry_id else None
    if entry is not None and (entry.source_key or "").startswith("faq:"):
        entry.is_active = False                  # a question of a post goes with its answer; a taught one stays taught
    db.delete(item)
    db.commit()


# ------------------------------------------------------------------
# Reading a carousel's pictures
# ------------------------------------------------------------------

def _kind(colour: tuple[int, int, int]) -> str:
    """What stands behind a line of a slide: the pink of an answer, the orange bar, or the white card / the wall."""
    red, green, blue = colour
    if red > 200 and red - green >= 12 and blue - green >= 4:
        return "answer"
    if red > 215 and 140 <= green <= 205 and blue < 140:
        return "header"
    return "plain"


def read_slide(data: bytes) -> dict | None:
    """One slide of an FAQ carousel: {"question", "answer"}, or None when it has no question card (the last slide)."""
    from PIL import Image, ImageOps
    import numpy

    from app.services import proof_reader

    picture = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    if max(picture.size) > 1600:
        picture.thumbnail((1600, 1600))
    pixels = numpy.array(picture)
    result, _ = proof_reader._engine()(pixels[:, :, ::-1])
    pieces = []
    for box, text, score in result or []:
        if score < 0.5 or not text.strip():
            continue
        xs, ys = [int(point[0]) for point in box], [int(point[1]) for point in box]
        left, right, top, bottom = max(0, min(xs)), min(pixels.shape[1], max(xs)), max(0, min(ys)), min(pixels.shape[0], max(ys))
        patch = pixels[top:bottom, left:right].reshape(-1, 3)
        light = patch[patch.sum(axis=1) > 450]            # the background, not the letters
        colour = tuple(int(value) for value in numpy.median(light, axis=0)) if len(light) else (255, 255, 255)
        pieces.append({"text": text.strip(), "top": top, "kind": _kind(colour)})
    pieces.sort(key=lambda piece: piece["top"])
    header = [piece for piece in pieces if piece["kind"] == "header"]
    answers = [piece for piece in pieces if piece["kind"] == "answer"]
    if not header or not answers:
        return None
    below, first_answer = max(piece["top"] for piece in header), min(piece["top"] for piece in answers)
    question = [piece["text"] for piece in pieces if piece["kind"] == "plain" and below < piece["top"] < first_answer]
    answer = [piece["text"] for piece in answers if not re.fullmatch(r"[\d\s+().-]{6,}", piece["text"])]
    if not question or not answer:
        return None
    return {"question": " ".join(question), "answer": "\n".join(answer)}


def read_slides(pictures: list[bytes]) -> list[dict]:
    """Every slide that holds a question and its answer, in the carousel's order."""
    found = []
    for position, data in enumerate(pictures, start=1):
        try:
            slide = read_slide(data)
        except Exception:
            slide = None
        if slide is not None:
            found.append({**slide, "position": position})
    return found

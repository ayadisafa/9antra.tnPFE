"""The knowledge agent (orchestrator.py): when no other agent has the answer, the closest passages of everything the
application knows are found (what was taught, the programs and their files, 9antra.tn, the guide, and for the admin the
journal's articles), and the sentences that answer are quoted with the number of their passage: [1], [2].

Retrieval, then the answer from what was retrieved: the local model, when it is there, only words the answer from the
numbered passages (chatbot_sources.grounded); without it, the sentences themselves are given. Nothing is said that is
not in a passage, and a question nothing answers gets no answer.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

FLOOR = 0.3            # a passage of the knowledge below this quality is not read
NEWS_DAYS = 60
MOST = 4               # passages quoted at most
_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")


def passages(db: Session, message: str, *, for_admin: bool = True, limit: int = MOST) -> list[dict]:
    """The closest passages, best first: {"n", "title", "text", "url", "kind", "id"}."""
    from app.models.knowledge import KnowledgeEntry
    from app.services import chatbot_sources, knowledge_service

    found: list[dict] = []
    try:
        hits = knowledge_service.search(db, message, limit=limit + 2)
    except Exception:
        hits = []
    for hit in hits:
        if (hit.get("quality") or 0) < FLOOR:
            continue
        entry = db.get(KnowledgeEntry, hit["id"])
        if entry is None or not (entry.content or "").strip():
            continue
        source = chatbot_sources.of_entry(db, entry)
        found.append({"title": entry.title or source.get("label") or "", "text": entry.content.strip(), "url": source.get("url"),
                      "label": source.get("label") or entry.title, "kind": entry.kind, "id": entry.id, "quality": hit["quality"]})
    if for_admin:
        found += _news(db, message)
    found.sort(key=lambda item: -item["quality"])
    found = found[:limit]
    for number, item in enumerate(found, start=1):
        item["n"] = number
    return found


def _news(db: Session, message: str, most: int = 2) -> list[dict]:
    """The journal's articles that share the question's words (the journal is the admin's)."""
    from app.models.news_item import NewsItem
    from app.services import knowledge_service

    asked = knowledge_service.tokenize(message)
    if len(asked) < 1:
        return []
    since = datetime.utcnow() - timedelta(days=NEWS_DAYS)
    scored = []
    for row in db.query(NewsItem).filter(NewsItem.fetched_at >= since).all():
        words = knowledge_service.tokenize(f"{row.title} {row.summary or ''} {' '.join(row.term_list())}")
        shared = asked & words
        if len(shared) >= 2 or (len(asked) == 1 and shared):
            scored.append((len(shared) / max(1, len(asked)), row))
    scored.sort(key=lambda pair: -pair[0])
    return [{"title": row.title, "text": f"{row.title}. {row.summary or ''}".strip(), "url": row.url, "label": f"{row.source}: {row.title[:80]}",
             "kind": "news", "id": row.id, "quality": round(0.3 + 0.5 * share, 3)} for share, row in scored[:most]]


def _best_sentences(message: str, text: str, most: int = 2) -> list[str]:
    from app.services import knowledge_service

    asked = knowledge_service.tokenize(message)
    sentences = [part.strip(" -•\t") for part in _SENTENCE.split(text or "") if len(part.strip()) >= 12]
    scored = []
    for index, sentence in enumerate(sentences):
        shared = asked & knowledge_service.tokenize(sentence)
        if shared:
            scored.append((len(shared), -index, sentence))
    scored.sort(reverse=True)
    chosen = sorted(scored[:most], key=lambda item: -item[1])
    return [sentence if len(sentence) <= 320 else sentence[:320].rsplit(" ", 1)[0] + "…" for _, _, sentence in chosen]


def answer(db: Session, message: str, *, lang: str | None = None, for_admin: bool = True) -> dict | None:
    """The answer quoted from the passages, or None when no passage answers."""
    from app.services import howto
    from app.services.ui_words import current, t as _t, use as _use_language

    found = passages(db, message, for_admin=for_admin)
    if not found:
        return None
    spoken = lang if lang in ("en", "fr", "ar", "tn") else howto.language_of(message)
    token = _use_language("fr" if spoken in ("fr", "tn") else spoken)
    try:
        lines = []
        for item in found:
            for sentence in _best_sentences(message, item["text"]):
                lines.append(f"{sentence} [{item['n']}]")
        if not lines:
            return None
        used = sorted({int(number) for number in re.findall(r"\[(\d+)\]$", "\n".join(lines), re.MULTILINE)})
        sources = [{"label": f"[{item['n']}] {item['label']}", "url": item.get("url")} for item in found if item["n"] in used]
        sources.append({"label": _t("Quoted as written: no sentence was added")})
        reply = _t("Here is what the application knows about it, quoted from its sources:") + "\n" + "\n".join(lines[:6])
        return {"reply": reply, "links": [], "source": "knowledge", "sources": sources, "resources": [],
                "passages": [{"n": item["n"], "title": item["title"], "kind": item["kind"]} for item in found]}
    finally:
        current.reset(token)

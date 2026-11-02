"""Talking with the email assistant about an answer it sent: why it answered that way, and putting it right.

"Why?": where the answer came from (the candidate's records, an answer you taught, a program's file, the facts every
assistant knows, what it knows about the programs), how sure it was against the confidence you asked for, and so why
it went alone. "The right answer is …": the right answer is kept for the next time this question comes (every
assistant uses it), the answer is marked wrong, and "send it" sends the corrected answer to the candidate. "It was
right": marked right. The admin's words are read with word lists, never by the local model.
"""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.services.ui_words import t as _t

_WHY = re.compile(r"\b(why|pourquoi|pk|pq|how come|comment ça|explain|explique\w*|reason|raison|source)\b|علاش|لماذا|\b3l[ae]?ch\b", re.I)
_RIGHT = re.compile(r"^\s*(it was right|right|correct|good answer|bonne r[ée]ponse|c'?est (?:juste|bon|correct|parfait)|c'?[ée]tait (?:juste|bon|correct|bien)|"
                    r"parfait|juste|bravo|s7i7a?)\b|^\s*(كان صحيحا|صحيح)", re.I)
_WRONG = re.compile(r"\b(wrong|faux|fausse|mauvaise|incorrect\w*|not right|pas (?:juste|bon|correct)|ghalet|غلط|خطأ)\b", re.I)
_CORRECT = re.compile(r"(?:the right answer is|right answer:|the answer is|la bonne r[ée]ponse (?:est|c'est)|la r[ée]ponse (?:est|c'est)|corrige(?:-la|-le)?\s*:?|"
                      r"correct(?: it)?\s*:|r[ée]ponds plut[ôo]t|answer instead|plut[ôo]t\s*:|dis(?:-lui)? plut[ôo]t|say instead|il fallait (?:dire|r[ée]pondre)|"
                      r"الإجابة الصحيحة هي|الجواب الصحيح هو|الرد الصحيح هو)\s*:?\s*(.+)$",
                      re.I | re.S)
_SEND = re.compile(r"^\s*(send it|send|envoie(?:-la|-le)?|l'envoyer|envoyer|envoie la correction|send the correction|ab3ath)\b|^\s*(ابعث|أرسل|إرسالها|ارسلها)", re.I)


def question_of(db: Session, reply):
    """The candidate's message this answer was for: the last one before it in the discussion."""
    from app.models.candidate_email import CandidateEmail

    return (db.query(CandidateEmail).filter(CandidateEmail.thread_id == reply.thread_id, CandidateEmail.direction == "inbound",
                                            CandidateEmail.received_at <= reply.received_at)
            .order_by(CandidateEmail.received_at.desc()).first())


_METHODS = {
    "record": "from the candidate's own records (payments, class, sessions)",
    "taught": "from an answer you taught",
    "document": "from a program's file",
    "facts": "from the facts every assistant knows",
    "register": "from the registration form and its link",
    "material": "from the course files of their class",
    "search": "from what the assistant knows about the programs and the centre",
}


def why(db: Session, reply) -> str:
    """Why it answered that way, in a few sentences."""
    from app.models.knowledge import KnowledgeEntry

    asked = question_of(db, reply)
    try:
        sources = json.loads((asked.sources if asked is not None else None) or "{}")
    except ValueError:
        sources = {}
    lines = []
    if asked is not None:
        lines.append(_t("The question it read: “{0}”.", (asked.question or asked.body or "")[:200]))
    method = sources.get("method") or ("search" if sources.get("hits") else None)
    if method:
        lines.append(_t("It answered {0}.", _t(_METHODS.get(method, "from what it knows"))))
    taught = db.get(KnowledgeEntry, sources["taught_id"]) if sources.get("taught_id") else None
    if taught is not None:
        lines.append(_t("The answer you taught for “{0}”{1}.", taught.title[:120], _t(", written afresh for this candidate") if sources.get("rewritten") else ""))
    titles = [hit.get("title") for hit in sources.get("hits") or [] if hit.get("title")][:3]
    if titles and not taught:
        lines.append(_t("What it used: {0}.", "; ".join(f"“{title[:80]}”" for title in titles)))
    if reply.confidence is not None:
        threshold = sources.get("threshold")
        if threshold:
            lines.append(_t("It was {0}% sure; you asked for {1}% to answer alone, so it went by itself.", round(reply.confidence * 100), round(float(threshold) * 100)))
        else:
            lines.append(_t("It was {0}% sure.", round(reply.confidence * 100)))
    if sources.get("unsupported"):
        lines.append(_t("Not found in what it knows: {0}.", ", ".join(sources["unsupported"][:4])))
    if not lines:
        lines.append(_t("This answer has no record of how it was found (it may have been written before this was kept)."))
    lines.append(_t("If it was wrong, write “the right answer is …”: it is kept for next time, and “send it” sends it."))
    return " ".join(lines)


def talk(db: Session, reply, text: str, *, by: str | None = None) -> dict:
    """{"reply": what the assistant says, "changed": bool, "reload": bool}, after doing what was asked."""
    from app.models.email_thread import EmailThread
    from app.services import email_assistant, email_threads, knowledge_service

    said = (text or "").strip()
    name = (getattr(reply.candidate, "full_name", None) or "").split(" ")[0] or _t("the candidate")
    corrected = _CORRECT.search(said)
    if corrected and len(corrected.group(1).strip()) >= 3:
        right = corrected.group(1).strip()
        asked = question_of(db, reply)
        question = (asked.question or email_threads.snippet(asked.body, 240)) if asked is not None else ""
        if not question:
            return {"reply": _t("The question this answer was for could not be found."), "changed": False, "reload": False}
        knowledge_service.save_entry(db, title=question, content=email_assistant.clean_answer(right) or right, origin="learned",
                                     source_email_id=asked.id)
        reply.admin_score = -1
        reply.suggested_reply = right
        db.commit()
        return {"reply": _t("Kept: from now on this question gets your answer, by every assistant. Say “send it” to send it to {0} now.", name),
                "changed": True, "reload": False}
    if _SEND.search(said):
        if not reply.suggested_reply:
            return {"reply": _t("Write the right answer first: “the right answer is …”."), "changed": False, "reload": False}
        thread = db.get(EmailThread, reply.thread_id)
        try:
            email_assistant.admin_reply(db, thread, reply.suggested_reply)
        except ValueError as problem:
            db.rollback()
            return {"reply": _t("It could not be sent: {0}", problem), "changed": False, "reload": False}
        reply.suggested_reply = None
        db.commit()
        return {"reply": _t("Sent to {0}.", name), "changed": True, "reload": True}
    if _RIGHT.search(said) and not _WRONG.search(said):
        reply.admin_score = 1
        db.commit()
        return {"reply": _t("Noted: this answer is right, and counts in the assistant's results."), "changed": True, "reload": False}
    if _WRONG.search(said):
        reply.admin_score = -1
        db.commit()
        return {"reply": _t("Noted as wrong. Tell me the right answer: “the right answer is …”; it is kept for next time."), "changed": True, "reload": False}
    if _WHY.search(said) or not said:
        return {"reply": why(db, reply), "changed": False, "reload": False}
    return {"reply": _t("Ask “why?”, say “it was right”, or write “the right answer is …” (then “send it”)."), "changed": False, "reload": False}


# For Yasmine: "pourquoi tu as répondu ça à Fares ?", "corrige ta réponse à Amira : …"
_ABOUT = re.compile(r"(why|pourquoi|علاش|3lech).{0,40}(answer\w*|repl\w*|r[ée]pon\w*|jawe?b\w*|رد)|(corrige|correct)\w*.{0,25}(r[ée]ponse|answer)", re.I)


def wants(text: str) -> bool:
    return bool(_ABOUT.search(text or "")) and not re.search(r"set aside|mis de c[ôo]t[ée]|sans r[ée]ponse", text or "", re.I)


def latest_reply(db: Session, text: str):
    """The assistant's latest answer, to the person named in the words if one is."""
    from app.models.candidate_email import CandidateEmail

    replies = (db.query(CandidateEmail).filter(CandidateEmail.direction == "outbound", CandidateEmail.author == "system",
                                               CandidateEmail.intent == "reply").order_by(CandidateEmail.received_at.desc()).limit(60).all())
    words = {word for word in re.findall(r"[^\W\d_]{3,}", (text or "").lower())}
    for reply in replies:
        names = {part for part in (getattr(reply.candidate, "full_name", "") or "").lower().split() if len(part) >= 3}
        if names & words:
            return reply
    return replies[0] if replies else None


def for_chat(db: Session, text: str, *, user=None, lang: str | None = None) -> dict:
    from app.services import howto, ui_words

    spoken = lang if lang in ("en", "fr", "ar", "tn") else howto.language_of(text)
    token = ui_words.use({"tn": "fr"}.get(spoken, spoken))
    try:
        reply = latest_reply(db, text)
        if reply is None:
            answer = {"reply": _t("The assistant has not answered anyone by itself yet.")}
        else:
            answer = talk(db, reply, text, by=getattr(user, "email", None))
            who = getattr(reply.candidate, "full_name", "") or ""
            answer["reply"] = _t("About its answer to {0}:", who) + " " + answer["reply"]
    finally:
        ui_words.current.reset(token)
    url = f"/inbox?thread={reply.thread_id}" if reply is not None else "/inbox"
    return {"reply": answer["reply"], "links": [{"label": "Open the discussion", "url": url}], "source": "system",
            "sources": [{"label": _t("The inbox"), "url": url}], "resources": []}

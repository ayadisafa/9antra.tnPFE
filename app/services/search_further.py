"""When the email assistant does not know: it looks further before giving up, tells the admin what it found and where
(or what is missing, and where to write it), and keeps looking. A question that waits is read again as soon as what it
lacked appears: a detail written in Settings › Your centre, something taught, or found by the regular look (every 30
minutes, with the knowledge).

Where it looks, on this machine only: the centre's details in Settings, what the team taught (answers, notes, files,
the website) with a lower bar than for answering alone, what the admin wrote to other candidates about the same thing,
and the admin's email templates. What is found here only reaches the admin: into a draft by their hand ("Add to the
draft"); once it is in Settings or taught, the reader uses it with its own checks. Nothing found here is sent alone.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.services import knowledge_service
from app.services.ui_words import t as _t

CENTRE = "Settings › Your centre"
TAUGHT = "What you taught"
ANSWERS = "Your answers to other candidates"
TEMPLATES = "Your email templates"
LOOKED = [CENTRE, TAUGHT, ANSWERS, TEMPLATES]
LEAD = 0.40               # a lead for the admin's eyes (answering alone needs email_assistant.KNOWS_FROM)
AGAIN_EVERY = timedelta(minutes=25)

# The centre's details, by the words a question uses for them.
_CENTRE = [
    ("centre_ribs", "The centre's RIB", r"\b(rib|iban|virement|transfer|bancaire|bank|banque|compte bancaire|bank account|versement)\b"),
    ("centre_beneficiaries", "Who cheques are made out to", r"\b(cheque|check|a l ordre|beneficiaire|beneficiary)\b"),
    ("centre_phone", "The centre's phone", r"\b(telephone|phone|numero de telephone|phone number|appeler|call you|joindre)\b"),
    ("centre_whatsapp", "The centre's WhatsApp", r"\bwhats ?app\b"),
    ("centre_address", "The centre's address", r"\b(adresse|address|localisation|location|located|situe|sur place|onsite|on site|where are you|ou etes vous|ou se trouve)\b"),
    ("centre_email", "The centre's email", r"\b(votre (adresse )?e ?mail|your e ?mail|email address|adresse mail|contact email)\b"),
    ("centre_website", "The centre's website", r"\b(site web|site internet|website)\b"),
    ("registration_form_url", "The registration form", r"\b(inscri\w*|register\w*|registration|formulaire)\b"),
]
_LABELS = {key: label for key, label, _ in _CENTRE}


def _plain(text: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", knowledge_service._fold(text or "")))


def _centre_value(db: Session, settings, key: str) -> str | None:
    if key == "registration_form_url":
        from app.services import registration_form

        return registration_form.url(db) or None
    if key == "centre_address":
        from app.services import prospects

        return (settings.centre_address or "").strip() or prospects._places(db) or None
    return (getattr(settings, key, None) or "").strip() or None


def _centre(db: Session, ask: str) -> tuple[list[dict], list[str]]:
    """The centre's details the point is about: found (with their value), or empty (to write in Settings)."""
    from app.services.settings_service import get_settings

    settings, plain = get_settings(db), _plain(ask)
    found, empty = [], []
    for key, label, pattern in _CENTRE:
        if not re.search(pattern, plain):
            continue
        value = _centre_value(db, settings, key)
        if value:
            found.append({"place": CENTRE, "where": label, "text": f"{label}: {value}", "insert": value, "link": "/settings#centre"})
        else:
            empty.append(key)
    return found, empty


def _taught(db: Session, ask: str, question: str) -> list[dict]:
    """What the team taught or wrote (answers, notes, files, the website), with a lower bar: the admin judges."""
    leads = []
    for hit in knowledge_service.search(db, ask, questions=[item for item in (ask, question) if item], limit=6):
        # What the team taught or added (answers, notes, course and program files); the application's own facts about
        # programs and how things go only when close. Never its help about its pages, its team or its settings.
        mine = hit["origin"] != "system" or hit["kind"] in ("fact", "course", "program_file", "website")
        if not mine and hit["kind"] not in ("program", "curriculum", "process"):
            continue
        if hit.get("quality", 0) < (LEAD if mine else 0.6):
            continue
        text = " ".join((hit["content"] or "").split())[:420]
        leads.append({"place": TAUGHT, "where": hit["title"], "text": text, "insert": text, "link": "/inbox/knowledge",
                      "sure": hit.get("quality", 0) >= 0.5})
    return leads[:2]


def _part_about(text: str, asked: set[str]) -> str | None:
    """The paragraph of a message that carries the point (its words), without greeting or signature."""
    best, best_share = None, 0.0
    for part in re.split(r"\n\s*\n", text or ""):
        part = " ".join(part.split())
        tokens = knowledge_service._core(knowledge_service.tokenize(part))
        if not part or not tokens:
            continue
        share = len(asked & tokens) / len(asked)
        if share > best_share:
            best, best_share = part, share
    return best[:420] if best is not None and best_share >= 0.6 else None


def _strong(ask: str) -> set[str]:
    return knowledge_service._core(knowledge_service.tokenize(ask)) - {"envoyer", "send", "donner", "give", "savoir", "know", "detail"}


def _answers(db: Session, ask: str, skip_thread_id: str | None) -> list[dict]:
    """What the admin wrote to other candidates about the same thing: the question they answered alike, or their
    answer carrying every strong word of the point."""
    from app.models.candidate_email import CandidateEmail

    asked, strong = knowledge_service.tokenize(ask), _strong(ask)
    if not strong:
        return []
    rows = (db.query(CandidateEmail).filter(CandidateEmail.direction == "outbound", CandidateEmail.author == "admin")
            .order_by(CandidateEmail.received_at.desc()).limit(300).all())
    rows = [row for row in rows if row.thread_id != skip_thread_id]
    threads = {row.thread_id for row in rows if row.thread_id}
    questions: dict[str, list] = {}
    if threads:
        for row in (db.query(CandidateEmail).filter(CandidateEmail.direction == "inbound", CandidateEmail.thread_id.in_(threads))
                    .order_by(CandidateEmail.received_at.asc()).all()):
            questions.setdefault(row.thread_id, []).append(row)
    leads = []
    for row in rows:
        part = _part_about(row.body, strong)          # the answer carries the point's words
        if part is None:
            continue
        before = [item for item in questions.get(row.thread_id, []) if item.received_at and row.received_at and item.received_at <= row.received_at]
        asked_then = (before[-1].question or before[-1].body) if before else ""
        name = row.candidate.full_name if row.candidate is not None else ""
        when = row.received_at.strftime("%d/%m/%Y") if row.received_at else ""
        leads.append({"place": ANSWERS, "where": " · ".join(item for item in (name, when) if item), "text": part, "insert": part,
                      "link": f"/inbox?thread={row.thread_id}",
                      "alike": knowledge_service.sameness(asked, knowledge_service.tokenize(asked_then))})
    # The answers to a question asked alike first, then the most recent.
    leads.sort(key=lambda lead: lead["alike"] >= knowledge_service.ALIKE_QUESTION, reverse=True)
    return leads[:2]


def _templates(db: Session, ask: str) -> list[dict]:
    from app.models.email_template import EmailTemplate

    strong = _strong(ask)
    if not strong:
        return []
    leads = []
    for template in db.query(EmailTemplate).all():
        part = _part_about(f"{template.body or ''}\n\n{template.closing or ''}", strong)
        if part and "{" not in part:              # a sentence with {variables} is filled per candidate: not a fact
            leads.append({"place": TEMPLATES, "where": template.name, "text": part, "insert": part, "link": "/inbox/templates"})
    return leads[:1]


def look(db: Session, asks: list[str], *, question: str = "", skip_thread_id: str | None = None) -> dict:
    """Each point the assistant could not answer, looked for in every place; what was found, and where; what was not."""
    asks = [" ".join(str(ask).split())[:200] for ask in asks if str(ask or "").strip()][:4]
    found, missing, empty = [], [], []
    for ask in asks:
        centre, blank = _centre(db, ask)
        empty += [key for key in blank if key not in empty]
        leads = centre + _taught(db, ask, question) + _answers(db, ask, skip_thread_id) + _templates(db, ask)
        if leads:
            found += [dict(lead, ask=ask) for lead in leads[:3]]
        else:
            missing.append({"ask": ask})
    return {"asks": asks, "found": found, "missing": missing, "empty": empty,
            "empty_labels": [_LABELS[key] for key in empty], "looked": LOOKED,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def told(searched: dict) -> str:
    """One line for the admin's notification: what the further look gave."""
    if searched["missing"]:
        return _t("Not found anywhere: {0}. Tell me under the draft and I answer.",
                  "; ".join(f"“{item['ask']}”" for item in searched["missing"][:2]))
    return _t("I looked further and found {0} lead(s): they are under the draft, to add in one tap.", len(searched["found"]))


def _points_of(inbound) -> list[str]:
    """What a question that waited before the further look existed could not be answered on, from why it waits."""
    reason = inbound.escalation_reason or ""
    if "not in the application's facts" in reason:
        return re.findall(r"“([^”]+)”", reason)[:4]
    if reason.startswith("Confidence ") or "no point of it could be checked" in reason:
        return [inbound.question] if inbound.question else []
    return []


def _key(lead: dict) -> str:
    return f"{lead.get('place')}|{lead.get('where')}|{lead.get('text', '')[:80]}"


def again(db: Session, *, force: bool = False, skip_thread_id: str | None = None) -> int:
    """The questions still waiting with something missing, looked for again. When what they lacked appears (a detail
    now written in Settings, something now taught well enough), they are read again: answered alone if every point
    is now checked, or a new draft for the admin. A new lead elsewhere: the admin is told. Returns how many were read
    again."""
    from app.models.email_thread import EmailThread
    from app.models.notification import Notification
    from app.services import email_assistant, email_threads

    now = datetime.now(timezone.utc)
    read_again = 0
    for thread in db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).all():
        if thread.id == skip_thread_id:
            continue
        inbound = email_assistant.latest_inbound(db, thread)
        if inbound is None or not inbound.requires_admin:
            continue
        try:
            sources = json.loads(inbound.sources) if inbound.sources else {}
        except ValueError:
            continue
        if not isinstance(sources, dict):
            continue
        before = sources.get("searched")
        if before is None:
            # A question that waited before the further look existed: looked for once, what it found shown to the admin.
            points = _points_of(inbound)
            if points:
                sources["searched"] = look(db, points, question=inbound.question or "", skip_thread_id=thread.id)
                inbound.sources = json.dumps(sources, ensure_ascii=False)
                db.commit()
            continue
        if not (before.get("missing") or before.get("empty")):
            continue
        try:
            last = datetime.fromisoformat(before.get("at") or "")
        except ValueError:
            last = None
        if not force and last is not None and now - last < AGAIN_EVERY:
            continue
        searched = look(db, before.get("asks") or [], question=inbound.question or "", skip_thread_id=thread.id)
        known = {_key(lead) for lead in before.get("found") or []}
        new = [lead for lead in searched["found"] if _key(lead) not in known]
        filled = [key for key in before.get("empty") or [] if key not in searched["empty"]]
        sources["searched"] = searched
        inbound.sources = json.dumps(sources, ensure_ascii=False)
        if filled or any(lead.get("sure") for lead in new):
            db.commit()
            if email_assistant.rerun(db, thread):
                read_again += 1
            continue
        if new:
            name = inbound.candidate.full_name if inbound.candidate is not None else "A candidate"
            db.add(Notification(recipient_role="admin", category="EMAIL_QUESTION", title=_t("Found something for {0}'s question", name),
                                message=email_threads.snippet(f"In {new[0]['place']} ({new[0]['where']}): {new[0]['text']}", 220),
                                action_url=f"/inbox?thread={thread.id}", candidate_id=thread.candidate_id))
        db.commit()
    return read_again

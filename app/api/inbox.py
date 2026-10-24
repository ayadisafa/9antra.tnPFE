"""Inbox: discussions with candidates, the questions waiting for the admin,
personalized emails, the email templates and the assistant's knowledge base."""
import dataclasses
import json
import os
import time
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import exists, or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.candidate import Candidate
from app.models.candidate_email import CandidateEmail
from app.models.classgroup import ClassGroup
from app.models.email_file import EmailFile
from app.models.email_template import EmailTemplate
from app.models.email_thread import EmailThread
from app.models.knowledge import KnowledgeEntry
from app.models.program import Program
from app.models.registration import Registration, RegistrationStatus
from app.services import (
    channels,
    email_assistant,
    email_composer,
    email_files,
    email_templates,
    email_threads,
    knowledge_service,
    mailbox_service,
    reply_writer,
)
from app.services import document_pdf, program_pdf
from app.services.background import run_in_background
from app.services.email_layout import STYLES
from app.services.email_threads import utcnow
from app.services.schedule_utils import utc_to_local
from app.services.settings_service import get_settings
from app.template_context import notification_context

router = APIRouter(prefix="/inbox", tags=["Inbox"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])
templates.env.filters["fromjson"] = lambda value: json.loads(value) if value else {}

FILTERS = {
    "todo": "Needs your answer",
    "conversations": "Conversations",
    "auto": "Answered by the system",
    "whatsapp": "WhatsApp",
    "sms": "SMS",
    "to_send": "To send from your phone",
    "sent": "Sent by 9antra",
    "all": "All",
}
PAGE_SIZE = 60
RECENT_MESSAGES = 12

# What each kind of outgoing email is called in the discussions.
KIND_LABELS = {
    **{key: spec.name for key, spec in email_templates.AUTO_EMAILS.items()},
    "reply": "Reply",
    "personal": "Personalized email",
    "payment": "Payment email",
    "quiz": "Quiz email",
    "notice": "Email",
}


def _local(value, fmt: str = "%d/%m/%Y %H:%M") -> str:
    local = utc_to_local(value)
    return local.strftime(fmt) if local else ""


def _ago(value) -> str:
    if value is None:
        return ""
    seconds = max(0, (utcnow() - value).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)} d ago"
    return _local(value, "%d/%m/%Y")


templates.env.filters["localdt"] = _local
templates.env.filters["ago"] = _ago


def _redirect(path: str, notice: str | None = None, error: str | None = None, **params) -> RedirectResponse:
    query = {key: value for key, value in params.items() if value}
    if notice:
        query["notice"] = notice
    if error:
        query["error"] = error
    separator = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{separator + urlencode(query) if query else ''}", status_code=303)


def _thread_or_404(db: Session, thread_id: str) -> EmailThread:
    thread = db.get(EmailThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Discussion not found")
    return thread


_model_check = {"at": 0.0, "ok": False}


def _model_ready() -> bool:
    """Whether the local model answers; checked at most once a minute."""
    if time.monotonic() - _model_check["at"] > 60:
        _model_check.update(at=time.monotonic(), ok=email_assistant.model_available())
    return _model_check["ok"]


def _parse_sources(message: CandidateEmail) -> dict:
    try:
        data = json.loads(message.sources) if message.sources else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------------
# Inbox
# ------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def inbox_page(request: Request, db: Session = Depends(get_db)):
    params = request.query_params
    waiting = db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).count()
    current = params.get("filter") or ("todo" if waiting else "conversations")
    if current not in FILTERS:
        current = "conversations"
    search = (params.get("q") or "").strip()
    candidate_id = params.get("candidate") or None

    query = db.query(EmailThread).options(joinedload(EmailThread.candidate))
    if candidate_id:
        query = query.filter(EmailThread.candidate_id == candidate_id)
        current = "all" if not params.get("filter") else current
    if current == "todo":
        query = query.filter(EmailThread.needs_admin.is_(True))
    elif current == "conversations":
        query = query.filter(EmailThread.inbound_count > 0)
    elif current == "sent":
        query = query.filter(EmailThread.inbound_count == 0)
    elif current == "whatsapp":
        query = query.filter(EmailThread.channel == "whatsapp")
    elif current == "sms":
        query = query.filter(EmailThread.channel == "sms")         # what candidates send by SMS, and the answers
    elif current == "to_send":
        query = query.filter(exists().where(
            CandidateEmail.thread_id == EmailThread.id, CandidateEmail.channel.in_(("whatsapp", "sms")), CandidateEmail.status == "to_send",
        ))
    elif current == "auto":
        query = query.filter(exists().where(
            CandidateEmail.thread_id == EmailThread.id,
            CandidateEmail.author == "system",
            CandidateEmail.intent == "reply",
        ))
    if search:
        like = f"%{search}%"
        query = query.join(Candidate, Candidate.id == EmailThread.candidate_id).filter(or_(
            Candidate.full_name.ilike(like), Candidate.email.ilike(like), EmailThread.subject.ilike(like),
        ))
    threads = (
        query.order_by(EmailThread.needs_admin.desc(), EmailThread.last_message_at.desc())
        .limit(PAGE_SIZE)
        .all()
    )

    selected = None
    selected_id = params.get("thread")
    if selected_id:
        selected = db.get(EmailThread, selected_id)
    elif threads:
        selected = threads[0]
    if selected is not None and selected_id and selected.unread:
        selected.unread = False
        db.commit()

    conversation = []
    pending = None
    registrations = []
    # "Everything": every email exchanged with this candidate, whatever its subject.
    whole_history = params.get("view") == "all" and selected is not None
    history_count = 0
    if selected is not None:
        with_candidate = db.query(CandidateEmail).filter(
            CandidateEmail.candidate_id == selected.candidate_id, CandidateEmail.thread_id.isnot(None),
        )
        history_count = with_candidate.count()
        if whole_history:
            messages = with_candidate.order_by(CandidateEmail.received_at.asc()).all()
            subjects = dict(
                db.query(EmailThread.id, EmailThread.subject)
                .filter(EmailThread.candidate_id == selected.candidate_id).all()
            )
        else:
            messages, subjects = selected.messages, {}
        previous_thread = None
        for message in messages:
            conversation.append({
                "message": message,
                "sources": _parse_sources(message),
                "files": email_threads.message_files(message),
                # Shown where the subject changes, when several discussions are read together.
                "subject": (subjects.get(message.thread_id) or "No subject")
                if whole_history and message.thread_id != previous_thread else None,
            })
            previous_thread = message.thread_id
            if message.thread_id == selected.id and message.direction == "inbound" and message.requires_admin:
                pending = message
        registrations = [
            item for item in selected.candidate.registrations
            if item.status != RegistrationStatus.CANCELLED
        ][:4]

    counts = {
        "todo": waiting,
        "conversations": db.query(EmailThread).filter(EmailThread.inbound_count > 0).count(),
        "sent": db.query(EmailThread).filter(EmailThread.inbound_count == 0).count(),
        "all": db.query(EmailThread).count(),
        "whatsapp": db.query(EmailThread).filter(EmailThread.channel == "whatsapp").count(),
        "sms": db.query(EmailThread).filter(EmailThread.channel == "sms").count(),
        "to_send": db.query(CandidateEmail).filter(CandidateEmail.channel.in_(("whatsapp", "sms")), CandidateEmail.status == "to_send").count(),
    }
    settings = get_settings(db)
    state = mailbox_service.get_state(db)
    pending_sources = _parse_sources(pending) if pending else {}
    db.commit()
    return templates.TemplateResponse(
        request=request,
        name="inbox.html",
        context={
            "active_page": "inbox",
            "filters": FILTERS,
            "current_filter": current,
            "counts": counts,
            "search": search,
            "candidate_filter": db.get(Candidate, candidate_id) if candidate_id else None,
            "threads": threads,
            "selected": selected,
            "conversation": conversation,
            "whole_history": whole_history,
            "history_count": history_count,
            "kind_labels": KIND_LABELS,
            "accept_documents": email_files.ACCEPT_DOCUMENTS,
            "pending": pending,
            "pending_sources": pending_sources,
            # A program's PDF the assistant prepared stays attached to its draft.
            "draft_files": [email_files.describe(file) for file in email_files.load(db, pending_sources.get("files"))] if pending and pending.suggested_reply else [],
            "learn_default": bool(pending and pending.intent not in email_assistant.PERSONAL_INTENTS),
            "registrations": registrations,
            "kpis": email_assistant.inbox_kpis(db),
            "knowledge": knowledge_service.stats(db),
            "mode": email_assistant.mode_of(settings),
            "threshold": round(email_assistant.threshold_of(settings) * 100),
            "intent_labels": email_assistant.INTENT_LABELS,
            "language_labels": email_assistant.LANGUAGE_LABELS,
            "mailbox_ready": mailbox_service.configured(),
            "mailbox_state": state,
            "smtp_ready": email_threads.smtp_configured(),
            "model_ready": _model_ready(),
            "model_name": email_assistant.MODEL,
            "candidates": db.query(Candidate).order_by(Candidate.full_name).all(),
            "whatsapp_ready": __import__("app.services.whatsapp", fromlist=["configured"]).configured(),
            "wa_link": __import__("app.services.whatsapp_inbox", fromlist=["link_of"]).link_of,
            "send_choice": channels.choice(db), "send_status": channels.status(), "send_names": channels.NAMES,
            "reword_default": bool(getattr(settings, "reword_and_send", False)),
            "notice": params.get("notice"),
            "error": params.get("error"),
        },
    )


@router.post("/channels")
def choose_channels(way: str = Form("auto"), fallback: bool = Form(False), db: Session = Depends(get_db)):
    """How messages to candidates go (email, WhatsApp, SMS or the system's choice), and whether a way that does not
    work hands over to the next one."""
    chosen = channels.set_choice(db, way, fallback)
    name = channels.NAMES[chosen["way"]]
    return _redirect("/inbox", notice=f"Messages now go: {name.lower() if chosen['way'] == 'auto' else 'by ' + name}"
                     + (", and by the next way when it does not work." if chosen["fallback"] else ", and only that way."))


@router.get("/pending-count")
def pending_count(db: Session = Depends(get_db)):
    return {"count": db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).count()}


def _initials(name: str) -> str:
    return "".join(part[0] for part in (name or "?").split()[:2]).upper() or "?"


@router.get("/recent")
def recent_messages(db: Session = Depends(get_db)):
    """The latest emails exchanged with candidates, for the envelope in the top bar."""
    rows = (
        db.query(CandidateEmail)
        .options(joinedload(CandidateEmail.candidate))
        .filter(CandidateEmail.thread_id.isnot(None))
        .order_by(CandidateEmail.received_at.desc())
        .limit(RECENT_MESSAGES)
        .all()
    )
    messages = []
    for message in rows:
        name = message.candidate.full_name or message.candidate.email
        inbound = message.direction == "inbound"
        if inbound:
            label = "Waiting for your answer" if message.requires_admin else "Received"
        elif message.author == "admin":
            label = "Sent by you"
        elif message.intent == "reply":
            label = "Answered by the system"
        else:
            label = KIND_LABELS.get(message.intent, "Email")
        messages.append({
            "thread_id": message.thread_id,
            "name": name,
            "initials": _initials(name),
            "inbound": inbound,
            "label": label,
            "state": (
                "todo" if inbound and message.requires_admin
                else "failed" if message.status == "failed"
                else "test" if message.status == "test"
                else "in" if inbound else "out"
            ),
            "subject": message.subject or "No subject",
            "snippet": email_threads.snippet(message.body, 90),
            "when": _ago(message.received_at),
            "files": len(email_threads.message_files(message)),
        })
    return {
        "waiting": db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).count(),
        "messages": messages,
    }


@router.post("/thread/{thread_id}/reply")
def reply(
    thread_id: str,
    body: str = Form(""),
    learn: bool = Form(False),
    question: str = Form(""),
    attachment_ids: list[str] = Form(default=[]),
    reword: bool = Form(False),
    reword_language: str = Form(""),
    db: Session = Depends(get_db),
):
    thread = _thread_or_404(db, thread_id)
    if reword and body.strip():
        # "Reword and send at once": the admin's words (any language) as a proper reply; sent only when nothing changed.
        last_inbound = (db.query(CandidateEmail).filter(CandidateEmail.thread_id == thread.id, CandidateEmail.direction == "inbound")
                        .order_by(CandidateEmail.received_at.desc()).first())
        name = (thread.candidate.full_name or "").split() if thread.candidate else []
        written = reply_writer.reformulate(body, first_name=name[0].capitalize() if name else "",
                                           question=(last_inbound.body or "") if last_inbound else "", language=reword_language or None)
        if written is None:
            return _redirect("/inbox", thread=thread_id, draft=body[:1500],
                             error="The assistant did not answer, so nothing was sent: your words are back in the box.")
        if written.get("warning"):
            return _redirect("/inbox", thread=thread_id, draft=written["reply"][:1500],
                             error="Not sent: read the reworded answer first. " + written["warning"])
        body = written["reply"]
    from app.services import derja

    untaught = False
    if learn and not reword and (derja.is_tunisian(body) or derja.has_arabic_letters(body)):
        learn, untaught = False, True       # learned in French or English only: what the assistant answers with
    try:
        message = email_assistant.admin_reply(
            db, thread, body, learn=learn, question=question,
            files=email_files.load(db, attachment_ids),
        )
    except ValueError as err:
        db.rollback()
        return _redirect("/inbox", error=str(err), thread=thread_id)
    db.commit()
    if message.status == "failed":
        return _redirect(
            "/inbox", thread=thread_id,
            error="Your answer is saved in the discussion but was not delivered: email delivery is not working. "
                  "Fix it in Settings, then use “Send again”.",
        )
    notice = "Test discussion: your answer was recorded, not sent." if message.status == "test" else "Answer sent."
    if untaught:
        notice += " Not learned: it is written in Tunisian. Tick “Reword and send at once” so the system learns it in French or English."
    taught = db.get(KnowledgeEntry, getattr(message, "taught_entry_id", None) or "")
    if taught is not None:
        notice += " The system learned it and will answer this question by itself."
        notice += _read_again(db, taught, skip_thread_id=thread_id)
    return _redirect("/inbox", notice=notice, thread=thread_id)


def _read_again(db: Session, entry: KnowledgeEntry, skip_thread_id: str | None = None) -> str:
    """Hand the waiting questions this answer fits back to the assistant; says so in a few words."""
    count = email_assistant.retry_waiting(db, entry, skip_thread_id=skip_thread_id)
    if not count:
        return ""
    return (" One waiting question looks like it: the assistant is reading it again." if count == 1
            else f" {count} waiting questions look like it: the assistant is reading them again.")


class Phrase(BaseModel):
    text: str = Field(min_length=2, max_length=reply_writer.MAX_PHRASE)
    language: str | None = None         # fr | en: the language of the reworded text (the note may be in Tunisian)
    first_name: str = ""


@router.post("/thread/{thread_id}/reformulate")
def reformulate(thread_id: str, payload: Phrase, db: Session = Depends(get_db)):
    """Write the admin's short phrase as a complete reply. Nothing is sent."""
    thread = _thread_or_404(db, thread_id)
    last_inbound = (
        db.query(CandidateEmail)
        .filter(CandidateEmail.thread_id == thread.id, CandidateEmail.direction == "inbound")
        .order_by(CandidateEmail.received_at.desc())
        .first()
    )
    name = (thread.candidate.full_name or "").split() if thread.candidate else []
    written = reply_writer.reformulate(
        payload.text,
        first_name=name[0].capitalize() if name else "",
        question=(last_inbound.body or "") if last_inbound else "",
        language=payload.language,
    )
    if written is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "The assistant did not answer. Try again in a moment; if it keeps failing, Settings says how to start it."},
        )
    return written


@router.post("/reformulate")
def reformulate_any(payload: Phrase):
    """Reword a short note (Tunisian, French or English) into a proper message in French or English. Nothing is sent."""
    written = reply_writer.reformulate(payload.text, first_name=payload.first_name.strip()[:40], language=payload.language)
    if written is None:
        return JSONResponse(status_code=503, content={"detail": "The assistant did not answer. Try again in a moment."})
    return written


@router.post("/reword-mode")
def reword_mode(on: str = Form("0"), db: Session = Depends(get_db)):
    """"Reword and send at once": on or off, remembered."""
    settings = get_settings(db)
    settings.reword_and_send = on == "1"
    db.commit()
    return {"on": bool(settings.reword_and_send)}


@router.post("/thread/{thread_id}/no-reply")
def no_reply(thread_id: str, db: Session = Depends(get_db)):
    thread = _thread_or_404(db, thread_id)
    email_assistant.mark_no_reply(db, thread)
    db.commit()
    return _redirect("/inbox", notice="Marked as not needing an answer.", thread=thread_id)


@router.post("/thread/{thread_id}/rerun")
def rerun(thread_id: str, db: Session = Depends(get_db)):
    thread = _thread_or_404(db, thread_id)
    if not email_assistant.rerun(db, thread):
        return _redirect("/inbox", error="There is no unanswered email in this discussion.", thread=thread_id)
    return _redirect("/inbox", notice="The assistant is reading the email again.", thread=thread_id)


@router.post("/thread/{thread_id}/close")
def close_thread(thread_id: str, db: Session = Depends(get_db)):
    thread = _thread_or_404(db, thread_id)
    email_assistant.mark_no_reply(db, thread)
    thread.status = "closed"
    thread.closed_at = utcnow()
    db.commit()
    return _redirect("/inbox", notice="Discussion closed. It reopens by itself if the candidate writes again.")


@router.post("/whatsapp/{message_id}/sent")
def whatsapp_sent(message_id: str, remove: bool = Form(False), db: Session = Depends(get_db)):
    """A message waiting in "To send on WhatsApp": the admin sent it from WhatsApp (or removes it)."""
    from app.services import whatsapp_inbox

    message = db.get(CandidateEmail, message_id)
    if message is None or message.channel not in ("whatsapp", "sms") or message.status != "to_send":
        raise HTTPException(status_code=404, detail="Message not found")
    whatsapp_inbox.mark_sent(db, message, cancelled=remove)
    db.commit()
    way = "by SMS" if message.channel == "sms" else "on WhatsApp"
    return _redirect("/inbox", notice="Removed: it will not be sent." if remove else f"Marked as sent {way}.",
                     thread=message.thread_id, filter="to_send" if whatsapp_inbox.outbox(db) else "whatsapp")


def _answered(db: Session, reply: CandidateEmail) -> CandidateEmail | None:
    """The candidate's message an answer was written for: the one marked answered right when it was sent,
    else the latest one before it in the discussion."""
    from datetime import timedelta

    sent = reply.received_at
    exact = (db.query(CandidateEmail).filter(CandidateEmail.thread_id == reply.thread_id, CandidateEmail.direction == "inbound",
                                             CandidateEmail.replied_at.isnot(None),
                                             CandidateEmail.replied_at >= sent - timedelta(seconds=2),
                                             CandidateEmail.replied_at <= sent + timedelta(minutes=2))
             .order_by(CandidateEmail.replied_at.asc()).first())
    if exact is not None:
        return exact
    return (db.query(CandidateEmail).filter(CandidateEmail.thread_id == reply.thread_id, CandidateEmail.direction == "inbound",
                                            CandidateEmail.received_at <= sent)
            .order_by(CandidateEmail.received_at.desc()).first())


@router.post("/message/{message_id}/score")
def score_answer(message_id: str, verdict: str = Form(""), db: Session = Depends(get_db)):
    """The admin says whether the assistant's answer was right. It counts in the assistant's results."""
    reply = db.get(CandidateEmail, message_id)
    if reply is None or reply.direction != "outbound" or reply.author != "system":
        raise HTTPException(status_code=404, detail="Answer not found")
    reply.admin_score = {"right": 1, "wrong": -1}.get(verdict)
    db.commit()
    notice = {"right": "Marked as a right answer.", "wrong": "Marked as wrong. Correct it below so the assistant learns the right answer."}.get(verdict, "Your mark was removed.")
    return _redirect("/inbox", notice=notice, thread=reply.thread_id)


@router.post("/message/{message_id}/correct")
def correct_answer(message_id: str, answer: str = Form(""), send: bool = Form(False), db: Session = Depends(get_db)):
    """The admin writes the right answer: the assistant uses it next time; it can also go to the candidate now."""
    reply = db.get(CandidateEmail, message_id)
    if reply is None or reply.direction != "outbound" or reply.author != "system":
        raise HTTPException(status_code=404, detail="Answer not found")
    text = (answer or "").strip()
    if len(text) < 3:
        return _redirect("/inbox", error="Write the right answer first.", thread=reply.thread_id)
    asked = _answered(db, reply)
    question = (asked.question or email_threads.snippet(asked.body, 240)) if asked is not None else ""
    if not question:
        return _redirect("/inbox", error="The question this answer was for could not be found.", thread=reply.thread_id)
    try:
        knowledge_service.save_entry(db, title=question, content=email_assistant.clean_answer(text) or text, origin="learned",
                                     source_email_id=asked.id if asked is not None else None)
    except ValueError as problem:
        db.rollback()
        return _redirect("/inbox", error=str(problem), thread=reply.thread_id)
    reply.admin_score = -1 if reply.admin_score is None else reply.admin_score
    notice = "The assistant learned the right answer: it uses it from now on."
    if send:
        thread = db.get(EmailThread, reply.thread_id)
        try:
            email_assistant.admin_reply(db, thread, text)
            notice += " It was also sent to the candidate."
        except ValueError as problem:
            notice += f" It could not be sent: {problem}"
    db.commit()
    return _redirect("/inbox", notice=notice, thread=reply.thread_id)


@router.post("/message/{message_id}/resend")
def resend(message_id: str, db: Session = Depends(get_db)):
    message = db.get(CandidateEmail, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Email not found")
    delivered = email_threads.resend(db, message)
    db.commit()
    if delivered:
        return _redirect("/inbox", notice="Email delivered.", thread=message.thread_id)
    return _redirect(
        "/inbox", thread=message.thread_id,
        error="Still not delivered. Check the email settings (SMTP_EMAIL and SMTP_PASSWORD in the .env file).",
    )


@router.get("/message/{message_id}/html", response_class=HTMLResponse)
def message_html(message_id: str, request: Request, db: Session = Depends(get_db)):
    """The email exactly as the candidate received it."""
    message = db.get(CandidateEmail, message_id)
    if message is None or not message.html_body:
        raise HTTPException(status_code=404, detail="No designed version of this email is stored")
    # The photo travels inside the real email; here it is read from this server.
    origin = str(request.base_url).rstrip("/")
    return HTMLResponse(
        email_files.for_browser(message.html_body, origin),
        headers={
            "Content-Security-Policy":
                f"sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data: https: {origin}",
        },
    )


# ------------------------------------------------------------------
# Photos and attached files
# ------------------------------------------------------------------

@router.post("/files")
def upload_file(
    file: UploadFile = File(...),
    purpose: str = Form("attachment"),
    db: Session = Depends(get_db),
):
    """Keep a photo ("image") or a document ("attachment") chosen for an email."""
    as_photo = purpose == "image"
    data = file.file.read(email_files.MAX_FILE_BYTES + 1)
    try:
        stored = email_files.save_upload(
            db, filename=file.filename, data=data, purpose="image" if as_photo else "attachment",
        )
    except ValueError as err:
        db.rollback()
        return JSONResponse(status_code=400, content={"detail": str(err)})
    db.commit()
    return email_files.describe(stored, inline=as_photo)


@router.get("/files/{file_id}")
def get_file(file_id: str, db: Session = Depends(get_db)):
    file = db.get(EmailFile, file_id)
    path = email_files.path_of(file) if file is not None else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="This file is no longer available")
    # Pictures and PDFs open in the browser; anything else is only ever downloaded.
    shown = file.content_type in email_files.INLINE_TYPES
    return FileResponse(
        path,
        media_type=file.content_type if shown else "application/octet-stream",
        filename=file.filename,
        content_disposition_type="inline" if shown else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )


@router.post("/check")
def check_mailbox(db: Session = Depends(get_db)):
    result = mailbox_service.poll(db)
    if result["status"] == "not_configured":
        return _redirect("/inbox", error="The mailbox is not connected yet. Open Settings to connect it.")
    if result["status"] == "error":
        return _redirect("/inbox", error=f"The mailbox could not be read: {result['error']}")
    if result["status"] == "busy":
        return _redirect("/inbox", notice="The mailbox is already being checked.")
    if result.get("first_connection"):
        return _redirect("/inbox", notice="Mailbox connected. Emails that arrive from now on are tracked.")
    count = result["imported"]
    return _redirect("/inbox", notice=f"{count} new email(s) from candidates." if count else "No new email from candidates.")


@router.post("/simulate")
def simulate(
    candidate_id: str = Form(...),
    subject: str = Form(""),
    body: str = Form(...),
    db: Session = Depends(get_db),
):
    """Try the assistant on a sample email. Nothing is sent to the candidate."""
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        return _redirect("/inbox", error="Choose a candidate.")
    result = email_assistant.ingest_inbound(
        db, sender=candidate.email, subject=subject.strip() or "Question", body=body, test=True,
    )
    if result["status"] != "received":
        return _redirect("/inbox", error=result.get("reason") or "The sample email could not be stored.")
    return _redirect(
        "/inbox", thread=result["thread_id"], filter="conversations",
        notice="Sample email received. The assistant is reading it; its answer appears here in a moment.",
    )


# ------------------------------------------------------------------
# Personalized emails
# ------------------------------------------------------------------

class PreviewRequest(BaseModel):
    subject: str = Field(default="", max_length=300)
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=20000)
    style: str = "brand"
    candidate_id: str | None = None
    cta_label: str = Field(default="", max_length=80)
    cta_url: str = Field(default="", max_length=500)
    image_id: str | None = None
    image_position: str = "top"


class AutoEmailDraft(BaseModel):
    """An automatic email as it is being edited, before it is saved."""
    key: str
    subject: str = Field(default="", max_length=300)
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=20000)
    closing: str = Field(default="", max_length=5000)
    style: str = "brand"
    show_details: bool = True
    cta_label: str = Field(default="", max_length=80)
    cta_url: str = Field(default="", max_length=500)
    image_id: str | None = None
    image_position: str = "top"
    attachment_ids: list[str] = []
    candidate_id: str | None = None


class DraftRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)
    tone: str = "warm"
    language: str = "fr"


@router.get("/compose", response_class=HTMLResponse)
def compose_page(request: Request, db: Session = Depends(get_db)):
    email_composer.seed_templates(db)
    candidates = (
        db.query(Candidate)
        .options(joinedload(Candidate.registrations).joinedload(Registration.program))
        .order_by(Candidate.full_name)
        .all()
    )
    recipients = []
    class_ids, program_ids = set(), set()
    for candidate in candidates:
        active = [item for item in candidate.registrations if item.status != RegistrationStatus.CANCELLED]
        classes = sorted({item.class_group_id for item in active if item.class_group_id})
        programs = sorted({item.program_id for item in active if item.program_id})
        class_ids.update(classes)
        program_ids.update(programs)
        recipients.append({
            "id": candidate.id,
            "name": candidate.full_name or candidate.email,
            "email": candidate.email,
            "classes": classes,
            "programs": programs,
            "unpaid": any(item.remaining_amount > 0 for item in active),
        })
    template_id = request.query_params.get("template")
    templates_list = [
        {"template": item, "extras": email_composer.template_extras(db, item)}
        for item in email_composer.own_templates(db)
    ]
    chosen = next((item for item in templates_list if item["template"].id == template_id), None)
    template = chosen["template"] if chosen else None
    extras = chosen["extras"] if chosen else {
        "image": None, "image_position": "top", "files": [], "cta_label": "", "cta_url": "",
    }
    preselected = request.query_params.get("candidate")
    # A PDF made here (a program, the programs in detail or in short, a document): already attached, the message written, ready to be changed.
    carried = [file for file in email_files.load(db, request.query_params.get("attach")) if (file.origin or "").startswith(("program:", "document:"))]
    if carried and template is None:
        about = request.query_params.get("program") or ""
        count = request.query_params.get("count") or ""
        count = int(count) if count.isdigit() else db.query(Program).count()
        if about == "all":      # several programs in one PDF
            template = SimpleNamespace(id="", name="", style="brand", **program_pdf.message_for_catalogue(count))
        elif about == "summary":
            template = SimpleNamespace(id="", name="", style="brand", **program_pdf.message_for_summary(count))
        elif about == "document":
            template = SimpleNamespace(id="", name="", style="brand", **document_pdf.message_for(request.query_params.get("title") or carried[0].filename.rsplit(".", 1)[0]))
        elif db.get(Program, about) is not None:
            template = SimpleNamespace(id="", name="", style="brand", **program_pdf.message_for(db.get(Program, about)))
        extras = dict(extras, files=[email_files.describe(file) for file in carried])
    # The preview is there as soon as the page opens; the script then keeps it current.
    initial = email_composer.render(
        db,
        subject=template.subject if template else "",
        title=(template.title or "") if template else "",
        body=(template.body if template else "") or "Votre message apparaît ici.",
        style=template.style if template else "brand",
        candidate=db.get(Candidate, preselected) if preselected else None,
        cta_label=extras["cta_label"], cta_url=extras["cta_url"],
        image_id=extras["image"]["id"] if extras["image"] else None,
        image_position=extras["image_position"], browser=True,
    )
    return templates.TemplateResponse(
        request=request,
        name="inbox_compose.html",
        context={
            "active_page": "inbox",
            "recipients": recipients,
            "preselected": preselected,
            "initial_preview": initial["html"],
            "initial_subject": initial["subject"],
            "classes": db.query(ClassGroup).filter(ClassGroup.id.in_(class_ids or [""])).order_by(ClassGroup.name).all(),
            "programs": db.query(Program).filter(Program.id.in_(program_ids or [""])).order_by(Program.name).all(),
            "templates_list": templates_list,
            "template": template,
            "extras": extras,
            "accept_documents": email_files.ACCEPT_DOCUMENTS,
            "variables": email_composer.VARIABLES,
            "styles": STYLES,
            "tones": email_composer.TONES,
            "smtp_ready": email_threads.smtp_configured(),
            "model_ready": _model_ready(),
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/compose/preview")
def compose_preview(payload: PreviewRequest, db: Session = Depends(get_db)):
    candidate = db.get(Candidate, payload.candidate_id) if payload.candidate_id else None
    rendered = email_composer.render(
        db, subject=payload.subject, title=payload.title, style=payload.style,
        body=payload.body or "Votre message apparaît ici.", candidate=candidate,
        cta_label=payload.cta_label, cta_url=payload.cta_url,
        image_id=payload.image_id, image_position=payload.image_position, browser=True,
    )
    return {
        "subject": rendered["subject"],
        "html": rendered["html"],
        "unknown": email_composer.unknown_variables(payload.subject, payload.title, payload.body),
        "sample": candidate is None,
    }


@router.post("/compose/draft")
def compose_draft(payload: DraftRequest):
    draft = email_composer.draft_with_ai(payload.instruction, tone=payload.tone, language=payload.language)
    if draft is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "The assistant did not respond. Try again in a moment; if it keeps failing, Settings says how to start it."},
        )
    return draft


@router.post("/compose/send")
def compose_send(
    candidate_ids: list[str] = Form(default=[]),
    subject: str = Form(""),
    title: str = Form(""),
    body: str = Form(""),
    style: str = Form("brand"),
    cta_label: str = Form(""),
    cta_url: str = Form(""),
    image_id: str = Form(""),
    image_position: str = Form("top"),
    attachment_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    unknown = email_composer.unknown_variables(subject, title, body)
    problems = [
        message for failed, message in (
            (not candidate_ids, "Choose at least one candidate."),
            (not subject.strip(), "Write a subject."),
            (not body.strip(), "Write a message."),
            (bool(unknown), "Unknown variable: " + ", ".join("{" + name + "}" for name in unknown) + "."),
            (bool(cta_url.strip()) and not cta_url.strip().lower().startswith(("http://", "https://")),
             "The button link must start with http:// or https://."),
        ) if failed
    ]
    if not problems:
        try:
            email_templates.check_extras(
                db, image_id=image_id or None, attachment_ids=attachment_ids,
                cta_label=cta_label, cta_url=cta_url,
            )
        except ValueError as err:
            problems.append(str(err))
    if problems:
        return _redirect("/inbox/compose", error=problems[0])

    fields = {
        "subject": subject, "title": title, "body": body, "style": style,
        "cta_label": cta_label.strip() or None, "cta_url": cta_url.strip() or None,
        "image_id": image_id or None, "image_position": image_position,
        "attachment_ids": attachment_ids,
    }
    candidates = db.query(Candidate).filter(Candidate.id.in_(candidate_ids)).all()
    if not candidates:
        return _redirect("/inbox/compose", error="Choose at least one candidate.")
    if len(candidates) == 1:
        delivered = email_composer.send_one(db, candidates[0], **fields)
        db.commit()
        name = candidates[0].full_name or candidates[0].email
        if delivered:
            return _redirect("/inbox", notice=f"Email sent to {name}.", filter="sent")
        return _redirect(
            "/inbox", filter="sent",
            error=f"The email to {name} is saved in the discussion but was not delivered. Check the email settings.",
        )
    run_in_background(email_composer.send_many_task, [candidate.id for candidate in candidates], fields)
    return _redirect(
        "/inbox", filter="sent",
        notice=f"Sending {len(candidates)} personalized emails. Each one appears here as it goes out.",
    )


@router.post("/templates/save")
def save_template(
    name: str = Form(""),
    subject: str = Form(""),
    title: str = Form(""),
    body: str = Form(""),
    style: str = Form("brand"),
    template_id: str = Form(""),
    cta_label: str = Form(""),
    cta_url: str = Form(""),
    image_id: str = Form(""),
    image_position: str = Form("top"),
    attachment_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    try:
        template = email_composer.save_template(
            db, name=name, subject=subject, title=title, body=body, style=style,
            template_id=template_id or None, cta_label=cta_label, cta_url=cta_url,
            image_id=image_id or None, image_position=image_position, attachment_ids=attachment_ids,
        )
    except ValueError as err:
        db.rollback()
        return _redirect("/inbox/compose", error=str(err))
    db.commit()
    return _redirect("/inbox/compose", notice=f"Template “{template.name}” saved.", template=template.id)


@router.post("/templates/{template_id}/delete")
def delete_template(template_id: str, db: Session = Depends(get_db)):
    template = db.get(EmailTemplate, template_id)
    # The automatic emails are restored from their own page, never deleted here.
    if template is not None and template.key is None:
        db.delete(template)
        db.commit()
    return _redirect("/inbox/compose", notice="Template deleted.")


# ------------------------------------------------------------------
# Email templates: the emails the system sends by itself
# ------------------------------------------------------------------

def _auto_or_404(key: str):
    if key not in email_templates.AUTO_EMAILS:
        raise HTTPException(status_code=404, detail="There is no automatic email with this name")
    return email_templates.AUTO_EMAILS[key]


def _draft_template(db: Session, payload: AutoEmailDraft) -> email_templates.Template:
    """The saved email with the fields being edited laid over it."""
    return dataclasses.replace(
        email_templates.get(db, payload.key),
        subject=payload.subject, title=payload.title, body=payload.body, closing=payload.closing,
        style=payload.style if payload.style in STYLES else "brand",
        show_details=payload.show_details, cta_label=payload.cta_label, cta_url=payload.cta_url,
        image_id=payload.image_id or None, image_position=payload.image_position,
        attachment_ids=email_files.parse_ids(payload.attachment_ids),
    )


@router.get("/templates", response_class=HTMLResponse)
def templates_page(request: Request, db: Session = Depends(get_db)):
    key = request.query_params.get("key")
    all_auto = email_templates.all_templates(db)
    current = next((item for item in all_auto if item.key == key), None)

    groups: dict[str, list] = {}
    for item in all_auto:
        groups.setdefault(item.spec.group, []).append({
            "template": item,
            "files": len(item.attachment_ids),
            "photo": bool(item.image_id),
        })

    context = {
        "active_page": "inbox",
        "groups": groups,
        "current": current,
        "whatsapp_ready": __import__("app.services.whatsapp", fromlist=["configured"]).configured(),
        "whatsapp_kinds": __import__("app.services.whatsapp", fromlist=["REMINDERS"]).REMINDERS,
        "reminder_hours": __import__("app.services.welcome", fromlist=["reminder_hours"]).reminder_hours(db),
        "own_templates": email_composer.own_templates(db),
        "smtp_ready": email_threads.smtp_configured(),
        "own_address": (os.getenv("SMTP_EMAIL") or "").strip(),
        "notice": request.query_params.get("notice"),
        "error": request.query_params.get("error"),
    }
    if current is not None:
        image = email_templates.find_image(db, current.image_id)
        preview = email_templates.render_auto(db, current.key, template=current, browser=True)
        context.update({
            "variables": email_templates.VARIABLES,
            "extra_variables": list(current.spec.extra),
            "styles": STYLES,
            "image": email_files.describe(image, inline=True) if image else None,
            "attached": [email_files.describe(file) for file in email_files.load(db, current.attachment_ids)],
            "accept_documents": email_files.ACCEPT_DOCUMENTS,
            "candidates": db.query(Candidate).order_by(Candidate.full_name).all(),
            "initial_preview": preview["html"],
            "initial_subject": preview["subject"],
        })
    return templates.TemplateResponse(request=request, name="inbox_templates.html", context=context)


@router.post("/templates/preview")
def auto_preview(payload: AutoEmailDraft, db: Session = Depends(get_db)):
    spec = _auto_or_404(payload.key)
    candidate = db.get(Candidate, payload.candidate_id) if payload.candidate_id else None
    draft = _draft_template(db, payload)
    if not draft.body.strip():
        draft.body = "Le texte de l'email apparaît ici."
    rendered = email_templates.render_auto(db, payload.key, candidate=candidate, template=draft, browser=True)
    return {
        "subject": rendered["subject"],
        "html": rendered["html"],
        "unknown": email_templates.unknown_variables(
            payload.subject, payload.title, payload.body, payload.closing,
            payload.cta_label, payload.cta_url, extra=spec.extra,
        ),
    }


@router.post("/templates/test")
def auto_test(payload: AutoEmailDraft, db: Session = Depends(get_db)):
    """Send the email being edited to the 9antra address itself, to see it in a real mailbox."""
    _auto_or_404(payload.key)
    own_address = (os.getenv("SMTP_EMAIL") or "").strip()
    if not email_threads.smtp_configured() or not own_address:
        return JSONResponse(status_code=400, content={"detail": "Email sending is not set up yet. Open Settings to connect it."})
    if not payload.subject.strip() or not payload.body.strip():
        return JSONResponse(status_code=400, content={"detail": "Write a subject and a text first."})
    try:
        email_templates.check_extras(
            db, image_id=payload.image_id or None, attachment_ids=payload.attachment_ids,
            cta_label=payload.cta_label, cta_url=payload.cta_url,
        )
    except ValueError as err:
        return JSONResponse(status_code=400, content={"detail": str(err)})
    candidate = db.get(Candidate, payload.candidate_id) if payload.candidate_id else None
    # Like the preview: details a candidate does not have yet are shown with samples.
    rendered = email_templates.render_auto(
        db, payload.key, candidate=candidate, template=_draft_template(db, payload), preview=True,
    )
    error = email_templates.send_test(own_address, rendered)
    if error:
        return JSONResponse(status_code=502, content={"detail": f"The test could not be sent: {error}"})
    return {"sent_to": own_address}


@router.post("/templates/auto/{key}/save")
def save_auto(
    key: str,
    subject: str = Form(""),
    title: str = Form(""),
    body: str = Form(""),
    closing: str = Form(""),
    style: str = Form("brand"),
    show_details: bool = Form(False),
    is_active: bool = Form(False),
    cta_label: str = Form(""),
    cta_url: str = Form(""),
    image_id: str = Form(""),
    image_position: str = Form("top"),
    attachment_ids: list[str] = Form(default=[]),
    channel: str = Form("auto"),
    hours: str = Form(""),
    db: Session = Depends(get_db),
):
    spec = _auto_or_404(key)
    if key == "payment_reminder" and hours.strip():
        from app.services import welcome

        welcome.set_reminder_hours(db, hours)
    try:
        email_templates.save(
            db, key, subject=subject, title=title, body=body, closing=closing, style=style,
            show_details=show_details, is_active=is_active, cta_label=cta_label, cta_url=cta_url,
            image_id=image_id or None, image_position=image_position, attachment_ids=attachment_ids, channel=channel,
        )
    except ValueError as err:
        db.rollback()
        return _redirect("/inbox/templates", error=str(err), key=key)
    db.commit()
    notice = f"“{spec.name}” saved. The next one the system sends uses your version."
    if spec.switchable and not is_active:
        notice = f"“{spec.name}” saved and switched off: the system no longer sends it by itself."
    return _redirect("/inbox/templates", notice=notice, key=key)


@router.post("/templates/auto/{key}/reset")
def reset_auto(key: str, db: Session = Depends(get_db)):
    spec = _auto_or_404(key)
    email_templates.reset(db, key)
    db.commit()
    return _redirect("/inbox/templates", notice=f"“{spec.name}” is back to its original text.", key=key)


# ------------------------------------------------------------------
# Knowledge base
# ------------------------------------------------------------------

class KnowledgeTest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(request: Request, db: Session = Depends(get_db)):
    knowledge_service.ensure_fresh(db)
    written = (
        db.query(KnowledgeEntry)
        .filter(KnowledgeEntry.origin.in_(["admin", "learned"]))
        .order_by(KnowledgeEntry.updated_at.desc())
        .all()
    )
    system = (
        db.query(KnowledgeEntry)
        .filter(KnowledgeEntry.origin == "system", KnowledgeEntry.is_active.is_(True))
        .order_by(KnowledgeEntry.kind, KnowledgeEntry.title)
        .all()
    )
    edit_id = request.query_params.get("edit")
    return templates.TemplateResponse(
        request=request,
        name="inbox_knowledge.html",
        context={
            "active_page": "inbox",
            "written": written,
            "system": system,
            "stats": knowledge_service.stats(db),
            "imported": __import__("app.services.knowledge_import", fromlist=["sources"]).sources(db),
            "editing": db.get(KnowledgeEntry, edit_id) if edit_id else None,
            "prefill_question": request.query_params.get("question", ""),
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/knowledge/save")
def save_knowledge(
    title: str = Form(""),
    content: str = Form(""),
    entry_id: str = Form(""),
    db: Session = Depends(get_db),
):
    already = None if entry_id else knowledge_service.same_question(db, title)
    try:
        entry = knowledge_service.save_entry(db, title=title, content=content, entry_id=entry_id or None)
    except ValueError as err:
        db.rollback()
        return _redirect("/inbox/knowledge", error=str(err))
    db.commit()
    notice = (
        "This question was already taught: its answer is replaced by the one you just wrote."
        if already is not None else "Saved. The assistant uses it from now on."
    )
    return _redirect("/inbox/knowledge", notice=notice + _read_again(db, entry))


@router.post("/knowledge/import")
def import_knowledge(url: str = Form(""), text: str = Form(""), title: str = Form(""), db: Session = Depends(get_db)):
    """What the assistant learns from a page (a public link) or a pasted text, kept with its source."""
    from app.services import knowledge_import

    url, text = (url or "").strip(), (text or "").strip()
    try:
        if text:
            source = url or (title or "Texte collé").strip()
            parts = knowledge_import.learn(db, title=title or source, text=text, source=source)
        elif url:
            found_title, found_text = knowledge_import.fetch(url)
            parts = knowledge_import.learn(db, title=title or found_title, text=found_text, source=url)
        else:
            return _redirect("/inbox/knowledge#learn", error="Give a link, or paste a text.")
    except ValueError as err:
        db.rollback()
        return _redirect("/inbox/knowledge#learn", error=str(err))
    return _redirect("/inbox/knowledge#learn",
                     notice=f"Learnt: {parts} part{'s' if parts != 1 else ''}. The assistant uses it from now on, and says where it comes from.")


@router.post("/knowledge/import/forget")
def forget_imported(key: str = Form(""), db: Session = Depends(get_db)):
    from app.services import knowledge_import

    knowledge_import.forget(db, key)
    return _redirect("/inbox/knowledge#learn", notice="Forgotten: the assistant no longer uses it.")


@router.post("/knowledge/{entry_id}/delete")
def delete_knowledge(entry_id: str, db: Session = Depends(get_db)):
    entry = db.get(KnowledgeEntry, entry_id)
    if entry is not None and entry.origin != "system":
        db.delete(entry)
        db.commit()
    return _redirect("/inbox/knowledge", notice="Removed from the knowledge base.")


@router.post("/knowledge/reindex")
def reindex_knowledge(db: Session = Depends(get_db)):
    result = knowledge_service.sync_knowledge(db)
    changed = result["created"] + result["updated"] + result["removed"]
    return _redirect(
        "/inbox/knowledge",
        notice=f"Knowledge refreshed from programs and classes ({changed} change(s))." if changed
        else "Knowledge is already up to date with programs and classes.",
    )


@router.post("/knowledge/test")
def test_knowledge(payload: KnowledgeTest, db: Session = Depends(get_db)):
    """What the assistant would find for a question (retrieval only, no email)."""
    knowledge_service.ensure_fresh(db)
    hits = knowledge_service.search(db, payload.question, limit=4)
    best = hits[0] if hits else None
    if best is None:
        verdict = "The knowledge base is empty."
    elif best["origin"] != "system" and best["same"] >= knowledge_service.SAME_QUESTION:
        verdict = "This is a question you taught: the system answers it by itself, in its own words."
    elif best["origin"] != "system" and best["quality"] >= knowledge_service.ALIKE_QUESTION:
        verdict = ("It looks like a question you taught: the system prepares the answer and asks you to check it. "
                   "Once you send it, this wording is answered alone.")
    elif best["quality"] >= 0.7:
        verdict = "Good match: the system has what it needs to try an answer."
    elif best["quality"] >= email_assistant.KNOWS_FROM:
        verdict = "Partial match: the system would probably ask you."
    else:
        verdict = "No real match: the system would ask you. Teach it the answer above."
    return {
        "verdict": verdict,
        "hits": [
            {
                "title": hit["title"], "origin": hit["origin"], "kind": hit["kind"],
                "score": round(hit["quality"] * 100),
                "wording": hit.get("wording") if hit.get("wording") != hit["title"] else None,
                "excerpt": email_threads.snippet(hit["content"], 260),
            }
            for hit in hits
        ],
    }

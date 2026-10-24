"""The team chat: the admin writes to the professors and the team members (in the application, by email, WhatsApp
or SMS) and reads their answers (/inbox/team); each of them has their own page to read and answer (/account/messages).
See services/team_messages.py."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import auth, knowledge_service, team_messages
from app.services.schedule_utils import utc_to_local
from app.services.settings_service import get_settings
from app.template_context import notification_context

router = APIRouter(tags=["Team chat"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])
templates.env.filters["local"] = lambda value: utc_to_local(value) if value else None


def _back(path: str, **params) -> RedirectResponse:
    query = urlencode({key: value for key, value in params.items() if value})
    return RedirectResponse(path + ("?" + query if query else ""), status_code=303)


def _me(request: Request):
    user = auth.current(request)
    if user is None or not user.id:
        raise HTTPException(status_code=401, detail="Sign in first.")
    return user


# ------------------------------------------------------------------
# The admin's side
# ------------------------------------------------------------------

@router.get("/inbox/team", response_class=HTMLResponse)
def team_page(request: Request, person: str = "", db: Session = Depends(get_db)):
    me = auth.current(request)
    everyone = team_messages.people(db, me_id=me.id if me else None)
    chosen = team_messages.person(db, person) if person else None
    if chosen is None and everyone and "person" not in request.query_params and request.headers.get("sec-ch-ua-mobile") != "?1":
        chosen = team_messages.person(db, everyone[0]["key"])       # on a computer, the first conversation is open
    messages = []
    if chosen is not None:
        team_messages.mark_read(db, chosen["key"])
        messages = team_messages.conversation(db, chosen["key"])
    last_in = next((bubble for bubble in reversed(messages) if bubble["direction"] == "in"), None)
    return templates.TemplateResponse(request, "inbox_team.html", {
        "active_page": "inbox", "people": everyone, "chosen": chosen, "messages": messages, "last_in": last_in,
        "channel_names": team_messages.CHANNEL_NAMES, "auto_reply": team_messages.answers_alone(db),
        "whatsapp_ready": __import__("app.services.whatsapp", fromlist=["configured"]).configured(),
        "waiting_phone": team_messages.waiting_phone(db),
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error"),
    })


@router.post("/inbox/team/send")
def team_send(request: Request, person: str = Form(...), body: str = Form(""), channels: list[str] = Form(default=[]),
              question: str = Form(""), learn: bool = Form(False), db: Session = Depends(get_db)):
    me = _me(request)
    if not body.strip():
        return _back("/inbox/team", person=person, error="Write your message first.")
    kept = team_messages.send(db, person, body, channels, author=me.name)
    if not kept:
        return _back("/inbox/team", person=person, error="This person cannot be reached: add their email or phone number.")
    notice = []
    sent = [row for row in kept if row.status == "sent"]
    waiting = [row for row in kept if row.status == "to_send"]
    failed = [row for row in kept if row.status == "failed"]
    if sent:
        notice.append("Sent: " + ", ".join(team_messages.CHANNEL_NAMES[row.channel] for row in sent) + ".")
    if waiting:
        notice.append("Press “Open” on " + " and ".join(team_messages.CHANNEL_NAMES[row.channel] for row in waiting)
                      + " to send it from your phone.")
    if learn and question.strip():
        try:
            knowledge_service.save_entry(db, title=question.strip()[:300], content=body.strip())
            db.commit()
            notice.append("The assistant learned this answer.")
        except ValueError:
            db.rollback()
    error = "; ".join(f"{team_messages.CHANNEL_NAMES[row.channel]}: {row.error}" for row in failed) or None
    return _back("/inbox/team", person=person, notice=" ".join(notice) or None, error=error)


@router.post("/inbox/team/message/{message_id}/sent")
def team_sent(message_id: str, remove: str = Form(""), person: str = Form(""), db: Session = Depends(get_db)):
    done = team_messages.mark_sent(db, message_id, cancelled=bool(remove))
    if done is None:
        return _back("/inbox/team", person=person, error="This message was already marked.")
    return _back("/inbox/team", person=person, notice="Removed." if remove else "Marked as sent.")


@router.post("/inbox/team/contact")
def team_contact(person: str = Form(...), email: str = Form(""), phone: str = Form(""), db: Session = Depends(get_db)):
    problem = team_messages.save_contact(db, person, email=email, phone=phone)
    return _back("/inbox/team", person=person, error=problem, notice=None if problem else "Saved.")


@router.post("/inbox/team/assistant")
def team_assistant(on: str = Form("0"), person: str = Form(""), db: Session = Depends(get_db)):
    settings = get_settings(db)
    settings.team_auto_reply = on == "1"
    db.commit()
    return _back("/inbox/team", person=person,
                 notice="The assistant now answers the team alone when you taught the answer." if settings.team_auto_reply
                 else "The assistant only prepares answers: you send them.")


@router.get("/inbox/team/count")
def team_count(db: Session = Depends(get_db)):
    return {"count": team_messages.unread_count(db)}


# ------------------------------------------------------------------
# A professor's or a team member's own side
# ------------------------------------------------------------------

@router.get("/account/messages", response_class=HTMLResponse)
def my_messages(request: Request, db: Session = Depends(get_db)):
    me = _me(request)
    if me.is_admin:
        return RedirectResponse("/inbox/team", status_code=303)
    if not me.viewing_as:              # an admin looking as them reads nothing in their place
        team_messages.member_read(db, me)
    mine = team_messages.for_member(db, me)
    from app.services import course_questions

    return templates.TemplateResponse(request, "my_messages.html", {
        "active_page": "messages", "messages": mine["messages"], "person_key": mine["key"],
        "questions": {row.batch or row.id: row.id for row in course_questions.waiting_for(db, mine["key"])},
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error"),
    })


@router.post("/account/messages")
def my_messages_write(request: Request, body: str = Form(""), db: Session = Depends(get_db)):
    me = _me(request)
    if me.is_admin:
        return RedirectResponse("/inbox/team", status_code=303)
    if not body.strip():
        return _back("/account/messages", error="Write your message first.")
    team_messages.member_write(db, me, body)
    return _back("/account/messages", notice="Sent to the centre.")


@router.post("/account/messages/question/{row_id}/answer")
def answer_candidate(row_id: str, request: Request, body: str = Form(""), db: Session = Depends(get_db)):
    """A professor answers a candidate's question the centre sent them: the answer goes to the candidate."""
    from app.models.team_message import TeamMessage
    from app.services import course_questions

    me = _me(request)
    row = db.get(TeamMessage, row_id)
    if row is None or row.person != team_messages.key_of_user(me) or row.question_message_id is None:
        raise HTTPException(status_code=404, detail="Question not found")
    if row.answered_at is not None:
        return _back("/account/messages", error="This question was already answered.")
    if not body.strip():
        return _back("/account/messages", error="Write your answer first.")
    try:
        course_questions.answer(db, row, body, professor_name=me.name)
    except ValueError as error:
        db.rollback()
        return _back("/account/messages", error=str(error))
    return _back("/account/messages", notice="Your answer was sent to the candidate.")


@router.get("/account/messages/count")
def my_messages_count(request: Request, db: Session = Depends(get_db)):
    me = auth.current(request)
    if me is None or not me.id or me.is_admin:
        return JSONResponse({"count": 0})
    return {"count": team_messages.member_unread(db, me)}

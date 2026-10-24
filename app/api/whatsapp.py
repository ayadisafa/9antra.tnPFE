"""WhatsApp Business (Cloud API from Meta): where Meta delivers the messages candidates send.

Meta checks the address once (GET, with the verify token written in Settings), then
posts every message to it, signed with the app's secret. A message whose signature
is not Meta's is refused; nothing is read from it. Each message is kept in the
candidate's WhatsApp discussion and given to the assistant, as an email would be.
"""
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.database import SessionLocal
from app.services import whatsapp, whatsapp_inbox

router = APIRouter(tags=["WhatsApp"])
logger = logging.getLogger(__name__)


@router.get("/whatsapp/webhook")
def verify(request: Request):
    """Meta's check when the address is given to it: it must send back the challenge."""
    params = request.query_params
    expected = whatsapp.verify_token()
    if params.get("hub.mode") == "subscribe" and expected and params.get("hub.verify_token") == expected:
        return PlainTextResponse(params.get("hub.challenge") or "")
    return PlainTextResponse("This address only answers Meta's check.", status_code=403)


@router.post("/whatsapp/webhook")
async def receive(request: Request):
    """Messages candidates sent on WhatsApp, posted by Meta."""
    body = await request.body()
    if not whatsapp.signature_ok(body, request.headers.get("X-Hub-Signature-256")):
        return JSONResponse({"ok": False, "reason": "not signed by Meta with this application's secret"}, status_code=403)
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"ok": False}, status_code=400)
    from app.services import email_assistant

    db = SessionLocal()
    kept = []
    try:
        for item in whatsapp_inbox.from_webhook(payload):
            message = whatsapp_inbox.receive(db, **item)
            if message is not None:
                kept.append(message.id)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("A WhatsApp message could not be kept.")
    finally:
        db.close()
    for message_id in kept:
        email_assistant.queue_processing(message_id)    # read and answered on the assistant's own worker
    # Meta wants a quick 200, whatever was in the message.
    return {"ok": True, "kept": len(kept)}

"""SMS through the Android phone (services/sms.py): where the phone forwards the SMS it receives.

Each SMS is signed by the phone's app with the signing key written in Settings; an SMS whose signature is wrong is
refused and nothing is read from it. A candidate's SMS goes to the assistant, like an email or a WhatsApp message.
"""
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.database import SessionLocal
from app.services import sms, whatsapp_inbox

router = APIRouter(tags=["SMS"])
logger = logging.getLogger(__name__)


@router.post("/sms/webhook")
async def receive(request: Request):
    body = await request.body()
    if not sms.signature_ok(body, request.headers.get("X-Signature"), request.headers.get("X-Timestamp")):
        return JSONResponse({"ok": False, "reason": "not signed with the signing key written in Settings"}, status_code=403)
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"ok": False}, status_code=400)
    item = sms.from_webhook(payload)
    if item is None:
        return {"ok": True, "kept": 0}
    from app.services import email_assistant

    db = SessionLocal()
    kept = None
    try:
        message = whatsapp_inbox.receive_sms(db, phone=item["phone"], text=item["text"], sms_id=item["sms_id"])
        kept = message.id if message is not None else None
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("An SMS could not be kept.")
    finally:
        db.close()
    if kept:
        email_assistant.queue_processing(kept)
    return {"ok": True, "kept": 1 if kept else 0}

"""SMS through an Android phone: the free "SMS Gateway for Android" app (sms-gate.app) sends SMS from the phone's own
SIM and forwards the SMS it receives to this application, signed.

Set up (Settings, SMS): on the Android phone, install the app, choose "Local server", note its address
(http://<phone>:8080), its username and password, and in its Settings > Webhooks the signing key. Its webhook for
"sms:received" points at <this application>/sms/webhook. Without it, an SMS waits in the inbox with a button that
opens the messages of the admin's phone, written (see whatsapp_inbox.send_sms).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

ENV_NAMES = ("SMS_GATEWAY_URL", "SMS_GATEWAY_USER", "SMS_GATEWAY_PASSWORD", "SMS_SIGNING_KEY")
MAX_SKEW = 300


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def configured() -> bool:
    """SMS go by themselves: the phone's address and its username and password are known."""
    return bool(_env("SMS_GATEWAY_URL") and _env("SMS_GATEWAY_USER") and _env("SMS_GATEWAY_PASSWORD"))


def receives() -> bool:
    """SMS that arrive can be trusted: the signing key is known."""
    return bool(_env("SMS_SIGNING_KEY"))


def send(phone: str, text: str) -> str | None:
    """Send an SMS from the phone. Returns the problem, or None when the phone took it."""
    from app.services import whatsapp

    number = whatsapp.normalise(phone)
    if number is None:
        return "This phone number cannot receive an SMS."
    if not configured():
        return "No phone is connected for SMS."
    base = _env("SMS_GATEWAY_URL").rstrip("/")
    auth = (_env("SMS_GATEWAY_USER"), _env("SMS_GATEWAY_PASSWORD"))
    body = {"phoneNumbers": [number], "textMessage": {"text": (text or "")[:1500]}}
    try:
        response = requests.post(f"{base}/3rdparty/v1/messages", json=body, auth=auth, timeout=20)
        if response.status_code == 404:          # older versions of the app
            response = requests.post(f"{base}/message", json={"phoneNumbers": [number], "message": (text or "")[:1500]}, auth=auth, timeout=20)
    except requests.RequestException as exc:
        return f"The phone did not answer ({type(exc).__name__}): is it on the same Wi-Fi, with the app open?"
    if response.status_code >= 300:
        return f"The phone refused the SMS ({response.status_code}): {response.text[:160]}"
    return None


def signature_ok(body: bytes, signature: str | None, timestamp: str | None, now: float | None = None) -> bool:
    """The app signs each SMS it forwards: HMAC-SHA256 of the body followed by the timestamp, with the signing key."""
    key = _env("SMS_SIGNING_KEY")
    if not key or not signature or not timestamp:
        return False
    try:
        if abs((now or time.time()) - int(timestamp)) > MAX_SKEW:
            return False
    except ValueError:
        return False
    expected = hmac.new(key.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def from_webhook(payload: dict) -> dict | None:
    """{"phone", "text", "sms_id", "when"} of a received SMS, or None for any other event."""
    if (payload or {}).get("event") != "sms:received":
        return None
    part = payload.get("payload") or {}
    phone = part.get("sender") or part.get("phoneNumber") or ""
    text = part.get("message") or ""
    if not phone:
        return None
    return {"phone": str(phone), "text": str(text), "sms_id": str(part.get("messageId") or payload.get("id") or ""),
            "when": part.get("receivedAt")}


def test_connection() -> tuple[bool, str]:
    """Ask the phone whether it is there (its health page)."""
    if not configured():
        return False, "Write the phone's address, username and password first."
    try:
        response = requests.get(_env("SMS_GATEWAY_URL").rstrip("/") + "/health",
                                auth=(_env("SMS_GATEWAY_USER"), _env("SMS_GATEWAY_PASSWORD")), timeout=10)
    except requests.RequestException as exc:
        return False, f"The phone did not answer ({type(exc).__name__}): same Wi-Fi? app open on “Local server”?"
    if response.status_code >= 300:
        return False, f"The phone answered {response.status_code}: check the username and password."
    return True, "The phone answers: SMS can go by themselves."


def save_account(values: dict[str, str]) -> None:
    """Keep the phone's details in the .env file (never in the database) and use them at once. Empty values keep the old ones."""
    from app.services import email_account

    kept = {name: (values.get(name) or "").strip() for name in ENV_NAMES if (values.get(name) or "").strip()}
    if kept:
        email_account._write(kept)
        os.environ.update(kept)

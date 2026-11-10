"""Notifications on the phone (iPhone, Android) and the computer, even when the application is closed: Web Push.

Each phone that said yes keeps a subscription here (table `push_subscriptions`): the address of its maker's push service
(Apple, Google, Mozilla, Microsoft) and two keys. A notification is encrypted for that phone alone (RFC 8291,
aes128gcm) and handed to that service, signed with the application's own key pair (VAPID, RFC 8292; created once and
kept in .env as VAPID_PRIVATE_KEY). The phone shows it with the application's icon and, on Android, a picture of its
subject; tapping it opens the exact place.

Phones only allow this for an application opened from an https address: here, the private Tailscale address. The
admin gets what needs them (or everything, or nothing: their choice per device); a professor gets the centre's messages.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import struct
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import Session

from app.database import Base

logger = logging.getLogger(__name__)

LEVELS = ("important", "all", "off")
TTL = 24 * 3600                 # a notification the phone could not receive within a day is dropped
CONTACT = "mailto:admin@9antra.tn"
_lock = threading.Lock()
_key = None


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False, index=True)
    endpoint = Column(String, nullable=False, unique=True)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    device = Column(String, nullable=True)              # "iPhone", "Android", "Windows"…
    level = Column(String, nullable=False, default="important")
    created_at = Column(DateTime, default=datetime.utcnow)
    last_ok_at = Column(DateTime, nullable=True)
    failures = Column(Integer, nullable=False, default=0)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    text = (text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ----------------------------------------------------------------------
# The application's key pair (VAPID)
# ----------------------------------------------------------------------

def _private_key():
    """The application's signing key: read from .env, made and written there the first time."""
    global _key
    from cryptography.hazmat.primitives.asymmetric import ec

    with _lock:
        if _key is not None:
            return _key
        raw = (os.getenv("VAPID_PRIVATE_KEY") or "").strip()
        if raw:
            _key = ec.derive_private_key(int.from_bytes(_unb64(raw), "big"), ec.SECP256R1())
        else:
            _key = ec.generate_private_key(ec.SECP256R1())
            value = _b64(_key.private_numbers().private_value.to_bytes(32, "big"))
            from app.services import email_account

            email_account._write({"VAPID_PRIVATE_KEY": value})
            os.environ["VAPID_PRIVATE_KEY"] = value
        return _key


def _point(public_key) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key() -> str:
    """What the phone needs to accept notifications from this application only (base64url, 65 bytes)."""
    return _b64(_point(_private_key().public_key()))


def _vapid(endpoint: str) -> str:
    """The Authorization header: a token signed by the application for that push service, valid 12 hours."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    parts = urlsplit(endpoint)
    header = _b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": CONTACT},
                             separators=(",", ":")).encode())
    signed = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(_private_key().sign(signed, ec.ECDSA(hashes.SHA256())))
    token = f"{header}.{claims}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={token}, k={public_key()}"


# ----------------------------------------------------------------------
# Encrypting a notification for one phone (RFC 8291, aes128gcm)
# ----------------------------------------------------------------------

def _hkdf(salt: bytes, info: bytes, length: int, key: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(key)


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None, server_key=None) -> bytes:
    """The body that only that phone can read."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    phone_point = _unb64(p256dh)
    phone_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), phone_point)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    server_point = _point(server_key.public_key())
    shared = server_key.exchange(ec.ECDH(), phone_key)
    ikm = _hkdf(_unb64(auth), b"WebPush: info\x00" + phone_point + server_point, 32, shared)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
    nonce = _hkdf(salt, b"Content-Encoding: nonce\x00", 12, ikm)
    sealed = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack(">I", 4096) + bytes([len(server_point)]) + server_point + sealed


# ----------------------------------------------------------------------
# Phones
# ----------------------------------------------------------------------

def device_of(user_agent: str) -> str:
    agent = user_agent or ""
    for word, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"), ("Windows", "Windows")):
        if word in agent:
            return name
    return "A browser"


def subscribe(db: Session, user, data: dict, user_agent: str = "") -> PushSubscription | None:
    """A phone said yes: kept (the same phone again replaces its old line)."""
    endpoint = str((data or {}).get("endpoint") or "")
    keys = (data or {}).get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth") or not getattr(user, "id", None):
        return None
    row = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    if row is None:
        row = PushSubscription(endpoint=endpoint, user_id=user.id)
        db.add(row)
    row.user_id, row.p256dh, row.auth = user.id, str(keys["p256dh"]), str(keys["auth"])
    row.device, row.failures = device_of(user_agent), 0
    db.commit()
    return row


def unsubscribe(db: Session, user, endpoint: str | None = None, row_id: str | None = None) -> bool:
    query = db.query(PushSubscription).filter(PushSubscription.user_id == getattr(user, "id", None))
    query = query.filter(PushSubscription.endpoint == endpoint) if endpoint else query.filter(PushSubscription.id == row_id)
    gone = query.delete(synchronize_session=False)
    db.commit()
    return bool(gone)


def devices(db: Session, user) -> list[PushSubscription]:
    return (db.query(PushSubscription).filter(PushSubscription.user_id == getattr(user, "id", None))
            .order_by(PushSubscription.created_at.desc()).all())


def send_one(row: PushSubscription, payload: dict) -> int:
    """Hands one notification to the phone's push service. Returns the HTTP status (0: not reached)."""
    import requests

    body = encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"), row.p256dh, row.auth)
    try:
        response = requests.post(row.endpoint, data=body, timeout=10, headers={
            "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(TTL), "Urgency": "high",
            "Authorization": _vapid(row.endpoint)})
    except Exception as error:
        logger.warning("Push not delivered (%s)", type(error).__name__)
        return 0
    return response.status_code


def send(db: Session, rows: list[PushSubscription], payload: dict) -> int:
    """Sends to each phone; a phone that said no or is gone (404, 410) is forgotten. Returns how many took it."""
    took = 0
    for row in rows:
        status = send_one(row, payload)
        if 200 <= status < 300:
            row.last_ok_at, row.failures = datetime.utcnow(), 0
            took += 1
        elif status in (404, 410):
            db.delete(row)
        else:
            row.failures = (row.failures or 0) + 1
            if row.failures >= 20:
                db.delete(row)
    db.commit()
    return took


# ----------------------------------------------------------------------
# What a notification says on the phone
# ----------------------------------------------------------------------

# A notification's category: its picture (an emoji, drawn big on a card in its colour) and the scene of the window.
KINDS = {
    "NEW_REGISTRATION": ("🧑‍🎓", "#db2777", "candidates"), "PAYMENT": ("💰", "#16a34a", "payments"), "PAYMENT_PROOF": ("🧾", "#16a34a", "payments"),
    "CANDIDATE_EMAIL": ("💌", "#a42c67", "inbox"), "EMAIL_QUESTION": ("❓", "#d97706", "inbox"), "EMAIL_ANSWERED": ("✅", "#16a34a", "inbox"),
    "EMAIL_FAILED": ("📭", "#e11d48", "inbox"), "CLASS": ("🎓", "#7c3aed", "classes"), "ASSIGNMENT": ("🎓", "#7c3aed", "classes"),
    "ASSIGNMENT_PENDING": ("⏳", "#d97706", "classes"), "REMINDER": ("⏰", "#d97706", "sessions"), "REVIEW_LOW": ("💬", "#e11d48", "feelings"),
    "REVIEW": ("⭐", "#f59e0b", "feelings"), "REPLACEMENT": ("🔁", "#e11d48", "classes"), "MEETING_LINK": ("🔗", "#0ea5e9", "link"),
    "TEAM_MESSAGE": ("💬", "#6366f1", "team"), "ACCOUNT": ("🔐", "#475569", "identity"), "REGISTRATION_REQUEST": ("🙋", "#db2777", "candidates"),
}
DEFAULT_KIND = ("🔔", "#a42c67", "dashboard")


def kind_of(category: str | None) -> tuple[str, str, str]:
    return KINDS.get((category or "").upper(), DEFAULT_KIND)


def picture_png(category: str) -> bytes:
    """The picture a notification shows (Android shows it big): its emoji on a card of its colour."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    emoji, colour, _ = kind_of(category)
    width, height = 720, 360
    base = tuple(int(colour[index:index + 2], 16) for index in (1, 3, 5))
    image = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(image)
    for y in range(height):                              # light at the top, its colour at the bottom
        mix = y / height
        draw.line([(0, y), (width, y)], fill=tuple(int(255 * (1 - mix) * 0.35 + channel * (0.65 + 0.35 * mix)) for channel in base))
    for x, y, radius, alpha in ((90, 70, 70, 40), (640, 300, 110, 30), (560, 60, 40, 50)):
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse((x - radius, y - radius, x + radius, y + radius), fill=(255, 255, 255, alpha))
        image = Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((width // 2 - 95, height // 2 - 95, width // 2 + 95, height // 2 + 95), radius=48, fill=(255, 255, 255))
    try:
        font = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "seguiemj.ttf"), 109)
        draw.text((width // 2, height // 2 + 4), emoji, font=font, embedded_color=True, anchor="mm")
    except Exception:
        draw.ellipse((width // 2 - 40, height // 2 - 40, width // 2 + 40, height // 2 + 40), fill=base)
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


def payload_of(notification: dict, count: int | None = None) -> dict:
    """{title, body, url, icon, image, tag, count} for one notification, as the phone shows it."""
    from app.services import brand

    category = notification.get("category") or "GENERAL"
    url = notification.get("action_url") or "/"
    if url.startswith("/") and category and "spot=" not in url:
        url += ("&" if "?" in url else "?") + f"spot={category}"
    icon = "/static/brand/icon-192.png"
    try:
        from app.database import SessionLocal
        from app.services.settings_service import get_settings

        db = SessionLocal()
        try:
            icon = brand.view(get_settings(db)).get("icon_big") or icon
        finally:
            db.close()
    except Exception:
        pass
    return {"title": notification.get("title") or "9antra", "body": (notification.get("message") or "")[:240], "url": url,
            "icon": icon, "image": f"/notify-picture/{category}.png", "tag": notification.get("id") or category, "count": count}


def _wanted(notification: dict, level: str) -> bool:
    from app.services import notification_rules

    if level == "off":
        return False
    if level == "all":
        return True
    return (notification.get("category") or "") in notification_rules.NEEDS_YOU


def _deliver_admins(notifications: list[dict]) -> None:
    """In the background: each admin's phones get the notifications they chose."""
    from app.database import SessionLocal
    from app.models import User
    from app.models.notification import Notification

    db = SessionLocal()
    try:
        admins = [row.id for row in db.query(User).filter(User.role == "admin", User.is_active.is_(True)).all()]
        rows = db.query(PushSubscription).filter(PushSubscription.user_id.in_(admins or [""])).all() if admins else []
        if not rows:
            return
        unread = db.query(Notification).filter(Notification.recipient_role == "admin", Notification.is_read.is_(False)).count()
        for notification in notifications:
            chosen = [row for row in rows if _wanted(notification, row.level or "important")]
            if chosen:
                send(db, chosen, payload_of(notification, unread))
    except Exception:
        logger.exception("Phone notifications could not be sent.")
    finally:
        db.close()


def after_notifications(notifications: list[dict]) -> None:
    """Called once the notifications are saved: phones are told in the background (never slows a page)."""
    if not notifications:
        return
    from app.services.background import run_in_background

    run_in_background(_deliver_admins, list(notifications))


def to_person(db: Session, key: str, title: str, body: str, url: str = "/account/messages") -> None:
    """A message of the centre to a professor or a team member: their phones are told too."""
    from app.models import User

    if not key or ":" not in key:
        return
    kind, value = key.split(":", 1)
    if kind == "professor":
        users = [row.id for row in db.query(User).filter(User.professor_id == value).all()]
    elif kind == "user":
        users = [value]
    else:
        return
    rows = db.query(PushSubscription).filter(PushSubscription.user_id.in_(users or [""]), PushSubscription.level != "off").all()
    if not rows:
        return
    payload = {"title": title, "body": body[:240], "url": url, "icon": "/static/brand/icon-192.png", "image": "/notify-picture/TEAM_MESSAGE.png",
               "tag": f"team:{key}"}
    ids = [row.id for row in rows]

    def deliver():
        from app.database import SessionLocal

        session = SessionLocal()
        try:
            send(session, session.query(PushSubscription).filter(PushSubscription.id.in_(ids)).all(), payload)
        except Exception:
            logger.exception("A team message could not reach a phone.")
        finally:
            session.close()

    from app.services.background import run_in_background

    run_in_background(deliver)

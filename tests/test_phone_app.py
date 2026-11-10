"""The application as an iPhone and Android app (10 November): its service worker and manifest, notifications on the phone
even when it is closed (Web Push: each phone's subscription, the payload encrypted for it alone, signed with the
application's key), what is sent (what needs the admin, or everything, or nothing), a picture of each kind, the banner
with the moving picture while it is open, a professor told of the centre's messages; and the iPhone's shortcut that sends
the waiting SMS. The push services are FAKED (nothing leaves this machine). Runs on a COPY of the database."""
import testenv
DB = testenv.prepare("phone_app")

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network
import requests
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
requests.get = _no_web; requests.post = _no_web

import base64
import json
import os
from datetime import datetime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, Professor, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.notification import Notification
from app.models.team_message import TeamMessage
from app.services import auth, email_threads as et, phone_push, phone_sms, team_messages
from app.services.phone_push import PushSubscription

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

et.smtp_configured = lambda: True
os.environ.pop("VAPID_PRIVATE_KEY", None)
db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
admin_row = auth.create_user(db, email="admin.pa@example.com", name="Leila Admin", password="correct horse battery", role=auth.ADMIN)
client = TestClient(main.app, follow_redirects=False)
client.post("/login", data={"email": "admin.pa@example.com", "password": "correct horse battery"})
anonymous = TestClient(main.app, follow_redirects=False)


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def phone():
    """A phone's keys, as a browser makes them for a subscription."""
    key = ec.generate_private_key(ec.SECP256R1())
    point = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return key, b64(point), b64(os.urandom(16))


def decrypt(body, key, p256dh, auth_secret):
    """What the phone does: reads the body only it can read (RFC 8291)."""
    salt, rs, length = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    server_point, sealed = body[21:21 + length], body[21 + length:]
    shared = key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_point))
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=unb64(auth_secret), info=b"WebPush: info\x00" + unb64(p256dh) + server_point).derive(shared)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, sealed, None)
    return rs, plain


# ======================================================================
# A. An app on the home screen
# ======================================================================
worker = anonymous.get("/sw.js")
check("the service worker: served from the root, as JavaScript, without signing in", worker.status_code == 200 and "javascript" in worker.headers["content-type"]
      and worker.headers.get("service-worker-allowed") == "/" and "showNotification" in worker.text and "notificationclick" in worker.text)
manifest = anonymous.get("/app.webmanifest").json()
check("the manifest: its own window, an id, shortcuts on a long press", manifest["display"] == "standalone" and manifest["id"] == "/"
      and [item["url"] for item in manifest["shortcuts"]][:2] == ["/inbox", "/candidate/all"])
picture = anonymous.get("/notify-picture/PAYMENT.png")
check("each kind of notification has its picture (a PNG the phone shows)", picture.status_code == 200 and picture.content[:8] == b"\x89PNG\r\n\x1a\n")
check("…and nothing else is served there", anonymous.get("/notify-picture/..%2F..%2Fapp.png").status_code == 404)
page = client.get("/").text
check("every page registers the worker and shows what arrives as a banner with its moving picture",
      'register("/sw.js"' in page and "window.showAppBanner" in page and "setAppBadge" in page and "data-app-name=" in page)

# ======================================================================
# B. The application's key, and a notification only that phone can read
# ======================================================================
key = client.get("/account/push/key").json()["key"]
check("the application's key is made once and kept in .env", len(unb64(key)) == 65 and "VAPID_PRIVATE_KEY=" in open(os.environ["ENV_FILE"], encoding="utf-8").read())
ua_key, p256dh, secret = phone()
body = phone_push.encrypt(b'{"title":"Bonjour"}', p256dh, secret)
rs, plain = decrypt(body, ua_key, p256dh, secret)
check("the notification is encrypted for that phone alone (RFC 8291), and the phone reads it", plain == b'{"title":"Bonjour"}\x02' and rs == 4096)
header = phone_push._vapid("https://web.push.apple.com/abc")
token = header.split("t=")[1].split(",")[0]
head, claims, signature = token.split(".")
raw = unb64(signature)
public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(key))
public.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
check("each push is signed by the application for that push service (VAPID)", json.loads(unb64(claims))["aud"] == "https://web.push.apple.com"
      and header.endswith(f"k={key}"))

# ======================================================================
# C. Phones that said yes, and what they are told
# ======================================================================
endpoint = "https://web.push.apple.com/QH-phone-1"
said = client.post("/account/push/subscribe", json={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": secret}},
                   headers={"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"}).json()
check("a phone says yes: kept, named", said["ok"] and said["device"] == "iPhone" and client.get("/account/push/key").json()["devices"][0]["level"] == "important")
check("…not without signing in", anonymous.post("/account/push/subscribe", json={"endpoint": endpoint}).status_code in (401, 303, 302))
posted = []


class Answer:
    def __init__(self, status):
        self.status_code, self.text = status, ""


replies = {}


def push_service(url, data=None, headers=None, timeout=None):
    posted.append({"url": url, "data": data, "headers": headers})
    return Answer(replies.get(url, 201))


requests.post = push_service
db.add(Notification(recipient_role="admin", category="CANDIDATE_EMAIL", title="New email from Rim", message="Rim asks when the class starts.",
                    action_url="/inbox?message=1"))
db.commit()
check("something that needs the admin: their phone is told at once", len(posted) == 1 and posted[0]["url"] == endpoint
      and posted[0]["headers"]["Content-Encoding"] == "aes128gcm" and posted[0]["headers"]["Authorization"].startswith("vapid t="), posted[:1])
_, plain = decrypt(posted[0]["data"], ua_key, p256dh, secret)
shown = json.loads(plain[:-1])
check("…with its title, its words, its picture, the exact place, and the count on the icon", shown["title"] == "New email from Rim"
      and shown["url"] == "/inbox?message=1&spot=CANDIDATE_EMAIL" and shown["image"] == "/notify-picture/CANDIDATE_EMAIL.png" and shown["count"] >= 1, shown)
posted.clear()
db.add(Notification(recipient_role="admin", category="CLASS", title="Class started", message="MD1 is now ongoing."))
db.commit()
check("something that only tells: not on the phone (only what needs you, by default)", not posted)
row = db.query(PushSubscription).first()
client.post("/account/push/level", data={"id": row.id, "level": "all"})
db.add(Notification(recipient_role="admin", category="CLASS", title="Class completed", message="MD1 has finished."))
db.commit()
check("…“Everything” chosen for that phone: told too", len(posted) == 1)
replies[endpoint] = 410
db.add(Notification(recipient_role="admin", category="CANDIDATE_EMAIL", title="Another email", message="…"))
db.commit()
db.expire_all()
check("a phone that is gone (410): forgotten", db.query(PushSubscription).count() == 0)
replies.clear()
check("the open page gets the moving picture of its subject with each notification",
      "picture" in __import__("app.services.realtime", fromlist=["_with_picture"])._with_picture({"category": "PAYMENT"}))

# A professor told of the centre's messages
teacher = Professor(name="Nadia Prof", specialties="Python", email="nadia.pa@example.com", is_available=True)
db.add(teacher); db.commit()
auth.create_user(db, email="nadia.pa@example.com", name="Nadia Prof", password="correct horse battery", role=auth.PROFESSOR, professor_id=teacher.id)
prof = TestClient(main.app, follow_redirects=False)
prof.post("/login", data={"email": "nadia.pa@example.com", "password": "correct horse battery"})
key2, p2, s2 = phone()
prof.post("/account/push/subscribe", json={"endpoint": "https://fcm.googleapis.com/fcm/send/prof", "keys": {"p256dh": p2, "auth": s2}},
          headers={"user-agent": "Mozilla/5.0 (Linux; Android 14)"})
posted.clear()
team_messages.send(db, f"professor:{teacher.id}", "Pouvez-vous confirmer la séance de samedi ?", ["app"], author="Leila Admin")
check("a professor's phone is told of a message from the centre", len(posted) == 1 and posted[0]["url"].startswith("https://fcm.googleapis.com/"))
_, plain = decrypt(posted[0]["data"], key2, p2, s2)
check("…saying who wrote and what", json.loads(plain[:-1])["title"] == "Leila Admin" and "samedi" in json.loads(plain[:-1])["body"])
page = client.get("/account").text
check("My account: notifications on this phone (turn on, test, per device)", 'id="phone"' in page and 'id="push-on"' in page and "data-push=\"install\"" in page)
requests.post = _no_web

# ======================================================================
# D. SMS from the iPhone, all at once (the shortcut)
# ======================================================================
person = Candidate(full_name="Rim Sms", email="rim.sms@example.com", phone="50 111 444")
db.add(person); db.flush()
thread = et.open_thread(db, person.id, "SMS")
thread.channel = "sms"
db.add(CandidateEmail(candidate_id=person.id, thread_id=thread.id, direction="outbound", channel="sms", status="to_send", sender="centre@example.com", recipient="+21650111444",
                      body="Bonjour Rim, la séance de samedi commence à 9h.", subject="SMS", author="admin", received_at=datetime.utcnow()))
db.add(TeamMessage(person=f"professor:{teacher.id}", direction="out", channel="sms", body="Rappel : séance samedi", author="Admin", status="to_send"))
teacher.phone = "22 555 666"
db.commit()
secret_link = phone_sms.token(db)
listed = anonymous.get(f"/phone-sms/{secret_link}/waiting").json()
check("the shortcut's link lists every SMS waiting (candidates and professors), with number and text", listed["count"] == 2
      and listed["messages"][0]["number"] == "+21650111444" and "samedi" in listed["messages"][0]["text"]
      and listed["messages"][1]["number"] == "+21622555666", listed)
check("…a wrong secret sees nothing", anonymous.get("/phone-sms/not-the-secret/waiting").status_code == 404)
for item in listed["messages"]:
    anonymous.post(f"/phone-sms/{secret_link}/sent/{item['id']}")
db.expire_all()
check("the shortcut says which went: marked sent, nothing waits any more", anonymous.get(f"/phone-sms/{secret_link}/waiting").json()["count"] == 0
      and db.query(CandidateEmail).filter(CandidateEmail.channel == "sms").first().status == "sent")
client.post("/settings/sms/iphone-link")
check("a new link cuts the old one off", anonymous.get(f"/phone-sms/{secret_link}/waiting").status_code == 404)
page = client.get("/settings").text
check("Settings › SMS: the iPhone's link and how to make the shortcut", 'id="iphone-sms"' in page and "/phone-sms/" in page and "Get Contents of URL" in page)
phone_sms._tailscale.update(at=9e12, address="https://centre-pc.tail1234.ts.net")
check("“On your phone”: the private secure address once Tailscale is on", "https://centre-pc.tail1234.ts.net" in client.get("/system").text)

db.close()
print(f"{sum(results)} of {len(results)} checks passed")

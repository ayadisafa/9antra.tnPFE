"""Videos played inside the application, the page guide out of the way (Yasmine knows the programs), how messages
go (email / WhatsApp / SMS / the system's choice, and the next way when one does not work), the welcome message
after registering, and the payment reminder's delay. Runs on a COPY; email and WhatsApp are fakes."""
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote_plus

import testenv

testenv.prepare("ways")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.website import WebsiteItem
from app.services import (auth, channels, email_templates as tp, email_threads as et, payment_reminders, settings_guide, videos,
                          welcome, whatsapp, whatsapp_inbox)
from app.services.settings_service import get_settings

emails = []
FAIL = {"on": False}


def fake_attempt(recipient, subject, *rest, **kw):
    if FAIL["on"]:
        return "the mail server refused", False
    emails.append((recipient, subject, rest[0] if rest else ""))
    return None, False


et._attempt = fake_attempt
et.smtp_configured = lambda: True
whatsapp.configured = lambda: False


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="ways-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "ways-test-1"})

# ---- A. Videos ----
ID = "1AbCdEfGhIjKlMnOpQrStUvWx"
check("video: a Drive “view” link plays in Drive's player", videos.embed_url(f"https://drive.google.com/file/d/{ID}/view?usp=drivesdk") == f"https://drive.google.com/file/d/{ID}/preview")
check("video: a Drive “open?id=” link too", videos.embed_url(f"https://drive.google.com/open?id={ID}") == f"https://drive.google.com/file/d/{ID}/preview")
check("video: YouTube watch, short link and shorts", videos.embed_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3") == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0"
      and videos.embed_url("https://youtu.be/dQw4w9WgXcQ").startswith("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ")
      and videos.embed_url("https://www.youtube.com/shorts/dQw4w9WgXcQ").startswith("https://www.youtube-nocookie.com/embed/"))
check("video: Vimeo", videos.embed_url("https://vimeo.com/123456789") == "https://player.vimeo.com/video/123456789")
check("video: a video file plays in the page itself", videos.embed_url("https://example.com/a.mp4") == "https://example.com/a.mp4" and videos.kind_of("https://example.com/a.mp4") == "file")
check("video: anything else is not taken for a video", videos.embed_url("https://example.com/page") is None and videos.embed_url("javascript:alert(1)") is None)
program = next(item for item in db.query(Program).order_by(Program.name).all() if not videos.of_program(db, item))
db.add(WebsiteItem(kind="formation", external_id="test-videos", title=program.name, program_id=program.id,
                   data=json.dumps({"weeks": [{"week": 1, "content": "Intro", "video_title": "Bienvenue au cours", "video_url": f"https://drive.google.com/file/d/{ID}/view",
                                               "video_length": "12:30", "free": True},
                                              {"week": 2, "content": "Suite", "video_title": "Les bases", "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}]})))
db.commit()
found = videos.of_program(db, program)
check("video: a program's videos, week by week", [item["week"] for item in found][:2] == [1, 2] and found[0]["title"] == "Bienvenue au cours" and found[0]["length"] == "12:30", found[:2])
page = admin.get("/programs/videos")
check("videos page: opens, with the player", page.status_code == 200 and f'data-video="https://drive.google.com/file/d/{ID}/preview"' in page.text and 'id="videoPlayer"' in page.text, page.status_code)
check("videos page: grouped by program, with a search", f'id="p-{program.id}"' in page.text and "data-vd-find" in page.text)
check("programs page: a Videos button", 'href="/programs/videos"' in admin.get("/programs/all").text)

# ---- B. The guide out of the way; Yasmine knows the program ----
page = admin.get("/candidate/all").text
check("guide: still there behind the “?”, hidden unless asked", 'id="pageGuide"' in page and 'aria-label="How this page works" hidden' in page and "data-guide-show" in page)
said = admin.get("/account/friend", params={"path": f"/programs/{program.id}"}).json()
check("Yasmine: on a program's page, its facts", said["tip"] and program.name in said["tip"]["text"] and "registered" in said["tip"]["text"]
      and "2 videos to watch here" in said["tip"]["text"], said["tip"])

# ---- C. How messages go ----
check("ways: by default the system chooses, and the next way when one fails", channels.choice(db) == {"way": "auto", "fallback": True})
lina = Candidate(full_name="Lina Ways", email="lina.ways@example.com", phone="98 111 222")
nophone = Candidate(full_name="Omar Ways", email="omar.ways@example.com")
db.add_all([lina, nophone])
db.commit()
check("ways: email first when it goes by itself (no Business number)", channels.order_for(db, lina) == ["email", "whatsapp", "sms"], channels.order_for(db, lina))
whatsapp.configured = lambda: True
check("ways: with a Business number too, email first, then WhatsApp (which goes by itself), then SMS", channels.order_for(db, lina) == ["email", "whatsapp", "sms"])
whatsapp.configured = lambda: False
check("ways: only the ways that reach them", channels.order_for(db, nophone) == ["email"])
response = admin.post("/inbox/channels", data={"way": "sms", "fallback": "true"})
db.expire_all()
check("ways: the admin chooses SMS above the inbox", channels.choice(db) == {"way": "sms", "fallback": True} and "by SMS" in where(response), where(response))
check("ways: SMS first, then the others", channels.order_for(db, lina)[0] == "sms" and channels.order_for(db, nophone) == ["email"])
admin.post("/inbox/channels", data={"way": "email"})
db.expire_all()
check("ways: without the next way, only the chosen one", channels.choice(db)["fallback"] is False and channels.order_for(db, lina) == ["email"])
FAIL["on"] = True
rendered = tp.render_auto(db, "payment_reminder", candidate=lina)
rendered["short"] = tp.whatsapp_text(rendered)
message = channels.deliver(db, lina, rendered, kind="payment_reminder")
db.commit()
check("ways: email fails and nothing else is tried when the admin said so", message is not None and message.status == "failed" and message.channel == "email")
admin.post("/inbox/channels", data={"way": "email", "fallback": "true"})
db.expire_all()
message = channels.deliver(db, lina, rendered, kind="payment_reminder")
db.commit()
check("ways: email fails, WhatsApp takes over (waits for the admin's phone)", message is not None and message.channel == "whatsapp" and message.status == "to_send",
      message and (message.channel, message.status))
FAIL["on"] = False
admin.post("/inbox/channels", data={"way": "sms", "fallback": "true"})
db.expire_all()
reg = Registration(candidate_id=lina.id, program_id=program.id, program_name=program.name, total_amount=300, total_price=300, paid_amount=0,
                   payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING)
db.add(reg)
db.commit()
message = tp.send(db, "payment_reminder", registration=reg, manual=True)
db.commit()
check("ways: an automatic email follows the choice: an SMS waits for the admin", message is not None and message.channel == "sms" and message.status == "to_send")
check("ways: the SMS opens the phone's messages, written", whatsapp_inbox.link_of(message).startswith("sms:+21698111222?body="), whatsapp_inbox.link_of(message))
page = admin.get(f"/inbox?thread={message.thread_id}").text
check("inbox: the choice is above it", 'action="/inbox/channels"' in page and "Let the system choose" in page and "If it does not work, the next way" in page)
check("inbox: the SMS has its button", "Open messages" in page and "sms:+21698111222" in page)
response = admin.post(f"/inbox/whatsapp/{message.id}/sent", data={})
db.expire_all()
check("inbox: “Sent” works for an SMS too", db.get(CandidateEmail, message.id).status == "sent" and "by SMS" in where(response), where(response))
check("templates: “as chosen above the inbox” by default", tp.get(db, "training_reminder").channel == "auto")
admin.post("/inbox/channels", data={"way": "auto", "fallback": "true"})

# ---- D. The welcome message ----
db.expire_all()
check("welcome: the first round only starts the clock", welcome.send_due(db) == 0 and get_settings(db).welcome_since is not None)
old = Candidate(full_name="Old Before", email="old.before@example.com")
db.add(old)
db.flush()
db.add(Registration(candidate_id=old.id, program_id=program.id, program_name=program.name, total_amount=300, total_price=300, paid_amount=0,
                    payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow() - timedelta(days=3)))
new = Candidate(full_name="Nadia Nouvelle", email="nadia.nouvelle@example.com")
db.add(new)
db.flush()
db.add(Registration(candidate_id=new.id, program_id=program.id, program_name=program.name, total_amount=300, total_price=300, paid_amount=0,
                    payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow() + timedelta(seconds=5)))
db.commit()
emails.clear()
sent = welcome.send_due(db, now=datetime.utcnow() + timedelta(minutes=1))
check("welcome: whoever just registered gets it", sent == 1 and emails and emails[0][0] == "nadia.nouvelle@example.com", emails)
text = emails[0][2] if emails else ""
check("welcome: your words", "Bonjour Nadia" in text and "Summer Camp (Formation + Stage + Encadrement)" in text and "58840064" in text
      and "drive.google.com/file/d/1pfE6BrUoRndWB4hRuulPIQ1lDQ-MyJ9P" in text and "Les Berges du Lac" in text, text[:500])
check("welcome: no second signature under yours", "Merci pour votre confiance" not in text)
check("welcome: once", welcome.send_due(db, now=datetime.utcnow() + timedelta(minutes=2)) == 0)
check("welcome: nobody who registered before is written to", not any(item[0] == "old.before@example.com" for item in emails))
check("welcome: its words are in the templates", 'href="/inbox/templates?key=welcome"' in admin.get("/inbox/templates").text
      or "Welcome, just registered" in admin.get("/inbox/templates").text)

# ---- E. The payment reminder's delay ----
check("reminder: 24 hours by default", welcome.reminder_hours(db) == 24)
spec = tp.get(db, "payment_reminder")
response = admin.post("/inbox/templates/auto/payment_reminder/save", data={"subject": spec.subject, "title": spec.title, "body": spec.body, "closing": spec.closing,
                                                                          "style": spec.style, "is_active": "true", "channel": "auto", "hours": "48"})
db.expire_all()
check("reminder: the admin changes it on its template", welcome.reminder_hours(db) == 48, (response.status_code, where(response)))
check("reminder: the template page shows it", 'name="hours"' in admin.get("/inbox/templates?key=payment_reminder").text)
late = Candidate(full_name="Sami Retard", email="sami.retard@example.com")
db.add(late)
db.flush()
late_reg = Registration(candidate_id=late.id, program_id=program.id, program_name=program.name, total_amount=300, total_price=300, paid_amount=0,
                        payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING, registration_date=datetime.now(timezone.utc) - timedelta(hours=30))
db.add(late_reg)
db.commit()
emails.clear()
payment_reminders.send_first(db)
check("reminder: not after 30 hours when the admin chose 48", not any(item[0] == "sami.retard@example.com" for item in emails))
late_reg.registration_date = datetime.now(timezone.utc) - timedelta(hours=49)
db.commit()
payment_reminders.send_first(db)
check("reminder: after 49 hours it goes", any(item[0] == "sami.retard@example.com" for item in emails), emails[-3:])

# ---- F. Words and the phone ----
words = " ".join(line["text"] for group in settings_guide.plain_words(db) for line in group["lines"])
check("plain words: the reminder's delay, the welcome, the ways", "48 hours after registering" in words and "welcome message" in words and "Messages to candidates go" in words)
check("phone: the System page explains the iPhone", "Add to Home Screen" in admin.get("/system").text)

db.close()
check.done()

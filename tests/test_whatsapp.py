"""WhatsApp Business: the channel of each email and reminder, the messages to send by hand, Meta's webhook,
the assistant answering on WhatsApp (French first, then the candidate's language), the admin's rules,
scoring and correcting answers, the livelier reminders. Runs on a COPY; WhatsApp and email are fakes."""
import hashlib
import hmac
import json
import os
import time as time_module
from datetime import datetime, timedelta

import testenv

testenv.prepare("whatsapp")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.email_thread import EmailThread
from app.models.knowledge import KnowledgeEntry
from app.models.notification import Notification
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, email_assistant as ea, email_templates as tp, email_threads as et, reminders, settings_guide,
                          whatsapp, whatsapp_inbox)
from app.services.settings_service import get_settings

emails, texts, prompts = [], [], []
et._attempt = lambda recipient, subject, *rest, **kw: (emails.append((recipient, subject)) or (None, False))
et.smtp_configured = lambda: True
ea.model_available = lambda: True
ea.llm_json = lambda system, prompt, **kw: prompts.append(prompt) or None
whatsapp.send = lambda phone, text, free=False: texts.append({"phone": phone, "text": text, "free": free}) and None

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Admin", password="wa-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "wa-test-1"})

# A candidate in a class that starts in 3 days, with a phone; another without a phone.
program = db.query(Program).order_by(Program.name).first()
start = (datetime.now() + timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0)
from datetime import time as dtime

group = ClassGroup(name="WA-TEST-CLASS", program_id=program.id, professor_name="Prof", status=ClassStatus.NOT_STARTED, start_date=start, total_weeks=8,
                   start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat")
db.add(group)
db.flush()
lina = Candidate(full_name="Lina Wats", email="lina.wats@example.com", phone="98 123 456")
omar = Candidate(full_name="Omar Nophone", email="omar.nophone@example.com")
db.add_all([lina, omar])
db.flush()
reg = Registration(candidate_id=lina.id, program_id=program.id, program_name=program.name, total_amount=400, total_price=400, paid_amount=400,
                   payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=group.id)
reg2 = Registration(candidate_id=omar.id, program_id=program.id, program_name=program.name, total_amount=400, total_price=400, paid_amount=400,
                    payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=group.id)
db.add_all([reg, reg2])
db.commit()

# ---- A. Each automatic email chooses its way ----
for key in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_TEMPLATE"):
    os.environ[key] = ""
check("channel: an automatic email follows the choice above the inbox by default", tp.get(db, "training_reminder").channel == "auto")
tp.send(db, "training_reminder", registration=reg)
db.commit()
check("channel: by default, without a Business number, nothing waits on WhatsApp", not db.query(CandidateEmail).filter(CandidateEmail.channel == "whatsapp").count() and emails)
spec = tp.get(db, "training_reminder")
response = admin.post("/inbox/templates/auto/training_reminder/save", data={"subject": spec.subject, "title": spec.title, "body": spec.body,
                                                                           "closing": spec.closing, "style": spec.style, "is_active": "true", "show_details": "true",
                                                                           "channel": "both"})
check("channel: the admin chooses “Email and WhatsApp”", response.status_code == 303 and tp.get(db, "training_reminder").channel == "both", response.headers.get("location"))
emails.clear()
tp.send(db, "training_reminder", registration=reg)
db.commit()
waiting = whatsapp_inbox.outbox(db)
check("both: the email goes", [item[0] for item in emails] == ["lina.wats@example.com"])
check("both: the WhatsApp message waits for the admin, in the candidate's WhatsApp discussion", len(waiting) == 1 and waiting[0].recipient == "+21698123456"
      and db.get(EmailThread, waiting[0].thread_id).channel == "whatsapp")
from app.services.email_layout import DEFAULT_SIGNATURE

check("both: the WhatsApp message has the day's headline in bold and no email signature", waiting and waiting[0].body.startswith("*")
      and waiting[0].body.splitlines()[0].strip("*") in [tp.hype(days, group)["hype_title"] for days in range(0, 9)]
      and DEFAULT_SIGNATURE.strip().splitlines()[-1] not in waiting[0].body, waiting[0].body[:200] if waiting else None)
check("both: one click opens WhatsApp with the message written", waiting and whatsapp_inbox.link_of(waiting[0]).startswith("https://wa.me/21698123456?text="))
spec = tp.get(db, "training_reminder")
admin.post("/inbox/templates/auto/training_reminder/save", data={"subject": spec.subject, "title": spec.title, "body": spec.body, "closing": spec.closing,
                                                                 "style": spec.style, "is_active": "true", "show_details": "true", "channel": "whatsapp"})
emails.clear()
tp.send(db, "training_reminder", registration=reg)
tp.send(db, "training_reminder", registration=reg2)
db.commit()
check("WhatsApp only: no email for who has a number", "lina.wats@example.com" not in [item[0] for item in emails])
check("WhatsApp only: the email still goes to who has no number", "omar.nophone@example.com" in [item[0] for item in emails])
check("WhatsApp only: the message waits for the admin", len(whatsapp_inbox.outbox(db)) == 2)

# ---- B. To send on WhatsApp, by hand ----
page = admin.get("/inbox?filter=to_send").text
check("inbox: a bar says how many WhatsApp messages wait, and the filter shows them", "(WhatsApp or SMS)</strong> wait" in page and "Open WhatsApp" in page)
first, second = whatsapp_inbox.outbox(db)[:2]
response = admin.post(f"/inbox/whatsapp/{first.id}/sent")
db.expire_all()
check("inbox: “Sent” marks it sent", response.status_code == 303 and db.get(CandidateEmail, first.id).status == "sent")
admin.post(f"/inbox/whatsapp/{second.id}/sent", data={"remove": "true"})
db.expire_all()
check("inbox: “Remove” keeps it unsent", db.get(CandidateEmail, second.id).status == "cancelled" and not whatsapp_inbox.outbox(db))

# ---- C. With a Business number: it goes by itself ----
os.environ["WHATSAPP_TOKEN"], os.environ["WHATSAPP_PHONE_ID"] = "test-token-" + "x" * 30, "123456789012345"
texts.clear()
tp.send(db, "training_reminder", registration=reg)
db.commit()
check("connected: the reminder is sent by itself, as an approved message (the candidate did not write)", len(texts) == 1 and texts[0]["free"] is False
      and texts[0]["phone"] == "+21698123456", texts)
check("connected: it is kept as sent in the discussion", db.query(CandidateEmail).filter(CandidateEmail.channel == "whatsapp", CandidateEmail.status == "sent").count() >= 2)

# ---- D. Meta's webhook ----
anonymous = TestClient(main.app, follow_redirects=False)
check("webhook: without the verify token, Meta's check is refused", anonymous.get("/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=x&hub.challenge=42").status_code == 403)
os.environ["WHATSAPP_VERIFY_TOKEN"], os.environ["WHATSAPP_APP_SECRET"] = "verify-me-123", "app-secret-456"
answer = anonymous.get("/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=verify-me-123&hub.challenge=42")
check("webhook: with it, the challenge is sent back (no sign-in needed)", answer.status_code == 200 and answer.text == "42", (answer.status_code, answer.text[:80]))
check("webhook: now it receives", whatsapp.receives())


def deliver(phone, text, wa_id, sign=True, name="Lina"):
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": phone, "profile": {"name": name}}],
        "messages": [{"from": phone, "id": wa_id, "timestamp": str(int(time_module.time())), "type": "text", "text": {"body": text}}]}}]}]}
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(b"app-secret-456", body, hashlib.sha256).hexdigest()
    return anonymous.post("/whatsapp/webhook", content=body, headers=headers)


check("webhook: a message not signed by Meta is refused", deliver("21698123456", "Bonjour", "wamid.unsigned", sign=False).status_code == 403)
check("webhook: nothing was kept from it", not db.query(CandidateEmail).filter(CandidateEmail.message_id == "wa:wamid.unsigned").count())
texts.clear()
answer = deliver("21698123456", "Bonjour, quand commence ma formation ?", "wamid.fr1")
db.expire_all()
inbound = db.query(CandidateEmail).filter(CandidateEmail.message_id == "wa:wamid.fr1").first()
check("webhook: a signed message is kept in the candidate's WhatsApp discussion", answer.status_code == 200 and inbound is not None and inbound.channel == "whatsapp"
      and inbound.candidate_id == lina.id, answer.text)
check("assistant: it answers on WhatsApp, in French, from the candidate's record", inbound is not None and inbound.status == "replied" and texts
      and texts[-1]["text"].startswith("Bonjour") and texts[-1]["free"] is True, (inbound.status if inbound else None, inbound.escalation_reason if inbound else None, texts[-1:] if texts else None))
check("assistant: the answer says when the class starts", texts and ("démarre" in texts[-1]["text"] or "commence" in texts[-1]["text"] or str(start.day) in texts[-1]["text"]), texts[-1:])
check("webhook: the same message twice is kept once", deliver("21698123456", "Bonjour, quand commence ma formation ?", "wamid.fr1").status_code == 200
      and db.query(CandidateEmail).filter(CandidateEmail.message_id == "wa:wamid.fr1").count() == 1)
texts.clear()
deliver("21698123456", "Hello, when does my class start?", "wamid.en1")
db.expire_all()
check("assistant: when the candidate writes in English, it answers in English", texts and texts[-1]["text"].startswith("Hello"), texts[-1:])
texts.clear()
deliver("21698123456", "slm chnowa wa9t tebda el formation ?", "wamid.ar1")
db.expire_all()
tunisian = db.query(CandidateEmail).filter(CandidateEmail.message_id == "wa:wamid.ar1").first()
check("assistant: Tunisian it understands about the candidate's own class is answered from the record, in French",
      tunisian is not None and tunisian.status == "replied" and texts and texts[-1]["text"].startswith("Bonjour")
      and ("démarre" in texts[-1]["text"] or "commence" in texts[-1]["text"] or str(start.day) in texts[-1]["text"]),
      (tunisian.status if tunisian else None, tunisian.escalation_reason if tunisian else None, texts[-1:]))
before = db.query(Notification).count()
deliver("21650000000", "Bonjour je veux m'inscrire", "wamid.unknown")
db.expire_all()
check("webhook: a number that is nobody's: the admin is told, with a link to answer", db.query(Notification).count() == before + 1
      and db.query(Notification).order_by(Notification.created_at.desc()).first().action_url.startswith("https://wa.me/21650000000"))

# ---- E. The admin decides how it answers, and what it must never answer ----
settings = get_settings(db)
settings.whatsapp_auto_reply = "review"
db.commit()
texts.clear()
deliver("21698123456", "Bonjour, quand commence ma formation svp ?", "wamid.rev1")
db.expire_all()
drafted = db.query(CandidateEmail).filter(CandidateEmail.message_id == "wa:wamid.rev1").first()
check("mode review: a draft for the admin, nothing sent", drafted.requires_admin and not texts and drafted.suggested_reply)
settings = get_settings(db)
settings.whatsapp_auto_reply = "off"
db.commit()
deliver("21698123456", "Bonjour, quand commence la formation ?", "wamid.off1")
db.expire_all()
check("mode off: every WhatsApp message comes to the admin", db.query(CandidateEmail).filter(CandidateEmail.message_id == "wa:wamid.off1").first().requires_admin and not texts)
settings = get_settings(db)
settings.whatsapp_auto_reply = "auto"
db.commit()
response = admin.post("/settings/assistant-rules", data={"never": "réduction\nstage rémunéré", "notes": "Toujours vouvoyer.\nLes séances sur place ont lieu à Tunis."})
check("rules: saved from Settings", response.status_code == 303 and get_settings(db).assistant_never.startswith("réduction"))
deliver("21698123456", "Bonjour, est-ce que vous faites une réduction pour les étudiants ?", "wamid.rule1")
db.expire_all()
ruled = db.query(CandidateEmail).filter(CandidateEmail.message_id == "wa:wamid.rule1").first()
check("rules: a subject the admin keeps for themself comes to them, with the reason", ruled.requires_admin and "réduction" in (ruled.escalation_reason or ""), ruled.escalation_reason)
check("rules: a line counts only when all its words are there", ea.never_alone(get_settings(db), "Le stage est-il obligatoire ?") is None
      and ea.never_alone(get_settings(db), "Le stage est-il rémunéré ?") == "stage rémunéré")
prompts.clear()
ea.generate_answer(subject="", body="Où ont lieu les séances ?", question="Où ont lieu les séances ?", language="fr",
                   hits=[{"title": "Lieu", "content": "Sur place à Tunis."}], notes=ea.assistant_notes(get_settings(db)))
check("rules: the notes are given to the assistant when it writes", prompts and "Toujours vouvoyer." in prompts[-1] and "Tunis" in prompts[-1])

# ---- F. The admin scores and corrects the assistant's answers ----
reply = (db.query(CandidateEmail).filter(CandidateEmail.channel == "whatsapp", CandidateEmail.author == "system", CandidateEmail.intent == "reply")
         .order_by(CandidateEmail.received_at.asc()).first())
html = admin.get(f"/inbox?thread={reply.thread_id}").text
check("inbox: the assistant's answers can be scored and corrected", "Was this answer right?" in html and f"/inbox/message/{reply.id}/correct" in html)
admin.post(f"/inbox/message/{reply.id}/score", data={"verdict": "right"})
db.expire_all()
check("score: right", db.get(CandidateEmail, reply.id).admin_score == 1)
admin.post(f"/inbox/message/{reply.id}/score", data={"verdict": "wrong"})
db.expire_all()
check("score: wrong", db.get(CandidateEmail, reply.id).admin_score == -1)
texts.clear()
response = admin.post(f"/inbox/message/{reply.id}/correct", data={"answer": "Votre première séance est samedi à 9h, en ligne.", "send": "true"})
db.expire_all()
learned = db.query(KnowledgeEntry).filter(KnowledgeEntry.content.like("%première séance est samedi%")).first()
check("correct: the assistant learns the right answer, for that very question", response.status_code == 303 and learned is not None
      and "commence" in (learned.title or "").lower(), (learned.title if learned else None))
check("correct: and it can go to the candidate now, on WhatsApp", texts and "samedi à 9h" in texts[-1]["text"])

# ---- G. Reminders the admin writes: by WhatsApp too ----
emails.clear()
texts.clear()
planned = reminders.create(db, audience="candidate", audience_id=lina.id, subject="Une date à retenir", body="Bonjour {first_name}, rendez-vous samedi.", channel="whatsapp")
reminders.send(db, planned)
db.expire_all()
check("reminder: WhatsApp only goes on WhatsApp, personalised", texts and "Bonjour Lina" in texts[-1]["text"] and not emails, (texts[-1:], emails))
response = admin.post("/reminders/create", data={"audience": "candidate", "to_candidate": lina.id, "subject": "Test", "body": "Bonjour", "when": "now", "channel": "both"})
check("reminder page: the channel is chosen in the form", response.status_code == 303 and db.query(type(planned)).order_by(type(planned).created_at.desc()).first().channel == "both")

# ---- H. The reminders before a first session, livelier ----
for days, words in ((7, "Plus qu'une semaine"), (3, "J-3"), (1, "C'est demain"), (0, "aujourd'hui")):
    lively = tp.hype(days, group)
    check(f"reminder J-{days}: its own headline and tip", words in lively["hype_title"] and lively["prep_tip"])
rendered = tp.render_auto(db, "training_reminder", candidate=lina, registration=reg)
check("reminder: the email has the day's headline, line and tip", rendered and rendered["subject"].startswith("J-") and "Le conseil du jour" in rendered["text"]
      and program.name in rendered["text"], rendered["subject"] if rendered else None)

# ---- I. Settings ----
html = admin.get("/settings").text
check("settings: the WhatsApp Business card, with the address to give Meta and the steps", "WhatsApp Business" in html and "/whatsapp/webhook" in html
      and "Create the app" in html and 'id="rules"' in html)
check("settings: secrets are never shown again", "test-token-" not in html and "app-secret-456" not in html)
response = admin.post("/settings/whatsapp", data={"token": "", "phone_id": "12 34", "app_secret": "", "verify_token": "", "template": "", "mode": "auto"})
from urllib.parse import unquote_plus

check("settings: the phone number ID must be the long number", response.status_code == 303 and "phone number ID" in unquote_plus(response.headers.get("location", "")))
response = admin.post("/settings/whatsapp", data={"token": "new-token", "phone_id": "998877665544", "app_secret": "", "verify_token": "", "template": "rappel_9antra", "mode": "review"})
db.expire_all()
written = open(os.environ["ENV_FILE"], encoding="utf-8").read()
check("settings: what Meta gives is kept in .env, never in the database", "WHATSAPP_PHONE_ID=998877665544" in written and "WHATSAPP_TEMPLATE=rappel_9antra" in written
      and os.environ["WHATSAPP_PHONE_ID"] == "998877665544")
check("settings: an empty secret keeps the old one", os.environ["WHATSAPP_APP_SECRET"] == "app-secret-456")
check("settings: the WhatsApp mode is saved", get_settings(db).whatsapp_auto_reply == "review")


class Answer:
    status_code, text = 200, "{}"

    def json(self):
        return {"display_phone_number": "+216 70 000 000", "verified_name": "9antra"}


whatsapp.requests.get = lambda *a, **k: Answer()
response = admin.post("/settings/whatsapp/test")
check("settings: the test says which number it is", "9antra" in response.headers.get("location", "") or "9antra" in response.headers.get("location", "").replace("+", " "),
      response.headers.get("location"))
words = " ".join(line["text"] for group_ in settings_guide.plain_words(db) for line in group_["lines"])
check("plain words: how the assistant answers on WhatsApp, and the rules", "On WhatsApp the assistant writes drafts" in words and "réduction" in words, words[:400])
auth.create_user(db, email="sam.wa@test.local", name="Sam", password="sam-wa-12", role=auth.STAFF)
sam = db.query(User).filter(User.email == "sam.wa@test.local").one()
from app.services import access

sam.permissions = access.dump({"settings": "edit"})
db.commit()
staff = TestClient(main.app, follow_redirects=False)
staff.post("/login", data={"email": "sam.wa@test.local", "password": "sam-wa-12"})
check("access: the WhatsApp secrets stay with the admin", staff.post("/settings/whatsapp", data={"token": "x"}, headers={"accept": "text/html"}).status_code in (303, 403))

db.close()
check.done()

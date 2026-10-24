"""Tunisian understood (incoming messages and the admin's own notes), rewording in French or English, "reword and send
at once", questions about the learning sent to the professor (paid candidates only; the others are told to pay first),
the professor's answer back to the candidate, reminders to professors, the professor's contact in replies, and SMS
through an Android phone (signed webhook, answers by SMS). Runs on a COPY; email, WhatsApp and the phone are fakes."""
import hashlib
import hmac
import json
import os
import time as time_module
from datetime import datetime, time as dtime, timedelta
from urllib.parse import parse_qs, unquote_plus, urlparse

import testenv

testenv.prepare("phoneprof")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Professor, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.email_thread import EmailThread
from app.models.notification import Notification
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.team_message import TeamMessage
from app.services import (access, auth, channels, course_questions, derja, email_assistant as ea, email_templates as tp,
                          email_threads as et, reply_writer, sms, team_messages, whatsapp_inbox)
from app.services.settings_service import get_settings

emails, texts, prompts = [], [], []
et._attempt = lambda recipient, subject, *rest, **kw: (emails.append((recipient, subject, rest[0] if rest else "")) or (None, False))
et.smtp_configured = lambda: True
ea.model_available = lambda: True
REPLY = {"text": None}


def fake_llm(system, prompt, **kw):
    prompts.append(prompt)
    return {"reply": REPLY["text"]} if REPLY["text"] else None


ea.llm_json = fake_llm


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="phone-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "phone-test-1"})

# ---- A. Tunisian ----
text = "3aslema, nheb na3ref 9adech soum formation python w waqtech tebda?"
check("derja: recognised", derja.is_tunisian(text))
check("derja: its words and their meaning", {"3aslema", "nheb", "9adech", "waqtech"} <= {word for word, *_ in derja.words_in(text)})
check("derja: French and English are not taken for it", not derja.is_tunisian("Bonjour, je voudrais le prix de la formation") and not derja.is_tunisian("Hello, when does it start?"))
check("derja: read in French", "combien" in derja.to_french(text) and "quand" in derja.to_french(text) and "python" in derja.to_french(text), derja.to_french(text))
found = ea.analyse("", text)
check("incoming: understood, answered in French, about the price", found["language"] == "fr" and found["derja"] and found["intent"] == "pricing", (found["language"], found["intent"]))

# ---- B. The admin writes in Tunisian; it is reworded in French or English ----
REPLY["text"] = "Hello Amine,\n\nYes, Saturday is possible, room 2.\n\nWe remain at your disposal."
written = reply_writer.reformulate("ey possible samedi salle 2, taw nab3athlek el adresse", first_name="Amine", language="en")
check("reword: written in the language chosen", written and written["reply"].startswith("Hello Amine"), written)
check("reword: the model is told what the Tunisian words mean", "Tunisian Arabic" in prompts[-1] and "“taw” = now" in prompts[-1] and "only in English" in prompts[-1], prompts[-1][-600:])
check("reword: Tunisian words are not reported as left out", not (written or {}).get("warning"), written)
REPLY["text"] = "Bonjour,\n\nLe prix est de 450 DT.\n\nCordialement"
written = reply_writer.reformulate("le prix c 390 dt", language="fr")
check("reword: a figure the admin did not write is reported", written and written["warning"] and "450" in written["warning"], written)
REPLY["text"] = "Bonjour,\n\nOui, c'est possible samedi.\n\nBien à vous."
response = admin.post("/inbox/reformulate", json={"text": "ey possible samedi", "language": "fr"})
check("reword anywhere (team chat, composer): an address for it", response.status_code == 200 and response.json()["reply"].startswith("Bonjour"))

# ---- C. A class, its professor, paid and unpaid candidates ----
REPLY["text"] = None
program = db.query(Program).order_by(Program.name).first()
nour = Professor(name="Nour Prof", specialties=program.name, email="nour.prof@example.com", phone="22 444 555")
db.add(nour)
db.flush()
group = ClassGroup(name="PROF-ROUTE-1", program_id=program.id, professor_id=nour.id, professor_name=nour.name, status=ClassStatus.ONGOING,
                   start_date=datetime.now() - timedelta(days=10), total_weeks=8, start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat")
db.add(group)
db.flush()
auth.create_user(db, email="nour.login@example.com", name="Nour Prof", password="nour-prof-12", role=auth.PROFESSOR, professor_id=nour.id)
prof = TestClient(main.app, follow_redirects=False)
prof.post("/login", data={"email": "nour.login@example.com", "password": "nour-prof-12"})


def person(name, email, paid, phone=None):
    one = Candidate(full_name=name, email=email, phone=phone)
    db.add(one)
    db.flush()
    reg = Registration(candidate_id=one.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390,
                       paid_amount=390 if paid else 0, payment_status=PaymentStatus.PAID if paid else PaymentStatus.UNPAID,
                       status=RegistrationStatus.ASSIGNED if paid else RegistrationStatus.PENDING, class_group_id=group.id if paid else None)
    db.add(reg)
    db.commit()
    return one, reg


lina, lina_reg = person("Lina Payee", "lina.payee@example.com", True, phone="98 111 333")
omar, omar_reg = person("Omar Nonpaye", "omar.nonpaye@example.com", False)
check("learning question: an exercise not understood", course_questions.is_learning_question("Je n'ai pas compris l'exercice 3 de la séance d'hier"))
check("learning question: a price is not one", not course_questions.is_learning_question("Quel est le prix de la formation Python ?"))
check("professor line: for a paid candidate, with the email", course_questions.professor_line(db, lina) == "Vous pouvez aussi poser vos questions à votre formateur, Nour Prof (nour.prof@example.com).")
check("professor line: nothing for an unpaid one", course_questions.professor_line(db, omar) == "")

# ---- D. A paid candidate asks: the professor answers ----
emails.clear()
ea.ingest_inbound(db, sender="lina.payee@example.com", subject="Exercice", body="Bonjour, je n'ai pas compris l'exercice 3 de la séance d'hier, pouvez-vous m'expliquer ?",
                  message_id="<route-1@example.com>")
db.expire_all()
asked = db.query(CandidateEmail).filter(CandidateEmail.message_id == "<route-1@example.com>").one()
row = db.query(TeamMessage).filter(TeamMessage.question_message_id == asked.id).one_or_none()
check("forward: the question goes to the professor", row is not None and row.person == f"professor:{nour.id}" and "exercice 3" in row.body, asked.analysis)
check("forward: in the application, by email and on WhatsApp", {item.channel for item in db.query(TeamMessage).filter(TeamMessage.batch == row.batch)} == {"app", "email", "whatsapp"} if row else False)
check("forward: the professor gets the email", any(item[0] == "nour.prof@example.com" for item in emails))
check("forward: the candidate is told, with the professor's email", any(item[0] == "lina.payee@example.com" and "transmise à votre formateur, Nour Prof" in item[2]
                                                                       and "nour.prof@example.com" in item[2] for item in emails), [item[1] for item in emails])
check("forward: nothing waits for the admin", asked.status == "no_reply" and not asked.requires_admin)
page = prof.get("/account/messages")
check("professor: the question with an answer box", page.status_code == 200 and "Answer the candidate" in page.text and "exercice 3" in page.text, page.status_code)
emails.clear()
response = prof.post(f"/account/messages/question/{row.id}/answer", data={"body": "Dans l'exercice 3, la boucle s'arrête quand i vaut 10."})
db.expire_all()
check("professor: the answer goes to the candidate, signed", "sent to the candidate" in where(response)
      and any(item[0] == "lina.payee@example.com" and "la boucle s'arrête" in item[2] and "Nour Prof" in item[2] for item in emails), (where(response), emails[-1:]))
check("professor: the question is answered", db.get(TeamMessage, row.id).answered_at is not None and db.get(CandidateEmail, asked.id).status == "replied")
check("professor: a second answer is refused", "already answered" in where(prof.post(f"/account/messages/question/{row.id}/answer", data={"body": "encore"})))
check("admin: told the professor answered", db.query(Notification).filter(Notification.title.like("Nour Prof answered%")).count() == 1)

# ---- E. An unpaid candidate asks: pay first ----
emails.clear()
ea.ingest_inbound(db, sender="omar.nonpaye@example.com", subject="Question", body="Bonjour, comment installer python et lancer le code du TP ?",
                  message_id="<route-2@example.com>")
db.expire_all()
check("unpaid: told to pay first, with what remains", any(item[0] == "omar.nonpaye@example.com" and "une fois l'inscription réglée" in item[2] and "390 DT" in item[2] for item in emails),
      [item[2][:200] for item in emails])
check("unpaid: nothing goes to the professor", db.query(TeamMessage).filter(TeamMessage.question_candidate_id == omar.id).count() == 0)

# ---- F. Reminders to the professor ----
ea.ingest_inbound(db, sender="lina.payee@example.com", subject="TP", body="Bonjour, j'ai une erreur dans le code du TP 4, pouvez-vous m'aider ?",
                  message_id="<route-3@example.com>")
db.expire_all()
second = db.query(TeamMessage).filter(TeamMessage.question_candidate_id == lina.id, TeamMessage.answered_at.is_(None)).one()
emails.clear()
course_questions.remind_due(db, now=datetime.utcnow() + timedelta(hours=25))
db.expire_all()
check("remind: after a day the professor is reminded (email too)", db.get(TeamMessage, second.id).reminded_at is not None and any(item[0] == "nour.prof@example.com" for item in emails))
course_questions.remind_due(db, now=datetime.utcnow() + timedelta(hours=26))
check("remind: once only", sum(1 for item in emails if item[0] == "nour.prof@example.com") == 1)
course_questions.remind_due(db, now=datetime.utcnow() + timedelta(hours=49))
db.expire_all()
check("remind: after two days the admin is told", db.query(Notification).filter(Notification.title.like("Nour Prof has not answered%")).count() == 1)

# ---- G. "Reword and send at once" ----
thread = db.query(EmailThread).filter(EmailThread.candidate_id == lina.id).first()
response = admin.post("/inbox/reword-mode", data={"on": "1"})
db.expire_all()
check("reword and send: switched on, remembered", response.json()["on"] is True and get_settings(db).reword_and_send is True)
check("reword and send: the box shows it ticked", 'id="reword-send" checked' in admin.get(f"/inbox?thread={thread.id}").text)
REPLY["text"] = "Bonjour Lina,\n\nOui, la séance de samedi est maintenue.\n\nBonne journée."
emails.clear()
response = admin.post(f"/inbox/thread/{thread.id}/reply", data={"body": "ey seance samedi maintenue", "reword": "true", "reword_language": "fr"})
check("reword and send: reworded and sent at once", any(item[0] == "lina.payee@example.com" and "la séance de samedi est maintenue" in item[2] for item in emails), where(response))
REPLY["text"] = "Bonjour Lina,\n\nLe prix est de 450 DT.\n\nBonne journée."
emails.clear()
response = admin.post(f"/inbox/thread/{thread.id}/reply", data={"body": "le prix 390 dt", "reword": "true", "reword_language": "fr"})
check("reword and send: a changed figure waits for the admin", not emails and "Not sent: read the reworded answer first" in where(response) and "450" in where(response), where(response))
admin.post("/inbox/reword-mode", data={"on": "0"})
response = admin.post(f"/inbox/thread/{thread.id}/reply", data={"body": "ey, taw nab3athlek el programme", "learn": "true", "question": "programme"})
check("learn: an answer written in Tunisian, sent as it is, is not learned", "Not learned: it is written in Tunisian" in where(response), where(response))
check("brochure: a request for a program's PDF is not a learning question", not course_questions.is_learning_question("Can you send me the brochure of Python please?"))

# ---- H. The professor's contact in the class emails ----
rendered = tp.render_auto(db, "training_reminder", candidate=lina, registration=lina_reg, preview=True)
check("class emails: “you can also ask your professor”, with the email", rendered and "Nour Prof (nour.prof@example.com)" in rendered["text"])

# ---- I. SMS through the Android phone ----
REPLY["text"] = None
os.environ.update({"SMS_GATEWAY_URL": "http://192.168.1.50:8080", "SMS_GATEWAY_USER": "sms", "SMS_GATEWAY_PASSWORD": "pw", "SMS_SIGNING_KEY": "sign-key-1"})
sent_sms = []
sms.send = lambda phone, text: sent_sms.append((phone, text)) and None
check("sms: goes by itself once the phone is connected", sms.configured() and channels.status()["sms"]["alone"])


def signed(payload):
    body = json.dumps(payload).encode()
    stamp = str(int(time_module.time()))
    return body, {"X-Signature": hmac.new(b"sign-key-1", body + stamp.encode(), hashlib.sha256).hexdigest(), "X-Timestamp": stamp,
                  "Content-Type": "application/json"}


anon = TestClient(main.app, follow_redirects=False)
payload = {"event": "sms:received", "id": "evt1", "payload": {"messageId": "SMS-1", "message": "3aslema, waqtech tebda el seance mte3 samedi?", "sender": "98111333"}}
body, headers = signed(payload)
check("sms: a wrong signature is refused", anon.post("/sms/webhook", content=body, headers={**headers, "X-Signature": "00" * 32}).status_code == 403)
check("sms: an old timestamp is refused", not sms.signature_ok(body, headers["X-Signature"], str(int(time_module.time()) - 3600)))
response = anon.post("/sms/webhook", content=body, headers=headers)
db.expire_all()
kept = db.query(CandidateEmail).filter(CandidateEmail.message_id == "sms:SMS-1").one_or_none()
check("sms: a candidate's SMS (local number) is kept in their SMS discussion", response.status_code == 200 and kept is not None and kept.candidate_id == lina.id
      and db.get(EmailThread, kept.thread_id).channel == "sms", response.text)
check("sms: read by the assistant, the Tunisian understood", kept and kept.status != "processing" and "Written in Tunisian" in (kept.analysis or ""), kept and (kept.status, kept.analysis))
check("sms: the same SMS twice is kept once", anon.post("/sms/webhook", content=body, headers=signed(payload)[1]).json()["kept"] == 0)
message = whatsapp_inbox.send_sms(db, lina, "Votre séance commence samedi à 9h.", kind="reply")
db.commit()
check("sms: an answer goes from the phone", message.status == "sent" and sent_sms and sent_sms[-1][0] == "+21698111333")
sms.send = lambda phone, text: "The phone did not answer"
message = whatsapp_inbox.send_sms(db, lina, "Test", kind="reply")
db.commit()
check("sms: a failure is shown and the admin told", message.status == "failed" and db.query(Notification).filter(Notification.title == "SMS not delivered").count() >= 1)
sms.send = lambda phone, text: sent_sms.append((phone, text)) and None
payload = {"event": "sms:received", "id": "evt2", "payload": {"messageId": "SMS-2", "message": "Je serai absent samedi", "sender": "+21622444555"}}
body, headers = signed(payload)
anon.post("/sms/webhook", content=body, headers=headers)
db.expire_all()
check("sms: the professor's SMS goes into the team chat", db.query(TeamMessage).filter(TeamMessage.message_id == "sms:SMS-2", TeamMessage.channel == "sms").count() == 1)
page = admin.get("/settings").text
check("settings: the SMS card, the phone's details kept secret", 'id="sms"' in page and "SMS Gateway for Android" in page and "pw" not in page.split('id="sms"')[1][:3000]
      and "/sms/webhook" in page)
auth.create_user(db, email="sam.sms@test.local", name="Sam", password="sam-sms-12", role=auth.STAFF)
sam = db.query(User).filter(User.email == "sam.sms@test.local").one()
sam.permissions = access.dump({"settings": "edit"})
db.commit()
staff = TestClient(main.app, follow_redirects=False)
staff.post("/login", data={"email": "sam.sms@test.local", "password": "sam-sms-12"})
check("settings: the phone's details are the admin's", staff.post("/settings/sms", data={"url": "http://x"}, headers={"accept": "text/html"}).status_code in (303, 403))
for name in ("SMS_GATEWAY_URL", "SMS_GATEWAY_USER", "SMS_GATEWAY_PASSWORD", "SMS_SIGNING_KEY"):
    os.environ[name] = ""

db.close()
check.done()

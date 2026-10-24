"""The assistant sends for the admin (email, WhatsApp, SMS; candidates, professors, team, any address or number),
rewords first (or "tel quel"), sends at once unless switched off, and says for each person and each way what went and
why not; resources with every answer; who teaches each program (as many as you like); moving emojis.
Runs on a COPY; email, WhatsApp and SMS are fakes."""
from datetime import datetime
from urllib.parse import unquote_plus

import testenv

testenv.prepare("assistsend")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, Professor, Program, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.team_message import TeamMessage
from app.services import access, auth, chatbot, chatbot_send, email_assistant as ea, email_service, email_threads as et, whatsapp

emails, direct, prompts = [], [], []
et._attempt = lambda recipient, subject, *rest, **kw: (emails.append((recipient, subject, rest[0] if rest else "")) or (None, False))
et.smtp_configured = lambda: True
email_service.send_email = lambda **kw: direct.append(kw) or True
whatsapp.configured = lambda: False
ea.model_available = lambda: True
REPLY = {"text": "Bonjour Fares,\n\nLa séance de samedi est annulée.\n\nBonne journée."}
ea.llm_json = lambda system, prompt, **kw: prompts.append(prompt) or ({"reply": REPLY["text"]} if REPLY["text"] else None)


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="send-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "send-test-1"})
fares = Candidate(full_name="Fares Zitouni", email="fares.zitouni@example.com", phone="98 444 555")
nophone = Candidate(full_name="Lotfi Sansnumero", email="lotfi.sans@example.com")
twin1 = Candidate(full_name="Sana Ayari", email="sana.ayari@example.com")
twin2 = Candidate(full_name="Sana Belhadj", email="sana.belhadj@example.com")
nour = Professor(name="Nour Formatrice", specialties="DevOps", email="nour.formatrice@example.com", phone="22 777 888")
db.add_all([fares, nophone, twin1, twin2, nour])
db.commit()

# ---- A. Understanding the request ----
parsed = chatbot_send.parse("envoie un email à Fares pour lui dire que la séance de samedi est annulée")
check("parse: who, what, and the way", parsed["who"] == "Fares" and parsed["what"] == "la séance de samedi est annulée" and parsed["ways"] == ["email"], parsed)
parsed = chatbot_send.parse("send a WhatsApp to Nour Formatrice saying the room changed, in english")
check("parse: English and WhatsApp", parsed["ways"] == ["whatsapp"] and parsed["english"] and parsed["what"].startswith("the room changed"), parsed)
check("parse: an SMS to a number, as is", (lambda p: p["ways"] == ["sms"] and p["as_is"] and "98 123 456" in p["who"])(chatbot_send.parse("sms au 98 123 456 : rendez-vous à 9h tel quel")))
check("parse: words between quotes", chatbot_send.parse("écris à Fares « Bienvenue chez nous »")["what"] == "Bienvenue chez nous")
check("parse: a question is not a request to send", chatbot_send.parse("who has not paid?") is None)

# ---- B. Sent at once, reworded, with what happened ----
found = chatbot.answer(db, "envoie un email à Fares Zitouni pour lui dire que la séance de samedi est annulée", [])
check("email: reworded and sent at once", any(item[0] == "fares.zitouni@example.com" and "La séance de samedi est annulée" in item[2] for item in emails), found["reply"])
check("email: the answer says what went", "✓ Fares Zitouni · email : envoyé." in found["reply"] and "Le texte envoyé" in found["reply"], found["reply"])
check("email: the rewording was asked to keep the meaning", any("la séance de samedi est annulée" in prompt for prompt in prompts))
found = chatbot.answer(db, "envoie un whatsapp à Fares Zitouni pour lui dire que la séance de samedi est annulée", [])
check("whatsapp: without a Business number it waits, and says why", "⏳ Fares Zitouni · WhatsApp" in found["reply"] and "no WhatsApp Business number" in found["reply"]
      and any("wa.me" in link["url"] for link in found["links"]), found["reply"])
found = chatbot.answer(db, "envoie un sms à Fares Zitouni pour lui dire que la séance de samedi est annulée", [])
check("sms: without the Android phone it waits, and says why", "SMS" in found["reply"] and "no Android phone" in found["reply"]
      and any(link["url"].startswith("sms:") for link in found["links"]), found["reply"])
found = chatbot.answer(db, "envoie un whatsapp à Lotfi Sansnumero pour lui dire que la séance de samedi est annulée", [])
check("problem: no number, not sent, and why", "✗ Lotfi Sansnumero · WhatsApp : pas envoyé, parce que their phone number cannot be used" in found["reply"], found["reply"])
emails.clear()
found = chatbot.answer(db, "envoie un message à Nour Formatrice pour lui dire que la séance de samedi est annulée", [])
check("professor: by email (no account in the application), kept in the team chat", "✓ Nour Formatrice · email" in found["reply"]
      and any(item[0] == "nour.formatrice@example.com" for item in emails)
      and db.query(TeamMessage).filter(TeamMessage.person == f"professor:{nour.id}").count() >= 1, found["reply"])
found = chatbot.answer(db, "send an email to someone@partner.tn saying the meeting is at 10, as is", [])
check("any address: sent, the words as they are", direct and direct[-1]["to_email"] == "someone@partner.tn" and "the meeting is at 10" in direct[-1]["text_body"]
      and "✓ someone@partner.tn" in found["reply"], found["reply"])
found = chatbot.answer(db, "sms au 98 123 456 : rendez-vous demain à 9h tel quel", [])
check("any number: the SMS is ready from your phone", "⏳ 98 123 456" in found["reply"] and any(link["url"].startswith("sms:+21698123456") for link in found["links"]), found["reply"])

# ---- C. What stops it ----
found = chatbot.answer(db, "envoie un email à Sana pour lui dire bonjour", [])
check("two people with that name: asked, nothing sent", "Plusieurs personnes" in found["reply"] and "Sana Ayari" in found["reply"] and "Sana Belhadj" in found["reply"])
found = chatbot.answer(db, "envoie un email à Zorro pour lui dire bonjour", [])
check("nobody with that name: asked", "Je ne trouve personne" in found["reply"])
REPLY["text"] = "Bonjour Fares,\n\nLe prix est de 450 DT.\n\nBonne journée."
emails.clear()
found = chatbot.answer(db, "envoie un email à Fares Zitouni pour lui dire que le prix est 390 dt", [])
check("a figure changed by the rewording: nothing sent, and why", not emails and "Rien n'est parti" in found["reply"] and "450" in found["reply"], found["reply"])
REPLY["text"] = None
found = chatbot.answer(db, "envoie un email à Fares Zitouni pour lui dire que le prix est 390 dt", [])
check("the rewording does not answer: nothing sent, and how to send as is", not emails and "tel quel" in found["reply"])
found = chatbot.answer(db, "envoie un email à Fares Zitouni pour lui dire que le prix est 390 dt tel quel", [])
check("as is: the admin's own words go", any(item[0] == "fares.zitouni@example.com" and "le prix est 390 dt" in item[2] for item in emails), found["reply"])
REPLY["text"] = "Bonjour Fares,\n\nLa séance de samedi est annulée.\n\nBonne journée."
response = admin.post("/assistant/auto-send", data={"on": "0"})
db.expire_all()
check("switch: “Send at once” turned off", response.json()["on"] is False and not chatbot_send.auto_send(db))
emails.clear()
found = chatbot.answer(db, "envoie un email à Fares Zitouni pour lui dire que la séance de samedi est annulée", [])
check("switched off: nothing goes, the text is ready", not emails and "désactivé" in found["reply"] and "La séance de samedi est annulée" in found["reply"])
admin.post("/assistant/auto-send", data={"on": "1"})
check("switch: in the assistant's window", 'id="assistant-auto"' in admin.get("/").text)
auth.create_user(db, email="viewer@test.local", name="Vera Viewer", password="viewer-pass-1", role=auth.STAFF)
viewer = db.query(User).filter(User.email == "viewer@test.local").one()
viewer.permissions = access.dump({"assistant": "view"})
db.commit()
viewer_client = TestClient(main.app, follow_redirects=False)
viewer_client.post("/login", data={"email": "viewer@test.local", "password": "viewer-pass-1"})
emails.clear()
answer = viewer_client.post("/assistant/chat", json={"message": "envoie un email à Fares Zitouni pour lui dire que la séance de samedi est annulée"}).json()
check("someone who may only look cannot send", not emails and "réservé" in answer["reply"], answer["reply"])

# ---- D. Sources and resources reach the window ----
answer = admin.post("/assistant/chat", json={"message": "who has not paid?"}).json()
check("chat: the sources reach the window", answer.get("sources") and answer["sources"][0]["label"].startswith("The application's records"), answer.get("sources"))
program = db.query(Program).order_by(Program.name).first()
answer = admin.post("/assistant/chat", json={"message": f"ressources sur {program.name}"}).json()
check("resources: a program's page and its PDF", any(item["url"] == f"/programs/{program.id}" for item in answer["resources"])
      and any(item["url"] == f"/programs/{program.id}/pdf" for item in answer["resources"]), answer["resources"])

# ---- E. Who teaches each program (as many professors as you like) ----
page = admin.get("/classes/?tab=professors").text
check("professors: who teaches each program, no target", 'id="coverage"' in page and "Who teaches each program" in page and "3 for each" not in page)
check("professors: places to name are no longer made", admin.post("/classes/professors/fill", data={"program_id": program.id}).status_code in (404, 405))
other = db.query(Program).order_by(Program.name.desc()).first()
admin.post(f"/classes/professors/{nour.id}/specialty", data={"program_id": other.id})
db.expire_all()
check("also teaches it: one click", other.name in (db.get(Professor, nour.id).specialties or ""))
admin.post("/classes/professors/create", data={"name": "Karim Nouveau", "programs": other.name, "email": "karim.nouveau@example.com"})
db.expire_all()
check("a new professor for this program", db.query(Professor).filter(Professor.name == "Karim Nouveau").one().specialties == other.name)
from app.services.enrollment_service import teaches

check("a program can count more than three", sum(1 for item in db.query(Professor).all() if teaches(item, other)) >= 2)

# ---- F. Moving emojis ----
page = admin.get(f"/candidate/{fares.id}").text
check("emojis: on the candidate's analyses", 'class="emo emo-' in page)
check("emojis: they stay still for less motion", "emoBounce" in page and "prefers-reduced-motion: no-preference" in page)

db.close()
check.done()

"""Who can do what (new parts, special rights, "everything", details, professor accounts, someone allowed to manage the
team), reviews at the end of a program (every channel, per program, per candidate), and the assistant: every answer
says its sources; program content, 9antra itself, grounded model answers, guides for the new parts; candidates'
replies say their source. Runs on a COPY; email and WhatsApp are fakes."""
import json
from datetime import datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("rights")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Professor, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.knowledge import KnowledgeEntry
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (access, auth, chatbot, email_assistant as ea, email_threads as et, enrollment_service as es,
                          knowledge_service, program_feedback)
from app.services.settings_service import get_settings

emails, prompts = [], []
et._attempt = lambda recipient, subject, *rest, **kw: (emails.append((recipient, subject, rest[0] if rest else "")) or (None, False))
et.smtp_configured = lambda: True
ea.model_available = lambda: False


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="rights-test-1", role=auth.ADMIN)
admin_id = db.query(User).filter(User.email == "admin@test.local").one().id
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "rights-test-1"})


def login(email, password):
    one = TestClient(main.app, follow_redirects=False)
    one.post("/login", data={"email": email, "password": password})
    return one


# ---- A. New parts and special rights ----
response = admin.post("/settings/team/add", data={"name": "Rim Chat", "email": "rim.chat@test.local", "password": "rim-chat-12", "role": "staff",
                                                  "perm_team_chat": "edit", "perm_assistant": "view"})
rim = db.query(User).filter(User.email == "rim.chat@test.local").one()
perms = access.clean(rim.permissions)
check("add: a team member with only the team chat and the assistant", perms["team_chat"] == "edit" and perms["assistant"] == "view" and perms["inbox"] == "none", perms)
rim_client = login("rim.chat@test.local", "rim-chat-12")
check("team chat: open to them", rim_client.get("/inbox/team").status_code == 200)
check("inbox: not open to them", rim_client.get("/inbox").status_code in (303, 403))
check("assistant: they can ask it", rim_client.post("/assistant/chat", json={"message": "who has not paid?"}).status_code == 200)
page = rim_client.get("/inbox/team").text
check("assistant: its button is on their pages", 'id="assistant-launcher"' in page)
check("team page: not theirs", rim_client.get("/settings/team").status_code in (303, 403))

response = admin.post("/settings/team/add", data={"name": "Mehdi Manager", "email": "mehdi.manager@test.local", "password": "mehdi-man-12",
                                                  "role": "staff", "perm_settings": "view", "right_team": "1", "right_accounts": "1"})
mehdi = db.query(User).filter(User.email == "mehdi.manager@test.local").one()
check("special rights: ticked one by one", access.clean(mehdi.permissions)["team"] == "edit" and access.clean(mehdi.permissions)["accounts"] == "edit"
      and access.clean(mehdi.permissions)["backups"] == "none")
manager = login("mehdi.manager@test.local", "mehdi-man-12")
check("special rights: the team page opens for them", manager.get("/settings/team").status_code == 200)
check("special rights: their menu has it", 'href="/settings/team"' in manager.get("/settings").text)
check("special rights: the WhatsApp account opens for them", manager.post("/settings/whatsapp/test", headers={"accept": "text/html"}).status_code == 303
      and "/settings" in manager.post("/settings/whatsapp/test").headers.get("location", ""))
check("special rights: backups stay closed", manager.get("/system/backups").status_code in (303, 403, 404) and manager.post("/system/backups").status_code in (303, 403))
response = manager.post("/settings/team/add", data={"name": "Sneaky Admin", "email": "sneaky@test.local", "password": "sneaky-12-ab", "role": "admin"})
sneaky = db.query(User).filter(User.email == "sneaky@test.local").one()
check("manager: cannot make an admin (added as a team member)", sneaky.role == auth.STAFF)
check("manager: cannot change an admin's password", manager.post(f"/settings/team/{admin_id}/password", data={"password": "new-pass-123"}).status_code == 403)
check("manager: cannot change an admin's details", manager.post(f"/settings/team/{admin_id}/details", data={"name": "X", "email": "x@test.local"}).status_code == 403)
check("manager: cannot change roles", manager.post(f"/settings/team/{sneaky.id}/role", data={"role": "admin"}).status_code == 403)
response = manager.post(f"/settings/team/{sneaky.id}/access", data={"preset": "all"})
db.expire_all()
check("everything: one click gives every part and every right", access.clean(db.get(User, sneaky.id).permissions) == access.everything() and "everything" in where(response))
response = admin.post(f"/settings/team/{rim.id}/details", data={"name": "Rim Chatti", "email": "rim.chatti@test.local", "phone": "98 765 111"})
db.expire_all()
check("details: name, sign-in email and phone changed", db.get(User, rim.id).name == "Rim Chatti" and db.get(User, rim.id).email == "rim.chatti@test.local"
      and db.get(User, rim.id).phone == "+216 98 765 111", where(response))
check("details: someone else's email is refused", "already someone's sign-in" in where(admin.post(f"/settings/team/{rim.id}/details",
                                                                                                 data={"name": "Rim", "email": "mehdi.manager@test.local"})))
professor = Professor(name="Hela Prof", specialties="Python", email="hela.prof@example.com")
db.add(professor)
db.commit()
check("professor account: offered for professors without one", "Hela Prof" in admin.get("/settings/team").text)
response = admin.post("/settings/team/add", data={"name": "", "email": "hela.login@example.com", "password": "hela-prof-12", "role": "professor",
                                                  "professor_id": professor.id})
hela = db.query(User).filter(User.email == "hela.login@example.com").one_or_none()
check("professor account: created, linked to the professor", hela is not None and hela.role == auth.PROFESSOR and hela.professor_id == professor.id and hela.name == "Hela Prof", where(response))
check("professor account: they see their space", login("hela.login@example.com", "hela-prof-12").get("/teacher").status_code == 200)

# ---- B. Reviews at the end of a program ----
program = db.query(Program).order_by(Program.name).first()
group = ClassGroup(name="REVIEW-CLASS", program_id=program.id, professor_name="Hela Prof", status=ClassStatus.ONGOING,
                   start_date=datetime.now() - timedelta(days=70), total_weeks=8, start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat")
db.add(group)
db.flush()


def learner(name, email, phone=None):
    one = Candidate(full_name=name, email=email, phone=phone)
    db.add(one)
    db.flush()
    reg = Registration(candidate_id=one.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390, paid_amount=390,
                       payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=group.id)
    db.add(reg)
    db.commit()
    return one, reg


ali, ali_reg = learner("Ali Avis", "ali.avis@example.com")
noor, noor_reg = learner("Noor Phone", "noor.phone@example.com", phone="98 222 777")
members = [ali_reg, noor_reg]
group.status = ClassStatus.COMPLETED
for reg in members:
    reg.status = RegistrationStatus.COMPLETED
program_feedback.queue_class(db, group, members)
db.commit()
from app.services import channels

channels.set_choice(db, "whatsapp", True)          # WhatsApp first above the inbox: no phone, then email
emails.clear()
sent = program_feedback.send_due(db)
channels.set_choice(db, "auto", True)
db.expire_all()
check("reviews: asked when the class finishes", any(item[0] == "ali.avis@example.com" for item in emails))
noor_row = program_feedback.of_registration(db, noor_reg.id)
check("reviews: asked on WhatsApp when it comes first (it waits for your phone)", noor_row is not None and noor_row.status == "asked"
      and db.query(CandidateEmail).filter(CandidateEmail.candidate_id == noor.id, CandidateEmail.channel.in_(("whatsapp", "sms"))).count() == 1,
      noor_row and noor_row.status)
ali_row = program_feedback.of_registration(db, ali_reg.id)
program_feedback.answer(db, ali_row, stars=5, words="Très bonne formation, le formateur explique bien.")
db.commit()
page = admin.get(f"/programs/{program.id}").text
check("program page: what learners said, with the stars", 'id="reviews"' in page and "What learners said" in page and "Très bonne formation" in page and "★★★★★" in page)
page = admin.get(f"/candidate/{ali.id}").text
check("candidate file: their review", 'id="reviews"' in page and "Très bonne formation" in page)
page = admin.get(f"/candidate/{noor.id}").text
check("candidate file: one button to ask again", f"/candidate/{noor.id}/ask-review/{noor_reg.id}" in page)
response = admin.post(f"/candidate/{noor.id}/ask-review/{noor_reg.id}")
check("candidate file: asked again", "Asked" in where(response), where(response))
check("candidate file: someone who answered is not asked again", "Not sent" in where(admin.post(f"/candidate/{ali.id}/ask-review/{ali_reg.id}")))

# ---- C. The assistant says its sources ----
found = chatbot.answer(db, "who has not paid?", [])
check("sources: from the records", found.get("sources") and found["sources"][0]["label"].startswith("The application's records"), found.get("sources"))
weeks = [(level, week) for level in program.levels for week in level.weeks]
word = None
for _, week in weeks:
    for candidate_word in (week.topic_title or "").split():
        clean = "".join(char for char in candidate_word if char.isalnum())
        if len(clean) >= 5 and clean.lower() not in ("semaine", "introduction"):
            word = clean
            break
    if word:
        break
if word:
    found = chatbot.answer(db, f"Does {program.name} cover {word}?", [])
    check("program content: the weeks that cover it", word.lower() in found["reply"].lower() and any(source.get("url") == f"/programs/{program.id}" for source in found["sources"]),
          (found["reply"][:200], found["sources"]))
found = chatbot.answer(db, f"what is in week 1 of {program.name}?", [])
check("program content: one week", ("week 1" in found["reply"].lower() or "semaine 1" in found["reply"].lower()) and found["sources"], found["reply"][:200])
found = chatbot.answer(db, "who is 9antra and where are you located?", [])
check("about 9antra: who, where, how to reach", "9antra" in found["reply"] and "Berges du Lac" in found["reply"]
      and any("welcome message" in source["label"] for source in found["sources"]), found["reply"][:300])
found = chatbot.answer(db, "how do I add a team member and give him access?", [])
check("guides: the new parts are explained, with the guide as source", "Team and access" in found["reply"] and found["sources"][0]["label"] == "The application's guide", found["reply"][:200])
found = chatbot.answer(db, "how do I check payment proofs?", [])
check("guides: payment proofs", "Payment proofs" in found["reply"] or "payment proofs" in found["reply"].lower(), found["reply"][:200])
knowledge_service.save_entry(db, title="Est-ce que la formation donne un certificat ?", content="Oui, un certificat est remis à la fin de la formation.")
db.commit()
found = chatbot.answer(db, "Est-ce que la formation donne un certificat ?", [])
check("sources: an answer you taught", any(source["label"].startswith("An answer you taught") for source in found["sources"]), found)
knowledge_service.sync_knowledge(db)
db.commit()
check("knowledge: 9antra itself is known (and answers candidates)", db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key == "system:about").count() == 1)
ea.model_available = lambda: True
ea.llm_json = lambda system, prompt, **kw: prompts.append(prompt) or {"reply": "Le centre se trouve aux Berges du Lac à Tunis et à Sahloul Sousse.", "used": [1]}
from app.services import chatbot_sources

found = chatbot_sources.grounded(db, "Où se trouve le centre de formation ?", True, "12 formations")
check("model: answers from numbered extracts and says which", found.get("source") == "model" and len(found["sources"]) >= 2
      and found["sources"][-1]["label"].startswith("Written by the local model"), found)
check("model: it was given the extracts", "Extracts:" in (prompts[-1] if prompts else ""))
ea.model_available = lambda: False

# ---- D. Replies to candidates say their source ----
owing = Candidate(full_name="Sami Source", email="sami.source@example.com")
db.add(owing)
db.flush()
db.add(Registration(candidate_id=owing.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390, paid_amount=100,
                    payment_status=PaymentStatus.PARTIAL, status=RegistrationStatus.PENDING))
db.commit()
emails.clear()
ea.ingest_inbound(db, sender="sami.source@example.com", subject="Paiement", body="Bonjour, combien il me reste à payer ?", message_id="<source-1@example.com>")
db.expire_all()
reply = next((item for item in emails if item[0] == "sami.source@example.com"), None)
message = db.query(CandidateEmail).filter(CandidateEmail.message_id == "<source-1@example.com>").one()
draft = message.suggested_reply or ""
check("candidates: the answer says where it comes from", (reply and "Source : votre dossier chez 9antra" in reply[2]) or "Source : votre dossier chez 9antra" in draft,
      (reply and reply[2][:300], draft[:300], message.status))

db.close()
check.done()

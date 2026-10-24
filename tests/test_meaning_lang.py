"""Meaning for the guide and the candidate assistant (a multilingual model; drafts only for candidates), the language
chosen for the assistant and Yasmine, the programs without a teacher (as many professors as you like), the new
dashboard and class cards, and the job offers read once.
Runs on a COPY; email, WhatsApp and SMS are fakes. Loads the multilingual sentence model (about 10 s)."""
import json
import os
from datetime import datetime
from urllib.parse import unquote_plus

import testenv

testenv.prepare("meaning_lang")
os.environ["MEANING_OFF"] = "0"                      # this suite is about meaning
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, Professor, Program, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.email_thread import EmailThread
from app.models.knowledge import KnowledgeEntry
from app.services import (auth, chatbot, completeness, email_assistant as ea, email_templates as tp, email_threads as et, friend, howto,
                          knowledge_service, market, meaning, overview)

sent = []


def fake_send(**kw):
    sent.append(kw)
    return True


et.send_email = fake_send
tp.send_email = fake_send
et.smtp_configured = lambda: True
ea.model_available = lambda: False
ea.llm_json = lambda *a, **k: None


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="meaning-lang-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "meaning-lang-1"})
boss = auth.CurrentUser(id="a", name="A", email="a", role="admin")

# ---- A. The guide understands by meaning ----
check("meaning: the multilingual model is on this computer", meaning.available() and "multilingual" in (meaning.model_name() or ""), meaning.model_name())
CASES = [
    ("كيف أسجل أن المترشح دفع؟", "payment-record"),
    ("a student gave me the money, where do I put it?", "payment-record"),
    ("fin nsajjel elli el candidat khallas?", "payment-record"),
    ("كيف أفتح قسما جديدا؟", "class-create"),
    ("je veux déplacer la séance de samedi à dimanche", "class-time"),
    ("le prix de python a augmenté", "program-edit"),
    ("lier notre page fb", "facebook"),
    ("rappeler à la classe d'apporter leur ordinateur vendredi", "reminder-plan"),
]
for question, key in CASES:
    task = howto.task_for(question, boss)
    check(f"guide by meaning: “{question}” → {key}", task is not None and task["key"] == key, task and task["key"])
OVERRULED = [
    ("nheb nbadel wa9t el classe", "class-time"),
    ("j'ai peur de perdre toutes les données", "backup"),
    ("cette phrase en arabe est mal traduite", "translations-fix"),
    ("le site a été mis à jour, il faut récupérer les changements", "website"),
]
for question, key in OVERRULED:
    task = howto.task_for(question, boss)
    check(f"guide: a sure meaning corrects the word rules: “{question}” → {key}", task is not None and task["key"] == key, task and task["key"])
check("guide: not sure, it does not guess", howto.task_for("the class should be in the evening now", boss) is None
      and howto.task_for("quel est le niveau de Fares avant de commencer ?", boss) is None)
check("guide: a word rule the meaning does not clearly contradict stays", (howto.task_for("je ne trouve pas où est le candidat Ahmed", boss) or {}).get("key") == "find")
said = howto.answer(db, "le site a été mis à jour, il faut récupérer les changements", boss)
check("guide: answered in the language of the question, with its diagram", said["schema"]["title"] == "Récupérer ce qui a changé sur 9antra.tn", said.get("schema"))
staff = auth.CurrentUser(id="s", name="S", email="s", role="staff", permissions={"candidates": "view"})
check("guide: by meaning too, only what this person may do", howto.task_for("j'ai peur de perdre toutes les données", staff) is None
      or howto.task_for("j'ai peur de perdre toutes les données", staff)["key"] != "backup")
reply = chatbot.answer(db, "kifech nbadel wa9t el classe?", [], user=boss)
check("assistant: a Tunisian how-to understood by meaning, answered in Tunisian", reply["source"] == "guide" and reply["reply"].startswith("7added"), reply["reply"][:80])
reply = chatbot.answer(db, "qui n'a pas encore payé ?", [], user=boss)
check("assistant: a question about the records is not taken for a how-to", reply.get("source") != "guide", reply.get("source"))

# ---- B. The candidate assistant: a taught answer for a question that means the same, as a draft ----
entry = knowledge_service.save_entry(db, title="Est-ce qu'il y a un parking pour les voitures ?", content="Oui : un parking gratuit est devant l'immeuble Nour, aux Berges du Lac 2.")
db.commit()
lina = Candidate(full_name="Lina Sens", email="lina.sens@example.com", phone="98 222 333")
db.add(lina)
db.commit()


def ask(body, key):
    out = ea.ingest_inbound(db, sender=lina.email, subject="Question", body=body, message_id=f"<meaning-{key}-{datetime.utcnow().timestamp()}@example.com>")
    db.expire_all()
    return db.get(CandidateEmail, out["email_id"])


inbound = ask("Hello, is there somewhere to park my car when I come to class?", "park")
sources = json.loads(inbound.sources or "{}")
check("candidate: other words, the same question: a draft with the taught answer, not sent", inbound.status != "replied"
      and inbound.suggested_reply and "parking gratuit" in inbound.suggested_reply and sources.get("taught_id") == entry.id,
      (inbound.status, inbound.escalation_reason, (inbound.suggested_reply or "")[:80]))
check("candidate: the admin is told it was understood by its meaning", "Understood by its meaning" in (inbound.escalation_reason or ""), inbound.escalation_reason)
thread = db.get(EmailThread, inbound.thread_id)
ea.admin_reply(db, thread, inbound.suggested_reply, learn=True, question="is there somewhere to park my car when I come to class?")
db.expire_all()
check("candidate: once the admin sends it, the wording is learnt", any("park my car" in item["q"] for item in knowledge_service.wordings_of(db.get(KnowledgeEntry, entry.id))))
again = ask("Hello, is there somewhere to park my car when I come to class?", "park-again")
check("candidate: the same wording next time is answered alone", again.status == "replied", (again.status, again.escalation_reason))
other = ask("Bonjour, est-ce que la formation donne droit à une attestation ?", "other")
check("candidate: a question that means something else gets no taught answer", "parking gratuit" not in (other.suggested_reply or ""), (other.suggested_reply or "")[:80])

# ---- C. The language chosen for the assistant and Yasmine ----
said = admin.get("/account/friend?path=/&lang=tn").json()
check("Yasmine in Tunisian: her greeting and words", said["lang"] == "tn" and said["greeting"].split()[0] in ("Sbe7", "Aslema", "Msa") and said["words"]["ask"] == "Es2alni…"
      and said["voice_lang"] == "fr-FR", (said["greeting"], said["words"]["ask"]))
said = admin.get("/account/friend?path=/&lang=ar").json()
check("Yasmine in Arabic", said["lang"] == "ar" and said["voice_lang"] == "ar-SA" and said["words"]["ask"] == "اطرح عليّ سؤالا…")
check("Yasmine: without a choice, the pages' language", admin.get("/account/friend?path=/").json()["lang"] == "en")
answer = admin.post("/account/guide", json={"message": "how do I record a payment?", "lang": "ar"}).json()
check("the guide answers in the chosen language, whatever the question's", answer["reply"].startswith("تسجيل دفع"), answer["reply"][:60])
answer = admin.post("/assistant/chat", json={"message": "how do I record a payment?", "lang": "tn"}).json()
check("the assistant too (Tunisian)", answer["reply"].startswith("Sajjel khlas"), answer["reply"][:60])
answer = admin.post("/assistant/chat", json={"message": "comment enregistrer un paiement ?", "lang": "en"}).json()
check("the assistant in English for a French question", answer["reply"].startswith("Record a payment"), answer["reply"][:60])
answer = admin.post("/assistant/chat", json={"message": "qui n'a pas payé ?", "lang": "en"}).json()
check("the assistant's figures follow the choice too (English)", not any(word in answer["reply"] for word in ("candidats", "n'ont", "doivent")), answer["reply"][:120])
answer = admin.post("/assistant/chat", json={"message": "how do I record a payment?"}).json()
check("no choice: the language of the question", answer["reply"].startswith("Record a payment"))
page = admin.get("/").text
check("the choice in the assistant's window and under Yasmine", 'id="assistant-lang"' in page and 'id="fr-lang"' in page and "guide-lang" in page
      and 'value="tn">Tounsi' in page)

# ---- D. As many professors as you like; the programs without a teacher ----
programs = db.query(Program).filter(Program.is_approved.is_(True)).all()
real = [item for item in db.query(Professor).all() if not (item.name or "").startswith("À nommer")]
from app.services.enrollment_service import teaches

nobody = [program for program in programs if not any(teaches(professor, program) for professor in real)]
check("no teacher: counted by the dashboard", [program.id for program in overview.programs_without_teacher(db)] == [program.id for program in sorted(nobody, key=lambda p: p.name)])
lines = completeness.items(db)
check("no teacher: in what is still missing (no target of three)", (not nobody or any("programs have no teacher yet" in item["text"] for item in lines))
      and not any("fewer than three" in item["text"] for item in lines))
page = admin.get("/classes/?tab=professors").text
check("professors: “Who teaches each program”, without a target", "Who teaches each program" in page and "3 for each" not in page and "to name" not in page.split('id="coverage"')[1].split("</section>")[0].replace("A place to name", ""))
check("professors: the programs nobody teaches come first, in red", not nobody or page.index(f'id="cover-{nobody[0].id}"') < min(
    page.index(f'id="cover-{program.id}"') for program in programs if program not in nobody) if len(nobody) < len(programs) else True)
check("professors: no more places to name", admin.post("/classes/professors/fill").status_code in (404, 405))
target = programs[0]
for number in range(1, 6):
    admin.post("/classes/professors/create", data={"name": f"Prof Extra {number}", "programs": target.name, "email": f"extra{number}@example.com"})
db.expire_all()
check("professors: as many as you like for one program", sum(1 for item in db.query(Professor).all() if item.name.startswith("Prof Extra") and teaches(item, target)) == 5)
page = admin.get("/").text
if overview.programs_without_teacher(db):
    check("dashboard: the card of the programs without a teacher", 'id="live-dashboard-teachers"' in page and "No teacher yet" in page)
check("dashboard: the day at a glance", 'class="db-hero"' in page and "session" in page.split('class="db-glance"')[1][:600])
check("dashboard: each figure of the week day by day", page.count('class="db-spark"') == len(overview.activity_parts(db, datetime.utcnow()))
      and all(len(part["days"]) == 7 for part in overview.activity_parts(db, datetime.utcnow())))
page = admin.get("/classes/").text
check("classes: the tile of the programs without a teacher", "Programs without a teacher" in page)
check("classes: cards with one row of buttons and a bar of places", 'class="cg-icon"' in page and 'class="cg-meter"' in page and 'class="cg-seat-row"' not in page)

# ---- E. The job offers are read once ----
import time

market.warm()
started = time.time()
for program in programs[:3]:
    market.analysis(db, program)
check("market: once read, a program's analysis takes no time", time.time() - started < 3, round(time.time() - started, 2))

db.close()
check.done()

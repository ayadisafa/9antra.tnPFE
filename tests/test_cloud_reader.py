"""The cloud reader: any question understood by the cloud model and answered only from the application's facts, for
Yasmine and the email assistant. The cloud model is FAKED here (a script plays its answers): nothing leaves this
machine. Checks the key rotation, the checks on figures and quoted facts, the routing, and every email outcome."""
import testenv
DB = testenv.prepare("cloud_reader")

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network
import requests
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
requests.get = _no_web; requests.post = _no_web

import json
import os
from datetime import datetime, timedelta
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, cloud_answer, cloud_model, email_assistant as ea, email_templates as tp, email_threads as et,
                          lookups, orchestrator)
from app.services.settings_service import get_settings

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

sent = []
def fake_send(**kw):
    sent.append(kw)
    return True
et.send_email = fake_send
tp.send_email = fake_send
et.smtp_configured = lambda: True
ea.model_available = lambda: False
ea.llm_json = lambda *a, **k: None

# ======================================================================
# A. The client: keys in turn, a spent key rests for that model only
# ======================================================================
os.environ["GEMINI_API_KEY"] = ""
check("no key: switched off, nothing is tried", not cloud_model.available() and cloud_model.generate([{"role": "user", "parts": [{"text": "x"}]}]) is None)
os.environ["GEMINI_API_KEY"] = "key-one, key-two"
check("keys written with commas are read one by one", cloud_model.keys() == ["key-one", "key-two"] and cloud_model.available())
os.environ["CLOUD_MODEL"] = "off"
check("CLOUD_MODEL=off switches it off", not cloud_model.available())
os.environ["CLOUD_MODEL"] = "on"


class Answer:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


asked = []
SPENT = {"error": {"status": "RESOURCE_EXHAUSTED", "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}
OK = {"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}]}


def google(url, data=None, headers=None, timeout=None):
    key, model = headers["x-goog-api-key"], url.split("/models/")[1].split(":")[0]
    asked.append((key, model))
    if key == "key-one" and model == cloud_model.WRITING[0]:
        return Answer(429, SPENT)
    return Answer(200, OK)


requests.post = google
content = cloud_model.generate([{"role": "user", "parts": [{"text": "hello"}]}])
check("the first key's daily quota is spent: the next key answers", cloud_model.text_of(content) == "ok"
      and asked == [("key-one", cloud_model.WRITING[0]), ("key-two", cloud_model.WRITING[0])], asked)
asked.clear()
cloud_model.generate([{"role": "user", "parts": [{"text": "again"}]}])
check("...and the spent key rests for that model (not asked again today)", asked == [("key-two", cloud_model.WRITING[0])], asked)
asked.clear()
cloud_model.generate([{"role": "user", "parts": [{"text": "light"}]}], models=[cloud_model.WRITING[1]])
check("...but it still works for the other model (Google counts each model apart)", asked == [("key-one", cloud_model.WRITING[1])], asked)
check("how many keys each model may still use", cloud_model.resting() == {cloud_model.WRITING[0]: 1, cloud_model.WRITING[1]: 2}, cloud_model.resting())


def unreachable(*a, **k):
    raise ConnectionError("offline")


requests.post = unreachable
check("no connection: None, the callers do as before", cloud_model.generate([{"role": "user", "parts": [{"text": "x"}]}]) is None)
requests.post = _no_web
cloud_model._resting.clear()

# ======================================================================
# B. The checks, without the model
# ======================================================================
facts = {"price": "390 DT", "phone": "+216 58 840 064", "form": "https://forms.example/register", "count": 4}
def unsupported(reply):
    return cloud_answer.check(reply, facts, "question", [])["unsupported"]
check("Tunisian letters written as digits are not figures", unsupported("L'equipe bech tjawbek 3la el nokta, 9addech 7aja.") == [])
check("figures copied from the facts pass (a price, a phone, a count)", unsupported("Soumha 390 DT, kallemna 3al +216 58 840 064. You have 4.") == [])
check("an invented figure is caught", unsupported("You can pay in 2 times, 450 DT.") == ["2", "450"])
check("an invented link is caught, a copied one passes", unsupported("Register: https://forms.example/register") == []
      and unsupported("See https://elsewhere.example/x") == ["https://elsewhere.example/x"])
said = cloud_answer.check("x", {"rules": ["A certificate is issued at the end of the training."]}, "q",
                          [{"ask": "certificate?", "answered": True, "evidence": ["certificate is issued at the end"]},
                           {"ask": "internship?", "answered": True, "evidence": ["an internship is included"]}])
check("a quoted fact is verified only when it really is in the facts", [item["verified"] for item in said["asks"]] == [True, False], said["asks"])

# ======================================================================
# C. The model, scripted: what it reads and what it writes
# ======================================================================
SCRIPT = {}          # question -> (lookups to call, written answer)
played = []


def fake_generate(contents, *, system=None, tools=None, schema=None, temperature=0.2, models=None):
    text = "\n".join(part.get("text", "") for turn in contents for part in turn.get("parts", []) if "text" in part)
    question = next((key for key in SCRIPT if key in text), None)
    played.append(("read" if tools else "write", question))
    if question is None or SCRIPT[question] is None:
        return None
    calls, written = SCRIPT[question]
    if tools:
        return {"role": "model", "parts": [{"functionCall": {"name": name, "args": args}} for name, args in calls]}
    return {"role": "model", "parts": [{"text": json.dumps(written(text) if callable(written) else written)}]}


cloud_model.generate = fake_generate
db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin.cr@example.com", name="Cr Admin", password="correct horse battery", role=auth.ADMIN)
client = TestClient(main.app, follow_redirects=False)
client.post("/login", data={"email": "admin.cr@example.com", "password": "correct horse battery"})
program = db.query(Program).first()
group = ClassGroup(name="CR-CLASS-1", program_id=program.id, status=ClassStatus.ONGOING, start_date=datetime.utcnow() - timedelta(days=10),
                   total_weeks=8, current_week=2, start_mode="fixed_date", capacity=10, max_capacity=10, professor_name="Cr Prof")
db.add(group); db.flush()
person = Candidate(full_name="Rania Cloud", email="rania.cloud@example.com", phone="+216 50 111 222"); db.add(person); db.flush()
other = Candidate(full_name="Someone Else", email="someone.else@example.com"); db.add(other); db.flush()
db.add(Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390,
                    total_price=390, paid_amount=390, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED,
                    registration_date=datetime.utcnow() - timedelta(days=20), class_group_id=group.id))
db.add(Registration(candidate_id=other.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390,
                    paid_amount=0, payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING,
                    registration_date=datetime.utcnow() - timedelta(days=3)))
db.commit()

total = db.query(Candidate).count()
SCRIPT["how many candidates do we have"] = ([("overview", {})], {
    "language": "en", "route": "answer", "sensitive": "none", "reply": f"You have {total} candidates.",
    "asks": [{"ask": "how many candidates", "answered": True, "evidence": ["candidates"]}]})
row = db.query(User).filter(User.role == "admin").first()
admin = auth.CurrentUser(id=row.id, name=row.name, email=row.email, role="admin")
found = cloud_answer.answer("how many candidates do we have?", lookups.for_admin(db, admin), "admin")
check("the lookups it asked for are run, and the answer written from them", found is not None and found["used"] == ["overview"]
      and found["reply"] == f"You have {total} candidates." and found["ok"] and found["covered"], found)
check("...two calls: reading, then writing", played == [("read", "how many candidates do we have"), ("write", "how many candidates do we have")], played)
SCRIPT["nothing to look up"] = ([], {"language": "en", "route": "answer", "sensitive": "none", "reply": "Hello", "asks": []})
check("for the admin, nothing looked up: no answer (the other ways answer)",
      cloud_answer.answer("nothing to look up", lookups.for_admin(db, admin), "admin") is None)
mine = json.dumps(lookups.for_candidate(db, person)["my_trainings"].run(), ensure_ascii=False, default=str)
check("a candidate's lookups see only their own trainings", "Rania Cloud" in mine and "Someone Else" not in mine and "CR-CLASS-1" in mine, mine[:300])

# ======================================================================
# D. Yasmine
# ======================================================================
def ask(message, lang="en"):
    response = client.post("/assistant/chat", json={"message": message, "history": [], "lang": lang, "path": "/"})
    return response.json() if response.status_code == 200 else {"reply": "", "status": response.status_code}


said = ask("how many candidates do we have?")
check("a question: read by the cloud model, answered from the records", said.get("path") == ["read", "cloud", "finish"]
      and said.get("agent", {}).get("key") == "reader" and said["reply"] == f"You have {total} candidates."
      and any(link["url"] == "/" for link in said.get("links") or []), (said.get("path"), said.get("reply")))
played.clear()
said = ask("send a reminder to Rania Cloud to pay")
check("asking it to do something: its own rules, not the reader", "cloud" not in said.get("path", []) and not played, (said.get("path"), played))
said = ask("ça va ?", lang="fr")
check("small talk: answered like a person, not read by the cloud", said.get("path") == ["read", "talk", "finish"], said.get("path"))
SCRIPT["who owes money right now"] = ([("not_paid", {})], {"language": "en", "route": "answer", "sensitive": "none",
                                                            "reply": "Someone Else owes 9999 DT.", "asks": [{"ask": "who owes", "answered": True, "evidence": ["Someone Else"]}]})
said = ask("who owes money right now?")
check("an answer with a figure the records do not show is not given: the subject's agent answers", "cloud" in said.get("path", [])
      and said.get("agent", {}).get("key") != "reader" and "9999" not in said["reply"], (said.get("path"), said.get("reply")))
SCRIPT["which classes are running"] = None
said = ask("which classes are running?")
check("the cloud does not answer (quota, connection): as before", "cloud" in said.get("path", []) and said.get("agent", {}).get("key") != "reader"
      and said["reply"], said.get("path"))
check("the graph has the step, drawn on the agents page", "cloud" in orchestrator.STEPS and "reader" in orchestrator.AGENTS
      and any(step["key"] == "cloud" for line in orchestrator.drawing() for step in line["steps"]))

# ======================================================================
# E. The email assistant
# ======================================================================
settings = get_settings(db)
settings.email_auto_reply_mode = "auto"
db.commit()
counter = [0]


def email(body):
    counter[0] += 1
    before = len(sent)
    ea.ingest_inbound(db, sender=person.email, subject="Question", body=body, message_id=f"<cr-{counter[0]}@example.com>")
    db.commit()
    db.expire_all()
    row = db.query(CandidateEmail).filter(CandidateEmail.message_id == f"<cr-{counter[0]}@example.com>").one()
    return row, sent[before:]


def written(reply, asks, route="answer", sensitive="none", language="fr"):
    return {"language": language, "route": route, "sensitive": sensitive, "reply": reply, "asks": asks}


SCRIPT["Où se trouve votre centre"] = ([("centre", {})], written("Notre centre se trouve à l'adresse écrite par l'équipe.",
                                                                 [{"ask": "où", "answered": True, "evidence": ["where"]}]))
row, out = email("Bonjour, Où se trouve votre centre ?")
body = out[-1].get("text_body", "") if out else ""
check("every point answered from checked facts: sent alone, with its source", row.status == "replied" and len(out) == 1
      and "Notre centre se trouve" in body and "Source : votre dossier et les informations de 9antra." in body, (row.status, row.escalation_reason, body[:300]))
settings.email_auto_reply_mode = "review"
db.commit()
row, out = email("Bonjour, Où se trouve votre centre ? (encore)")
check("“you send”: the same answer waits as a draft", row.status == "needs_admin" and not out and "Notre centre se trouve" in (row.suggested_reply or ""),
      (row.status, row.escalation_reason))
settings.email_auto_reply_mode = "auto"
db.commit()
SCRIPT["Est-ce qu'il y a un stage"] = ([("taught", {"question": "stage"})], written("L'équipe vous répondra à ce sujet.",
                                                                                  [{"ask": "stage", "answered": False, "evidence": []}]))
row, out = email("Est-ce qu'il y a un stage après la formation ?")
check("a point the facts do not answer: a draft for the admin, saying which, never a guess", row.status == "needs_admin" and not out
      and "not in the application's facts" in (row.escalation_reason or "") and "stage" in (row.escalation_reason or ""), (row.status, row.escalation_reason))
SCRIPT["rembourser ma formation"] = ([("my_trainings", {})], written("…", [{"ask": "remboursement", "answered": False, "evidence": []}], sensitive="refund"))
row, out = email("Je voudrais que vous me remboursiez, rembourser ma formation svp")
check("a refund: always to the admin, with no draft", row.status == "needs_admin" and not out and not row.suggested_reply, (row.status, row.escalation_reason))
SCRIPT["ce prof va trop vite"] = ([("my_trainings", {})], written("…", [{"ask": "plainte", "answered": False, "evidence": []}], sensitive="complaint"))
row, out = email("Franchement ce prof va trop vite, on ne suit pas")
check("a complaint read in the words: to the admin, no draft", row.status == "needs_admin" and not out and "complaint" in (row.escalation_reason or ""),
      (row.status, row.escalation_reason))
SCRIPT["Super séance hier, merci"] = ([], written("", [], route="no_reply"))
row, out = email("Super séance hier, merci beaucoup à toute l'équipe et au formateur, vraiment top")
check("only thanks: no answer needed, nothing sent", row.status == "no_reply" and not out, (row.status, row.escalation_reason))
SCRIPT["Le prix est de combien"] = ([("programs", {})], written("Le prix est de 777 DT.", [{"ask": "prix", "answered": True, "evidence": ["price"]}]))
row, out = email("Le prix est de combien pour la formation ?")
check("an answer with a figure the facts do not show: never sent, the admin reads it", row.status == "needs_admin" and not out
      and "facts do not show" in (row.escalation_reason or ""), (row.status, row.escalation_reason))
SCRIPT["Comment s'inscrire à une autre formation"] = None
row, out = email("Comment s'inscrire à une autre formation ?")
check("the cloud reader cannot read it (quota, connection): the answer waits, nothing goes alone", row.status == "needs_admin" and not out
      and "could not read this message" in (row.escalation_reason or ""), (row.status, row.escalation_reason))
os.environ["GEMINI_API_KEY"] = ""
row, out = email("Comment s'inscrire à une autre formation ? (sans clé)")
check("no key at all: the email assistant works exactly as before (the form, sent alone)", row.status == "replied" and len(out) == 1
      and "could not read this message" not in (row.escalation_reason or ""), (row.status, row.escalation_reason))

db.close()
print(f"{sum(results)} of {len(results)} checks passed")

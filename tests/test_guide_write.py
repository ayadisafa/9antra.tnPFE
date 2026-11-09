"""Yasmine writes and sends emails alone, and guides every page (9 November, second part): "écris à Fares pour lui
rappeler la séance" is written by her from the admin's words (no figure added or lost, else nothing goes) and sent at
once, to a person or a whole class; "explique cette page" gives what the page is for and each of its parts, from its
tour, with a walk through it on the page. The cloud model is FAKED and mail is recorded: nothing leaves this machine."""
import testenv
DB = testenv.prepare("guide_write")

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network
import requests
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
requests.get = _no_web; requests.post = _no_web

import json
import os
from datetime import datetime, time as dtime, timedelta
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Professor, Program, Registration, User, UserSession
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import auth, chatbot_send, cloud_model, email_threads as et, lookups, orchestrator, page_help, page_tours, reminder_writer, ui_words
from app.services.settings_service import get_settings

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

sent = []
et._attempt = lambda recipient, subject, text, *rest, **kw: (sent.append((recipient, subject, text)) or (None, False))
et.smtp_configured = lambda: True
os.environ["GEMINI_API_KEY"] = ""
db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin.gw@example.com", name="Leila Admin", password="correct horse battery", role=auth.ADMIN)
client = TestClient(main.app, follow_redirects=False)
client.post("/login", data={"email": "admin.gw@example.com", "password": "correct horse battery"})
get_settings(db).assistant_auto_send = True
from app.services import translations
translations.ensure(db)                 # the hand-made translations, as the application loads them when it starts
program = db.query(Program).filter(Program.name == "DevOps").first()
group = ClassGroup(name="GW1", program_id=program.id, status=ClassStatus.NOT_STARTED, start_date=datetime.utcnow() + timedelta(days=9),
                   total_weeks=4, start_time=dtime(18, 0), end_time=dtime(20, 0), days_of_week="sat", capacity=10, max_capacity=10)
db.add(group); db.flush()
people = []
for name in ("Rami Ecrit", "Sonia Ecrit"):
    person = Candidate(full_name=name, email=f"{name.split()[0].lower()}.gw@example.com", phone="50 222 333")
    db.add(person); db.flush()
    db.add(Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390, paid_amount=390,
                        payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, registration_date=datetime.utcnow(), class_group_id=group.id))
    people.append(person)
db.commit()


def ask(message, lang="fr", path="/"):
    response = client.post("/assistant/chat", json={"message": message, "history": [], "lang": lang, "path": path})
    return response.json() if response.status_code == 200 else {"reply": "", "status": response.status_code}


# ======================================================================
# A. She is the guide of every page
# ======================================================================
said = ask("explique cette page", path="/payments")
tour = page_tours.for_path("/payments")
check("“explique cette page” on the payments page: what it is for and every part of it", said["reply"].startswith("Paiements")
      and said["agent"]["key"] == "guide" and said["cards"][0]["kind"] == "steps" and len(said["cards"][0]["items"]) >= len(tour["steps"]) - len(page_tours.FRAME), said.get("cards"))
check("...with a walk through it on the page itself", said["page_tour"] == {"url": "/payments", "here": True, "label": "Montrez-moi chaque partie sur la page"}, said.get("page_tour"))
said = ask("how does the reminders page work?", lang="en", path="/")
check("another page named: its guide, and a walk that opens it", said["page_tour"]["url"] == "/reminders" and not said["page_tour"]["here"]
      and said["reply"].startswith("Reminders"), said.get("page_tour"))
check("in Tunisian and Arabic too", page_help.wants("chnowa najjem na3mel lehna?") and page_help.wants("اشرح لي هذه الصفحة")
      and page_help.wants("qu'est-ce que je peux faire ici ?") and page_help.wants("what can I do on this page?"))
check("…not “résume cette page” (the summary) nor “what can I do?” (the day's list)", not page_help.wants("résume cette page") and not page_help.wants("what can I do?"))
check("the walk starts by itself on the page she opens (?tour=1)", "tour=1" in client.get("/reminders?tour=1").text)
check("the cloud reader knows every page too (page_guide, pages)", {"page_guide", "pages"} <= set(lookups.for_admin(db, None, path="/payments")))
found = lookups._page_guide(db, None, "", "/payments")
check("...the page open now, every part", found["address"] == "/payments" and found["parts"], found)
check("the graph has the page guide", "page" in orchestrator.STEPS and "page" in {step["key"] for line in orchestrator.drawing() for step in line["steps"]})

# ======================================================================
# B. She writes the email herself, and sends it alone
# ======================================================================
os.environ["GEMINI_API_KEY"] = "key-one"
cloud_model._resting.clear()
script = []


class Answer:
    def __init__(self, body):
        self.status_code, self._body, self.text = 200, body, json.dumps(body)

    def json(self):
        return self._body


asked = []


def google(url, data=None, headers=None, timeout=None):
    body = json.loads(data.decode("utf-8"))
    asked.append(body)
    return Answer({"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(script.pop(0))}]}}]})


requests.post = google
check("“écris à Fares pour lui rappeler …”: what it is for stays in the words to write",
      chatbot_send.parse("écris un email à Rami Ecrit pour lui rappeler la séance de samedi à 18h")["what"] == "pour lui rappeler la séance de samedi à 18h")
sent.clear()
script = [{"message": "Bonjour Rami,\n\nPetit rappel : votre séance de samedi commence à 18h. À samedi !"}]
said = ask("écris un email à Rami Ecrit pour lui rappeler la séance de samedi à 18h")
check("she writes it herself from the words, and it goes at once", said["reply"].startswith("Voici ce qui s'est passé") and "✓ Rami Ecrit" in said["reply"]
      and any("Petit rappel" in (item[2] or "") for item in sent), (said["reply"][:300], sent[:1]))
check("...said as written by her", any(item["label"] == "Written by Yasmine from your words (figures checked)" for item in said["sources"]), said["sources"])
sent.clear()
script = [{"message": "Bonjour Rami, votre séance commence à 17h."}, {"message": "Bonjour Rami, votre séance commence à 17h."}]
said = ask("écris un email à Rami Ecrit pour lui rappeler la séance de samedi à 18h")
check("a figure changed: nothing goes, and she says why", not sent and said["reply"].startswith("Rien n'est parti"), said["reply"][:200])
sent.clear()
script = [{"message": "Bonjour,\n\nLa séance de samedi est déplacée à dimanche. Merci de votre compréhension."}]
said = ask("envoie un email à la classe GW1 pour leur dire que la séance de samedi est déplacée à dimanche")
check("a whole class: every candidate of it receives it", {item[0] for item in sent} >= {"rami.gw@example.com", "sonia.gw@example.com"}
      and "✓ Rami Ecrit" in said["reply"] and "✓ Sonia Ecrit" in said["reply"], (said["reply"][:300], [item[0] for item in sent]))
os.environ["GEMINI_API_KEY"] = ""
check("without the cloud model she does not write it (the inbox's rewording does, as before)", reminder_writer.reword("bonjour") is None)
requests.post = _no_web

# ======================================================================
# C. Her words
# ======================================================================
phrases = ui_words.phrases()
needed = ["Page guide", "What the page is for and every part of it, from its tour, with a walk through it on the page."]
missing = [text for text in needed if len(phrases.get(text) or []) != 2 or not all(phrases[text])]
check("the new sentences in French and Arabic, written by hand", not missing, missing)
check("her four languages for the page guide", set(page_help.SAY) == {"fr", "en", "ar", "tn"})
page = client.get("/").text
check("her window: the parts card and the walk button", "steps: function" in page and "assistant-walk" in page)

db.close()
print(f"{sum(results)} of {len(results)} checks passed")

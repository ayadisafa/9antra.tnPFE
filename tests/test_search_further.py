"""When the email assistant does not know (10 November): it looks further before giving up (the centre's details,
what was taught, the admin's answers to other candidates, the templates), tells the admin what it found and where, or
what is missing and where to write it ("Tell me" teaches it), and keeps looking: a waiting question is read again
when what it lacked appears (Settings › Your centre, something taught, the 30-minute look).
Runs on a COPY; mail is recorded, no model and no cloud: nothing leaves this machine."""
import os
import testenv
DB = testenv.prepare("search_further")
os.environ["BACKGROUND_TASKS_INLINE"] = "1"

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network
import requests
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
requests.get = _no_web; requests.post = _no_web

import json
from datetime import datetime, timedelta
from urllib.parse import unquote_plus
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.email_template import EmailTemplate
from app.models.email_thread import EmailThread
from app.models.knowledge import KnowledgeEntry
from app.models.notification import Notification
from app.services import (auth, cloud_model, email_assistant as ea, email_templates as tp, email_threads as et, knowledge_service,
                          search_further, tunisian_banks)
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
cloud_model.available = lambda: False


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin.sf@example.com", name="Sf Admin", password="correct horse battery", role=auth.ADMIN)
client = TestClient(main.app, follow_redirects=False)
client.post("/login", data={"email": "admin.sf@example.com", "password": "correct horse battery"})
settings = get_settings(db)
settings.centre_ribs = None
settings.email_auto_reply_mode = "auto"
db.commit()
counter = [0]


def person(name):
    row = Candidate(full_name=name, email=f"{name.split()[0].lower()}.sf@example.com"); db.add(row); db.commit()
    return row


def email(candidate, body, subject="Question"):
    counter[0] += 1
    ea.ingest_inbound(db, sender=candidate.email, subject=subject, body=body, message_id=f"<sf-{counter[0]}@example.com>")
    db.commit()
    db.expire_all()
    return db.query(CandidateEmail).filter(CandidateEmail.message_id == f"<sf-{counter[0]}@example.com>").one()


def searched_of(row):
    db.refresh(row)
    return (json.loads(row.sources or "{}") or {}).get("searched")


# ======================================================================
# A. Where it looks
# ======================================================================
out = search_further.look(db, ["send RIB for payment"])
check("the centre's RIB asked and not written: said, with where to write it", out["missing"] and "centre_ribs" in out["empty"]
      and "The centre's RIB" in out["empty_labels"], out)
check("…every place it looked is named", out["looked"] == search_further.LOOKED and len(out["looked"]) == 4)
first18 = "080060123456789012"
rib = tunisian_banks.pretty_rib(first18 + tunisian_banks.rib_key(first18))
settings.centre_ribs = rib
db.commit()
out = search_further.look(db, ["send RIB for payment"])
check("…written in Settings: found there, with its value", out["found"] and out["found"][0]["place"] == search_further.CENTRE
      and rib in out["found"][0]["text"] and out["found"][0]["insert"] == rib and not out["empty"], out)
settings.centre_ribs = None
db.commit()

knowledge_service.save_fact(db, text="Un certificat est délivré à la fin de chaque formation, signé par le centre.")
db.commit()
out = search_further.look(db, ["certificat à la fin de la formation"])
check("what the team taught, found with a lower bar than for answering alone", any(lead["place"] == search_further.TAUGHT
      and "certificat" in lead["text"] for lead in out["found"]), out)

amine = person("Amine Sf")
asked = email(amine, "Bonjour, est-ce qu'il y a un parking près du centre ?", subject="Parking")
thread = db.get(EmailThread, asked.thread_id)
et.deliver(db, amine, "Re: Parking", "Bonjour Amine,\n\nOui : un parking gratuit est juste devant le centre, rue du Lac.\n\nÀ bientôt.",
           None, kind="reply", author="admin", thread=thread, language="fr")
db.commit()
out = search_further.look(db, ["parking près du centre"])
lead = next((lead for lead in out["found"] if lead["place"] == search_further.ANSWERS), None)
check("what the admin wrote to another candidate about it: the paragraph, who and when, and a link", lead is not None
      and lead["text"].startswith("Oui : un parking gratuit") and "Amine Sf" in lead["where"] and lead["link"] == f"/inbox?thread={thread.id}", out)
check("…never the discussion being answered itself", not any(lead["place"] == search_further.ANSWERS
      for lead in search_further.look(db, ["parking près du centre"], skip_thread_id=thread.id)["found"]))

db.add(EmailTemplate(name="Après la formation", subject="Après", body="Bonjour {first_name},\n\nLes attestations de stage sont remises sur demande au secrétariat."))
db.commit()
out = search_further.look(db, ["attestation de stage sur demande"])
check("the admin's email templates (a sentence without {variables})", any(lead["place"] == search_further.TEMPLATES
      and "attestations de stage" in lead["text"] for lead in out["found"]), out)
out = search_further.look(db, ["est-ce que vous organisez des sorties en montagne"])
check("nothing anywhere: found nowhere, said as it is", not out["found"] and out["missing"] and "Not found anywhere" in search_further.told(out), out)

# ======================================================================
# B. An email it cannot answer: looked further, the admin told
# ======================================================================
fares = person("Fares Sf")
row = email(fares, "Hello, can you send me your RIB so I can pay by transfer? Thank you", subject="RIB")
searched = searched_of(row)
check("a question it cannot answer goes to the admin, looked for further first", row.status == "needs_admin" and searched is not None
      and "centre_ribs" in searched["empty"], (row.status, row.escalation_reason, searched))
alert = db.query(Notification).filter(Notification.action_url == f"/inbox?thread={row.thread_id}", Notification.category == "EMAIL_QUESTION").order_by(Notification.created_at.desc()).first()
check("…the admin's notification says what was not found and asks to be told", alert is not None and "Tell me" in alert.message, alert.message if alert else None)
page = client.get(f"/inbox?thread={row.thread_id}").text
check("the inbox shows what it looked for, where, and what is missing", "I looked further for what I did not know" in page
      and "Not written in Settings › Your centre:" in page and "The centre&#39;s RIB" in page and "Looked in:" in page, page[page.find("ix-further"):][:600])
check("…with “Tell me”, and “Add to the draft” for what it found", f'action="/inbox/thread/{row.thread_id}/teach-missing"' in page
      and "[data-add-to-draft]" in page)

# Settings › Your centre: the RIB written there, and the waiting question is read again at once.
other = person("Rim Sf")
waiting = email(other, "Bonjour, quel est votre RIB pour le virement ?", subject="Virement")
check("…another one waits for the same detail", (searched_of(waiting) or {}).get("empty") == ["centre_ribs"], searched_of(waiting))
answer = client.post("/settings/numbers", data={"ribs": rib, "whole": ""})
check("the RIB written in Settings: the questions that lacked it are read again at once", "read again" in where(answer), where(answer))
db.expire_all()
again = db.get(CandidateEmail, waiting.id)
check("…read again with it (answered, or a new draft that has it)", again.status == "replied" or rib in (again.suggested_reply or "")
      or not (searched_of(again) or {}).get("empty"), (again.status, again.escalation_reason, searched_of(again)))

# "Tell me": what it lacked, taught from the inbox.
settings = get_settings(db)
db.refresh(settings)
settings.centre_ribs = None
db.commit()
leila = person("Leila Sf")
row = email(leila, "Bonjour, je voudrais votre RIB pour payer, merci", subject="Paiement")
answer = client.post(f"/inbox/thread/{row.thread_id}/teach-missing", data={"fact": f"Notre RIB pour les virements : {rib}"})
db.expire_all()
check("“Tell me”: kept for every assistant, and the question read again", "Kept: every assistant knows it now" in where(answer)
      and "reading the question again" in where(answer)
      and db.query(KnowledgeEntry).filter(KnowledgeEntry.kind == "fact", KnowledgeEntry.content.like("%RIB pour les virements%")).count() == 1, where(answer))
check("…a RIB given for “your RIB” is also written in Settings, where receipts are checked", get_settings(db).centre_ribs == rib
      and "also written in Settings" in where(answer), get_settings(db).centre_ribs)
check("…a few letters are not enough", "Write what was missing" in where(client.post(f"/inbox/thread/{row.thread_id}/teach-missing", data={"fact": "a"})))

# ======================================================================
# C. It keeps looking
# ======================================================================
salma = person("Salma Sf")
row = email(salma, "Bonjour, est-ce que vous faites des sorties pédagogiques en entreprise pendant la formation ?", subject="Sorties")
before = searched_of(row)
check("…a point found nowhere is kept to look for again", before is not None and before["missing"], before)
other_thread = db.get(EmailThread, email(person("Nour Sf"), "Vous faites des visites en entreprise ?", subject="Visites").thread_id)
et.deliver(db, other_thread.candidate, "Re: Visites", "Bonjour,\n\nOui : des sorties pédagogiques en entreprise sont prévues pendant la formation.\n\nBonne journée.",
           None, kind="reply", author="admin", thread=other_thread, language="fr")
db.commit()
check("not looked again before its time (every 30 minutes)", search_further.again(db) == 0 and searched_of(row)["at"] == before["at"])
count = search_further.again(db, force=True)
db.expire_all()
told = db.query(Notification).filter(Notification.action_url == f"/inbox?thread={row.thread_id}", Notification.title.like("Found something%")).first()
check("the admin answered it elsewhere since: found by looking again, and the admin is told", told is not None and "Your answers to other candidates" in told.message
      and any(lead["place"] == search_further.ANSWERS for lead in searched_of(row)["found"]), (count, told.message if told else None, searched_of(row)))
# A question that waited before the further look existed (no search kept): looked for once by the regular look.
old_one = email(person("Yassine Sf"), "Hello, can you send me your RIB for the payment please?", subject="RIB please")
kept = json.loads(old_one.sources or "{}")
kept.pop("searched", None)
old_one.sources = json.dumps(kept)
old_one.escalation_reason = "Part of the message is not in the application's facts: “send RIB for payment”. The draft answers the rest and says the team will answer that point."
db.commit()
search_further.again(db)
check("a question that waited before: looked for by the regular look, from the points its note names",
      (searched_of(old_one) or {}).get("asks") == ["send RIB for payment"], searched_of(old_one))
check("…the 30-minute look runs with the knowledge", "search_further.again(db)" in open(os.path.join(os.path.dirname(main.__file__), "scheduler", "jobs.py"), encoding="utf-8").read())

db.close()
print(f"{sum(results)} of {len(results)} checks passed")

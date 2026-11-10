"""The email assistant tells apart what a candidate's message is, and knows every step of a candidate (9 November):
a yes/no question answered Oui/Non first; answers to the questions they were sent, written their own way, graded; their
opinion kept (not answered); a question about the course sent to their professor only once paid; "no cv" (= ça va,
fine) needing no answer; their payments, receipts, placement, level check, weekly questions and presence looked up.
And on an iPhone: the page fits the notch and the home bar, fields do not zoom, SMS links open Messages filled in.
The cloud model is FAKED (a script plays it) and mail is recorded: nothing leaves this machine."""
import testenv
DB = testenv.prepare("email_kinds")

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
from app.models import Candidate, ClassGroup, ClassStatus, Professor, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.payment import Payment
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, cloud_answer, cloud_model, course_questions, email_assistant as ea, email_templates as tp,
                          email_threads as et, lookups, program_feedback, sms, weekly_questions)
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
os.environ["GEMINI_API_KEY"] = "key-one"

SCRIPT = {}
prompts = []


def fake_generate(contents, *, system=None, tools=None, schema=None, temperature=0.2, models=None):
    text = "\n".join(part.get("text", "") for turn in contents for part in turn.get("parts", []) if "text" in part)
    prompts.append((system or "", text))
    question = next((key for key in SCRIPT if key in text), None)
    if question is None:
        return None
    calls, written = SCRIPT[question]
    if tools:
        return {"role": "model", "parts": [{"functionCall": {"name": name, "args": args}} for name, args in calls]}
    return {"role": "model", "parts": [{"text": json.dumps(written)}]}


cloud_model.generate = fake_generate
db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin.ek@example.com", name="Ek Admin", password="correct horse battery", role=auth.ADMIN)
client = TestClient(main.app, follow_redirects=False)
client.post("/login", data={"email": "admin.ek@example.com", "password": "correct horse battery"})
settings = get_settings(db)
settings.email_auto_reply_mode = "auto"
settings.centre_ribs = "08 006 0123456789012 34"
program = db.query(Program).first()
teacher = Professor(name="Nadia Prof", specialties=program.name, email="nadia.prof@example.com", is_available=True)
db.add(teacher); db.flush()
teacher_id = teacher.id
group = ClassGroup(name="EK-CLASS-1", program_id=program.id, status=ClassStatus.ONGOING, start_date=datetime.utcnow() - timedelta(days=10),
                   total_weeks=8, current_week=2, start_mode="fixed_date", capacity=10, max_capacity=10, professor_id=teacher.id, professor_name=teacher.name)
db.add(group); db.flush()
paid = Candidate(full_name="Rim Payee", email="rim.ek@example.com", phone="+216 50 111 333"); db.add(paid); db.flush()
owing = Candidate(full_name="Omar Doit", email="omar.ek@example.com"); db.add(owing); db.flush()
paid_registration = Registration(candidate_id=paid.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390,
                                 total_price=390, paid_amount=390, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED,
                                 registration_date=datetime.utcnow() - timedelta(days=20), class_group_id=group.id)
db.add(paid_registration)
db.add(Registration(candidate_id=owing.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390, paid_amount=100,
                    payment_status=PaymentStatus.PARTIAL, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow() - timedelta(days=5)))
db.flush()
db.add(Payment(registration_id=paid_registration.id, amount=390, payment_method="virement", payment_date=datetime.utcnow() - timedelta(days=18)))
db.commit()
counter = [0]


def email(person, body, subject="Question"):
    counter[0] += 1
    before = len(sent)
    ea.ingest_inbound(db, sender=person.email, subject=subject, body=body, message_id=f"<ek-{counter[0]}@example.com>")
    db.commit()
    db.expire_all()
    row = db.query(CandidateEmail).filter(CandidateEmail.message_id == f"<ek-{counter[0]}@example.com>").one()
    return row, sent[before:]


def written(reply, asks, route="answer", sensitive="none", **more):
    return {"language": "fr", "route": route, "sensitive": sensitive, "reply": reply, "asks": asks, **more}


# ======================================================================
# A. What a candidate's file says, looked up
# ======================================================================
money = lookups.for_candidate(db, paid)["my_payments"].run()
check("their payments: what, when, how, what is left, how to pay", money["trainings"][0]["paid_in_full"] and money["trainings"][0]["payments"][0]["how"] == "virement"
      and "08 006 0123456789012 34" in money["how_to_pay"][0] and "receipt" in money["after_paying"], money)
progress = lookups.for_candidate(db, owing)["my_progress"].run()
check("where they are: not in a class until paid in full, the level check, their presence",
      progress["trainings"][0]["place"].startswith("not in a class yet") and progress["trainings"][0]["level_check"].startswith("not sent yet"), progress)
check("…in the class they follow", "EK-CLASS-1" in lookups.for_candidate(db, paid)["my_progress"].run()["trainings"][0]["place"])
check("the steps of a candidate, for the reader", any("Once paid in full" in rule for rule in lookups.for_candidate(db, paid)["rules"].run()["rules"]))

# ======================================================================
# B. What the message is
# ======================================================================
SCRIPT["Est-ce que j'ai tout payé"] = ([("my_payments", {})], written("Oui, vous avez tout payé : 390 DT.",
                                                                      [{"ask": "tout payé ?", "answered": True, "evidence": ["390 DT"]}], kind="yes_no"))
row, out = email(paid, "Bonjour, Est-ce que j'ai tout payé ?")
check("a yes/no question: Oui first, from their records, sent alone", row.status == "replied" and out and "Oui, vous avez tout payé" in out[-1].get("text_body", ""),
      (row.status, row.escalation_reason))
check("…the writer is told to answer yes/no first", any("answered Oui or Non first" in system for system, _ in prompts))
captured = []
import re


def fake_reply(db, candidate_id, subject, body):
    """Answers waiting to be graded: only "2-B, 1-C" written that way (what the rules read themselves)."""
    if not re.fullmatch(r"\d+-[A-F](?:, \d+-[A-F])*", body or ""):
        return None
    captured.append(body)
    return {"text": "Bravo : 2 sur 2.", "done": True, "quiz": type("Q", (), {"course_name": "Semaine 2"})()}


weekly_questions.handle_reply = fake_reply
SCRIPT["pour la question 2 je dirais B"] = ([("my_progress", {})], written("", [], route="quiz_answers",
                                                                         quiz_answers=[{"question": 2, "choice": "b"}, {"question": 1, "choice": "C"}]))
row, out = email(paid, "pour la question 2 je dirais B et la 1 c'est C")
check("answers written their own way: read as question and letter, then graded", captured[-1:] == ["2-B, 1-C"] and row.status == "replied"
      and "Bravo" in (out[-1].get("text_body", "") if out else ""), (captured, row.status))
SCRIPT["La formation était vraiment top"] = ([("my_trainings", {})], written("", [], route="review"))
asked = program_feedback.ask(db, paid_registration, manual=True)
db.commit()
row, out = email(paid, "La formation était vraiment top, le formateur explique très bien.")
review = program_feedback.of_registration(db, paid_registration.id)
check("their opinion: kept as their review, nothing answered", row.status == "no_reply" and not out and review is not None and review.status == "received",
      (row.status, review.status if review else None))
forwarded = []
course_questions.forward = lambda db, **kw: forwarded.append({"professor": kw["professor"].id, "message": kw["message"].id}) or None
SCRIPT["le décorateur dans la séance"] = ([("my_trainings", {})], written("", [], route="course_question"))
row, out = email(paid, "je n'ai pas bien compris le décorateur dans la séance")
check("a question about the course (paid): sent to their professor with their email", forwarded and forwarded[-1]["professor"] == teacher_id
      and forwarded[-1]["message"] == row.id, (row.status, row.escalation_reason))
SCRIPT["les closures de JavaScript"] = ([("my_trainings", {})], written("", [], route="course_question"))
row, out = email(owing, "pouvez-vous m'expliquer les closures de JavaScript ?")
check("…not paid in full: told to pay first, the professor is not asked", len(forwarded) == 1 and row.status in ("replied", "needs_admin")
      and ("pay" in ((out[-1].get("text_body", "") if out else "") + (row.suggested_reply or "")).lower()
           or "pai" in ((out[-1].get("text_body", "") if out else "") + (row.suggested_reply or "")).lower()), (row.status, row.escalation_reason))
os.environ["GEMINI_API_KEY"] = ""
for text in ("no cv", "Non cv merci", "cv", "ça va merci"):
    row, out = email(paid, text, subject="Re: votre séance")
    if row.status != "no_reply" or out:
        break
check("“no cv”, “cv”, “ça va merci”: they are fine, nothing to answer", row.status == "no_reply" and not out, (text, row.status))
row, out = email(paid, "cv ci-joint pour le stage, vous pouvez le regarder ?")
check("…but a question with a CV in it is still read as a question", row.status != "no_reply", row.status)

# ======================================================================
# C. On an iPhone
# ======================================================================
page = client.get("/").text
check("the page fits the notch and the home bar, its own icon on the home screen", "viewport-fit=cover" in page and 'rel="apple-touch-icon"' in page
      and "apple-mobile-web-app-status-bar-style" in page)
check("fields do not zoom on an iPhone, the page's real height", "-webkit-touch-callout: none" in page and "100dvh" in page)
check("an SMS link opens Messages with the text written, on an iPhone and on Android", sms.link("+21650111333", "Bonjour Rim") == "sms:+21650111333?&body=Bonjour%20Rim")
check("“On your phone” says how SMS go from an iPhone", 'id="phone-sms"' in client.get("/system").text)

db.close()
print(f"{sum(results)} of {len(results)} checks passed")

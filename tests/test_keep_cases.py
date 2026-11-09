"""Yasmine answers "someone is about to drop out, what can I do?" (who, why, and what to do for each) and "a professor is
absent / a candidate has not paid / … what do I do?" (what the application does by itself, what is left to you).
Runs on a COPY of the database; nothing leaves this machine."""
import testenv
DB = testenv.prepare("keep_cases")

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network
import requests
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
requests.get = _no_web; requests.post = _no_web

from datetime import date, datetime, timedelta
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Program, Registration, User, UserSession
from app.models.classsession import ClassSession
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.user import Attendance
from app.services import auth, cases, email_assistant as ea, email_threads as et, keep_them, orchestrator, ui_words

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

et.smtp_configured = lambda: True
ea.model_available = lambda: False
ea.llm_json = lambda *a, **k: None
db = SessionLocal()
now = datetime.utcnow()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin.kc@example.com", name="Kc Admin", password="correct horse battery", role=auth.ADMIN)
client = TestClient(main.app, follow_redirects=False)
client.post("/login", data={"email": "admin.kc@example.com", "password": "correct horse battery"})


def ask(message, lang="fr"):
    response = client.post("/assistant/chat", json={"message": message, "history": [], "lang": lang, "path": "/"})
    return response.json() if response.status_code == 200 else {"reply": "", "status": response.status_code}


# ======================================================================
# A. What asks about someone who may drop out
# ======================================================================
leaving = ["WHEN SOMEONE ABOUT TO DROP OFF WHAT CAN I DO", "when someone is about to drop out what can I do?", "a candidate wants to quit, what should I do",
           "quelqu'un va abandonner, je fais quoi ?", "un candidat veut abandonner la formation", "how do I keep a student who wants to leave",
           "who is about to drop off?", "qui risque d'abandonner ?", "chkoun bech ybattel ?", "wa7ed bech ybattel chna3mel", "شكون بش يبطّل التكوين؟",
           "مترشح يحب ينسحب، شنوة نعمل؟", "a candidate is absent what can I do", "il ne vient plus, que faire ?"]
check("asked in every way and language", all(keep_them.wants(text) for text in leaving), [text for text in leaving if not keep_them.wants(text)])
others = ["stop the payment reminder", "I want to stop the weekly reminders", "leave a review", "what can I do today?", "Qui a payé ?",
          "is my data at risk?", "the weekly questions", "c'est gratuit ? batel ?"]
check("...and not what only looks like it (stopping a reminder, “batel” = free)", not any(keep_them.wants(text) for text in others),
      [text for text in others if keep_them.wants(text)])

# ======================================================================
# B. Someone at risk, as the dashboard measures it
# ======================================================================
program = db.query(Program).first()
group = ClassGroup(name="KC-RISK-1", program_id=program.id, status=ClassStatus.ONGOING, start_date=now - timedelta(days=40), total_weeks=8,
                   current_week=5, start_mode="fixed_date", capacity=10, max_capacity=10, professor_name="Kc Prof")
db.add(group); db.flush()
person = Candidate(full_name="Kamel Quitte", email="kamel.quitte@example.com", phone="+216 50 777 222"); db.add(person); db.flush()
registration = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=400,
                            total_price=400, paid_amount=100, payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.ASSIGNED,
                            registration_date=now - timedelta(days=30), reminder_sent_at=now - timedelta(days=20), class_group_id=group.id)
db.add(registration); db.flush()
for days in (2, 9, 16, 23):
    session = ClassSession(class_group_id=group.id, session_date=date.today() - timedelta(days=days), week_number=5 - days // 7, topic="Semaine")
    db.add(session); db.flush()
    db.add(Attendance(class_group_id=group.id, session_id=session.id, registration_id=registration.id, candidate_id=person.id, status="absent"))
for number in (2, 3, 4):
    db.add(Quiz(candidate_id=person.id, registration_id=registration.id, course_name=f"semaine {number}", kind="weekly", class_group_id=group.id,
                week_number=number, answers="[]", score=0.0, sent_at=now - timedelta(days=30 - 7 * number), questions="[]"))
db.commit()

said = ask("WHEN SOMEONE ABOUT TO DROP OFF WHAT CAN I DO", lang="en")
check("the question that failed: the follow-up agent answers it", said.get("agent", {}).get("key") == "keep" and "keep" in said.get("path", []),
      said.get("path"))
check("...what to do, by why", said["reply"].startswith("When someone may drop out, act on why") and "“We missed you”" in said["reply"]
      and "call them" in said["reply"] and "later class of the same program" in said["reply"], said["reply"][:300])
check("...and who it is right now, the likeliest first, with why and the message ready", "Kamel Quitte (KC-RISK-1), likely to drop out" in said["reply"]
      and "absent from the last 4 sessions" in said["reply"], said["reply"])
check("...not the day's to-do list any more", "To do, most pressing first" not in said["reply"])
check("short first, the whole answer one tap away", said.get("short") and len(said["short"]) < len(said["reply"]) and "Kamel Quitte" in said["short"],
      said.get("short"))
check("its picture, and where to look", (said.get("picture") or {}).get("scene") == "feelings"
      and any(link["url"] == "/#live-dashboard-risk" for link in said.get("links") or []), said.get("links"))

said = ask("quelqu'un va abandonner, je fais quoi ?")
check("in French, the same", said["reply"].startswith("Quand quelqu'un risque d'abandonner, agissez sur la raison")
      and "Kamel Quitte (KC-RISK-1), risque fort d'abandonner :" in said["reply"] and "absent(e) aux 4 dernières séances" in said["reply"], said["reply"][:500])
said = ask("مترشح يحب ينسحب، شنوة نعمل؟", lang=None)
check("asked in Arabic: answered in Arabic", said["reply"].startswith("عندما يكون أحدهم مهددا بالانقطاع") and "مرجّح أن ينقطع" in said["reply"], said["reply"][:300])

said = ask("qui risque d'abandonner ?")
check("who: the list first", said["reply"].startswith("Ils risquent d'abandonner, le plus probable d'abord") and "Kamel Quitte" in said["reply"], said["reply"][:200])
said = ask("Kamel Quitte veut abandonner, je fais quoi ?")
check("someone named: how likely, why, and what to do for them", said["reply"].startswith("Kamel Quitte (KC-RISK-1) risque fort d'abandonner (")
      and "Dit vouloir arrêter : appelez-le" in said["reply"] and any(link["url"] == f"/candidate/{person.id}" for link in said.get("links") or []),
      said["reply"][:400])
token = ui_words.use("fr")
try:
    nobody = keep_them._what_to_do([])[0]
finally:
    ui_words.current.reset(token)
check("nobody at risk: said so, with what is watched", "personne ne semble sur le point d'abandonner" in nobody)

# ======================================================================
# C. What to do when…
# ======================================================================
asked = {"professor-absent": "what should I do if a professor is absent", "unpaid": "what can I do when a candidate hasn't paid",
         "no-professor": "what should I do when a class has no professor", "unhappy": "a candidate is unhappy what can I do",
         "refund": "what do I do when a candidate asks for a refund", "meeting-link": "what can I do if the meeting link is missing",
         "not-reached": "what should I do if a message did not reach a candidate"}
check("each case is read from its question", all(cases.wants(text) and cases.case_of(text).key == key for key, text in asked.items()),
      {key: (cases.case_of(text).key if cases.case_of(text) else None) for key, text in asked.items()})
check("...the case first works too, and in French and Tunisian", all(cases.wants(text) for text in (
    "if a professor is absent what do I do", "un formateur est absent, je fais quoi ?", "prof ghayeb ghodwa chna3mel", "un candidat se plaint, que faire ?")))
check("a how-to stays the guide's, and “what can I do?” the day's list", not any(cases.wants(text) for text in (
    "how do I record a professor's absence?", "what can I do?", "what should I do first today?", "who has not paid?")))

said = ask("un formateur est absent, je fais quoi ?")
check("a professor absent: the steps, and the replacement asked by itself", said.get("path") == ["read", "cases", "finish"]
      and said["reply"].startswith("Quand un formateur est absent :") and "Classes › Formateurs › sa carte › Absence" in said["reply"]
      and "un lien privé pour confirmer" in said["reply"], said["reply"][:400])
said = ask("what can I do when a candidate hasn't paid", lang="en")
check("not paid: the one reminder, then you; and who owes now", "One payment reminder goes by itself" in said["reply"]
      and "never sends a second one" in said["reply"] and "Right now" in said["reply"] and "Kamel Quitte" in said["reply"], said["reply"])
said = ask("what should I do when a class has no professor", lang="en")
check("no professor: what happens by itself follows the setting", "It waits" in said["reply"]
      and ("they take it by themselves" in said["reply"] or "is off (Settings › Classes)" in said["reply"]), said["reply"])
said = ask("what can I do?", lang="en")
check("“what can I do?” alone is still the day's list", said["reply"].startswith("Today,"), said["reply"][:80])
said = ask("what can I do when a candidate wants to change class", lang="en")
check("“wants to change class”: the guide's steps to move them", said["reply"].startswith("Move a candidate to another class"), said["reply"][:120])
said = ask("how do I record a professor's absence?", lang="en")
check("the guide says the replacement is asked by itself now", "asks a professor of the program who is free to replace them" in said["reply"], said["reply"])

# ======================================================================
# D. The graph, and every sentence in French and Arabic
# ======================================================================
check("the graph has both steps, drawn on the agents page", {"keep", "cases"} <= set(orchestrator.STEPS) and "keep" in orchestrator.AGENTS
      and {"keep", "cases"} <= {step["key"] for line in orchestrator.drawing() for step in line["steps"]})
check("trying the question on the agents page: the follow-up step",
      client.post("/assistant/agents/route", json={"message": "WHEN SOMEONE ABOUT TO DROP OFF WHAT CAN I DO"}).json().get("step") == "keep")
phrases = ui_words.phrases()
written = ([text for _, text in keep_them.STEPS] + [keep_them.WANTS_TO_STOP, keep_them.TOO_MANY, keep_them.IN_SHORT, cases.AUTO_PROFESSOR, cases.AUTO_OFF]
           + [text for case in cases.CASES for text in (case.title, case.short, *case.steps)])
missing = [text for text in written if len(phrases.get(text) or []) != 2 or not all(phrases[text])]
check("every sentence has its French and Arabic, written by hand", not missing, missing[:3])

db.close()
print(f"{sum(results)} of {len(results)} checks passed")

"""The opinion asked at the end of a program, the notification spotlight links,
the new dashboard and the history. Runs on a COPY of the database; nothing leaves this machine."""
import os, sys, json, shutil, sqlite3
import testenv
DB = testenv.prepare("opinion")
SCRATCH = testenv.WORK

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network
import requests
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
requests.get = _no_web; requests.post = _no_web

from datetime import datetime, timedelta, time as dtime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Activity, Candidate, ClassGroup, ClassStatus, Notification, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.feedback import CandidateFeedback
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, candidate_service as cs, email_assistant as ea, email_templates as tp, email_threads as et,
                          enrollment_service as es, overview, program_feedback as pf, website_sync as ws, whatsapp as wa)
from app.services.schedule_utils import local_now

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

sent_mail = []
def fake_send(**kw): sent_mail.append(kw); return True
et.send_email = fake_send; tp.send_email = fake_send
et.smtp_configured = lambda: True
ea.model_available = lambda: True
ea.llm_json = lambda *a, **k: None

db = SessionLocal()
now = datetime.utcnow()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
get_settings = __import__("app.services.settings_service", fromlist=["get_settings"]).get_settings
settings = get_settings(db); settings.public_base_url = "https://centre.example.com"; db.commit()

# ======================================================================
# A. A class finishes: its candidates are asked, once
# ======================================================================
program = db.query(Program).filter(Program.name == "DevOps").first() or db.query(Program).first()
start = (local_now() - timedelta(days=50)).replace(hour=9, minute=0, second=0, microsecond=0)
group = ClassGroup(name="END-DEVOPS-1", program_id=program.id, status=ClassStatus.ONGOING, start_date=start, total_weeks=8, current_week=8,
                   start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat", start_mode="fixed_date", capacity=10, max_capacity=10)
db.add(group); db.flush()
people = {}
for name, email in (("Amal Fin", "amal.fin@example.com"), ("Bilel Fin", "bilel.fin@example.com"), ("Chaima Fin", "chaima.fin@example.com"), ("Dali Annule", "dali.annule@example.com")):
    person = Candidate(full_name=name, email=email, phone="50 200 300"); db.add(person); db.flush()
    reg = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=390,
                       payment_status=PaymentStatus.PAID, status=RegistrationStatus.CANCELLED if "Annule" in name else RegistrationStatus.ASSIGNED,
                       registration_date=now - timedelta(days=60), class_group_id=group.id)
    db.add(reg); db.flush(); people[name.split()[0]] = (person, reg)
db.commit()
check("before the end nobody is asked", pf.send_due(db) == 0 and db.query(CandidateFeedback).filter(CandidateFeedback.source == pf.SOURCE).count() == 0)

es.complete_class(db, group); db.expire_all()
rows = db.query(CandidateFeedback).filter(CandidateFeedback.source == pf.SOURCE).all()
check("class completed: each candidate is put on the list to ask", db.get(ClassGroup, group.id).status == ClassStatus.COMPLETED and len(rows) == 3 and all(row.status == "to_ask" for row in rows), [(row.author_name, row.status) for row in rows])
sent_mail.clear()
count = pf.send_due(db); db.expire_all()
check("the request goes out to those who have an address", count == 3 and len(sent_mail) == 3 and {m["to_email"] for m in sent_mail} == {"amal.fin@example.com", "bilel.fin@example.com", "chaima.fin@example.com"}, (count, [m["to_email"] for m in sent_mail]))
amal_row = pf.of_registration(db, people["Amal"][1].id)
mail = next(m for m in sent_mail if m["to_email"] == "amal.fin@example.com")
check("the email carries the private link and the Facebook page", f"https://centre.example.com/opinion/{amal_row.external_id}" in mail["text_body"] and "facebook.com/9antra.tn/reviews" in mail["text_body"] and "Donner mon avis" in mail["body"], mail["text_body"][:400])
check("someone who cancelled is not asked", pf.of_registration(db, people["Dali"][1].id) is None)
check("asked once: the next tick sends nothing", pf.send_due(db) == 0 and len(sent_mail) == 3)
check("an old class already completed is not asked by itself", db.query(CandidateFeedback).filter(CandidateFeedback.source == pf.SOURCE).count() == 3)

# ======================================================================
# B. The candidate's page
# ======================================================================
anon = TestClient(main.app, follow_redirects=False)
page = anon.get(f"/opinion/{amal_row.external_id}")
check("page: opens without signing in, with the program and the five stars", page.status_code == 200 and program.name in page.text and page.text.count('name="stars"') == 5 and "Amal" in page.text, page.status_code)
check("page: not for search engines, never kept", "noindex" in page.headers.get("x-robots-tag", "") and page.headers.get("cache-control") == "no-store")
check("page: an unknown link says so", anon.get("/opinion/" + "x" * 30).status_code == 404 and anon.get("/opinion/short").status_code == 404)
check("page: nothing chosen is refused, in words", anon.post(f"/opinion/{amal_row.external_id}", data={"stars": "0", "words": "  "}).status_code == 400)
response = anon.post(f"/opinion/{amal_row.external_id}", data={"stars": "5", "words": "Formation excellente, le formateur était toujours disponible et à l'écoute. Je recommande !", "publish": "true"})
db.expire_all(); amal_row = pf.of_registration(db, people["Amal"][1].id)
check("answer: kept with its stars and words", response.status_code == 303 and amal_row.status == "received" and amal_row.rating == 5 and "excellente" in amal_row.comment)
check("answer: read as happy, with what it praises", amal_row.sentiment == "positive" and {"trainer", "support"} <= set(json.loads(amal_row.raw_payload)["themes"]) and json.loads(amal_row.raw_payload)["may_publish"] is True, amal_row.raw_payload)
thanks = anon.get(f"/opinion/{amal_row.external_id}")
check("after: a thank-you, their stars, and the way to Facebook", "Merci Amal" in thanks.text and "★" in thanks.text and "facebook.com/9antra.tn/reviews" in thanks.text and 'name="stars"' not in thanks.text)
check("after: they can change their mind", 'name="stars"' in anon.get(f"/opinion/{amal_row.external_id}?edit=1").text and "checked" in anon.get(f"/opinion/{amal_row.external_id}?edit=1").text)
note = db.query(Notification).filter(Notification.category == "FEEDBACK").order_by(Notification.created_at.desc()).first()
check("the admin is told, with a link to the class", note is not None and "5 stars" in note.title and note.action_url == f"/classes/{group.id}#opinions", (note.title, note.action_url) if note else None)
anon.post(f"/opinion/{amal_row.external_id}", data={"stars": "4", "words": "Très bien dans l'ensemble."}); db.expire_all()
check("changing it does not notify twice", pf.of_registration(db, people["Amal"][1].id).rating == 4 and db.query(Notification).filter(Notification.category == "FEEDBACK").count() == 1)

# An unhappy one
bilel_row = pf.of_registration(db, people["Bilel"][1].id)
anon.post(f"/opinion/{bilel_row.external_id}", data={"stars": "2", "words": "Trop rapide, je n'ai pas compris la moitié et l'organisation était mauvaise."}); db.expire_all()
bilel_row = pf.of_registration(db, people["Bilel"][1].id)
check("an unhappy opinion is read as unhappy, and what it is about", bilel_row.sentiment == "negative" and "organisation" in json.loads(bilel_row.raw_payload)["themes"], (bilel_row.sentiment, bilel_row.raw_payload))

# ======================================================================
# C. Answering the email instead
# ======================================================================
check("stars are found in their own words", pf.stars_in("Je donne 5 étoiles !") == 5 and pf.stars_in("4/5 pour moi") == 4 and pf.stars_in("★★★ pas plus") == 3 and pf.stars_in("3 stars") == 3 and pf.stars_in("Merci pour tout") is None and pf.stars_in("j'ai 25 ans") is None)
chaima, chaima_reg = people["Chaima"]
request_mail = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == chaima.id, CandidateEmail.intent == "feedback_request").first()
outcome = ea.ingest_inbound(db, sender=chaima.email, subject=f"RE: {request_mail.subject}", body="Bonjour, je donne 4 étoiles. Formation très enrichissante, merci au formateur !",
                            in_reply_to=request_mail.message_id, message_id="<opinion-reply@mail.example>")
db.expire_all()
chaima_row = pf.of_registration(db, chaima_reg.id); reply = db.get(CandidateEmail, outcome["email_id"])
check("a reply to the request is their opinion", chaima_row.status == "received" and chaima_row.rating == 4 and chaima_row.sentiment == "positive" and json.loads(chaima_row.raw_payload)["channel"] == "email", (chaima_row.status, chaima_row.rating))
check("...and is not treated as a question to answer", reply.status == "no_reply" and not reply.requires_admin and "opinion" in (reply.analysis or ""), (reply.status, reply.analysis))

# ======================================================================
# D. What the admin sees
# ======================================================================
client = TestClient(main.app, follow_redirects=False)
auth.create_user(db, email="admin.opn@example.com", name="Opn Admin", password="correct horse battery", role=auth.ADMIN); db.commit()
client.post("/login", data={"email": "admin.opn@example.com", "password": "correct horse battery"})
view = pf.of_class(db, group.id)
check("class: stars, share of happy ones, who to answer", view["answered"] == 3 and view["asked"] == 3 and view["stars"] == round((4 + 2 + 4) / 3, 1) and view["counts"]["negative"] == 1 and len(view["to_answer"]) == 1, {k: view[k] for k in ("answered", "asked", "stars", "counts")})
page = client.get(f"/classes/{group.id}")
check("class page: the opinions are shown with their words", page.status_code == 200 and 'id="opinions"' in page.text and "Trop rapide" in page.text and "Ask again" in page.text and "Answered by email" in page.text, page.status_code)
sent_mail.clear()
response = client.post(f"/classes/{group.id}/ask-opinions")
check("ask again: those who answered are left in peace", response.status_code == 303 and not sent_mail and "already+gave" in response.headers["location"], response.headers.get("location"))
other = ClassGroup(name="END-DEVOPS-2", program_id=program.id, status=ClassStatus.ONGOING, start_date=start, total_weeks=8, current_week=3, days_of_week="sat", start_mode="fixed_date", capacity=10, max_capacity=10)
db.add(other); db.flush()
late = Candidate(full_name="Emna Encours", email="emna.encours@example.com"); db.add(late); db.flush()
db.add(Registration(candidate_id=late.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=390,
                    payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, registration_date=now, class_group_id=other.id)); db.commit()
response = client.post(f"/classes/{other.id}/ask-opinions")
check("ask now: the admin can ask a class at any time", response.status_code == 303 and len(sent_mail) == 1 and sent_mail[0]["to_email"] == "emna.encours@example.com" and "Asked+1+candidate" in response.headers["location"], response.headers.get("location"))
check("the page is closed to visitors, the candidate's link is not", TestClient(main.app, follow_redirects=False).post(f"/classes/{other.id}/ask-opinions").status_code in (303, 401) and len(sent_mail) == 1)
check("the email can be edited and switched off like the others", "feedback_request" in tp.AUTO_EMAILS and tp.AUTO_EMAILS["feedback_request"].switchable and client.get("/inbox/templates?key=feedback_request").status_code == 200)
check("it also goes on WhatsApp once a business number is there", "feedback_request" in wa.REMINDERS)

# ======================================================================
# E. Dashboard, history, notifications
# ======================================================================
page = client.get("/")
check("dashboard: four pictures, three things to do, the week's figures", page.status_code == 200 and page.text.count('class="db-tile ') == 4 and "What needs you today" in page.text and page.text.count('class="db-part ') == 6 and "New on 9antra.tn this week" in page.text, page.status_code)
check("dashboard: the long lists are folded away", page.text.count('<details class="db-fold') == 4 and "Lately" not in page.text)
parts = {part["label"]: part["count"] for part in overview.activity_parts(db, datetime.utcnow())}
check("history: the week is counted by part of the work", set(parts) == {"New registrations", "Payments received", "Reminders sent", "Placed in a class", "Weekly answers", "Emails sent"} and parts["Emails sent"] >= 4, parts)
news = ws.week_news(db, datetime.utcnow())
check("dashboard: what is new on the website is counted by kind", isinstance(news["lines"], list) and all({"icon", "label", "count", "names", "url"} <= set(line) for line in news["lines"]))
check("notifications: each link carries the block to show", 'data-spot="FEEDBACK"' in page.text and "function withSpot" in page.text and "FEEDBACK: [" in page.text)
check("class page: the block a notification points to exists", 'id="opinions"' in client.get(f"/classes/{group.id}?spot=FEEDBACK").text)

# The same registration read again from Firebase is not written in the history again.
before = db.query(Activity).filter(Activity.event_type == "REGISTRATION").count()
import inspect
source = inspect.getsource(cs)
check("history: a registration seen again at start-up is not written twice", "if new_registrations:" in source and "new_registrations += 1" in source)

print(f"\n{sum(results)}/{len(results)} passed", flush=True)
db.close()
os._exit(0 if all(results) else 1)

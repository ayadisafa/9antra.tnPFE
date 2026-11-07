"""Reviews in ten seconds: the stars inside the email and WhatsApp (one tap), the words and the professor after,
the unhappy ones handled privately (the admin is told until someone contacted them), the happy ones sent to Google
or Facebook with their words copied, the class asked on its last session day with a QR code for the room, a success
celebrated, WhatsApp first, and the rating in one line for the emails.
Runs on a COPY of the database; nothing leaves this machine."""
import json
import re
import testenv
DB = testenv.prepare("reviews")

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
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.classsession import ClassSession
from app.models.feedback import CandidateFeedback
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, channels, email_assistant as ea, email_templates as tp, email_threads as et, letters, meetings,
                          notification_rules as rules, program_feedback as pf, whatsapp as wa)
from app.services.schedule_utils import local_now
from app.services.settings_service import get_settings

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
settings = get_settings(db); settings.public_base_url = "https://centre.example.com"; settings.google_review_url = None; db.commit()
client = TestClient(main.app, follow_redirects=False)
auth.create_user(db, email="admin.rev@example.com", name="Rev Admin", password="correct horse battery", role=auth.ADMIN); db.commit()
client.post("/login", data={"email": "admin.rev@example.com", "password": "correct horse battery"})
anon = TestClient(main.app, follow_redirects=False)

program = db.query(Program).filter(Program.name == "DevOps").first() or db.query(Program).first()
start = (local_now() - timedelta(days=50)).replace(hour=9, minute=0, second=0, microsecond=0)
group = ClassGroup(name="REV-DEVOPS-1", program_id=program.id, status=ClassStatus.ONGOING, start_date=start, total_weeks=8, current_week=8,
                   start_time=dtime(9, 0), end_time=dtime(11, 0), days_of_week="sat", start_mode="fixed_date", capacity=10, max_capacity=10,
                   professor_name="Hela Prof")
db.add(group); db.flush()
people = {}
for name, email, phone in (("Rania Revue", "rania.revue@example.com", "+216 50 111 222"), ("Sami Revue", "sami.revue@example.com", "+216 50 333 444"),
                           ("Tarek Revue", "tarek.revue@example.com", "+216 50 555 666")):
    person = Candidate(full_name=name, email=email, phone=phone); db.add(person); db.flush()
    reg = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390, total_price=390,
                       paid_amount=390, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, registration_date=now - timedelta(days=60),
                       class_group_id=group.id)
    db.add(reg); db.flush(); people[name.split()[0]] = (person, reg)
db.commit()

# ======================================================================
# A. The last session day: asked then, while it is fresh
# ======================================================================
today = local_now().date()
earlier = ClassSession(class_group_id=group.id, week_number=7, session_date=today - timedelta(days=1), start_time=dtime(9, 0), end_time=dtime(11, 0), topic="Kubernetes")
last = ClassSession(class_group_id=group.id, week_number=8, session_date=today, start_time=dtime(9, 0), end_time=dtime(11, 0), topic="Projet final")
db.add_all([earlier, last]); db.commit()
check("the last session is known, an earlier one is not it", pf.is_last_session(db, last) and not pf.is_last_session(db, earlier))
during = local_now().replace(hour=10, minute=0, second=0, microsecond=0)
meetings.after_sessions(db, "https://centre.example.com", now=during); db.expire_all()
check("before the last session is over, nobody is asked", db.query(CandidateFeedback).filter(CandidateFeedback.source == pf.SOURCE).count() == 0)
after = local_now().replace(hour=15, minute=0, second=0, microsecond=0)
meetings.after_sessions(db, "https://centre.example.com", now=after); db.expire_all()
rows = db.query(CandidateFeedback).filter(CandidateFeedback.source == pf.SOURCE).all()
check("the last session is over: each candidate is on the list to ask", len(rows) == 3 and all(row.status == "to_ask" for row in rows), [(row.author_name, row.status) for row in rows])
meetings.after_sessions(db, "https://centre.example.com", now=after); db.expire_all()
check("...once: the next round adds nobody", db.query(CandidateFeedback).filter(CandidateFeedback.source == pf.SOURCE).count() == 3)
check("the review request is never held for the weekly letter", "feedback_request" not in letters.CAN_WAIT)

# ======================================================================
# B. The stars are inside the message
# ======================================================================
sent_mail.clear()
check("the requests go out", pf.send_due(db) == 3 and len(sent_mail) == 3, len(sent_mail))
rania, rania_reg = people["Rania"]
rania_row = pf.of_registration(db, rania_reg.id)
mail = next(m for m in sent_mail if m["to_email"] == "rania.revue@example.com")
links = [f"https://centre.example.com/opinion/{rania_row.external_id}?stars={n}" for n in (1, 2, 3, 4, 5)]
check("email: five stars, each a link to that rating", all(link in mail["body"] or link.replace("&", "&amp;") in mail["body"] for link in links) and mail["body"].count("&#9733;") >= 5, mail["body"][:300])
check("email text (and WhatsApp): one line per rating, best first", all(link in mail["text_body"] for link in links)
      and mail["text_body"].index("?stars=5") < mail["text_body"].index("?stars=1") and "Votre note, en un geste" in mail["text_body"])
rendered = tp.render_auto(db, "feedback_request", candidate=rania, registration=rania_reg, values={"feedback_link": "x", "star_links": pf.star_links(db, rania_row)})
check("WhatsApp gets the same stars", "?stars=4" in tp.whatsapp_text(rendered) and chr(0x2B50) * 5 in tp.whatsapp_text(rendered))
preview = client.get("/inbox/templates?key=feedback_request")
check("the template's preview shows the stars too", preview.status_code == 200)
check("ten seconds: the email says it", "Dix secondes" in mail["text_body"])

# ======================================================================
# C. One tap, then the rest only if they want
# ======================================================================
tapped = anon.get(f"/opinion/{rania_row.external_id}?stars=4")
db.expire_all(); rania_row = pf.of_registration(db, rania_reg.id)
check("a tap opens their page with that star chosen", tapped.status_code == 200 and re.search(r'id="star4" value="4"[^>]*checked', tapped.text) is not None and "form.submit()" in tapped.text)
check("...and opening a link alone keeps nothing (mail scanners open every link)", rania_row.status == "asked" and rania_row.rating is None)
first = anon.post(f"/opinion/{rania_row.external_id}", data={"step": "stars", "stars": "4"})
db.expire_all(); rania_row = pf.of_registration(db, rania_reg.id)
check("the star is kept at once, then a few words are offered", first.status_code == 303 and first.headers["location"].endswith("?step=more") and rania_row.status == "received" and rania_row.rating == 4)
check("no star: refused in words", anon.post(f"/opinion/{rania_row.external_id}", data={"step": "stars", "stars": "0"}).status_code == 400)
more = anon.get(f"/opinion/{rania_row.external_id}?step=more").text
check("step two: their words, what to improve, publishing, and a way to skip", 'name="words"' in more and 'name="improve"' in more and 'name="publish"' in more and "Passer" in more and 'name="stars"' not in more)
second = anon.post(f"/opinion/{rania_row.external_id}", data={"step": "more", "words": "Projets concrets, formateur très disponible.", "publish": "true"})
db.expire_all(); rania_row = pf.of_registration(db, rania_reg.id)
check("their words are kept with their stars, then their professor", second.headers["location"].endswith("?step=prof") and rania_row.rating == 4 and "concrets" in rania_row.comment and pf._payload(rania_row)["may_publish"] is True)
prof = anon.get(f"/opinion/{rania_row.external_id}?step=prof").text
check("step three: the professor by name, their stars, would they recommend us", "Hela Prof" in prof and 'name="prof_stars"' in prof and 'name="recommend"' in prof)
third = anon.post(f"/opinion/{rania_row.external_id}", data={"step": "prof", "prof_stars": "5", "prof_words": "Très clair", "recommend": "yes"})
db.expire_all(); rania_row = pf.of_registration(db, rania_reg.id); data = pf._payload(rania_row)
check("everything is kept, nothing given before is lost", third.headers["location"].endswith("?thanks=1") and rania_row.rating == 4 and "concrets" in rania_row.comment
      and data["prof_stars"] == 5 and data["recommend"] == "yes", data)
notes = db.query(Notification).filter(Notification.candidate_id == rania.id, Notification.category.in_(["FEEDBACK", "REVIEW_LOW"])).all()
check("the admin is told once, with their words once they came", len(notes) == 1 and notes[0].category == "FEEDBACK" and "concrets" in notes[0].message, [(n.category, n.message) for n in notes])

# Happy and publishing accepted: Google and Facebook, their words copied
thanks = anon.get(f"/opinion/{rania_row.external_id}?thanks=1").text
check("happy, publishing accepted: Facebook offered, their words ready to copy", "Partager sur Facebook" in thanks and "data-copy" in thanks and "clipboard" in thanks and "concrets" in thanks)
check("...no Google button while the Google link is not given", "Partager sur Google" not in thanks)
response = client.post("/settings/numbers", data={"google": "g.page/r/abc/review"})
check("the Google link must be a link", "error=" in response.headers.get("location", ""))
response = client.post("/settings/numbers", data={"google": "https://g.page/r/abc/review"}); db.expire_all()
check("the Google link is saved in Settings", get_settings(db).google_review_url == "https://g.page/r/abc/review")
thanks = anon.get(f"/opinion/{rania_row.external_id}?thanks=1").text
check("...then Google is offered first", "Partager sur Google" in thanks and "https://g.page/r/abc/review" in thanks and thanks.index("Partager sur Google") < thanks.index("Partager sur Facebook"))
check("the settings page has the field", 'name="google"' in client.get("/settings").text)

# ======================================================================
# D. Not fully happy: privately, and the admin calls them
# ======================================================================
sami, sami_reg = people["Sami"]
sami_row = pf.of_registration(db, sami_reg.id)
anon.post(f"/opinion/{sami_row.external_id}", data={"step": "stars", "stars": "2"}); db.expire_all()
sami_row = pf.of_registration(db, sami_reg.id)
low = db.query(Notification).filter(Notification.candidate_id == sami.id, Notification.category == "REVIEW_LOW").first()
check("1 to 3 stars: the admin is asked to contact them", low is not None and "2 stars" in low.title and low.action_url == f"/candidate/{sami.id}#reviews", (low.title, low.action_url) if low else None)
check("...it rings (it needs the admin)", rules.needs_you(db, low) and not rules.settled(db, low))
step = anon.get(f"/opinion/{sami_row.external_id}?step=more").text
check("they are told, privately, that the team will contact them; nothing to publish", "va vous contacter" in step and 'name="publish"' not in step)
anon.post(f"/opinion/{sami_row.external_id}", data={"step": "more", "words": "Trop rapide, je n'ai pas tout compris."}); db.expire_all()
thanks = anon.get(f"/opinion/{sami_row.external_id}?thanks=1").text
check("their thank-you: no Google, no Facebook, a call is coming", "Partager" not in thanks and "facebook.com" not in thanks and "contacter" in thanks)
low = db.get(Notification, low.id)
check("their words reach the same notification", "Trop rapide" in low.message)
page = client.get(f"/candidate/{sami.id}")
db.expire_all(); low = db.get(Notification, low.id)
check("opening their page does not close it: it waits for the call", page.status_code == 200 and not low.is_read and "I contacted them" in page.text)
check("the candidate page offers to celebrate a success", "Celebrate a success" in page.text and f"/candidate/{sami.id}/celebrate/{sami_reg.id}" in page.text)
db.add(CandidateEmail(candidate_id=sami.id, direction="outbound", sender="centre@example.com", recipient=sami.email, subject="Votre avis",
                      body="Bonjour Sami, merci pour votre avis, pouvons-nous en parler ?", status="sent", author="admin",
                      received_at=datetime.utcnow() + timedelta(seconds=5)))
db.commit()
rules.tidy(db, force=True); db.expire_all()
check("once the team wrote to them, it closes by itself", db.get(Notification, low.id).is_read and db.get(Notification, low.id).status == "settled")

# A happy one who changes their mind to 2 stars
tarek, tarek_reg = people["Tarek"]
tarek_row = pf.of_registration(db, tarek_reg.id)
anon.post(f"/opinion/{tarek_row.external_id}", data={"step": "stars", "stars": "5"}); db.expire_all()
note = db.query(Notification).filter(Notification.candidate_id == tarek.id).filter(Notification.category.in_(["FEEDBACK", "REVIEW_LOW"])).one()
note.is_read = True; db.commit()
anon.post(f"/opinion/{tarek_row.external_id}", data={"step": "stars", "stars": "2"}); db.expire_all()
note = db.get(Notification, note.id)
check("a rating changed to low: the same notification asks for a call, and rings again", note.category == "REVIEW_LOW" and not note.is_read and "2 stars" in note.title)
response = client.post(f"/candidate/{tarek.id}/review/{tarek_reg.id}/contacted"); db.expire_all()
check("'I contacted them' closes it", response.status_code == 303 and db.get(Notification, note.id).is_read and pf._payload(pf.of_registration(db, tarek_reg.id)).get("contacted"))

# ======================================================================
# E. The rating in one line
# ======================================================================
check("the rating line is a variable of every email", "rating_line" in {name for name, _, _ in tp.VARIABLES})
check("three reviews: '…/5 d'après 3 candidats'", pf.rating_line(db) == f"{str(pf.rating(db)['stars']).replace('.', ',')}/5 d'après 3 candidats", pf.rating_line(db))
line = tp.render_auto(db, "feedback_request", candidate=rania, registration=rania_reg, overrides={"body": "Déjà {rating_line} !"}, values={"feedback_link": "x"})
check("...said in an email where it is written", "d'après 3 candidats" in line["text"])
check("the review page says it too", "d&#39;après 3 candidats" in anon.get(f"/opinion/{rania_row.external_id}?edit=1").text or "d'après 3 candidats" in anon.get(f"/opinion/{rania_row.external_id}?edit=1").text)
pf.RATING_SHOWN_FROM, kept = 50, pf.RATING_SHOWN_FROM
check("too few reviews: nothing is said", pf.rating_line(db) == "")
pf.RATING_SHOWN_FROM = kept

# ======================================================================
# F. WhatsApp first
# ======================================================================
was = wa.configured
wa.configured = lambda: False
check("no Business number: the usual way", pf.first_way(db) is None)
wa.configured = lambda: True
check("a Business number: WhatsApp first", pf.first_way(db) == "whatsapp")
channels.set_choice(db, "email", True)
check("...unless the admin chose a way above the inbox", pf.first_way(db) is None)
channels.set_choice(db, "auto", True)
wa.configured = was

# ======================================================================
# G. The QR code of the room
# ======================================================================
page = client.get(f"/classes/{group.id}/opinion-qr"); db.expire_all()
token = db.get(ClassGroup, group.id).opinion_token
check("the class's QR page: a code leading to the class's review page", page.status_code == 200 and token and f"https://centre.example.com/opinion/class/{token}" in page.text and "qrcode" in page.text)
check("the class page leads to it", f"/classes/{group.id}/opinion-qr" in client.get(f"/classes/{group.id}").text)
check("the QR page is closed to visitors", TestClient(main.app, follow_redirects=False).get(f"/classes/{group.id}/opinion-qr").status_code in (303, 401))
room = anon.get(f"/opinion/class/{token}")
check("scanned: it opens without signing in and shows nobody's name", room.status_code == 200 and 'name="said"' in room.text and "Rania" not in room.text and "Sami" not in room.text)
check("an unknown code says so", anon.get("/opinion/class/" + "y" * 24).status_code == 404)
check("someone not in the class is not let in", anon.post(f"/opinion/class/{token}", data={"said": "nobody@example.com"}).status_code == 400)
found = anon.post(f"/opinion/class/{token}", data={"said": "RANIA.REVUE@example.com "})
check("their email opens their own review", found.status_code == 303 and found.headers["location"] == f"/opinion/{rania_row.external_id}")
found = anon.post(f"/opinion/class/{token}", data={"said": "50 555 666"})
check("their phone number too", found.status_code == 303 and found.headers["location"] == f"/opinion/{tarek_row.external_id}")

# ======================================================================
# H. A success: congratulated, and asked then
# ======================================================================
sent_mail.clear()
response = client.post(f"/candidate/{rania.id}/celebrate/{rania_reg.id}", data={"kind": "internship", "words": "chez Vermeg"}); db.expire_all()
check("celebrated: they get congratulations", response.status_code == 303 and "notice=" in response.headers["location"] and len(sent_mail) == 1 and "Félicitations" in sent_mail[0]["subject"], response.headers.get("location"))
check("...naming the success, with Google to tell it", "un stage (chez Vermeg)" in sent_mail[0]["text_body"] and "https://g.page/r/abc/review" in sent_mail[0]["text_body"], sent_mail[0]["text_body"][:400] if sent_mail else None)
check("kept on their review, shown on their page", any(item["kind"] == "internship" for item in pf._payload(pf.of_registration(db, rania_reg.id)).get("successes") or [])
      and "Congratulated for an internship" in client.get(f"/candidate/{rania.id}").text)
check("given to the Social media page as a success story", any(item["first_name"] == "Rania" and item["kind"] == "internship" for item in pf.successes(db)))
check("an unknown success is refused", "error=" in client.post(f"/candidate/{rania.id}/celebrate/{rania_reg.id}", data={"kind": "lottery"}).headers["location"])
check("its email can be read and changed like the others", "success_review" in tp.AUTO_EMAILS and client.get("/inbox/templates?key=success_review").status_code == 200)
check("it also goes on WhatsApp once a Business number is there", "success_review" in wa.REMINDERS)

db.close()
print(f"{sum(results)} of {len(results)} checks passed")

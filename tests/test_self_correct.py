"""The batch of 2 November (3): the system corrects itself. A reply to the opinion email that asks something is a
question, not a review; the admin's correction ("it's a question, not an opinion") takes the review back from every
analysis (stars, mood, themes, the dashboard) and is learnt; the system reads its own past decisions again with what
it learnt. Runs on a COPY; no mail leaves this machine."""
import json
from datetime import datetime, timedelta, time as dtime
from urllib.parse import unquote_plus

import testenv

testenv.prepare("self_correct")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.feedback import CandidateFeedback
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, email_assistant as ea, email_templates as tp, email_threads as et, enrollment_service as es, feedback_hub,
                          orchestrator, program_feedback as pf, reply_rules, whats_new)
from app.services.schedule_utils import local_now

sent = []


def fake_send(**kw):
    sent.append(kw)
    return True


et.send_email = fake_send
tp.send_email = fake_send
et.smtp_configured = lambda: True
ea.model_available = lambda: False

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Leila Admin", password="self-correct-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "self-correct-1"})


def where(response):
    return unquote_plus(response.headers.get("location", ""))


def fresh(message_id):
    db.expire_all()
    return db.query(CandidateEmail).filter(CandidateEmail.message_id == message_id).one()


# ======================================================================
# A. Reading a reply to the opinion email
# ======================================================================
cases = {"comment puis je savoir les procedure de l'inscription": "question", "l'ambiance, grace eux j'ai appris": "opinion",
         "Très bonne formation, merci ! Quand commence la prochaine session ?": "both", "Je voudrais une attestation": "question",
         "Le formateur était super mais les séances trop longues": "opinion", "Je donne 4 étoiles, très enrichissant": "opinion"}
wrong = {text: pf.reading_of(None, text) for text, kind in cases.items() if pf.reading_of(None, text) != kind}
check("reading: an opinion, a question, or both", not wrong, wrong)

# A class ends; four candidates are asked for their opinion.
program = db.query(Program).filter(Program.name == "DevOps").first() or db.query(Program).first()
start = (local_now() - timedelta(days=50)).replace(hour=9, minute=0, second=0, microsecond=0)
group = ClassGroup(name="SELF-1", program_id=program.id, status=ClassStatus.ONGOING, start_date=start, total_weeks=8, current_week=8,
                   start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat", start_mode="fixed_date", capacity=10, max_capacity=10)
db.add(group)
db.flush()
people = {}
for name in ("Amira", "Bilel", "Chaima", "Dora", "Emna"):
    person = Candidate(full_name=f"{name} Self", email=f"{name.lower()}.self@example.com", phone="50 200 300")
    db.add(person)
    db.flush()
    registration = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390,
                                total_price=390, paid_amount=390, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED,
                                registration_date=datetime.utcnow() - timedelta(days=60), class_group_id=group.id)
    db.add(registration)
    db.flush()
    people[name] = (person, registration)
db.commit()
es.complete_class(db, group)
pf.send_due(db)
db.expire_all()


def reply_to_request(name, body, key):
    person, _ = people[name]
    request = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == person.id, CandidateEmail.intent == "feedback_request").first()
    ea.ingest_inbound(db, sender=person.email, subject=f"RE: {request.subject}", body=body, in_reply_to=request.message_id, message_id=key)
    return fresh(key)


asked = reply_to_request("Amira", "comment puis je savoir les procedure de l'inscription", "<self-1@example.com>")
row = pf.of_registration(db, people["Amira"][1].id)
check("a question is not kept as an opinion: still waiting for their real opinion", row.status == "asked", row.status)
check("…and the question is not set aside: it is answered or comes to you", asked.status != "no_reply" and "read as a question" in (asked.analysis or ""),
      (asked.status, asked.analysis))
said = reply_to_request("Bilel", "Formation très enrichissante, je donne 4 étoiles, merci au formateur !", "<self-2@example.com>")
row = pf.of_registration(db, people["Bilel"][1].id)
check("an opinion is kept, and set aside as one", row.status == "received" and row.rating == 4 and said.status == "no_reply"
      and reply_rules.reason_of(said)["rule"] == "opinion")
both = reply_to_request("Chaima", "Très bonne formation, merci ! Quand commence la prochaine session DevOps ?", "<self-3@example.com>")
row = pf.of_registration(db, people["Chaima"][1].id)
check("both: the opinion is kept and the question is answered", row.status == "received" and both.status != "no_reply" and "question in it" in (both.analysis or ""),
      (row.status, both.status, both.analysis))

# ======================================================================
# B. The admin's correction takes the review back from every analysis
# ======================================================================
# As it was before this batch: a question kept as an opinion, and its message set aside.
person, registration = people["Dora"]
request = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == person.id, CandidateEmail.intent == "feedback_request").first()
from app.models.email_thread import EmailThread

wrongly = et.record_message(db, db.get(EmailThread, request.thread_id), direction="inbound", author="candidate", message_id="<self-4@example.com>",
                            sender=person.email, recipient="9antra", subject="RE: avis", body="Merci, je voudrais savoir le prix du niveau 2 ?", status="no_reply")
wrongly.analysis = "Their opinion on the program, kept with the class."
reply_rules.mark(wrongly, "opinion")
pf.from_reply(db, registration.id, wrongly.body, message_id=wrongly.id)
db.commit()
reviews_before = [item for item in feedback_hub.overview(db)["rows"] if item["source"] == "review"]
check("before: the question is counted as a review", any("prix du niveau 2" in item["text"] for item in reviews_before))
page = admin.get(f"/inbox?thread={wrongly.thread_id}").text
check("inbox: on a message read as an opinion, “It's a question, not an opinion”", "It's a question, not an opinion" in page)
response = admin.post(f"/inbox/message/{wrongly.id}/needs-answer")
db.expire_all()
row = pf.of_registration(db, registration.id)
check("corrected: the review is taken back (back to waiting for their real opinion)", row.status == "asked" and row.comment is None
      and json.loads(row.raw_payload)["withdrawn"]["words"] == "Merci, je voudrais savoir le prix du niveau 2 ?")
reviews_after = [item for item in feedback_hub.overview(db)["rows"] if item["source"] == "review"]
check("corrected: it leaves the dashboard's reviews (stars, mood, themes)", not any("prix du niveau 2" in item["text"] for item in reviews_after)
      and len(reviews_after) == len(reviews_before) - 1)
message = fresh("<self-4@example.com>")
check("corrected: the message comes back with an answer prepared, never sent alone", message.status == "needs_admin" and message.requires_admin
      and "You said this message needs an answer" in (message.escalation_reason or ""), (message.status, message.escalation_reason))
check("corrected: the example is learnt", "merci, je voudrais savoir le prix du niveau 2 ?" in reply_rules.rules(db)["not_opinions"])
check("learnt: a reply like it is read as a question (it was “both” without the example)",
      pf.reading_of(None, "Merci, je voudrais savoir le prix du niveau 2 svp ?") == "both" and pf.reading_of(db, "Merci, je voudrais savoir le prix du niveau 2 svp ?") == "question")
answer = reply_rules.talk(db, "annule")
check("“undo” puts the rule back", "merci, je voudrais savoir le prix du niveau 2 ?" not in reply_rules.rules(db)["not_opinions"], answer["reply"])

# In words, under the message: "c'est une question, pas un avis".
person, registration = people["Emna"]
request = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == person.id, CandidateEmail.intent == "feedback_request").first()
talked = et.record_message(db, db.get(EmailThread, request.thread_id), direction="inbound", author="candidate", message_id="<self-5@example.com>",
                           sender=person.email, recipient="9antra", subject="RE: avis", body="Est-ce que je peux refaire le niveau 1 ?", status="no_reply")
reply_rules.mark(talked, "opinion")
pf.from_reply(db, registration.id, talked.body, message_id=talked.id)
db.commit()
said = admin.post(f"/inbox/message/{talked.id}/talk", json={"text": "C'est une question, pas un avis"}).json()
db.expire_all()
check("talk: “c'est une question, pas un avis” does the same", pf.of_registration(db, registration.id).status == "asked" and said["reload"]
      and fresh("<self-5@example.com>").status != "no_reply", said)

# ======================================================================
# C. The system reads its own decisions again and corrects itself
# ======================================================================
person, registration = people["Bilel"]
false_opinion = pf.of_registration(db, registration.id)
false_opinion.comment = "Comment je peux avoir mon attestation ?"          # as if an older reading had kept a question as an opinion
db.commit()
thanks = et.open_thread(db, person.id, "Merci")
setaside = et.record_message(db, thanks, direction="inbound", author="candidate", message_id="<self-6@example.com>", sender=person.email,
                             recipient="9antra", subject="Merci", body="Ok merci, et pour le certificat je fais comment", status="no_reply")
reply_rules.mark(setaside, "thanks", word="ok")
db.commit()
result = reply_rules.read_again(db)
db.expire_all()
check("read again: a message it had set aside that asks something comes back", fresh("<self-6@example.com>").status != "no_reply"
      and any(item.message_id == "<self-6@example.com>" for item in result["messages"]), [item.message_id for item in result["messages"]])
check("read again: an opinion that was a question leaves the reviews", result["opinions"] >= 1 and pf.of_registration(db, registration.id).status == "asked")
check("read again: you are told, with where to look", db.query(Notification).filter(Notification.title == "The system corrected itself").count() == 1)
check("read again: what it brings back says the system read it again", "Read again by the system" in (fresh("<self-6@example.com>").escalation_reason or ""))
again = reply_rules.read_again(db)
check("read again: twice changes nothing more", not again["messages"] and again["opinions"] == 0)
page = admin.get("/inbox?filter=aside").text
check("inbox: “read them again” on the set-aside list", 'action="/inbox/rules/read-again"' in page)
check("inbox: the button says what it found", "every decision still holds" in where(admin.post("/inbox/rules/read-again")))
answer = orchestrator.dispatch_chat(db, "relis les messages mis de côté")
check("Yasmine: “relis les messages mis de côté”", answer["agent"]["key"] == "inbox" and ("Relu" in answer["reply"] or "Read again" in answer["reply"]), answer["reply"][:120])
check("what's new: this batch is told", any(release["key"] == "2026-11-02c" for release in whats_new.RELEASES))

db.close()
check.done()

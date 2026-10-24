"""The catalogue read on this computer (no Gemini), the review form (happiness, what to improve, the professor,
recommend, then Facebook) and its analysis per program and professor, the emotions in candidates' messages (and the
tone of answers), the candidate analyses (motivation, use, loyalty, feelings: each saying how and from what), and
messages in Arabic letters. Runs on a COPY; email and WhatsApp are fakes."""
import json
import os
from datetime import datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("insights")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, candidate_insights, catalogue_reader, derja, email_assistant as ea, email_threads as et, emotions,
                          program_feedback)

emails = []
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
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="insights-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "insights-test-1"})

# ---- A. The catalogue, read here ----
catalogue = os.path.join(testenv.PROJECT, "app", "knowledge", "Planning Courses.pdf")
data = open(catalogue, "rb").read()
programs = catalogue_reader.read(data)
names = [item["name"] for item in programs]
check("catalogue: the nine programs are read", len(programs) == 9 and "DevOps" in names and "Cyber Security" in names, names)
devops = next(item for item in programs if item["name"] == "DevOps")
check("catalogue: prices, duration and hours", devops["price_online"] == 390 and devops["price_onsite"] == 490 and devops["total_duration_days"] == 60
      and devops["hours_per_week"] == 6 and devops["training_hours"] == 4 and devops["supervision_hours"] == 2, devops)
check("catalogue: the session time", devops["class_session_time"] == "Saturday 9:00 - 13:00", devops["class_session_time"])
check("catalogue: levels and weeks", len(devops["levels"]) == 2 and [week["week_number"] for week in devops["levels"][1]["weeks"]] == [5, 6, 7, 8]
      and "Kubernetes" in devops["levels"][1]["weeks"][0]["topic_title"], [level["title"] for level in devops["levels"]])
check("catalogue: the technologies", "Terraform" in devops["description"])
check("catalogue: the letters spaced one by one are put back", catalogue_reader.despace("P L A N N I N G  C O U R S E S") == "PLANNING COURSES")
page = admin.post("/programs/upload", files={"file": ("Planning Courses.pdf", data, "application/pdf")})
check("import page: the catalogue is read without Gemini", page.status_code == 200 and "Cyber Security" in page.text and "Marketing Digital" in page.text,
      (page.status_code, "Cyber Security" in page.text, page.text[page.text.find("alert"):page.text.find("alert") + 300]))

# ---- B. The review: the program, happiness, what to improve, the professor, recommend; then Facebook ----
program = db.query(Program).order_by(Program.name).first()
group = ClassGroup(name="INSIGHT-CLASS", program_id=program.id, professor_name="Hela Prof", status=ClassStatus.COMPLETED,
                   start_date=datetime.now() - timedelta(days=70), total_weeks=8, start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat")
db.add(group)
db.flush()
learner = Candidate(full_name="Ines Avis", email="ines.avis@example.com", phone="98 333 444")
db.add(learner)
db.flush()
reg = Registration(candidate_id=learner.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390, paid_amount=390,
                   payment_status=PaymentStatus.PAID, status=RegistrationStatus.COMPLETED, class_group_id=group.id,
                   registration_date=datetime.utcnow() - timedelta(days=90))
db.add(reg)
db.commit()
program_feedback.queue_class(db, group, [reg])
row = program_feedback.ask(db, reg, manual=True)
db.commit()
anon = TestClient(main.app, follow_redirects=False)
form = anon.get(f"/opinion/{row.external_id}").text
check("review form: happiness, what to improve, the professor by name, recommend", 'name="happy"' in form and 'name="improve"' in form
      and 'name="prof_stars"' in form and "Hela Prof" in form and 'name="recommend"' in form)
answer = anon.post(f"/opinion/{row.external_id}", data={"stars": "4", "words": "Les projets étaient concrets.", "happy": "5",
                                                         "improve": "Le rythme était trop rapide, plus d'exercices svp.", "prof_stars": "5",
                                                         "prof_words": "Très disponible", "recommend": "yes"})
check("review: kept, then the thank-you page", answer.status_code == 303 and "thanks=1" in answer.headers["location"])
thanks = anon.get(answer.headers["location"]).text
check("review: then they are taken to the Facebook page", "facebook.com/" in thanks and "window.location.href" in thanks and "Rester ici" in thanks)
db.expire_all()
kept = program_feedback._payload(program_feedback.of_registration(db, reg.id))
check("review: everything kept", kept.get("happy") == 5 and kept.get("prof_stars") == 5 and kept.get("recommend") == "yes" and "rythme" in kept.get("improve", "")
      and kept.get("professor") == "Hela Prof", kept)
summary = program_feedback.of_program(db, program.id)
check("analysis: happiness, recommendation, the professor's stars, what to improve", summary["happy_pct"] == 100 and summary["recommend_pct"] == 100
      and summary["professors"] == [{"name": "Hela Prof", "stars": 5.0, "count": 1}] and summary["to_improve"], summary["to_improve"])
check("analysis: says how it was counted", "fixed word lists" in summary["how"])
page = admin.get(f"/programs/{program.id}").text
check("program page: the analysis", "Happy 100%" in page and "Recommend 100%" in page and "Hela Prof" in page and "How this is counted" in page)

# ---- C. Emotions ----
read = emotions.read("Bonjour, je suis inquiet, c'est urgent !!!")
check("emotions: worried and in a hurry, with the words", {item["key"] for item in read["emotions"]} >= {"worried", "urgent"}
      and any("inquiet" in item["words"] for item in read["emotions"]))
check("emotions: Tunisian", emotions.read("mafhemtch el exercice w khayef")["main"] in ("worried", "confused"))
check("emotions: Arabic", emotions.read("أنا خايف برشا")["main"] == "worried")
check("emotions: motivated", emotions.read("J'ai hâte de commencer, merci beaucoup !")["main"] == "eager")
check("emotions: nothing in a plain question", emotions.read("Bonjour, quel est le prix ?")["main"] is None)
check("emotions: says how", "word lists" in read["how"])
ea.ingest_inbound(db, sender="ines.avis@example.com", subject="Inadmissible", body="C'EST INADMISSIBLE, personne ne répond à mes emails depuis une semaine !!",
                  message_id="<emo-1@example.com>")
db.expire_all()
angry = db.query(CandidateEmail).filter(CandidateEmail.message_id == "<emo-1@example.com>").one()
check("emotions: kept on the message", angry.emotions and json.loads(angry.emotions)["main"] == "frustrated")
check("emotions: a frustrated candidate is answered by a person", angry.status == "needs_admin"
      and ("frustrated" in (angry.escalation_reason or "") or "Complaint" in (angry.escalation_reason or "") or "complaint" in (angry.escalation_reason or "").lower()),
      angry.escalation_reason)
page = admin.get(f"/inbox?thread={angry.thread_id}").text
check("inbox: the feeling shows, with the words that showed it", "Frustrated, angry" in page and "Words that showed it" in page)
check("tone: a worried candidate is reassured first", emotions.opening("worried", "fr").startswith("Je comprends votre inquiétude"))

# ---- D. The candidate seen whole ----
insights = candidate_insights.of_candidate(db, learner)
check("analyses: motivation, use, loyalty, feelings", set(insights) == {"motivation", "usage", "loyalty", "feelings"})
check("analyses: each parameter says its rule and where it was read", all(param["rule"] and param["source"] for key in ("motivation", "usage", "loyalty")
                                                                       for param in insights[key]["params"]))
check("analyses: loyalty is counted (programs, finished, time, cancellations, review)", insights["loyalty"]["score"] is not None
      and {"programs", "completed", "review"} <= {param["key"] for param in insights["loyalty"]["params"] if param["score"] is not None},
      [(param["key"], param["score"]) for param in insights["loyalty"]["params"]])
check("analyses: feelings counted from their messages", any(item["key"] == "frustrated" for item in insights["feelings"]["emotions"]))
page = admin.get(f"/candidate/{learner.id}").text
check("candidate file: the analyses, with how they were counted", 'id="motivation"' in page and 'id="usage"' in page and 'id="loyalty"' in page
      and "How it was counted, and from what" in page and "Read in:" in page and "What they felt when writing to you" in page)

# ---- E. Arabic letters ----
check("arabic: the list reads Tunisian in Arabic letters", derja.arabic_coverage("قداش باقي نخلص") == 1.0 and "combien" in derja.to_french("قداش باقي نخلص"))
owing = Candidate(full_name="Amel Arabe", email="amel.arabe@example.com")
db.add(owing)
db.flush()
db.add(Registration(candidate_id=owing.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390, paid_amount=190,
                    payment_status=PaymentStatus.PARTIAL, status=RegistrationStatus.PENDING))
db.commit()
emails.clear()
ea.ingest_inbound(db, sender="amel.arabe@example.com", subject="الخلاص", body="عسلامة، قداش باقي نخلص ؟", message_id="<ar-1@example.com>")
db.expire_all()
message = db.query(CandidateEmail).filter(CandidateEmail.message_id == "<ar-1@example.com>").one()
reply = next((item for item in emails if item[0] == "amel.arabe@example.com"), None)
check("arabic: understood (what they asked, read in French)", message.analysis and "Written in Tunisian" in message.analysis, message.analysis)
check("arabic: a sure answer goes, in French, after a sentence in Arabic", (reply is not None and "شكرًا على رسالتك" in reply[2] and "200" in reply[2])
      or (message.status == "needs_admin" and message.suggested_reply), (message.status, message.escalation_reason, reply and reply[2][:200]))

db.close()
check.done()

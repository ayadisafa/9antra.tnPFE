"""Weekly questions always on (every running class, every week, by themselves; from the course plan, about what the class
already studied, when there is no course file or no model; the email cannot be switched off) and candidates asking for the
course: sent attached when its files are there, else their professor and the admin are asked and it goes by itself once
added; a week not taught yet is said so. Runs on a COPY; email, WhatsApp and SMS are fakes; the model is off."""
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("weekly_requests")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Professor, Program, Registration, User, UserSession
from app.models.course_material import ClassWeek, WeekQuestion
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.team_message import TeamMessage
from app.services import (auth, course_requests, email_assistant as ea, email_templates as tp, email_threads as et, weekly_questions)
from app.services.settings_service import get_settings
from app.services import chatbot_sources

delivered = []


def fake_attempt(to, subject, plain, html_body, *rest, **kw):
    files = rest[3] if len(rest) > 3 else []
    delivered.append({"to": to, "subject": subject, "text": plain, "files": [getattr(item, "filename", "") for item in (files or [])]})
    return None, "msgid"


et._attempt = fake_attempt
et.smtp_configured = lambda: True
ea.model_available = lambda: False
ea.llm_json = lambda *a, **k: None
weekly_questions.write_for_passage = lambda *a, **k: []       # the model writes nothing: the plan must answer


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="weekly-test-12", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "weekly-test-12"})
settings = get_settings(db)
settings.email_auto_reply_mode = "auto"
db.commit()

program = next(item for item in db.query(Program).order_by(Program.name).all() if len([w for w in chatbot_sources._weeks_of(db, item)[0] if w["title"]]) >= 6)
teacher = Professor(name="Pr Hela Cours", specialties=program.name, email="hela.cours@example.com", phone="22 333 444")
db.add(teacher)
db.flush()
start = datetime.combine(date.today() - timedelta(days=20), dtime(9, 0))
group = ClassGroup(name="WQ-CLASS", program_id=program.id, professor_id=teacher.id, professor_name=teacher.name, status=ClassStatus.ONGOING,
                   start_date=start, total_weeks=8, current_week=3, start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat",
                   weekly_questions_mode="manual")
db.add(group)
db.flush()
learner = Candidate(full_name="Rania Apprend", email="rania.apprend@example.com", phone="50 444 555")
db.add(learner)
db.flush()
db.add(Registration(candidate_id=learner.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390, paid_amount=390,
                    payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, registration_date=datetime.utcnow(), class_group_id=group.id))
db.commit()

# ---- A. Always on ----
check("the email of the weekly questions cannot be switched off", tp.is_on(db, "weekly_questions"))
row = tp.get(db, "weekly_questions")
check("the course page says it is always on", "Always on: every week, after its session" in admin.get(f"/classes/{group.id}/course").text)
queued = weekly_questions.queue_due(db)
weeks = {week.week_number: week.questions_status for week in db.query(ClassWeek).filter(ClassWeek.class_group_id == group.id).all()}
check("always on: the week the class is in and the one before are asked for, not further back", queued >= 2 and set(weeks) == {2, 3}
      and all(state == "queued" for state in weeks.values()), weeks)

# ---- B. No course file, no model: questions from what the class already studied ----
while weekly_questions.work_one(db):
    pass
db.expire_all()
written = db.query(WeekQuestion).filter(WeekQuestion.class_group_id == group.id, WeekQuestion.week_number == 3).all()
plan_weeks = {week["number"]: week for week in chatbot_sources._weeks_of(db, program)[0]}
subject = next((item for item in written if item.question == "Quel était le sujet de la semaine 3 ?"), None)
check("written from the course plan, ready to send", written and all(item.author == "plan" and item.status == "ready" for item in written), [item.question for item in written])
check("the right answer is the week's own subject", subject is not None and subject.choices[subject.correct_answer] == plan_weeks[3]["title"].strip(),
      subject.choices if subject else None)
check("and what they studied before", any(item.question.startswith("Vous l'avez déjà étudié") for item in written))
check("the week says where its questions come from", "course plan" in (db.query(ClassWeek).filter(ClassWeek.class_group_id == group.id,
                                                                                                ClassWeek.week_number == 3).one().questions_note or ""))

# ---- C. Sent by themselves once the week is over ----
delivered.clear()
sent = weekly_questions.send_due(db, "http://127.0.0.1:8000")          # this computer: candidates cannot open its pages
db.expire_all()
check("sent by themselves (the class was set to “by hand”: always on now)", sent >= 1 and db.get(ClassGroup, group.id).weekly_questions_mode == "auto"
      and any(item["to"] == learner.email for item in delivered), (sent, [item["to"] for item in delivered]))
mail = next((item for item in delivered if item["to"] == learner.email), {"text": ""})
check("no public address: the questions are in the email, answered by replying", "1. " in mail["text"] and "A) " in mail["text"]
      and "répondez simplement à cet email" in mail["text"] and "127.0.0.1" not in mail["text"], mail["text"][-400:])

# ---- C2. The candidate answers by replying: graded at once, the score and corrections sent back ----
from app.models.quiz import Quiz

quiz = weekly_questions.waiting_quiz(db, learner.id)
asked = weekly_questions.questions_in(quiz)
reply = ", ".join(f"{index}-{weekly_questions.LETTERS[item['correct_answer']]}" for index, item in enumerate(asked, 1))
delivered.clear()
ea.ingest_inbound(db, sender=learner.email, subject=f"Re: {quiz.course_name}", body=f"{reply}\n\nLe lun. 26 oct. 2026, 9antra a écrit :\n> 1. ...",
                  message_id=f"<answers-{datetime.utcnow().timestamp()}@example.com>")
db.expire_all()
quiz = db.get(Quiz, quiz.id)
graded = [item for item in delivered if item["to"] == learner.email]
check("the reply is read as the answers and graded", quiz.completed_at is not None and quiz.score == 100.0 and quiz.answer_source == "email",
      (quiz.completed_at, quiz.score))
check("the score is sent back", graded and f"{len(asked)} bonne(s) réponse(s) sur {len(asked)}" in graded[-1]["text"] and "Bravo" in graded[-1]["text"],
      graded[-1]["text"][:200] if graded else None)

# ---- D. The candidate asks for the course: the files are there ----
upload = admin.post(f"/classes/{group.id}/course/2/files", files=[("files", ("cours-semaine-2.txt", b"Les boucles for et while en Python.\n" * 20, "text/plain"))],
                    data={"note": "Les boucles"})
check("a course file added for week 2", upload.status_code in (200, 303), upload.status_code)
delivered.clear()
out = ea.ingest_inbound(db, sender=learner.email, subject="Support", body="Bonjour, pouvez-vous m'envoyer le support de la semaine 2 ? Merci",
                        message_id=f"<course-2-{datetime.utcnow().timestamp()}@example.com>")
mail = [item for item in delivered if item["to"] == learner.email]
check("sent at once, attached", out.get("status") in ("received", "stored") and mail and "cours-semaine-2.txt" in mail[-1]["files"]
      and "semaine 2" in mail[-1]["text"], (out, mail[-1] if mail else None))

# ---- E. Not there yet: their professor and the admin are asked; it goes by itself once added ----
delivered.clear()
before = db.query(TeamMessage).filter(TeamMessage.person == f"professor:{teacher.id}").count()
ea.ingest_inbound(db, sender=learner.email, subject="Cours", body="Bonjour, envoyez-moi le cours de la semaine 3 svp",
                  message_id=f"<course-3-{datetime.utcnow().timestamp()}@example.com>")
db.expire_all()
told = [item for item in delivered if item["to"] == learner.email]
waiting = db.query(course_requests.CourseRequest).filter(course_requests.CourseRequest.class_group_id == group.id, course_requests.CourseRequest.week_number == 3).one_or_none()
check("not there yet: the candidate is told it was passed on", told and "transmise" in told[-1]["text"] and teacher.name in told[-1]["text"], told[-1]["text"][:200] if told else None)
check("their professor is asked", db.query(TeamMessage).filter(TeamMessage.person == f"professor:{teacher.id}").count() > before)
check("the admin is told", db.query(Notification).filter(Notification.title.like("% asks for the course of week 3")).count() == 1)
check("the request waits for the files", waiting is not None and waiting.status == "waiting")
delivered.clear()
admin.post(f"/classes/{group.id}/course/3/files", files=[("files", ("cours-semaine-3.txt", b"Les fonctions en Python.\n" * 20, "text/plain"))], data={"note": "Les fonctions"})
db.expire_all()
mail = [item for item in delivered if item["to"] == learner.email]
check("added: it goes to them by itself", mail and "cours-semaine-3.txt" in mail[-1]["files"], mail[-1] if mail else delivered[:1])
check("the request is done", db.get(course_requests.CourseRequest, waiting.id).status == "sent")

# ---- F. A week not taught yet ----
delivered.clear()
ea.ingest_inbound(db, sender=learner.email, subject="Cours", body="Bonjour, envoyez-moi le support de la semaine 7",
                  message_id=f"<course-7-{datetime.utcnow().timestamp()}@example.com>")
told = [item for item in delivered if item["to"] == learner.email]
check("a week not taught yet is said so", told and "pas encore été vue" in told[-1]["text"], told[-1]["text"][:160] if told else None)

# ---- G. Not a request for files ----
check("“when does the course start” is not a request for files", not course_requests.asks_for_material("je veux savoir quand commence le cours"))
check("an exercise not understood is not a request for files", not course_requests.asks_for_material("je n'ai pas compris l'exercice 3 de la semaine 2"))
check("the program's PDF stays the program's PDF", not course_requests.asks_for_material("envoyez-moi le programme en pdf"))
check("Arabic and Tunisian requests are understood", course_requests.asks_for_material("أرسل لي ملفات الدرس") and course_requests.asks_for_material("ab3athli el cours mta3 el semaine 2"))

db.close()
check.done()

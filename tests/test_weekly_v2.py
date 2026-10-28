"""Weekly questions, second step: better questions by rules when there is no course file (the model is never asked from the
course plan alone; its vague questions are replaced), the admin chooses how often (every week, 2 or 4 weeks, stopped) and
how candidates answer, replies are read however they are written and add up, earlier replies are read again, and the
answers are shown like the level checks. Runs on a COPY; sending is faked; the model is off."""
import json
from datetime import date, datetime, time as dtime, timedelta

import testenv

testenv.prepare("weekly_v2")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Professor, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.course_material import ClassWeek, WeekQuestion
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import auth, chatbot_sources, email_assistant as ea, email_threads as et, level_bank, weekly_questions as wq

delivered = []
et._attempt = lambda to, subject, plain, *rest, **kw: (delivered.append({"to": to, "subject": subject, "text": plain}) or (None, "id"))
et.smtp_configured = lambda: True
ea.llm_json = lambda *a, **k: None
asked_model = []
wq.write_for_passage = lambda *a, **k: (asked_model.append(1) or [])

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="weekly-v2-123", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "weekly-v2-123"})

program = next(item for item in db.query(Program).order_by(Program.name).all()
               if level_bank.subject_of(item.name, item.website_slug) and len([w for w in chatbot_sources._weeks_of(db, item)[0] if w["title"]]) >= 6)
teacher = Professor(name="Pr Nadia Semaine", specialties=program.name, email="nadia.semaine@example.com")
db.add(teacher)
db.flush()
group = ClassGroup(name="WV2-CLASS", program_id=program.id, professor_id=teacher.id, professor_name=teacher.name, status=ClassStatus.ONGOING,
                   start_date=datetime.combine(date.today() - timedelta(days=35), dtime(9, 0)), total_weeks=8, current_week=5,
                   start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat")
db.add(group)
db.flush()
person = Candidate(full_name="Sana Reponse", email="sana.reponse@example.com", phone="50 666 777")
db.add(person)
db.flush()
db.add(Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390, paid_amount=390,
                    payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, registration_date=datetime.utcnow(), class_group_id=group.id))
db.commit()

# ---- A. Better questions, by rules, without the model ----
for number in range(1, 6):
    wq.queue(db, group, number)
while wq.work_one(db):
    pass
written = db.query(WeekQuestion).filter(WeekQuestion.class_group_id == group.id).all()
bank_texts = {item["question"] for item in level_bank.questions_for(program.name)}
check("no course file: the model is never asked from the course plan alone", not asked_model)
check("the hand-made questions of the subject are used", any(item.question in bank_texts for item in written), len(written))
check("never the same hand-made question twice in one class", len([item.question for item in written if item.question in bank_texts])
      == len({item.question for item in written if item.question in bank_texts}))
check("questions in French, each with four choices and a right answer", written and all(len(item.choices) >= 3 and 0 <= item.correct_answer < len(item.choices)
                                                                                         for item in written))
notion = next((item for item in written if item.question.startswith("Laquelle de ces notions")), None)
if notion is not None:
    week_text = " ".join((lambda w: (w["title"], w["objectives"], w["site"]))(next(w for w in chatbot_sources._weeks_of(db, program)[0] if w["number"] == notion.week_number)))
    check("a notion question's right answer is in that week's text", notion.choices[notion.correct_answer] in week_text, notion.choices)
# A vague question the model wrote from the course plan, in a week not sent: replaced by the rules.
db.add(WeekQuestion(class_group_id=group.id, week_number=6, question="What is the main focus of the passage?", options=json.dumps(["A", "B", "C", "D"]),
                    correct_answer=0, material_id=f"plan:{program.id}:6", source_name="The course plan for this week", status="ready", author="system"))
db.commit()
check("the model's vague questions from the plan are replaced in the weeks not sent", wq.replace_thin(db) == 1
      and not db.query(WeekQuestion).filter(WeekQuestion.question == "What is the main focus of the passage?").count())

# ---- B. How often ----
wq.save_settings(db, every=0, way="auto")
delivered.clear()
check("stopped: nothing is sent", wq.send_due(db, "http://127.0.0.1:8000") == 0 and not delivered)
wq.save_settings(db, every=4, way="email")
db.query(ClassWeek).filter(ClassWeek.class_group_id == group.id).update({"sent_at": None})
db.commit()
group.current_week = 5
db.commit()
delivered.clear()
wq.send_due(db, "https://app.example.com")
db.expire_all()
quiz = db.query(Quiz).filter(Quiz.class_group_id == group.id, Quiz.kind == "weekly").order_by(Quiz.sent_at.desc()).first()
check("every 4 weeks: one review of weeks 1 to 4", quiz is not None and quiz.week_number == 4 and "semaines 1 à 4" in quiz.course_name,
      quiz.course_name if quiz else None)
mail = next((item for item in delivered if item["to"] == person.email), {"text": ""})
check("“in the email”: the questions are in it even with a reachable address", "répondez simplement à cet email" in mail["text"]
      and "app.example.com/learn" not in mail["text"], mail["text"][-200:])
wq.save_settings(db, every=1, way="link")
check("the choices are kept", wq.settings_of(db) == {"every": 1, "way": "link"})

# ---- C. Replies read however they are written, adding up ----
count = len(wq.questions_in(quiz))
thread_subject = f"Re: Sana, vos questions de la semaine 4 — {program.name}"
first = wq.handle_reply(db, person.id, thread_subject, "Bonjour 9antra team\nma response est C")
check("“ma response est C”: noted for the first question, the rest asked for", first is not None and not first["done"]
      and "1 → C" in first["text"] and "Il reste" in first["text"], first["text"] if first else None)
rest = ", ".join(f"{index}-A" for index in range(2, count + 1))
second = wq.handle_reply(db, person.id, thread_subject, rest)
db.expire_all()
quiz = db.get(Quiz, quiz.id)
check("the next reply completes it: graded", second is not None and second["done"] and quiz.completed_at is not None and json.loads(quiz.answers)[0] == 2,
      (second["text"][:120] if second else None))
check("not an answer: left to the assistant", wq.handle_reply(db, person.id, thread_subject, "Merci beaucoup pour le cours !") is None)

# ---- D. A reply that went to the admin before is read again ----
wq.save_settings(db, every=1, way="email")
db.query(ClassWeek).filter(ClassWeek.class_group_id == group.id, ClassWeek.week_number == 4).update({"sent_at": None})
db.query(Quiz).filter(Quiz.id == quiz.id).delete()
db.commit()
wq.send_week(db, group, 4, base_url="http://127.0.0.1:8000", manual=True)
fresh = db.query(Quiz).filter(Quiz.class_group_id == group.id, Quiz.kind == "weekly", Quiz.completed_at.is_(None)).first()
letters = " ".join(wq.LETTERS[item["correct_answer"]] for item in wq.questions_in(fresh))
ea.ingest_inbound(db, sender=person.email, subject=thread_subject, body="Bonjour", message_id=f"<hello-{datetime.utcnow().timestamp()}@example.com>")
message = db.query(CandidateEmail).filter(CandidateEmail.candidate_id == person.id, CandidateEmail.direction == "inbound").order_by(CandidateEmail.received_at.desc()).first()
message.body, message.requires_admin, message.replied_at, message.status = f"mes réponses : {letters}", True, None, "needs_admin"
db.commit()
delivered.clear()
check("a reply left to the admin is read again and graded", wq.reread_replies(db) == 1 and db.get(Quiz, fresh.id).completed_at is not None
      and db.get(Quiz, fresh.id).score == 100.0)
check("and the candidate gets their score", any(item["to"] == person.email and "Bravo" in item["text"] for item in delivered))

# ---- D2. A question badly written on our side, already sent: counted as right, with an excuse ----
check("a question whose answer repeats it cannot be marked", wq.badly_written({
    "question": "Pourquoi est-il important de comprendre les objectifs d'un Dashboard Business Intelligence ?",
    "options": ["Pour gagner de l'argent", "Pour améliorer les compétences", "Pour comprendre les données",
                "Comprendre les objectifs d'un Dashboard Business Intelligence"], "correct_answer": 3}))
check("“the main topic” questions neither", wq.badly_written({"question": "What is the main topic of the subject?", "options": ["A", "B", "C"], "correct_answer": 0}))
check("the hand-made and the rules' questions are all fair", not any(wq.badly_written({"question": item.question, "options": item.choices,
                                                                                      "correct_answer": item.correct_answer}) for item in written))
old = Quiz(candidate_id=person.id, registration_id=fresh.registration_id, kind="weekly", class_group_id=group.id, week_number=1,
           course_name=f"{program.name} — semaine 1", sent_at=datetime.utcnow(), answers="[]", questions=json.dumps([{
               "id": "old-1", "question": "Pourquoi est-il important de comprendre les objectifs d'un Dashboard Business Intelligence ?",
               "options": ["Pour gagner de l'argent", "Pour améliorer les compétences", "Pour comprendre les données",
                           "Comprendre les objectifs d'un Dashboard Business Intelligence"], "correct_answer": 3,
               "explanation": "Comprendre les objectifs d'un Dashboard Business Intelligence"}], ensure_ascii=False))
db.add(old)
db.commit()
excused = wq.handle_reply(db, person.id, f"Re: Sana, vos questions de la semaine 1 — {program.name}", "Bonjour 9antra team\nma response est C")
db.expire_all()
check("already sent and answered: counted as right, and the candidate told why", excused is not None and excused["done"]
      and db.get(Quiz, old.id).score == 100.0 and "mal formulée de notre côté" in excused["text"], excused["text"] if excused else None)
db.add(WeekQuestion(class_group_id=group.id, week_number=7, question="What is the purpose of error and exception handling?",
                    options=json.dumps(["Speed", "Security", "Error and exception handling", "Design"]), correct_answer=2,
                    material_id="file:abc", source_name="Course file", status="ready", author="system"))
db.commit()
wq.replace_thin(db)
check("not sent yet: removed and the week written again", not db.query(WeekQuestion).filter(WeekQuestion.question == "What is the purpose of error and exception handling?").count())

# ---- E. The page, like the level checks ----
page = admin.get("/quiz/weekly")
check("the weekly answers page: settings, figures, each candidate's answers", page.status_code == 200 and 'id="wk-how"' in page.text
      and "Sana Reponse" in page.text and "Their answers" in page.text and 'name="every"' in page.text, page.status_code)
check("a badly written question is shown as such", "Badly written on our side" in page.text)
check("next to the level checks", 'href="/quiz/weekly"' in admin.get("/quiz/all").text)
response = admin.post("/quiz/weekly/settings", data={"every": "2", "way": "auto"})
check("the admin saves how often and how", response.status_code == 303 and wq.settings_of(db) == {"every": 2, "way": "auto"})

db.close()
check.done()

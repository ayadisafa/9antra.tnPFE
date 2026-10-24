"""After each session: what was learnt (questions from what the system knows of the course, some hours after the
session, the answers next to the presence); orders in Tunisian to the admin's assistant (reworded in French, sent
at once, only when nothing was lost); sending again what goes by itself once (now or at a chosen time, on the System
page or in words); translations reaching open pages; the look; one in the singular.
Runs on a COPY; email, WhatsApp and SMS are fakes."""
import json
import re
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("session_reset")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.classsession import ClassSession
from app.models.course_material import ClassMaterial, ClassWeek
from app.models.knowledge import KnowledgeEntry
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, chatbot, chatbot_send, completeness, course_materials, email_assistant as ea, email_templates as tp,
                          email_threads as et, enrollment_service, knowledge_service, realtime, reply_writer, resends, session_check,
                          weekly_questions)
from app.services.schedule_utils import local_now
from app.services.settings_service import get_settings

sent = []


def fake_send(**kw):
    sent.append(kw)
    return True


et.send_email = fake_send
tp.send_email = fake_send
et.smtp_configured = lambda: True
ea.model_available = lambda: False
ea.llm_json = lambda *a, **k: None


def where(response):
    return unquote_plus(response.headers.get("location", ""))


def mails_to(address):
    return [item for item in sent if address in " ".join(str(value) for value in item.values())]


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="session-reset-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "session-reset-1"})
boss = auth.CurrentUser(id="a", name="Safa Admin", email="admin@test.local", role="admin")
now = local_now()

# A course with a weekly plan, a class that runs, two candidates, a session that ended yesterday.
program = next(item for item in db.query(Program).order_by(Program.name).all()
               if len([week for week in enrollment_service.curriculum_weeks(db, item.id) if (week.learning_objectives or "").strip()]) >= 6)
weeks = enrollment_service.curriculum_weeks(db, program.id)
group = ClassGroup(name="SC-RUN-1", program_id=program.id, status=ClassStatus.ONGOING, start_date=datetime.combine(now.date() - timedelta(days=21), dtime(9, 0)),
                   total_weeks=len(weeks), start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat", capacity=20, max_capacity=20)
db.add(group)
db.flush()
people = []
for name, email in (("Zied Tounsi", "zied.tounsi@example.com"), ("Rania Sessions", "rania.sessions@example.com")):
    person = Candidate(full_name=name, email=email, phone="98 444 555")
    db.add(person)
    db.flush()
    registration = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=500, total_price=500,
                                paid_amount=500, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=group.id,
                                registration_date=datetime.utcnow())
    db.add(registration)
    people.append((person, registration))
yesterday = now.date() - timedelta(days=1)
session = ClassSession(class_group_id=group.id, week_number=4, session_date=yesterday, start_time=dtime(9, 0), end_time=dtime(12, 0), topic=weeks[3].topic_title)
db.add(session)
db.commit()
zied, zied_registration = people[0]

# ---- A. After each session: what was learnt ----
check("session: on by default, 24 hours after", session_check.settings_of(db) == {"on": True, "hours": 24})
sources = course_materials.sources(db, group.id, 4)
check("expert: the course's own plan for the week is something to learn from, for any course", any(item["key"].startswith("plan:") for item in sources),
      [item["name"] for item in sources])
asked = session_check.plan_questions(db, group, 4, weeks[3].topic_title)
subject = next((item for item in asked if item["id"].endswith(":subject")), None)
check("questions from the plan: “what was this session about?”, its subject among others of the course", subject is not None
      and subject["options"][subject["correct_answer"]] == weeks[3].topic_title and len(set(subject["options"])) == 4, asked[:1])
covered = next((item for item in asked if item["id"].endswith(":covered")), None)
check("questions from the plan: one thing the week covered, among others", covered is not None
      and covered["options"][covered["correct_answer"]] in (weeks[3].learning_objectives or ""), covered)
session_check.save_settings(db, on=True, hours=4)
check("session: the admin chooses the hours (4 here)", session_check.settings_of(db)["hours"] == 4)
old_session = ClassSession(class_group_id=group.id, week_number=3, session_date=now.date() - timedelta(days=5), start_time=dtime(9, 0), end_time=dtime(12, 0), topic="Old")
db.add(old_session)
db.commit()
session_check.plan(db, now)
check("session: switched on, a session of last week is never asked about", not db.query(session_check.SessionCheck).filter_by(class_session_id=old_session.id).count()
      and get_settings(db).session_check_since is not None)
db.query(session_check.SessionCheck).delete()
get_settings(db).session_check_since = now - timedelta(days=2)       # this test's session ended yesterday
db.commit()
session_check.plan(db, now)
check("session: planned when it ends, and the week's questions asked for",
      db.query(session_check.SessionCheck).filter_by(class_session_id=session.id).count() == 1
      and course_materials.get_week(db, group, 4).questions_status == "queued", course_materials.get_week(db, group, 4).questions_status)
row = db.query(session_check.SessionCheck).filter_by(class_session_id=session.id).one()
waiting_moment = row.ends_at + timedelta(hours=5)
session_check.send_due(db, "https://desk.example", now=waiting_moment)
check("session: while the writer works, it waits a little", db.get(session_check.SessionCheck, row.id).status == "waiting"
      and not db.query(Quiz).filter(Quiz.class_session_id == session.id).count())
sent.clear()
session_check.send_due(db, "https://desk.example", now=row.ends_at + timedelta(hours=11))
db.expire_all()
quizzes = db.query(Quiz).filter(Quiz.kind == "session", Quiz.class_session_id == session.id).all()
count = sum(1 for item in quizzes if item.sent_at)
check("session: each candidate gets their questions (from the plan when nothing else is ready)", count == 2 and len(quizzes) == 2
      and all(len(weekly_questions.questions_in(quiz)) >= 2 for quiz in quizzes), (count, len(quizzes)))
mail = mails_to("zied.tounsi@example.com")
check("session: by its own message, with the private link", mail and "https://desk.example/learn/" in json.dumps(mail[-1], ensure_ascii=False)
      and "qu'avez-vous retenu de la séance" in json.dumps(mail[-1], ensure_ascii=False), [item.get("subject") for item in sent])
session_check.send_due(db, "https://desk.example", now=row.ends_at + timedelta(hours=12))
check("session: never twice for one session", db.query(Quiz).filter(Quiz.class_session_id == session.id).count() == 2)
quiz = next(item for item in quizzes if item.registration_id == zied_registration.id)
form = {f"q{index}": str(item["correct_answer"]) for index, item in enumerate(weekly_questions.questions_in(quiz))}
response = TestClient(main.app, follow_redirects=False).post(f"/learn/{quiz.access_token}", data=form)
db.expire_all()
proof = session_check.evidence(db, group.id).get((session.id, zied_registration.id))
check("presence: their answers count for that session", response.status_code == 303 and proof and proof["answered"] and proof["right"] == proof["total"], proof)
page = admin.get(f"/classes/{group.id}/attendance?session={session.id}").text
check("presence sheet: who answered after the session, and how well", "Answered after the session" in page and "Questions sent · not answered yet" in page
      and "asked what they learnt, 1 answered" in page)
check("presence sheet: it follows live changes", 'data-live-topics="class:' + group.id in page)
summary = session_check.session_summary(db, session.id)
check("class: asked, answered, the average", summary["asked"] == 2 and summary["answered"] == 1 and summary["average"] == 100, summary)
session_check.save_settings(db, on=False, hours=4)
later = ClassSession(class_group_id=group.id, week_number=4, session_date=now.date() - timedelta(days=1), start_time=dtime(14, 0), end_time=dtime(15, 0), topic="Extra")
db.add(later)
db.commit()
session_check.send_due(db, "https://desk.example", now=now + timedelta(days=1))
check("session: switched off, nothing goes", not db.query(Quiz).filter(Quiz.class_session_id == later.id).count())
session_check.save_settings(db, on=True, hours=24)
page = admin.get("/settings").text
check("settings: on or off, and the hours, under Classes", 'id="session-check"' in page and 'name="session_check_hours"' in page)
check("everything it does by itself: listed, with “send again”", "After each session: what was learnt" in admin.get("/system").text)
db.add(ClassMaterial(class_group_id=group.id, week_number=4, filename="docker-volumes.pdf", stored_name="x", content_type="application/pdf", size=10,
                     kind="document", learned_status="read",
                     learned_text="Les volumes Docker gardent les données quand le conteneur est supprimé. Un volume nommé se crée avec docker volume create. " * 3))
db.commit()
knowledge_service.sync_knowledge(db)
check("expert: the course files of a week become what the assistant knows", db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key.like("course:%")).count() >= 1)

# ---- B. Orders in Tunisian ----
check("tunisian order: “ab3athli message lfares kolo ija”", chatbot_send.from_tunisian("ab3athli message lfares kolo ija")
      == "envoie un message à fares pour lui dire que ija")
check("tunisian order: the way and several people", chatbot_send.from_tunisian("3aychek ab3athli mail l nour w l amira kolhom el cours yebda 9h")
      == "envoie un email à nour et amira pour lui dire que el cours yebda 9h")
check("tunisian order: not an order", chatbot_send.from_tunisian("chnowa wa9t tebda el formation ?") is None)
seen = {}


def reworded(text, **kw):
    seen["text"] = text
    return {"reply": "Bonjour Zied,\n\nVenez au centre demain, s'il vous plaît.\n\nNous restons étonnés à votre disposition.", "warning": None}


reply_writer.reformulate = reworded
get_settings(db).assistant_auto_send = True
db.commit()
sent.clear()
answer = chatbot.answer(db, "ab3athli mail l zied tounsi kolo ija ghodwa", [], user=boss)
db.expire_all()
out = (db.query(CandidateEmail).filter(CandidateEmail.candidate_id == zied.id, CandidateEmail.direction == "outbound")
       .order_by(CandidateEmail.received_at.desc()).first())
check("tunisian order: read in French for the rewording", seen.get("text") == "venez demain", seen)
check("tunisian order: sent at once, answered in French", "envoyé" in answer["reply"] and mails_to("zied.tounsi@example.com"), answer["reply"][:160])
check("tunisian order: only what carries the order goes (the model's own sentence is left out)", out is not None and "Venez au centre demain" in out.body
      and "étonnés" not in out.body, out.body[:200] if out else None)
reply_writer.reformulate = lambda text, **kw: {"reply": "Bonjour Zied,\n\nVenez.", "warning": None}
sent.clear()
answer = chatbot.answer(db, "ab3athli mail l zied tounsi kolo ija ghodwa", [], user=boss)
check("tunisian order: a rewording that lost a word is not sent", not mails_to("zied.tounsi@example.com") and ("demai" in answer["reply"] or "deman" in answer["reply"]),
      answer["reply"][:200])

# ---- C. Sending again ----
moment = datetime(2026, 10, 24, 10, 0)
check("when: now / tawa", resends.when_from("renvoie maintenant", moment) == moment and resends.when_from("3awed tawa", moment) == moment)
check("when: tomorrow at 9", resends.when_from("demain à 9h", moment) == datetime(2026, 10, 25, 9, 0)
      and resends.when_from("ghodwa 14:30", moment) == datetime(2026, 10, 25, 14, 30))
check("when: an hour already past today means tomorrow", resends.when_from("à 8h", moment) == datetime(2026, 10, 25, 8, 0)
      and resends.when_from("à 18h", moment) == datetime(2026, 10, 24, 18, 0))
check("when: a date, in hours", resends.when_from("le 30/10 à 14h", moment) == datetime(2026, 10, 30, 14, 0)
      and resends.when_from("dans 2 heures", moment) == datetime(2026, 10, 24, 12, 0))
check("when: no moment said", resends.when_from("renvoie le message", moment) is None)
sent.clear()
response = admin.post("/system/resend", data={"kind": "welcome", "candidate_id": zied.id, "when": "now"})
check("again now (System page): it goes, and the admin is told what happened", "goes again to Zied Tounsi now" in where(response)
      and mails_to("zied.tounsi@example.com"), where(response))
later_moment = (local_now() + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
response = admin.post("/system/resend", data={"kind": "payment", "candidate_id": zied.id, "when": "later", "at": later_moment.strftime("%Y-%m-%dT%H:%M")})
planned = resends.planned(db)
check("again later: it says when it will go", "Reset: “Payment reminder” will go again to Zied Tounsi on" in where(response) and "09:00" in where(response)
      and len(planned) == 1, where(response))
check("again later: nothing before its time", resends.run_due(db, "https://desk.example") == 0)
page = admin.get("/system").text
check("system page: the card, what is planned, “send again” on each automatic thing", 'id="resend"' in page and "Planned to go again" in page
      and "?again=welcome#resend" in page)
response = admin.post(f"/system/resend/{planned[0].id}/cancel")
check("again later: it can be cancelled", "Cancelled" in where(response) and not resends.planned(db))
check("again: a moment already past is refused", "That moment has passed" in where(admin.post("/system/resend", data={
    "kind": "welcome", "candidate_id": zied.id, "when": "later", "at": "2020-01-01T09:00"})))
answer = chatbot.answer(db, "renvoie le message de bienvenue à Zied Tounsi demain à 9h", [], user=boss)
check("assistant: “renvoie … demain à 9h”: reset, and when it goes", answer["reply"].startswith("C'est réinitialisé : « Message de bienvenue » repartira à Zied Tounsi")
      and "09:00" in answer["reply"], answer["reply"])
answer = chatbot.answer(db, "3awed ab3ath el rappel mta3 el khlas l zied tounsi ghodwa", [], user=boss)
check("assistant: in Tunisian too", "Rappel de paiement" in answer["reply"] and "repartira" in answer["reply"], answer["reply"])
answer = chatbot.answer(db, "renvoie le test de niveau à Zied Tounsi", [], user=boss)
check("assistant: no time said, it asks when", answer["reply"].startswith("Quand voulez-vous qu'il reparte"), answer["reply"])
answer = chatbot.answer(db, "après-demain à 10h", [{"role": "user", "content": "renvoie le test de niveau à Zied Tounsi"},
                                                  {"role": "assistant", "content": answer["reply"]}], user=boss)
check("assistant: the time given next, it plans it", "Test de niveau" in answer["reply"] and "10:00" in answer["reply"], answer["reply"])
check("assistant: three planned now", len(resends.planned(db)) == 3)
staff = auth.CurrentUser(id="s", name="S", email="s", role="staff", permissions={"assistant": "view"})
before = len(resends.planned(db))
chatbot.answer(db, "renvoie le message de bienvenue à Zied Tounsi demain à 9h", [], user=staff)
check("assistant: someone who may only look cannot reset", len(resends.planned(db)) == before)

# ---- D. Live, the look, the singular ----
check("live: a saved translation reaches open pages", realtime._topics_for(type("UiTranslation", (), {})()) == {"i18n"})
admin.cookies.set("lang", "fr")
check("live: the translator takes new translations at once", 'topics.indexOf("i18n")' in admin.get("/").text)
admin.cookies.set("lang", "en")
page = admin.get("/").text
check("live: the day at a glance follows changes", 'id="live-dashboard-glance" data-live-region' in page)
check("look: page heads as bands, notices that leave by themselves", ".ui-head::after" in page and "page-flash" in page)
lines = completeness.items(db)
check("one in the singular: “1 … has”, never “1 … have”", not any(re.match(r"1 \w+ have\b", item["text"]) for item in lines), [item["text"] for item in lines][:6])

db.close()
check.done()

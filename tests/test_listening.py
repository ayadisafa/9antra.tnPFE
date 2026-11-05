"""The batch of 3 November (2): the email assistant reads every request of a message (nothing missed) and asks the
agent of the candidate's program; the class page says who takes part and how the professor follows the class; the
Social media page shows one part at a time with its picture, and marketing is soft. Runs on a COPY; no mail leaves."""
import json
from datetime import datetime, timedelta, time as dtime

import testenv

testenv.prepare("listening")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Professor, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.classsession import ClassSession
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.session_join import SessionJoin
from app.models.team_message import TeamMessage
from app.models.user import Attendance
from app.services import (auth, class_dynamics, email_assistant as ea, email_templates as tp, email_threads as et, knowledge_service,
                          program_agents, whats_new)
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
auth.create_user(db, email="admin@test.local", name="Leila Admin", password="listening-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "listening-1"})


def fresh(message_id):
    db.expire_all()
    return db.query(CandidateEmail).filter(CandidateEmail.message_id == message_id).one()


# ======================================================================
# A. Every request of a message is read
# ======================================================================
three = ea.requests_of("Bonjour, quel est le prix de la formation Python ? Et quand commence la prochaine session ? "
                       "Aussi, est-ce qu'il y a un certificat ? Merci")
check("three requests, in their order", three == ["quel est le prix de la formation Python ?", "Et quand commence la prochaine session ?",
                                                  "Aussi, est-ce qu'il y a un certificat ?"], three)
check("…in English and in Tunisian too", len(ea.requests_of("Hello, I would like to know the duration. Can you send me the address of the centre?")) == 2
      and len(ea.requests_of("9adech soum el formation w wa9tech tabda?")) == 1)
check("a thank-you asks nothing", ea.requests_of("Merci beaucoup pour votre aide, à bientôt.") == [])
missed = ea.missed_requests(three, "Python : 390 DT en ligne. La prochaine session commence le 12/11.")
check("an answer that leaves the certificate out is caught; a price in DT and a date answer the rest",
      [item["subjects"] for item in missed] == [["certificates"]], missed)
check("“how much do I still owe” is answered by an amount", ea.missed_requests(ea.requests_of("Combien je dois encore payer ?"), "Il vous reste 200 DT.") == [])
check("“when” or “combien” inside a sentence is not a subject of its own", ea.subjects_of("is there somewhere to park my car when I come to class?") == set()
      and ea.subjects_of("Combien de semaines dure la formation ?") == {"_duration"} and ea.subjects_of("Combien ça coûte ?") == {"_price"})
check("the analysis carries the requests", ea.analyse("Questions", "Quel est le prix ? Et la durée ?")["requests"] == ["Quel est le prix ?", "Et la durée ?"])

# ======================================================================
# B. The agent of the candidate's program answers; nothing missed before sending
# ======================================================================
program = db.query(Program).first()
professor = Professor(name="Nadia Prof", email="nadia.prof@example.com", specialties=program.name)
db.add(professor)
db.flush()
group = ClassGroup(name="LISTEN-1", program_id=program.id, professor_id=professor.id, professor_name=professor.name, status=ClassStatus.IN_PROGRESS,
                   start_date=(local_now() - timedelta(days=21)).replace(hour=18, minute=0, second=0, microsecond=0), total_weeks=8,
                   start_time=dtime(18, 0), end_time=dtime(21, 0), days_of_week="mon", start_mode="fixed_date", capacity=12, max_capacity=12)
db.add(group)
db.flush()


def member(name, email):
    person = Candidate(full_name=name, email=email, phone="50 333 444")
    db.add(person)
    db.flush()
    registration = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, class_group_id=group.id, delivery_mode="online",
                                total_amount=390, total_price=390, paid_amount=390, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED)
    db.add(registration)
    db.commit()
    return person, registration


hiba, hiba_reg = member("Hiba Sassi", "hiba.listen@example.com")
ines, ines_reg = member("Ines Mejri", "ines.listen@example.com")
omar, omar_reg = member("Omar Feki", "omar.listen@example.com")
knowledge_service.save_fact(db, text="Le centre prête un ordinateur portable à chaque candidat pendant les séances de la formation.", program_id=program.id)
db.commit()
check("the program the candidate follows is found even when the message does not name it",
      program_agents.find(db, "Est-ce que vous prêtez un ordinateur portable ?", hiba).id == program.id)
ea.ingest_inbound(db, sender=hiba.email, subject="Ordinateur", body="Bonjour, est-ce que le centre prête un ordinateur portable pendant les séances ?",
                  message_id="<listen-1@example.com>")
asked = fresh("<listen-1@example.com>")
used = json.loads(asked.sources or "{}")
check("the agent of her program answers, from what the admin told it", used.get("method") == "program_agent" and used.get("agent") == program_agents.key_of(program)
      and asked.status == "replied", (asked.status, used.get("method"), asked.analysis))
check("…and the answer says it", any("ordinateur portable" in (item.get("text_body") or "") for item in sent[-2:]))
ea.ingest_inbound(db, sender=ines.email, subject="Questions",
                  body="Bonjour, est-ce que le centre prête un ordinateur portable pendant les séances ? Et est-ce qu'on aura un certificat à la fin ?",
                  message_id="<listen-2@example.com>")
both = fresh("<listen-2@example.com>")
used = json.loads(both.sources or "{}")
check("a request left out keeps the answer for you, saying which", both.status == "needs_admin" and "certificat" in (both.escalation_reason or "")
      and [item["answered"] for item in used.get("requests", [])] == [True, False], (both.status, both.escalation_reason, used.get("requests")))
from app.models.agent_run import AgentRun  # noqa: E402

check("the agents page counts it for that program's agent", db.query(AgentRun).filter(AgentRun.agent == program_agents.key_of(program),
                                                                                       AgentRun.channel == "email").count() >= 1)

# ======================================================================
# C. Who takes part, who arrives late, and how the professor follows the class
# ======================================================================
now = local_now()
sessions = []
for days in (14, 7):
    session = ClassSession(class_group_id=group.id, week_number=days // 7, session_date=(now - timedelta(days=days)).date(),
                           start_time=dtime(18, 0), end_time=dtime(21, 0), topic=f"Séance {days}", meeting_url="https://meet.jit.si/listen-room")
    db.add(session)
    db.flush()
    sessions.append(session)
first, second = sessions
for session, marks in ((first, {hiba_reg: ("present", 3, 4), ines_reg: ("late", 2, 1), omar_reg: ("present", 1, 0)}),
                       (second, {hiba_reg: ("present", 3, 2), ines_reg: ("late", 2, 0), omar_reg: ("absent", None, None)})):
    for registration, (status, part, questions) in marks.items():
        db.add(Attendance(class_group_id=group.id, session_id=session.id, registration_id=registration.id, candidate_id=registration.candidate_id,
                          status=status, participation=part, questions=questions, marked_by="Nadia Prof",
                          marked_at=datetime.utcnow() - timedelta(days=(14 if session is first else 7)) + timedelta(hours=2)))
    late_one = datetime.combine(session.session_date, dtime(18, 25))
    db.add(SessionJoin(session_id=session.id, registration_id=ines_reg.id, candidate_id=ines.id, token=f"listen-{session.id}",
                       sent_at=datetime.utcnow() - timedelta(days=15), first_joined_at=late_one, joins=1, minutes_late=25))
db.add(TeamMessage(person=f"professor:{professor.id}", direction="out", channel="app", body="Question de Hiba", status="sent",
                   question_candidate_id=hiba.id, created_at=datetime.utcnow() - timedelta(hours=10), answered_at=datetime.utcnow() - timedelta(hours=7)))
db.commit()
dynamics = class_dynamics.of_class(db, group)
check("who takes part, most first", [person["name"] for person in dynamics["talking"]][:2] == ["Hiba Sassi", "Ines Mejri"],
      [person["name"] for person in dynamics["talking"]])
check("who arrives late, from their own links", any(item["name"] == "Ines Mejri" and "late 2 times" in item["why"] for item in dynamics["to_watch"]), dynamics["to_watch"])
prof = dynamics["professor"]
check("the professor: sheets confirmed, within a day", prof["sheets_confirmed"] == 2 and prof["sheets_in_time"] == 2 and prof["sessions_ended"] >= 2, prof)
check("…candidates' questions answered, and how fast", prof["questions"] == 1 and prof["answered"] == 1 and prof["answer_hours"] == 3.0, prof)
page = admin.get(f"/classes/{group.id}").text
check("the class page shows who takes part and the professor", "Who takes part" in page and "The professor" in page and "presence sheets confirmed" in page)

# ======================================================================
# D. Social media: one part at a time, a picture each; marketing is soft
# ======================================================================
page = admin.get("/social").text
check("four tabs, posts first", page.count('data-tab="') == 4 and page.index('data-tab="create"') < page.index('data-tab="ideas"'))
check("each part says itself in a picture and three steps", page.count('class="so-flow"') == 3 and page.count('<svg class="art"') >= 4)
check("trends, best times and the plan live together in one soft tab", page.count('data-panel="ideas"') == 4
      and "A little help for your posts" in page)
check("old links still land on their part", 'id="plan"' in page and 'id="trends"' in page and 'id="timing"' in page and "parts = { plan:" in page)
home = admin.get("/").text
check("dashboard: marketing is a soft fold, closed", '<details class="db-card db-market" id="dashboard-marketing"' in home
      and '<details class="db-card db-market" id="dashboard-marketing" open' not in home
      and "posts planned" in home)
check("what's new: this batch is told", any(release["key"] == "2026-11-03c" for release in whats_new.RELEASES))

db.close()
check.done()

"""The dashboard's two lists with what candidates said, the table “who can do what” (one click per cell) and removing an
account, a professor never in two classes at once, one program at a time (the next one starts some days after the
first ends, with a professor free then; the candidate and the assistant say so), and professors told of a new class.
Runs on a COPY; email, WhatsApp and SMS are fakes."""
import inspect
import json
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("desk_rules")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Professor, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.feedback import CandidateFeedback
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.team_message import TeamMessage
from app.scheduler import jobs
from app.services import (access, auth, email_assistant as ea, email_templates as tp, email_threads as et, enrollment_service as es, overview,
                          payment_service, professor_announcements as pa, program_feedback)
from app.services.schedule_utils import class_end_dt
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


def mail_text(item):
    return " ".join(str(value) for value in item.values())


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="desk-rules-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "desk-rules-1"})
boss = db.query(User).filter(User.email == "admin@test.local").one()
today = date.today()

# ---- A. The dashboard's lists ----
devops = db.query(Program).filter(Program.name == "DevOps").first()
other = db.query(Program).filter(Program.id != devops.id, Program.is_approved.is_(True)).order_by(Program.name).first()


def candidate(name, email, program, paid=0.0, total=1000.0):
    person = Candidate(full_name=name, email=email, phone="98 000 111")
    db.add(person)
    db.flush()
    registration = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=total,
                                total_price=total, paid_amount=paid, payment_status=PaymentStatus.PAID if paid >= total else
                                (PaymentStatus.PARTIAL if paid else PaymentStatus.UNPAID),
                                status=RegistrationStatus.PENDING, registration_date=datetime.utcnow())
    db.add(registration)
    db.flush()
    return person, registration


rated, _ = candidate("Amal Rated", "amal.rated@example.com", devops, paid=400)
felt, _ = candidate("Badr Felt", "badr.felt@example.com", devops)
quiet, _ = candidate("Chiraz Quiet", "chiraz.quiet@example.com", other)
db.add(CandidateFeedback(candidate_id=rated.id, source=program_feedback.SOURCE, status="received", rating=4, comment="Très bien expliqué",
                         program_name=devops.name, submitted_at=datetime.utcnow()))
db.add(CandidateEmail(candidate_id=felt.id, direction="inbound", sender=felt.email, recipient="centre@example.com", subject="Merci", body="Merci, j'adore !",
                      status="received", received_at=datetime.utcnow(),
                      emotions=json.dumps({"emotions": [{"key": "joy", "label": "Happy", "words": ["adore"]}]})))
db.commit()
check("feedback: their review first, with its stars", overview.candidate_feedback(db, rated) == {"kind": "review", "stars": 4, "words": "Très bien expliqué"},
      overview.candidate_feedback(db, rated))
said = overview.candidate_feedback(db, felt)
check("feedback: else what their messages showed", said and said["kind"] == "feeling" and said["key"] == "joy", said)
check("feedback: nothing when nothing was said", overview.candidate_feedback(db, quiet) is None)
newest = {item["name"]: item for item in overview.newest_registrations(db, limit=10)}
check("newest: each with what they paid of their total", newest["Amal Rated"]["total"] == 1000 and newest["Amal Rated"]["paid_pct"] == 40,
      {key: (value["total"], value["paid_pct"]) for key, value in newest.items()})
check("newest: and what they said", newest["Amal Rated"]["feedback"]["kind"] == "review" and newest["Chiraz Quiet"]["feedback"] is None)
popular = overview.popular_programs(db)
check("most chosen: each with its share, paid, running and feedback", popular and popular[0]["share"] == 100
      and all({"stars", "answered", "happy_pct", "recommend_pct", "asked"} <= set(item["feedback"]) for item in popular), popular[:1])
page = admin.get("/")
check("dashboard: opens", page.status_code == 200, page.status_code)
page = page.text
check("dashboard: both lists side by side, each row with its feedback", 'class="pp-time"' in page and 'class="pp-race"' in page
      and page.count('class="pp-feel"') == page.count('class="pp-person"') + page.count('class="pp-prog"') and page.count('class="pp-person"') > 0)
check("dashboard: a review, a feeling, and “No feedback yet”", "pp-stars" in page and "From their messages" in page and "No feedback yet" in page)
check("dashboard: the rows come in one after the other", "--i:0" in page and "--i:1" in page and "prefers-reduced-motion" in page)

# ---- B. Who can do what ----
auth.create_user(db, email="lea.staff@test.local", name="Lea Staff", password="lea-staff-12", role=auth.STAFF)
auth.create_user(db, email="sam.team@test.local", name="Sam Team", password="sam-team-12", role=auth.STAFF)
lea = db.query(User).filter(User.email == "lea.staff@test.local").one()
sam = db.query(User).filter(User.email == "sam.team@test.local").one()
lea_id = lea.id
lea.permissions = access.dump({"candidates": "view"})
sam.permissions = access.dump({"candidates": "edit", "team": "edit"})
db.commit()
page = admin.get("/settings/team").text
check("matrix: the table, every person and every part", 'id="matrix"' in page and "Lea Staff" in page and "Sam Team" in page
      and all(f'data-part="{key}"' in page for key, *_ in access.SECTIONS))
response = admin.post(f"/settings/team/{lea.id}/cell", data={"part": "candidates", "level": "edit"})
db.expire_all()
check("matrix: one click changes one cell, saved at once", response.json() == {"ok": True, "level": "edit"}
      and access.clean(db.get(User, lea.id).permissions)["candidates"] == "edit", response.text)
admin.post(f"/settings/team/{lea.id}/cell", data={"part": "payments", "level": "view"})
response = admin.post(f"/settings/team/{lea.id}/cell", data={"part": "team", "level": "edit"})
db.expire_all()
rights = access.clean(db.get(User, lea.id).permissions)
check("matrix: a part to look at, and a special right", rights["payments"] == "view" and rights["team"] == "edit" and response.json()["ok"], rights)
admin.post(f"/settings/team/{lea.id}/cell", data={"part": "team", "level": "none"})
check("matrix: an unknown part is refused", admin.post(f"/settings/team/{lea.id}/cell", data={"part": "everything", "level": "edit"}).status_code == 400)
check("matrix: an admin's row is not chosen part by part", admin.post(f"/settings/team/{boss.id}/cell", data={"part": "payments", "level": "none"}).status_code == 400)
helper = TestClient(main.app, follow_redirects=False)
helper.post("/login", data={"email": "sam.team@test.local", "password": "sam-team-12"})
response = helper.post(f"/settings/team/{sam.id}/cell", data={"part": "payments", "level": "edit"})
db.expire_all()
check("matrix: nobody changes their own access", response.status_code == 403 and access.clean(db.get(User, sam.id).permissions).get("payments", "none") == "none",
      response.text[:120])
check("matrix: someone allowed to manage the team changes the others", helper.post(f"/settings/team/{lea.id}/cell", data={"part": "classes", "level": "view"}).json()["ok"])
check("remove: someone who manages the team never removes an admin", helper.post(f"/settings/team/{boss.id}/delete").status_code == 403
      and db.query(User).filter(User.id == boss.id).count() == 1)
response = admin.post(f"/settings/team/{boss.id}/delete")
check("remove: never one's own account", "cannot remove your own account" in where(response), where(response))
gone = TestClient(main.app, follow_redirects=False)
gone.post("/login", data={"email": "lea.staff@test.local", "password": "lea-staff-12"})
check("remove: (Lea is signed in)", gone.get("/candidate/all").status_code == 200)
response = admin.post(f"/settings/team/{lea_id}/delete")
db.expire_all()
check("remove: the account is gone, with its sign-ins", "can no longer sign in" in where(response) and db.query(User).filter(User.id == lea_id).count() == 0
      and db.query(UserSession).filter(UserSession.user_id == lea_id).count() == 0, where(response))
check("remove: they are out at once", gone.get("/candidate/all", headers={"accept": "text/html"}).status_code in (303, 401, 403))
again = TestClient(main.app, follow_redirects=False)
again.post("/login", data={"email": "lea.staff@test.local", "password": "lea-staff-12"})
check("remove: and cannot sign in again", again.get("/candidate/all", headers={"accept": "text/html"}).status_code in (303, 401, 403))

# ---- C. A professor is never in two classes at once ----
walid = Professor(name="Walid Prof", specialties=other.name, email="walid.prof@example.com", phone="22 111 222")
amel = Professor(name="Amel Prof", specialties=other.name, email="amel.prof@example.com", phone="22 333 444")
db.add_all([walid, amel])
db.commit()
auth.create_user(db, email="walid.login@example.com", name="Walid Prof", password="walid-prof-12", role=auth.PROFESSOR, professor_id=walid.id)
first_day = today + timedelta(days=30)
evening = es.create_class(db, other, professor=walid, start_date=first_day, total_weeks=6, days_of_week="mon,wed", start_time=dtime(18, 0), end_time=dtime(20, 0))
db.commit()
try:
    es.create_class(db, other, professor=walid, start_date=first_day + timedelta(days=7), total_weeks=6, days_of_week="wed",
                    start_time=dtime(19, 0), end_time=dtime(21, 0))
    clash = ""
except ValueError as err:
    clash = str(err)
    db.rollback()
check("clash: the same days and hours in the same weeks are refused, with whom", "Walid Prof already teaches" in clash and evening.name in clash, clash)
morning = es.create_class(db, other, professor=walid, start_date=first_day, total_weeks=6, days_of_week="wed", start_time=dtime(9, 0), end_time=dtime(11, 0))
db.commit()
check("clash: other hours are fine", morning.professor_id == walid.id)
later = es.create_class(db, other, professor=walid, start_date=first_day + timedelta(weeks=8), total_weeks=4, days_of_week="mon",
                        start_time=dtime(18, 0), end_time=dtime(20, 0))
db.commit()
check("clash: the same hours once the first class is over are fine", later.professor_id == walid.id)
response = admin.post(f"/classes/{morning.id}/parameters", data={
    "professor_id": walid.id, "capacity": "20", "total_weeks": "6", "start_mode": es.START_FIXED_DATE, "fixed_start_date": first_day.isoformat(),
    "days": ["wed"], "start_time": "18:30", "end_time": "19:30", "reminder_days": "7,3,1"})
db.expire_all()
check("clash: changing a class's hours onto another of theirs is refused too", "already teaches" in where(response)
      and db.get(ClassGroup, morning.id).start_time == dtime(9, 0), where(response))

# ---- D. The professor is told ----
waiting = db.query(pa.ProfessorAnnouncement).filter(pa.ProfessorAnnouncement.professor_id == walid.id, pa.ProfessorAnnouncement.sent_at.is_(None)).all()
check("told: each class given to a professor leaves one line to send", {row.class_group_id for row in waiting} == {evening.id, morning.id, later.id},
      len(waiting))
count = pa.send_due(db, "https://desk.example")
db.expire_all()
said = db.query(TeamMessage).filter(TeamMessage.person == f"professor:{walid.id}").all()
check("told: sent, with the class, its hours and what to prepare", count >= 3 and any(
    f"Vous êtes désigné(e) pour enseigner la classe {evening.name}" in row.body and "ajoutez les ressources des premières semaines" in row.body
    and f"https://desk.example/classes/{evening.id}/course" in row.body for row in said), (count, [row.body[:90] for row in said][:2]))
check("told: in the application (they have an account), by email and on WhatsApp", {row.channel for row in said} >= {"app", "email", "whatsapp"}, {row.channel for row in said})
check("told: never twice", pa.send_due(db, "https://desk.example") == 0)
group = db.get(ClassGroup, later.id)
group.professor_id, group.professor_name = amel.id, amel.name
db.commit()
pa.send_due(db, "https://desk.example")
check("told: a new professor for a class is told in turn", db.query(TeamMessage).filter(TeamMessage.person == f"professor:{amel.id}",
                                                                                         TeamMessage.body.like(f"%{later.name}%")).count() >= 1)
done = ClassGroup(name="TOLD-DONE", program_id=other.id, status=ClassStatus.COMPLETED, professor_id=amel.id, professor_name=amel.name,
                  start_date=datetime.now() - timedelta(days=90), total_weeks=4)
db.add(done)
db.commit()
check("told: not for a finished class", db.query(pa.ProfessorAnnouncement).filter(pa.ProfessorAnnouncement.class_group_id == done.id).count() == 0)
check("told: the reminders job sends them", "professor_announcements.send_due" in inspect.getsource(jobs))

# ---- E. One program at a time ----
response = admin.post("/settings/classes", data={
    "auto_create_class_on_payment": "1", "auto_assign_professor": "1", "class_start_mode": es.START_AFTER_DAYS, "default_class_start_days": "10",
    "min_students_to_start": "1", "min_start_notice_days": "3", "default_class_capacity": "20", "default_program_weeks": "8",
    "days": ["sat"], "default_session_start_time": "09:00", "default_session_end_time": "12:00", "training_reminder_days": "7,3,1",
    "enable_automated_reminders": "1", "next_class_gap_days": "14"})
db.expire_all()
check("gap: set in Settings, Classes", get_settings(db).next_class_gap_days == 14 and es.gap_days(get_settings(db)) == 14, where(response))
check("gap: its field on the page", 'id="next-program"' in admin.get("/settings").text)
running = ClassGroup(name="ONE-RUNNING", program_id=devops.id, status=ClassStatus.ONGOING, start_date=datetime.combine(today - timedelta(days=40), dtime(9, 0)),
                     total_weeks=8, start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat")
db.add(running)
db.flush()
nour, first = candidate("Nour Busy", "nour.busy@example.com", devops, paid=1000)
first.class_group_id, first.status = running.id, RegistrationStatus.ASSIGNED
second = Registration(candidate_id=nour.id, program_id=other.id, program_name=other.name, delivery_mode="online", total_amount=800, total_price=800,
                      paid_amount=0, payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow())
db.add(second)
soon = ClassGroup(name="ONE-TOO-SOON", program_id=other.id, status=ClassStatus.NOT_STARTED, start_date=datetime.combine(today + timedelta(days=5), dtime(14, 0)),
                  total_weeks=8, start_time=dtime(14, 0), end_time=dtime(16, 0), days_of_week="sun", capacity=20, max_capacity=20,
                  start_mode=es.START_FIXED_DATE)
db.add(soon)
db.commit()
sent.clear()
payment_service.confirm_payment(db, second.id, 800)
db.expire_all()
second = db.get(Registration, second.id)
check("one program: paid while in a class (other hours): not placed yet", second.class_group_id is None, second.class_group_id)
note = db.query(Notification).filter(Notification.registration_id == second.id, Notification.category == "ASSIGNMENT_PENDING").all()
check("one program: the admin is told, with the gap", len(note) == 1 and "One program at a time" in note[0].message and "14 days" in note[0].message,
      [n.message for n in note])
asked = [item for item in sent if "nour.busy@example.com" in mail_text(item) and "environ 14 jours" in mail_text(item)]
check("one program: the candidate is told by email, with the gap", len(asked) == 1 and "Cela vous convient-il" in mail_text(asked[0])
      and "au même horaire" not in mail_text(asked[0]), [mail_text(item)[:160] for item in sent])
out = ea.ingest_inbound(db, sender=nour.email, subject="Ma classe", body=f"Bonjour, quand commence ma classe de {other.name} ?",
                        message_id=f"<busy-{datetime.utcnow().timestamp()}@example.com>")
db.expire_all()
inbound = db.get(CandidateEmail, out["email_id"])
reply = (db.query(CandidateEmail).filter(CandidateEmail.thread_id == inbound.thread_id, CandidateEmail.direction == "outbound")
         .order_by(CandidateEmail.received_at.desc()).first())
check("one program: asked when their class starts, the assistant says they cannot be placed yet, and when they will be",
      reply is not None and "vous ne pouvez pas encore être placé(e) dans une classe" in reply.body and "14 jours" in reply.body,
      (inbound.status, inbound.escalation_reason, reply.body[:300] if reply else None))
answer = admin.post(f"/classes/registration/{second.id}/decision", data={"decision": "auto"})
check("one program: “place automatically” says why", "one program at a time" in where(answer), where(answer))
ends = class_end_dt(db.get(ClassGroup, running.id))
es.complete_class(db, db.get(ClassGroup, running.id))
db.commit()
es.assign_pending_paid_registrations(db)
db.expire_all()
second = db.get(Registration, second.id)
placed = second.class_group
starts = placed.start_date.date() if placed and placed.start_date else None
check("one program: once the first is over, placed in a class of the new program", placed is not None and placed.program_id == other.id
      and second.status == RegistrationStatus.ASSIGNED, second.status)
check("one program: not the class that starts too soon", placed is not None and placed.id != soon.id, placed.name if placed else None)
check("one program: it starts some days after the first one ended", starts is not None and starts >= max(ends.date() + timedelta(days=14), today),
      (starts, ends))
check("one program: with a professor free then", placed is not None and placed.professor_id is not None
      and es.professor_clash(db, placed.professor_id, es.parse_days(placed.days_of_week), placed.start_time, placed.end_time,
                             placed.start_date.date(), placed.total_weeks, exclude_id=placed.id) is None)

db.close()
check.done()

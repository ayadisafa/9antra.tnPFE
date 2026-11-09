"""A class waiting for a professor takes, by itself, the first one who teaches its program and is free on its start day
(no class at its hours, not away that day); that professor is told in the application, by email, WhatsApp and SMS.
And the admin's reminders: every day, before each of their sessions (with the session's day, hour and topic), and
started again with "Reset". Runs on a COPY; sending is faked."""
import json
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("professor_takes")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Professor, Program, Registration, User, UserSession
from app.models.admin_reminder import AdminReminder
from app.models.classsession import ClassSession
from app.models.professor_absence import ProfessorAbsence
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.team_message import TeamMessage
from app.services import auth, email_composer, email_threads as et, enrollment_service as es, reminders
from app.services.schedule_utils import local_now
from app.services.settings_service import get_settings

sent = []
et._attempt = lambda recipient, subject, text, *rest, **kw: (sent.append((recipient, subject, text)) or (None, False))
et.smtp_configured = lambda: True
composed = []
email_composer.send_one = lambda db, candidate, **fields: (composed.append((candidate.email, fields)) or True)


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="takes-test-12", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "takes-test-12"})

used = {program_id for (program_id,) in db.query(ClassGroup.program_id).all()}
program = [row for row in db.query(Program).order_by(Program.name).all() if row.id not in used][0]
for row in db.query(Professor).all():                 # the copy's own professors stay out of the way
    row.is_available = False
settings = get_settings(db)
settings.auto_assign_professor = True
db.commit()

first_day = local_now().date() + timedelta(days=10)
while first_day.weekday() != 5:                        # a Saturday
    first_day += timedelta(days=1)
group = ClassGroup(name="TAKES-1", program_id=program.id, status=ClassStatus.NOT_STARTED, start_date=datetime.combine(first_day, dtime(9, 0)),
                   start_mode=es.START_FIXED_DATE, total_weeks=8, start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat",
                   capacity=20, max_capacity=20)
db.add(group)
db.flush()
person = Candidate(full_name="Rim Takes", email="rim.takes@example.com", phone="50 444 555")
db.add(person)
db.flush()
db.add(Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390, paid_amount=390,
                    payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, registration_date=datetime.utcnow(), class_group_id=group.id))
away = Professor(name="Nadia Absente", specialties=program.name, is_available=True, email="nadia@example.com", phone="22 333 444")
db.add(away)
db.flush()
db.add(ProfessorAbsence(professor_id=away.id, absence_date=first_day, reason="Congé"))
db.commit()

# ======================================================================
# A. A professor away on the start day does not take it
# ======================================================================
check("away on the class's first day: not offered", away.id not in [row.id for row in es.able_to_take(db, group)])
given = es.assign_waiting_professors(db)
db.commit()
check("...and the class keeps waiting", not given and db.get(ClassGroup, group.id).professor_id is None, given)
check("away only on other hours that day: still free", not es.absent_on(db, away.id, first_day + timedelta(days=7), dtime(9, 0), dtime(13, 0)))
db.add(ProfessorAbsence(professor_id=away.id, absence_date=first_day + timedelta(days=7), start_time=dtime(14, 0), end_time=dtime(16, 0)))
db.commit()
check("an absence in the afternoon does not stop a morning class", not es.absent_on(db, away.id, first_day + timedelta(days=7), dtime(9, 0), dtime(13, 0))
      and es.absent_on(db, away.id, first_day + timedelta(days=7), dtime(15, 0), dtime(17, 0)))

# ======================================================================
# B. One free that day takes it at once, and is told every way
# ======================================================================
free = Professor(name="Karim Libre", specialties=program.name, is_available=True, email="karim.libre@example.com", phone="22 555 666")
db.add(free)
db.commit()
auth.create_user(db, email="karim.libre@example.com", name="Karim Libre", password="takes-prof-12", role=auth.PROFESSOR, professor_id=free.id)
sent.clear()
es.sync_class_lifecycle(db)
db.expire_all()
taken = db.get(ClassGroup, group.id)
check("free on the start day: the class takes them by itself", taken.professor_id == free.id, taken.professor_name)
told = db.query(TeamMessage).filter(TeamMessage.person == f"professor:{free.id}", TeamMessage.direction == "out").all()
ways = {row.channel for row in told}
check("the professor is told in the application, by email, WhatsApp and SMS", ways == {"app", "email", "whatsapp", "sms"}, ways)
check("...with the class's code, its hours and their space", all("TAKES-1" in row.body and "/teacher" in row.body and "09:00" in row.body for row in told),
      [row.body for row in told][:1])
check("...the email really goes", any(recipient == "karim.libre@example.com" and "TAKES-1" in text for recipient, subject, text in sent), sent[:2])
note = db.query(Notification).filter(Notification.title == "A class took its professor by itself", Notification.action_url == f"/classes/{group.id}").first()
check("the admin is told it happened, and how the professor was told", note is not None and "Karim Libre" in note.message and "WhatsApp" in note.message)
es.sync_class_lifecycle(db)
db.expire_all()
check("told once", db.query(TeamMessage).filter(TeamMessage.person == f"professor:{free.id}", TeamMessage.direction == "out").count() == len(told))

# A class made at payment, with its professor chosen then: told too.
other_program = [row for row in db.query(Program).order_by(Program.name).all() if row.id not in used and row.id != program.id][0]
third = Professor(name="Lina Paiement", specialties=other_program.name, is_available=True, email="lina.paiement@example.com", phone="22 777 888")
db.add(third)
buyer = Candidate(full_name="Sami Paye", email="sami.paye@example.com")
db.add(buyer)
db.flush()
registration = Registration(candidate_id=buyer.id, program_id=other_program.id, program_name=other_program.name, total_amount=390, total_price=390,
                            paid_amount=390, payment_status=PaymentStatus.PAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow())
db.add(registration)
settings.auto_create_class_on_payment = True
db.commit()
result = es.enroll_paid_registration(db, registration)
db.expire_all()
check("a class made at payment takes a free professor", result.created_class and result.class_group.professor_id == third.id,
      (result.reason, result.class_group.professor_name if result.class_group else None))
check("...who is told at once", db.query(TeamMessage).filter(TeamMessage.person == f"professor:{third.id}", TeamMessage.direction == "out").count() >= 1)

# ======================================================================
# C. Reminders: every day, before each session, and reset
# ======================================================================
check("how often: every day, and before each of their sessions", {"daily", "session"} <= set(reminders.REPEATS))
daily = reminders.create(db, audience="class", audience_id=group.id, subject="Bonjour {first_name}", body="Pensez à réviser.", repeat="daily",
                         send_at=datetime.utcnow() - timedelta(minutes=1))
composed.clear()
reminders.send(db, daily)
check("every day: it goes, then waits for the same hour tomorrow", daily.status == "scheduled" and daily.times == 1
      and timedelta(hours=23) < daily.send_at - datetime.utcnow() <= timedelta(days=1), (daily.status, daily.send_at))

session_day = local_now() + timedelta(hours=20)
session = ClassSession(class_group_id=group.id, week_number=1, session_date=session_day.date(), start_time=session_day.time().replace(second=0, microsecond=0),
                       end_time=(session_day + timedelta(hours=2)).time().replace(second=0, microsecond=0), topic="Les capteurs")
far = ClassSession(class_group_id=group.id, week_number=2, session_date=(session_day + timedelta(days=7)).date(), start_time=dtime(9, 0), end_time=dtime(13, 0),
                   topic="Plus tard")
db.add_all([session, far])
db.commit()
response = admin.post("/reminders/create", data={"audience": "class", "to_class": group.id, "subject": "Demain, {session_topic}",
                                                 "body": "Bonjour {first_name}, rendez-vous le {session_date} à {session_time}.",
                                                 "repeat": "session", "hours_before": "24", "channel": "email"})
before = db.query(AdminReminder).filter(AdminReminder.repeat == "session").order_by(AdminReminder.created_at.desc()).first()
check("before each session: saved, it waits, with its hours", "24 hours before each" in where(response) and before is not None
      and before.status == "watching" and before.hours_before == 24, where(response))
check("the session's day, hour and topic are known words there", "Unknown" not in where(response))
check("...but not in a reminder that is not about a session", "Unknown variable" in where(admin.post("/reminders/create", data={
    "audience": "class", "to_class": group.id, "subject": "{session_topic}", "body": "x", "repeat": "once", "when": "now"})))
composed.clear()
reminders.send_due(db)
db.expire_all()
before = db.get(AdminReminder, before.id)
check("the session within 24 hours: each of its candidates gets it", len(composed) == 1 and composed[0][0] == "rim.takes@example.com"
      and before.times == 1, [item[0] for item in composed])
check("...naming that session", composed and composed[0][1]["values"]["session_topic"] == "Les capteurs" and composed[0][1]["values"]["session_time"])
check("...not the one next week yet", not any(item[1]["values"]["session_topic"] == "Plus tard" for item in composed))
composed.clear()
reminders.send_due(db)
check("once per session", not composed)
page = admin.get("/reminders").text
check("the page: the new rhythms, the hours before, and Reset", "Before each of their sessions" in page and "Every day" in page
      and 'name="hours_before"' in page and f"/reminders/{before.id}/reset" in page and "h before each session" in page)
response = admin.post(f"/reminders/{before.id}/reset")
db.expire_all()
before = db.get(AdminReminder, before.id)
check("reset: started again, as if just written", "Started again" in where(response) and before.times == 0 and before.status == "watching"
      and not json.loads(before.done_keys or "[]"))
composed.clear()
reminders.send_due(db)
check("...so the coming session is reminded again", len(composed) == 1)
reminders.cancel(db, daily)
response = admin.post(f"/reminders/{daily.id}/reset")
db.expire_all()
daily = db.get(AdminReminder, daily.id)
check("reset: a stopped reminder is on again, at its next hour", daily.status == "scheduled" and daily.send_at > datetime.utcnow() and daily.times == 0
      and "Started again: it goes" in where(response), (daily.status, where(response)))
check("before each session: for candidates only", "candidates" in where(admin.post("/reminders/create", data={
    "audience": "professors", "subject": "x", "body": "y", "repeat": "session", "hours_before": "2"})))
check("before each session: from 1 to 168 hours", "168" in where(admin.post("/reminders/create", data={
    "audience": "class", "to_class": group.id, "subject": "x", "body": "y", "repeat": "session", "hours_before": "500"})))

db.close()
check.done()

"""One class at a time at the same hours for a professor, until the whole class is over; a class online, on site or
both, its professor asked for the meeting link 3 days before the start and the link going to the online candidates;
and a professor away one day: a free professor asked to confirm a replacement for that day only.
Runs on a COPY; sending is faked."""
import json
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("link_replace")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Professor, Program, Registration, User, UserSession
from app.models.classsession import ClassSession
from app.models.professor_absence import ProfessorAbsence
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.session_join import SessionJoin
from app.models.team_message import TeamMessage
from app.services import auth, email_threads as et, enrollment_service as es, meetings, notification_rules as rules, replacements
from app.services.schedule_utils import local_now
from app.services.settings_service import get_settings

sent = []
et._attempt = lambda recipient, subject, text, *rest, **kw: (sent.append((recipient, subject, text)) or (None, False))
et.smtp_configured = lambda: True
BASE = "https://centre.example.com"


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="link-test-12", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "link-test-12"})
settings = get_settings(db)
settings.public_base_url, settings.auto_assign_professor = BASE, True
used = {program_id for (program_id,) in db.query(ClassGroup.program_id).all()}
program = [row for row in db.query(Program).order_by(Program.name).all() if row.id not in used][0]
for row in db.query(Professor).all():                 # the copy's own professors stay out of the way
    row.is_available = False
db.commit()
today = local_now().date()


def saturday(after_days):
    day = today + timedelta(days=after_days)
    while day.weekday() != 5:
        day += timedelta(days=1)
    return day


def professor(name, email, account=False):
    row = Professor(name=name, specialties=program.name, is_available=True, email=email, phone="22 " + str(abs(hash(name)) % 900 + 100) + " 456")
    db.add(row)
    db.commit()
    if account:
        auth.create_user(db, email=email, name=name, password="link-prof-12", role=auth.PROFESSOR, professor_id=row.id)
    return row


def klass(name, who, first_day, status=ClassStatus.NOT_STARTED, weeks=8, mode=None):
    row = ClassGroup(name=name, program_id=program.id, professor_id=who.id if who else None, professor_name=who.name if who else None,
                     status=status, start_date=datetime.combine(first_day, dtime(9, 0)), start_mode=es.START_FIXED_DATE, total_weeks=weeks,
                     start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat", capacity=20, max_capacity=20, delivery_mode=mode)
    db.add(row)
    db.flush()
    return row


def member(group, name, mode="online"):
    person = Candidate(full_name=name, email=f"{name.split()[0].lower()}.link@example.com", phone="50 777 888")
    db.add(person)
    db.flush()
    registration = Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390,
                                paid_amount=390, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, delivery_mode=mode,
                                registration_date=datetime.utcnow(), class_group_id=group.id)
    db.add(registration)
    db.flush()
    return registration


# ======================================================================
# A. One class at a time at the same hours, until the whole class is over
# ======================================================================
hela = professor("Hela Prise", "hela.prise@example.com")
late = klass("LATE-1", hela, today - timedelta(weeks=12), status=ClassStatus.ONGOING, weeks=8)   # planned for 8 weeks, still running
member(late, "Anis Late")
waiting = klass("NEXT-1", None, saturday(7))
member(waiting, "Bassem Next")
db.commit()
check("a class still running past its planned end keeps its professor's hours", hela.id not in [row.id for row in es.able_to_take(db, waiting)])
check("...so the same professor is not given a class at the same hours", es.professor_clash(db, hela.id, [5], dtime(9, 0), dtime(13, 0), saturday(7), 8) is not None)
es.complete_class(db, db.get(ClassGroup, late.id))
db.commit()
check("once the whole class is over, they are free again", hela.id in [row.id for row in es.able_to_take(db, db.get(ClassGroup, waiting.id))])

# ======================================================================
# B. Online, on site, or both
# ======================================================================
check("a class's mode: online, on site, or both", set(meetings.MODES) == {"online", "onsite", "mixed"})
mixed = klass("MIX-1", hela, today + timedelta(days=2))
online_one = member(mixed, "Chaima Online", "online")
onsite_one = member(mixed, "Dhia Onsite", "onsite")
db.commit()
check("read from its candidates' choices: both", meetings.mode_of(db, mixed) == "mixed")
session = ClassSession(class_group_id=mixed.id, week_number=1, session_date=mixed.start_date.date(), start_time=dtime(9, 0), end_time=dtime(13, 0), topic="Intro")
db.add(session)
db.commit()
joins = meetings.links_for(db, mixed, session)
check("both: only its online candidates get a link", set(joins) == {online_one.id}, list(joins))
mixed.delivery_mode = "onsite"
db.commit()
check("set fully on site: nobody gets a link", meetings.mode_of(db, mixed) == "onsite" and not meetings.follows_online(db, mixed, online_one))
mixed.delivery_mode = None
db.commit()

# ======================================================================
# C. Three days before: the professor is asked for the meeting link
# ======================================================================
sami = professor("Sami Lien", "sami.lien@example.com", account=True)
mixed.professor_id, mixed.professor_name = sami.id, sami.name
far = klass("FAR-1", sami, saturday(9))
member(far, "Emna Far")
db.commit()
sent.clear()
done = meetings.ask_links_due(db, BASE)
db.expire_all()
asked = db.query(TeamMessage).filter(TeamMessage.person == f"professor:{sami.id}", TeamMessage.direction == "out").all()
check("3 days before the start: its professor is asked, in the application, by email, WhatsApp and SMS",
      done["asked"] == 1 and {row.channel for row in asked} == {"app", "email", "whatsapp", "sms"}, (done, [row.channel for row in asked]))
check("...with where to paste it", all(f"{BASE}/teacher/classes/{mixed.id}/link" in row.body for row in asked))
check("...not a class that starts later", db.get(ClassGroup, far.id).link_asked_at is None)
check("asked once", meetings.ask_links_due(db, BASE)["asked"] == 0)
onsite_class = klass("ROOM-1", sami, saturday(2), mode="onsite")
member(onsite_class, "Fares Room", "onsite")
db.commit()
meetings.ask_links_due(db, BASE)
db.expire_all()
check("a class fully on site: nobody is asked", db.get(ClassGroup, onsite_class.id).link_asked_at is None)
soon = local_now() + timedelta(hours=20)
rym = professor("Rym Demain", "rym.demain@example.com")
tomorrow = klass("SOON-1", rym, soon.date())
tomorrow.start_date = soon.replace(second=0, microsecond=0)
member(tomorrow, "Ghada Soon")
db.commit()
meetings.ask_links_due(db, BASE)
note = db.query(Notification).filter(Notification.category == "MEETING_LINK", Notification.action_url == f"/classes/{tomorrow.id}").first()
check("a day before, still no link: the admin is told, and it waits", note is not None and rules.needs_you(db, note) and not rules.settled(db, note))

# ======================================================================
# D. The professor gives it: every session uses it, the online candidates get their own link
# ======================================================================
teacher = TestClient(main.app, follow_redirects=False)
teacher.post("/login", data={"email": "sami.lien@example.com", "password": "link-prof-12"})
page = teacher.get(f"/teacher/classes/{mixed.id}/link")
check("their page to paste it", page.status_code == 200 and 'name="meeting_url"' in page.text and "MIX-1" in page.text, page.status_code)
check("not another professor's class", teacher.get(f"/teacher/classes/{late.id}/link").status_code == 404)
sent.clear()
response = teacher.post(f"/teacher/classes/{mixed.id}/link", data={"meeting_url": "meet.google.com/abc-defg-hij"})
db.expire_all()
group = db.get(ClassGroup, mixed.id)
check("kept for the class and every session to come", group.meeting_url == "https://meet.google.com/abc-defg-hij"
      and db.get(ClassSession, session.id).meeting_url == group.meeting_url, (group.meeting_url, where(response)))
check("each online candidate gets their own link at once, the one on site does not",
      any(recipient == "chaima.link@example.com" for recipient, _, _ in sent) and not any(recipient == "dhia.link@example.com" for recipient, _, _ in sent),
      [recipient for recipient, _, _ in sent])
check("...never the meeting's address itself", not any("abc-defg-hij" in text for _, _, text in sent))
check("a wrong address is refused", "https://" in where(teacher.post(f"/teacher/classes/{mixed.id}/link", data={"meeting_url": "not a link"})))
answer = admin.post(f"/classes/{tomorrow.id}/parameters", data={"name": "SOON-1", "capacity": 20, "total_weeks": 8, "start_mode": es.START_FIXED_DATE,
                                                       "fixed_start_date": tomorrow.start_date.date().isoformat(), "days": ["sat"],
                                                       "start_time": "09:00", "end_time": "13:00", "meeting_url": "https://zoom.us/j/123456",
                                                       "delivery_mode": "online", "professor_id": rym.id, "min_students_to_start": 1})
db.expire_all()
check("the admin can give it too, in the class's parameters", db.get(ClassGroup, tomorrow.id).meeting_url == "https://zoom.us/j/123456"
      and db.get(ClassGroup, tomorrow.id).delivery_mode == "online", where(answer))
rules.tidy(db, force=True)
db.expire_all()
check("...and the admin's notification closes by itself", db.get(Notification, note.id).is_read)
check("the class page says how it is followed", "How it is followed" in admin.get(f"/classes/{mixed.id}").text)

# ======================================================================
# E. A professor away one day: a replacement for that day only
# ======================================================================
day = saturday(14)
course = klass("AWAY-1", hela, day - timedelta(weeks=1), status=ClassStatus.ONGOING)
member(course, "Hamza Away")
lesson = ClassSession(class_group_id=course.id, week_number=2, session_date=day, start_time=dtime(9, 0), end_time=dtime(13, 0), topic="Les API")
db.add(lesson)
busy_prof = professor("Ines Occupee", "ines.occupee@example.com")
klass("INES-1", busy_prof, day - timedelta(weeks=2), status=ClassStatus.ONGOING)
first = professor("Jamel Premier", "jamel.premier@example.com")
second = professor("Kenza Second", "kenza.second@example.com", account=True)
db.commit()
response = admin.post(f"/classes/professors/{hela.id}/absences", data={"absence_date": day.isoformat(), "reason": "Malade"})
db.expire_all()
row = db.query(replacements.SessionReplacement).filter(replacements.SessionReplacement.session_id == lesson.id).first()
check("an absence that day: its session looks for a replacement", "need a replacement" in where(response) and row is not None and row.status == "asked", where(response))
check("...asking one who teaches it and is free that day, not one who teaches then", row.asked_professor_id in (first.id, second.id) and row.asked_professor_id != busy_prof.id)
asked = db.query(TeamMessage).filter(TeamMessage.person == f"professor:{row.asked_professor_id}", TeamMessage.direction == "out").all()
check("...by a message with a private link to confirm", asked and all(f"/replace/{row.token}" in item.body for item in asked) and "ce jour seulement" in asked[0].body.lower()
      or (asked and "pour ce jour seulement" in asked[0].body), [item.body for item in asked][:1])
note = db.query(Notification).filter(Notification.category == "REPLACEMENT").first()
check("the admin has one notification that waits for it", note is not None and rules.needs_you(db, note) and not rules.settled(db, note))
anon = TestClient(main.app, follow_redirects=False)
page = anon.get(f"/replace/{row.token}")
check("the link opens without signing in: the class, the day, yes or no", page.status_code == 200 and "AWAY-1" in page.text and "Je confirme" in page.text)
first_asked = row.asked_professor_id
anon.post(f"/replace/{row.token}", data={"answer": "no"})
db.expire_all()
row = db.get(replacements.SessionReplacement, row.id)
check("no: the next one is asked", row.status == "asked" and row.asked_professor_id not in (first_asked, None) and first_asked in json.loads(row.declined))
answer = anon.post(f"/replace/{row.token}", data={"answer": "yes"})
db.expire_all()
row = db.get(replacements.SessionReplacement, row.id)
check("yes: they replace the usual professor for that session only", row.status == "accepted" and db.get(ClassSession, lesson.id).substitute_professor_id == row.asked_professor_id
      and db.get(ClassGroup, course.id).professor_id == hela.id and "Merci" in answer.text)
rules.tidy(db, force=True)
db.expire_all()
check("the admin's notification closes, saying who", db.get(Notification, note.id).is_read and "replaces them that day" in db.get(Notification, note.id).message)
if row.asked_professor_id == second.id:
    sub = TestClient(main.app, follow_redirects=False)
    sub.post("/login", data={"email": "kenza.second@example.com", "password": "link-prof-12"})
    check("the substitute opens that class's presence sheet", sub.get(f"/classes/{course.id}/attendance?session={lesson.id}").status_code == 200)
check("the link works once", anon.get(f"/replace/{row.token or 'x' * 24}").status_code == 404)
check("they now teach then: not asked for another class at those hours that day", replacements.busy_then(db, row.asked_professor_id, day, dtime(10, 0), dtime(11, 0)))

# Nobody answers in time: the next; nobody left: the admin is told.
other_day = saturday(21)
lesson2 = ClassSession(class_group_id=course.id, week_number=3, session_date=other_day, start_time=dtime(9, 0), end_time=dtime(13, 0), topic="Tests")
db.add(lesson2)
db.add(ProfessorAbsence(professor_id=hela.id, absence_date=other_day))
db.commit()
absence = db.query(ProfessorAbsence).filter(ProfessorAbsence.absence_date == other_day).first()
(row2,) = replacements.open_for_absence(db, absence, BASE)
row2.asked_at = datetime.utcnow() - timedelta(hours=7)
db.commit()
replacements.tick(db, BASE)
db.expire_all()
row2 = db.get(replacements.SessionReplacement, row2.id)
check("no answer in time: the next one is asked", row2.status == "asked" and len(json.loads(row2.declined)) == 1)
row2.asked_at = datetime.utcnow() - timedelta(hours=7)
db.commit()
replacements.tick(db, BASE)
db.expire_all()
row2 = db.get(replacements.SessionReplacement, row2.id)
check("nobody left: the admin is told", row2.status == "none" and db.query(Notification).filter(Notification.category == "REPLACEMENT",
      Notification.message.like("%no professor who teaches it is free%")).count() == 1, row2.status)
check("the class page lists its replacements", "replaces them that day" in admin.get(f"/classes/{course.id}").text)

db.close()
check.done()

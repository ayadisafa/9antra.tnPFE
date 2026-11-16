"""A class never runs empty: who leaves a class is written down with why; an empty class is removed (not started) or
ended (running) by itself, and its professor is told why. Staging copy, COPY of the database, no mail leaves this machine."""
import testenv
testenv.prepare("empty_classes")
check = testenv.Checks()

from datetime import date, datetime, timedelta, time as dtime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from urllib.parse import unquote_plus
from app.database import SessionLocal
from app.models import Activity, Candidate, ClassGroup, ClassStatus, Notification, Professor, Program, Registration, User, UserSession
from app.models.classsession import ClassSession
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.team_message import TeamMessage
from app.models.user import Attendance
from app.services import (auth, email_templates as tp, email_threads as et, empty_classes as ec, enrollment_service as es,
                          monthly, team_messages)
from app.services.schedule_utils import local_now

sent = []
def fake_send(**kw):
    sent.append(kw); return True
et.send_email = fake_send; tp.send_email = fake_send
et.smtp_configured = lambda: True                     # the professor's designed email is kept here, never sent
et._attempt = lambda to, subject, plain, html, *rest: (sent.append({"to_email": to, "subject": subject, "html": html}), (None, None))[1]

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="empty-classes-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "empty-classes-1"})
def where(response): return unquote_plus(response.headers.get("location", ""))

# The copy's own classes stay out of the way: only this suite's are looked at.
mine = set()

program = Program(name="Empty Classes Program")
db.add(program); db.flush()
teacher = Professor(name="Salma Trabelsi", email="salma.empty@test.local", phone="50 111 222", specialties="Empty Classes Program")
db.add(teacher); db.flush()

def new_class(name, start, status=ClassStatus.NOT_STARTED, weeks=8):
    group = ClassGroup(name=name, program_id=program.id, professor_id=teacher.id, professor_name=teacher.name, status=status, start_date=start,
                       total_weeks=weeks, start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat", start_mode="fixed_date",
                       capacity=12, max_capacity=12)
    db.add(group); db.flush()
    es.reschedule_sessions(db, group)
    db.commit()
    mine.add(group.id)
    return group

def member(name, email, group):
    one = Candidate(full_name=name, email=email, phone="50 300 400"); db.add(one); db.flush()
    reg = Registration(candidate_id=one.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=300,
                       total_price=300, paid_amount=300, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED,
                       class_group_id=group.id)
    db.add(reg); db.commit()
    return one, reg

def told_professor(since):
    return [row for row in db.query(TeamMessage).filter(TeamMessage.person == f"professor:{teacher.id}", TeamMessage.direction == "out",
                                                         TeamMessage.created_at >= since).all()]

def sweep(now=None):
    """The sweep on this suite's classes only (the copy's real classes are not touched)."""
    others = {group.id: group.status for group in db.query(ClassGroup).filter(ClassGroup.status != ClassStatus.COMPLETED).all()
              if group.id not in mine}
    for group_id in others:
        db.get(ClassGroup, group_id).status = ClassStatus.COMPLETED
    db.commit()
    try:
        return ec.sweep(db, now)
    finally:
        for group_id, status in others.items():
            db.get(ClassGroup, group_id).status = status
        db.commit()

later = (local_now() + timedelta(days=20)).replace(hour=9, minute=0, second=0, microsecond=0)

# ======================================================================
# A. Who leaves a class, and why
# ======================================================================
first = new_class("EMPTY-A", later)
second = new_class("EMPTY-B", later)
lina, lina_reg = member("Lina Empty", "lina.empty@test.local", first)
omar, omar_reg = member("Omar Empty", "omar.empty@test.local", first)
es.move_registration(db, lina_reg.id, second.id, notify_candidate=False)
rows = db.query(ec.ClassDeparture).filter(ec.ClassDeparture.class_group_id == first.id).all()
check("a candidate moved to another class is written down, with where they went", len(rows) == 1 and rows[0].kind == "moved"
      and rows[0].to_class_id == second.id and rows[0].candidate_name == "Lina Empty" and rows[0].class_name == "EMPTY-A", [(r.kind, r.to_class_id) for r in rows])
db.expire_all()
reg = db.get(Registration, omar_reg.id)
reg.status = RegistrationStatus.CANCELLED
db.commit()
rows = db.query(ec.ClassDeparture).filter(ec.ClassDeparture.class_group_id == first.id).order_by(ec.ClassDeparture.created_at).all()
check("a cancelled registration too", [row.kind for row in rows] == ["moved", "cancelled"])
check("in words, for the admin and in French for the professor",
      ec.why(db, rows) == "Omar Empty: registration cancelled; Lina Empty was moved to EMPTY-B"
      and ec.why(db, rows, "fr") == "Omar Empty : inscription annulée; Lina Empty a été déplacé(e) vers la classe EMPTY-B", ec.why(db, rows, "fr"))
third = new_class("EMPTY-C", later)
sami, sami_reg = member("Sami Empty", "sami.empty@test.local", third)
es.unassign_registration(db, db.get(Registration, sami_reg.id))
db.commit()
check("taken out of the class", [row.kind for row in db.query(ec.ClassDeparture).filter(ec.ClassDeparture.class_group_id == third.id)] == ["out"])
fourth = new_class("EMPTY-D", later)
nadia, nadia_reg = member("Nadia Empty", "nadia.empty@test.local", fourth)
db.delete(db.get(Candidate, nadia.id)); db.commit()
check("removed from the candidates", [row.kind for row in db.query(ec.ClassDeparture).filter(ec.ClassDeparture.class_group_id == fourth.id)] == ["deleted"])
fifth = new_class("EMPTY-E", later)
yassine, yassine_reg = member("Yassine Empty", "yassine.empty@test.local", fifth)
with ec.because("one_class", "2026-12-08"):
    moving = db.get(Registration, yassine_reg.id)
    moving.class_group_id, moving.class_group, moving.status = second.id, second, RegistrationStatus.ASSIGNED
    db.commit()
row = db.query(ec.ClassDeparture).filter(ec.ClassDeparture.class_group_id == fifth.id).one()
check("one class at a time: the reason and the day their other class ends", row.cause == "one_class" and row.detail == "2026-12-08"
      and "one class at a time, so they now join EMPTY-B" in ec.why(db, [row]) and "8 décembre 2026" in ec.why(db, [row], "fr"), ec.why(db, [row], "fr"))
before = db.query(ec.ClassDeparture).count()
reg = db.get(Registration, omar_reg.id)
reg.class_group_id = None; db.commit()
check("an already cancelled registration leaving is not counted twice", db.query(ec.ClassDeparture).count() == before)
fresh, _ = member("Rania Empty", "rania.empty@test.local", second)
check("joining a class is not a departure", db.query(ec.ClassDeparture).count() == before)

# ======================================================================
# B. Emptied before its start: removed half an hour after the last one left, the professor told why
# ======================================================================
first_id, first_name = first.id, first.name
done = sweep()
check("just emptied: it waits half an hour (time to move people between classes)", first_id not in done["removed"] and db.get(ClassGroup, first_id) is not None)
page = admin.get(f"/classes/{first_id}")
check("its page says so: who left, when it goes, its professor told", page.status_code == 200 and "Its last candidate left" in page.text
      and "Lina Empty was moved to EMPTY-B" in page.text and "Salma Trabelsi is told why" in page.text, page.status_code)
for row in db.query(ec.ClassDeparture).filter(ec.ClassDeparture.class_group_id == first_id):
    row.created_at = datetime.utcnow() - timedelta(minutes=31)
db.commit()
since = datetime.utcnow() - timedelta(seconds=1)
sent.clear()
done = sweep()
db.expire_all()
check("after half an hour it is removed by itself", first_name in done["removed"] and db.get(ClassGroup, first_id) is None, done)
words = told_professor(since)
check("its professor is told, in French, with why", words and any("EMPTY-A" in row.body and "annulée" in row.body and "Lina Empty a été déplacé(e) vers la classe EMPTY-B" in row.body
                                                                  and "Omar Empty : inscription annulée" in row.body for row in words), [row.body[:200] for row in words])
check("by email too, a designed one", any(mail.get("to_email") == "salma.empty@test.local" and "EMPTY-A" in (mail.get("subject") or "")
                                         and "Première séance prévue" in (mail.get("html") or "") for mail in sent),
      [mail.get("subject") for mail in sent])
told = db.query(Notification).filter(Notification.title == "Class removed: nobody in it").order_by(Notification.created_at.desc()).first()
check("the admin is told why", told is not None and "EMPTY-A" in told.message and "Lina Empty was moved to EMPTY-B" in told.message and "Salma Trabelsi was told" in told.message)
check("the history keeps it", db.query(Activity).filter(Activity.event_type == "CLASS_REMOVED", Activity.details.like("EMPTY-A%")).count() == 1)
check("the departures stay after the class is gone", db.query(ec.ClassDeparture).filter(ec.ClassDeparture.class_group_id == first_id).count() == 2)
unreachable = Professor(name="Karim Nowhere", specialties="Empty Classes Program")
db.add(unreachable); db.flush()
lonely = new_class("EMPTY-NOWAY", later)
lonely.professor_id, lonely.professor_name = unreachable.id, unreachable.name
db.commit()
page = admin.get(f"/classes/{lonely.id}")
check("a professor who cannot be reached: the page says to tell them yourself", "Karim Nowhere cannot be reached: tell them yourself" in page.text)
lonely.start_date = local_now() - timedelta(hours=1); db.commit()
sweep()
told = db.query(Notification).filter(Notification.title == "Class removed: nobody in it", Notification.message.like("EMPTY-NOWAY%")).first()
check("...and so does the admin's notification, never “was told”", told is not None and "Karim Nowhere could not be told" in told.message
      and "was told" not in told.message, told.message if told else None)
check("a class with somebody in it is never touched", db.get(ClassGroup, second.id) is not None and "EMPTY-B" not in done["removed"])

# ======================================================================
# C. Nobody ever joined: removed when its first session comes, the admin told three days before
# ======================================================================
quiet = new_class("EMPTY-NEVER", (local_now() + timedelta(days=10)).replace(hour=9, minute=0, second=0, microsecond=0))
sweep()
check("ten days before: nothing", db.get(ClassGroup, quiet.id) is not None
      and not db.query(Notification).filter(Notification.title == "Nobody in this class yet", Notification.message.like("EMPTY-NEVER%")).count())
page = admin.get(f"/classes/{quiet.id}")
check("its page says it never starts empty, and what happens then", "A class never starts empty. If nobody is in it at its first session" in page.text
      and "Salma Trabelsi is told why" in page.text)
soon = local_now() + timedelta(days=2)
quiet.start_date = soon.replace(hour=9, minute=0, second=0, microsecond=0); db.commit()
sweep(); sweep()
check("two days before: the admin is told once", db.query(Notification).filter(Notification.title == "Nobody in this class yet",
                                                                                 Notification.message.like("EMPTY-NEVER%")).count() == 1)
since = datetime.utcnow() - timedelta(seconds=1)
quiet_id = quiet.id
done = sweep(quiet.start_date + timedelta(minutes=1))
db.expire_all()
check("its first session comes and nobody is in it: removed", "EMPTY-NEVER" in done["removed"] and db.get(ClassGroup, quiet_id) is None, done)
check("its professor is told that nobody joined it", any("EMPTY-NEVER" in row.body and "aucun candidat ne l'a rejointe depuis sa création" in row.body for row in told_professor(since)))

# ======================================================================
# D. Already running: it ends by itself (its history stays)
# ======================================================================
started = (local_now() - timedelta(days=15)).replace(hour=9, minute=0, second=0, microsecond=0)
running = new_class("EMPTY-RUN", started, status=ClassStatus.ONGOING)
anis, anis_reg = member("Anis Empty", "anis.empty@test.local", running)
held = ClassSession(class_group_id=running.id, week_number=1, session_date=started.date(), topic="Introduction")
db.add(held); db.flush()
db.add(Attendance(class_group_id=running.id, session_id=held.id, registration_id=anis_reg.id, candidate_id=anis.id, status="present"))
db.commit()
es.move_registration(db, anis_reg.id, second.id, notify_candidate=False)
since = datetime.utcnow() - timedelta(seconds=1)
done = sweep()
db.expire_all()
running = db.get(ClassGroup, running.id)
check("a running class left by everyone ends at once", "EMPTY-RUN" in done["ended"] and running is not None and running.status == ClassStatus.COMPLETED
      and running.closed_note.startswith("Ended early on") and "Anis Empty was moved to EMPTY-B" in running.closed_note, (done, running.closed_note if running else None))
check("what happened in it stays", db.query(Attendance).filter(Attendance.class_group_id == running.id).count() == 1)
ended_told = db.query(Notification).filter(Notification.title == "Class ended early: nobody left").count()
es.sync_class_lifecycle(db); es.sync_class_lifecycle(db)
db.expire_all()
check("the lifecycle keeps it ended, and tells nobody twice", db.get(ClassGroup, running.id).status == ClassStatus.COMPLETED
      and db.query(Notification).filter(Notification.title == "Class ended early: nobody left", Notification.message.like("EMPTY-RUN%")).count() == 1)
check("its professor is told it stops, and why", any("EMPTY-RUN" in row.body and "s'arrête aujourd'hui" in row.body and "Anis Empty" in row.body for row in told_professor(since)))
page = admin.get(f"/classes/{running.id}")
check("its page says it ended early, and why", "Ended early on" in page.text and "Anis Empty was moved to EMPTY-B" in page.text)

stopping = new_class("EMPTY-STOP", started, status=ClassStatus.ONGOING)
kais, kais_reg = member("Kais Empty", "kais.empty@test.local", stopping)
db.add(ClassSession(class_group_id=stopping.id, week_number=4, session_date=date.today() + timedelta(days=3), topic="La suite")); db.commit()
_ended = monthly.ended_before
monthly.ended_before = lambda registration, day: registration.id == kais_reg.id
try:
    done = sweep()
finally:
    monthly.ended_before = _ended
db.expire_all()
check("everyone stopped after their months: it ends, and says so", "EMPTY-STOP" in done["ended"] and db.get(ClassGroup, stopping.id).status == ClassStatus.COMPLETED
      and "Kais Empty chose to stop after their months" in db.get(ClassGroup, stopping.id).closed_note
      and db.get(Registration, kais_reg.id).status == RegistrationStatus.COMPLETED)
still = new_class("EMPTY-STILL", started, status=ClassStatus.ONGOING)
member("Hela Empty", "hela.empty@test.local", still)
check("a running class with someone still following it goes on", "EMPTY-STILL" not in sweep()["ended"] and db.get(ClassGroup, still.id).status == ClassStatus.ONGOING)

# ======================================================================
# E. Emptied before this was kept: read from the history
# ======================================================================
old = new_class("EMPTY-OLD", later)
ahmed, ahmed_reg = member("Ahmed Empty", "ahmed.empty@test.local", old)
target = db.get(Registration, ahmed_reg.id)
target.class_group_id = second.id; db.commit()
db.query(ec.ClassDeparture).filter(ec.ClassDeparture.class_group_id == old.id).delete(); db.commit()
db.add(Activity(candidate_id=ahmed.id, event_type="CLASS_MOVED", details="Moved out of EMPTY-OLD: their class before it now ends Tue 08 Dec 2026.",
                created_at=datetime.utcnow() - timedelta(hours=10)))
db.commit()
since = datetime.utcnow() - timedelta(seconds=1)
done = sweep()
check("an old move read from the history: removed at once, with its reason", "EMPTY-OLD" in done["removed"]
      and any("EMPTY-OLD" in row.body and "Ahmed Empty suit encore une autre classe qui se termine le" in row.body and "EMPTY-B" in row.body
              for row in told_professor(since)), [row.body[:240] for row in told_professor(since)])

# ======================================================================
# F. It cannot start empty; the lifecycle runs it every minute
# ======================================================================
blank = new_class("EMPTY-BLANK", later)
try:
    es.start_class_now(db, blank); refused = False
except ValueError as problem:
    refused = "no candidate is enrolled" in str(problem)
check("an empty class cannot be started by hand", refused)
check("the lifecycle runs the check", "empty_classes.sweep(db, now)" in open(es.__file__, encoding="utf-8").read())
from app.services import settings_guide
check("Yasmine knows the rule", "A class never runs empty" in open(settings_guide.__file__, encoding="utf-8").read())
check.done()

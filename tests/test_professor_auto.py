"""Only the professors who can take a class are offered for it (its program, available, free at its hours); a class
waiting for a professor, with somebody in it, takes the first one who can by itself (also when one becomes available
again, or after a professor is deleted); its candidates get “Your professor” with how to write to them; the admin's
switch stops it. And nothing waits on the network while holding the database. Runs on a COPY; sending is faked."""
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("professor_auto")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal, let_others_write
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Professor, Program, Registration, User, UserSession
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import auth, email_threads as et, enrollment_service as es, professor_announcements as pa, sms
from app.services.settings_service import get_settings
from app.api import classgroup

sent = []
et._attempt = lambda recipient, subject, text, *rest, **kw: (sent.append((recipient, subject, text)) or (None, False))
et.smtp_configured = lambda: True


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="auto-test-12", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "auto-test-12"})

used = {program_id for (program_id,) in db.query(ClassGroup.program_id).all()}
free_programs = [program for program in db.query(Program).order_by(Program.name).all() if program.id not in used]
program, other_program = free_programs[0], free_programs[1]
for row in db.query(Professor).all():                 # the copy's own professors stay out of the way
    row.is_available = False
settings = get_settings(db)
settings.auto_assign_professor = True
db.commit()
start = datetime.combine(date.today() - timedelta(days=10), dtime(9, 0))


def professor(name, teaches=program, available=True, **extra):
    row = Professor(name=name, specialties=teaches.name if teaches else "Cooking", is_available=available, **extra)
    db.add(row)
    db.flush()
    return row


def group(name, who=None, prog=program, hours=(9, 13)):
    row = ClassGroup(name=name, program_id=prog.id, professor_id=who.id if who else None, professor_name=who.name if who else None,
                     status=ClassStatus.ONGOING, start_date=start, total_weeks=8, start_time=dtime(hours[0], 0), end_time=dtime(hours[1], 0),
                     days_of_week="sat")
    db.add(row)
    db.flush()
    return row


def member(class_group, name):
    person = Candidate(full_name=name, email=f"{name.split()[0].lower()}.auto@example.com", phone="50 333 444")
    db.add(person)
    db.flush()
    db.add(Registration(candidate_id=person.id, program_id=class_group.program_id, program_name=class_group.program.name if class_group.program else "",
                        total_amount=390, total_price=390, paid_amount=390, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED,
                        registration_date=datetime.utcnow(), class_group_id=class_group.id))
    db.flush()
    return person


able = professor("Amel Disponible", email="amel@example.com", phone="22 111 222")
away = professor("Ugo Indisponible", available=False)
later = professor("Dalia Plus Tard", available_from=date.today() + timedelta(days=200))
cook = professor("Omar Cuisine", teaches=None)
busy = professor("Badis Occupe")
kept = group("AUTO-KB", busy)                          # Badis teaches on Saturday 09:00-13:00
waiting = group("AUTO-W")
member(waiting, "Wafa Candidate")
empty = group("AUTO-E", hours=(14, 16))                # nobody in it
db.commit()

# ---- A. The list: only who can take the class ----
choices = classgroup._professor_choices(db, waiting)
check("list: only the professor who teaches it, is available and free at its hours", [row.name for row in choices] == ["Amel Disponible"],
      [row.name for row in choices])
page = admin.get(f"/classes/{waiting.id}/parameters-form")
text = page.text if page.status_code == 200 else ""
check("parameters: the select offers only them", page.status_code == 200 and "Amel Disponible" in text and "Ugo Indisponible" not in text
      and "Badis Occupe" not in text and "Omar Cuisine" not in text and "are available and free at its hours" in text, page.status_code)

# ---- B. The waiting class takes the first one who can, by itself; its candidates are told ----
sent.clear()
es.sync_class_lifecycle(db)
db.expire_all()
check("auto: the waiting class takes the professor who can", db.get(ClassGroup, waiting.id).professor_id == able.id
      and db.get(ClassGroup, waiting.id).professor_name == "Amel Disponible")
check("auto: a class with nobody in it does not take a professor", db.get(ClassGroup, empty.id).professor_id is None)
check("auto: the professor is told of the class", db.query(pa.ProfessorAnnouncement).filter(pa.ProfessorAnnouncement.class_group_id == waiting.id,
                                                                                            pa.ProfessorAnnouncement.professor_id == able.id).count() == 1)
check("auto: the admin is told", db.query(Notification).filter(Notification.title == "A class took its professor by itself",
                                                                 Notification.message.like("AUTO-W · Amel Disponible.%")).count() == 1)
mail = [item for item in sent if item[0] == "wafa.auto@example.com"]
check("candidates: “Your professor”, with their email, phone, and the reply that reaches them",
      len(mail) == 1 and "Votre formateur" in mail[0][1] and "Amel Disponible" in mail[0][2] and "amel@example.com" in mail[0][2]
      and "22 111 222" in mail[0][2] and "en réponse à cet email" in mail[0][2], (mail[0][1], mail[0][2][:300]) if mail else sent[:2])
sent.clear()
es.sync_class_lifecycle(db)
check("auto: told once, not at every check", not [item for item in sent if item[0] == "wafa.auto@example.com"])

# ---- C. The admin's switch stops it ----
settings = get_settings(db)
settings.auto_assign_professor = False
db.commit()
second = group("AUTO-W2", hours=(15, 17))
member(second, "Sami Second")
db.commit()
es.sync_class_lifecycle(db)
db.expire_all()
check("switch off: the class keeps waiting", db.get(ClassGroup, second.id).professor_id is None)
settings = get_settings(db)
settings.auto_assign_professor = True
db.commit()

# ---- D. A professor available again takes a waiting class at once ----
third = group("AUTO-W3", prog=other_program, hours=(9, 12))
member(third, "Tarek Third")
back = professor("Rim Revient", teaches=other_program, available=False, email="rim@example.com")
db.commit()
es.sync_class_lifecycle(db)
db.expire_all()
check("unavailable: the class waits", db.get(ClassGroup, third.id).professor_id is None)
sent.clear()
response = admin.post(f"/classes/professors/{back.id}/edit", data={"name": back.name, "specialties": other_program.name, "email": "rim@example.com",
                                                                  "phone": "", "available_from": "", "available_until": "", "is_available": "true"})
db.expire_all()
check("available again: the waiting class takes them at once", response.status_code == 303 and db.get(ClassGroup, third.id).professor_id == back.id,
      where(response))
check("available again: its candidates are told", any(item[0] == "tarek.auto@example.com" and "Rim Revient" in item[2] for item in sent))

# ---- E. Deleting a professor: the class goes by itself to the next one who can ----
spare = professor("Sonia Relais", email="sonia@example.com")
db.commit()
dialog = admin.get(f"/classes/professors/{able.id}/delete").text
check("dialog: “automatically” is the choice by default", "Automatically: the first professor who can take it" in dialog and "Sonia Relais" in dialog)
sent.clear()
response = admin.post(f"/classes/professors/{able.id}/delete", data={f"give_{waiting.id}": ""})
db.expire_all()
check("deleted: the class went by itself to the next one who can", db.get(ClassGroup, waiting.id).professor_id == spare.id
      and "AUTO-W now goes to Sonia Relais" in where(response) and "Waiting for a professor" not in where(response), where(response))
check("deleted: its candidates are told who their new professor is", any(item[0] == "wafa.auto@example.com" and "Sonia Relais" in item[2] for item in sent))

# ---- F. Nothing waits on the network while holding the database ----
first = SessionLocal()
row = first.get(ClassGroup, empty.id)
row.room = "Salle réseau"
first.flush()                                          # the write lock is taken...
sms.send("50 000 000", "test")                         # ...and saved before the (unconfigured) phone is reached
other = SessionLocal()
check("network: what was changed is saved before waiting", other.get(ClassGroup, empty.id).room == "Salle réseau")
other.close()
first.close()
quiet = SessionLocal()
quiet.query(ClassGroup).first()
check("network: a session that changed nothing is left alone", let_others_write() == 0)
quiet.close()

db.close()
check.done()

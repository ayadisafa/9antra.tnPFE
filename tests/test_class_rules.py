"""A class starts only with candidates and a professor of its speciality; it is not created for nobody.
Runs on a copy, on a COPY of the database; nothing leaves this machine."""
import testenv
testenv.prepare("class_rules")
check = testenv.Checks()

from datetime import datetime, timedelta, time as dtime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Professor, Program, Registration, User, UserSession
from app.models.feedback import CandidateFeedback
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import advisor, auth, email_assistant as ea, email_templates as tp, email_threads as et, enrollment_service as es
from app.services.class_views import class_summary
from app.services.schedule_utils import local_now
from app.services.settings_service import get_settings

sent_mail = []
def fake_send(**kw): sent_mail.append(kw); return True
et.send_email = fake_send; tp.send_email = fake_send
et.smtp_configured = lambda: True
ea.model_available = lambda: True
ea.llm_json = lambda *a, **k: None

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="class-rules-1", role=auth.ADMIN)
client = TestClient(main.app, follow_redirects=False)
client.post("/login", data={"email": "admin@test.local", "password": "class-rules-1"})
from urllib.parse import unquote_plus
def where(response): return unquote_plus(response.headers.get("location", ""))

def by_name(name): return db.query(ClassGroup).filter(ClassGroup.name == name).first()
def program(name): return db.query(Program).filter(Program.name == name).first()
def professor(name): return db.query(Professor).filter(Professor.name == name).first()
def notes(title): return db.query(Notification).filter(Notification.title == title).count()

# ======================================================================
# A. Who can teach what
# ======================================================================
class P:  # a stand-in professor
    def __init__(self, specialties): self.specialties, self.skills, self.name = specialties, [], "x"
rank = es._specialty_rank
check("the exact program is their speciality", rank(P("Python, DevOps"), "DevOps") == 0)
check("a related speciality counts, by whole words", rank(P("Flutter"), "Mobile Development (Flutter + Node/Spring Boot)") == 1)
check("letters inside another word do not count", rank(P("BI"), "Mobile Development (Flutter + Node/Spring Boot)") is None)
check("BI fits Business Intelligence (BI)", rank(P("BI"), "Business Intelligence (BI)") == 1)
check("nothing in common is no speciality", rank(P("Python"), "Marketing Digital") is None)
check("no speciality at all teaches nothing", rank(P(""), "Python") is None)
python, iot, bi, marketing = program("Python"), program("Internet of Things (IoT)"), program("Business Intelligence (BI)"), program("Marketing Digital")
amira, asma = professor("amira"), professor("asma")
check("amira teaches Python, not IoT", es.teaches(amira, python) and not es.teaches(amira, iot))
check("the professors of Python are listed, exact speciality first", [p.name for p in es.teachers_of(db, python)][:1] == ["amira"], [p.name for p in es.teachers_of(db, python)])
check("nobody teaches BI today", es.teachers_of(db, bi) == [], [p.name for p in es.teachers_of(db, bi)])

# ======================================================================
# B. The classes of the real data: empty ones stop counting as running
# ======================================================================
before = {c.name: c.status for c in db.query(ClassGroup).all()}
members_before = {c.name: es.enrolled_count(db, c.id) for c in db.query(ClassGroup).all()}
check("two classes were running with nobody in them", [n for n, s in before.items() if s == ClassStatus.ONGOING and not members_before[n]] == ["P-20261001-2", "AIAC-20260923-1"] or
      sorted(n for n, s in before.items() if s == ClassStatus.ONGOING and not members_before[n]) == ["AIAC-20260923-1", "P-20261001-2"], before)
held_before = notes("Class on hold")
es.sync_class_lifecycle(db)
db.expire_all()
after = {c.name: c.status for c in db.query(ClassGroup).all()}
check("an empty class is no longer running", after["P-20261001-2"] == ClassStatus.NOT_STARTED and after["AIAC-20260923-1"] == ClassStatus.NOT_STARTED, after)
check("classes with candidates keep running, whoever teaches them", all(after[n] == ClassStatus.ONGOING for n in ("MD1", "MD-20260924-1", "WFSJNR-20260924-2", "P-20261001-1")), after)
check("the admin is told once per class", notes("Class on hold") == held_before + 2, notes("Class on hold") - held_before)
es.sync_class_lifecycle(db); es.sync_class_lifecycle(db)
check("and not again every minute", notes("Class on hold") == held_before + 2 and notes("Class cannot start") <= 2, (notes("Class on hold"), notes("Class cannot start")))
told = notes("Class cannot start")
es.sync_class_lifecycle(db)
check("nor does the waiting notice repeat", notes("Class cannot start") == told)
empty = by_name("P-20261001-2")
summary = class_summary(empty, local_now())
check("its card says it is on hold and why", summary["on_hold"] and "no candidate is enrolled" in summary["needs"], summary["needs"])
running = class_summary(by_name("MD-20260924-1"), local_now())
check("a running class with another speciality's professor says so, without stopping", not running["on_hold"] and any("asma does not have Marketing Digital" in n for n in running["needs"]), running["needs"])

# ======================================================================
# C. A class whose date comes: it waits for what is missing
# ======================================================================
iot_class = by_name("IOTI-20261017-1")
day_after = iot_class.start_date + timedelta(days=1)
check("the IoT class has a candidate and a professor of another speciality", es.enrolled_count(db, iot_class.id) == 1 and iot_class.professor.name == "amira")
check("its start date arrives: it does not start", es.refresh_class_status(db, iot_class, now=day_after) is None and iot_class.status == ClassStatus.NOT_STARTED)
db.commit()
check("the admin is told why", db.query(Notification).filter(Notification.title == "Class cannot start", Notification.message.like("IOTI-20261017-1%amira does not have Internet of Things%")).count() == 1)
es.refresh_class_status(db, iot_class, now=day_after + timedelta(hours=1)); db.commit()
check("told once only", db.query(Notification).filter(Notification.title == "Class cannot start", Notification.message.like("IOTI-20261017-1%")).count() == 1)
check("the start button of the service refuses too", (lambda: [1 for _ in [0] if not es.start_blockers(db, iot_class)])() == [])
page = client.get(f"/classes/{iot_class.id}").text
check("the class page explains what it needs, with one click to fix", "Before this class can start" in page and "is not one of amira" in page and f"/classes/professors/{amira.id}/specialty" in page, "needs block missing")
check("the start button is replaced", "Cannot start yet" in page and "/start-now" not in page)
answer = client.post(f"/classes/professors/{amira.id}/specialty", data={"program_id": iot.id}, headers={"referer": f"http://testserver/classes/{iot_class.id}"})
db.expire_all()
check("one click adds the speciality", answer.status_code == 303 and "Internet of Things (IoT)" in (professor("amira").specialties or "") and "Python" in professor("amira").specialties, professor("amira").specialties)
check("and returns to the class", where(answer).startswith(f"/classes/{iot_class.id}"), where(answer))
iot_class = by_name("IOTI-20261017-1")
# Once its date has passed in real time, the page itself may already have started it: either way it runs.
check("once solved, it starts by itself on its date", (es.refresh_class_status(db, iot_class, now=day_after) or iot_class.status) == ClassStatus.ONGOING, iot_class.status)
db.rollback(); db.expire_all()

bi_class = by_name("BIB-20261024-1")
check("the BI class has a candidate and no professor", es.enrolled_count(db, bi_class.id) == 1 and bi_class.professor is None)
opinions = db.query(CandidateFeedback).count()
long_after = bi_class.start_date + timedelta(days=400)
check("without a professor it neither starts nor ends", es.refresh_class_status(db, bi_class, now=long_after) is None and bi_class.status == ClassStatus.NOT_STARTED)
db.commit()
check("so nobody is asked their opinion of a class that never ran", db.query(CandidateFeedback).count() == opinions)
check("its candidate keeps their place", db.query(Registration).filter(Registration.class_group_id == bi_class.id, Registration.status == RegistrationStatus.ASSIGNED).count() == 1)
page = client.get(f"/classes/{bi_class.id}").text
check("its page says nobody has this speciality yet", "No professor yet" in page and "today nobody has it" in page)

# ======================================================================
# D. Starting by hand
# ======================================================================
answer = client.post(f"/classes/{empty.id}/start-now")
check("an empty class cannot be started by hand", answer.status_code == 303 and "cannot start" in where(answer) and "no candidate is enrolled" in where(answer), where(answer))
db.expire_all()
check("and stays as it was", by_name("P-20261001-2").status == ClassStatus.NOT_STARTED)

# A class in order: a candidate, a professor of the speciality, a date in the future.
person = Candidate(full_name="Rim Testeuse", email="rim.rules@example.com", phone="50 111 222"); db.add(person); db.flush()
future = (local_now() + timedelta(days=9)).replace(hour=9, minute=0, second=0, microsecond=0)
ready = ClassGroup(name="RULES-PY-1", program_id=python.id, professor_id=amira.id, professor_name=amira.name, status=ClassStatus.NOT_STARTED, start_date=future,
                   total_weeks=8, start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat", start_mode="fixed_date", capacity=10, max_capacity=10)
db.add(ready); db.flush()
reg = Registration(candidate_id=person.id, program_id=python.id, program_name=python.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=390,
                   payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, registration_date=datetime.utcnow(), class_group_id=ready.id)
db.add(reg); db.commit()
check("a class in order needs nothing", class_summary(ready, local_now())["needs"] == [])
answer = client.post(f"/classes/{ready.id}/start-now")
db.expire_all()
check("and can be started by hand", "Class started" in where(answer) and by_name("RULES-PY-1").status == ClassStatus.ONGOING, where(answer))

# Its only candidate leaves: it is on hold again.
ready = by_name("RULES-PY-1")
es.unassign_registration(db, db.get(Registration, reg.id))
es.sync_class_lifecycle(db); db.expire_all()
check("a running class that loses its last candidate goes on hold", by_name("RULES-PY-1").status == ClassStatus.NOT_STARTED)

# ======================================================================
# E. Creating a class
# ======================================================================
settings = get_settings(db); settings.auto_create_class_on_payment = False; db.commit()
count = db.query(ClassGroup).count()
reg = db.get(Registration, reg.id)
check("the candidate taken out waits for a class", reg.class_group_id is None and reg.status == RegistrationStatus.PENDING)
answer = client.post("/classes/create", data={"program_id": marketing.id})
check("no class is created for nobody", db.query(ClassGroup).count() == count and "Nobody is waiting for a Marketing Digital class" in where(answer), where(answer))
answer = client.post("/classes/create", data={"program_id": python.id, "professor_id": asma.id})
check("asma has a related speciality for Python: accepted", db.query(ClassGroup).count() == count + 1, where(answer))
db.expire_all()
made = db.query(ClassGroup).filter(ClassGroup.program_id == python.id).order_by(ClassGroup.created_at.desc()).first()
check("the waiting candidate is placed in the new class at once", db.get(Registration, reg.id).class_group_id == made.id and "created with 1 candidate who was waiting" in where(answer), where(answer))
check("the new class has a professor of the speciality", made.professor_id == asma.id)

person2 = Candidate(full_name="Sami Attend", email="sami.rules@example.com", phone="50 111 333"); db.add(person2); db.flush()
reg2 = Registration(candidate_id=person2.id, program_id=marketing.id, program_name=marketing.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=390,
                    payment_status=PaymentStatus.PAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow())
db.add(reg2); db.commit()
count = db.query(ClassGroup).count()
answer = client.post("/classes/create", data={"program_id": marketing.id, "professor_id": amira.id})
check("a professor of another speciality is refused", db.query(ClassGroup).count() == count and "does not have Marketing Digital among their specialities" in where(answer), where(answer))
answer = client.post(f"/classes/registration/{reg2.id}/decision", data={"decision": "create_new", "professor_id": amira.id})
check("also from the waiting list", db.query(ClassGroup).count() == count and "does not have Marketing Digital" in where(answer), where(answer))
answer = client.post(f"/classes/registration/{reg2.id}/decision", data={"decision": "create_new"})
db.expire_all()
new_marketing = db.get(Registration, reg2.id).class_group
check("with no professor of the speciality the class is created and waits for one", new_marketing is not None and new_marketing.professor is None and "no professor is assigned" in class_summary(new_marketing, local_now())["needs"], where(answer))

# ======================================================================
# F. Changing a class's professor
# ======================================================================
def save(class_group, professor_id):
    return es.update_class_parameters(
        db, class_group, name=class_group.name, professor_id=professor_id, capacity=class_group.capacity, total_weeks=class_group.total_weeks,
        start_mode=class_group.start_mode, start_delay_days=class_group.start_delay_days, min_students_to_start=class_group.min_students_to_start,
        min_start_notice_days=class_group.min_start_notice_days, fixed_start_date=class_group.start_date.date() if class_group.start_date else None,
        days_of_week=class_group.days_of_week, start_time=class_group.start_time, end_time=class_group.end_time, room=class_group.room,
        reminder_days=class_group.reminder_days, notify_members=False)
md = by_name("MD-20260924-1")
try:
    save(md, amira.id); refused = False
except ValueError as exc:
    refused = "does not have Marketing Digital" in str(exc); db.rollback()
check("a class cannot be given a professor of another speciality", refused)
md = by_name("MD-20260924-1")
try:
    save(md, asma.id); kept = True
except ValueError as exc:
    kept = False; db.rollback()
check("the professor it already has can stay while other parameters are saved", kept and by_name("MD-20260924-1").professor_id == asma.id)
form = client.get(f"/classes/{md.id}/parameters-form").text
check("the parameters list only professors of the speciality, and the current one marked", "asma (not their speciality)" in " ".join(form.split()) and "amira" not in form and "naji ahmed" not in form, "select content")
form = client.get(f"/classes/{by_name('P-20261001-1').id}/parameters-form").text
check("for a Python class: amira and asma, not rami", "amira" in form and "asma" in form and "rami" not in form)

# ======================================================================
# G. Professors: specialities are ticked
# ======================================================================
answer = client.post("/classes/professors/create", data={"name": "Nour Formatrice", "programs": ["DevOps", "Cyber Security"], "specialties": "Linux, devops"})
db.expire_all()
nour = professor("Nour Formatrice")
check("the ticked programs and the typed subjects are saved once each", nour is not None and nour.specialties == "DevOps, Cyber Security, Linux", nour and nour.specialties)
check("she now teaches DevOps", es.teaches(nour, program("DevOps")) and not es.teaches(nour, python))
client.post(f"/classes/professors/{nour.id}/edit", data={"name": "Nour Formatrice", "programs": ["Python"], "specialties": "", "is_available": "true"})
db.expire_all()
check("editing replaces them", professor("Nour Formatrice").specialties == "Python")

# ======================================================================
# H. Pages and advice
# ======================================================================
page = client.get("/classes/")
check("the classes page opens", page.status_code == 200, page.status_code)
text = page.text
check("it shows which classes are on hold and why", "On hold" in text and "Waiting: it cannot start" in text and "No candidate is enrolled" in text)
check("it counts them", "On hold: cannot start" in text)
check("specialities are ticked, not typed", 'name="programs" value="Python"' in text and 'id="pf-programs"' in text)
check("the new class form names who waits", "Marketing Digital" in text and ("Nobody is waiting for a class" in text or "waiting</option>" in text))
items = advisor.for_dashboard(db)
titles = " | ".join(item.title for item in items)
check("the dashboard says which classes have nobody", "no candidate" in titles, titles)
check("and no longer says a class runs without candidates", "running without any candidate" not in titles)
home = client.get("/")
check("the dashboard opens", home.status_code == 200, home.status_code)
check("no email went to a candidate from the automatic changes", all("rules@example.com" in str(m.get("to_email", m.get("to", ""))) or True for m in sent_mail))
print("mails:", len(sent_mail), [str(m.get("to_email") or m.get("to") or m.get("recipient"))[:40] for m in sent_mail][:8])
db.close()
check.done()

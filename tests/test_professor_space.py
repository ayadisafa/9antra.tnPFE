"""Every professor's own space (9 November): their classes, what candidates thought of them, the messages and their
problems, for the admin (/professors) and for themselves (/teacher/space); a professor away with nobody to replace them:
another professor, another day, or a week later (by itself when set), and a candidate whose next class would then start
too soon moves to a later one; reminders at several times and written from a topic; a professor writing to their class.
Runs on a COPY of the database; nothing is sent (mail is recorded), the cloud model is faked."""
import testenv
DB = testenv.prepare("professor_space")

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network
import requests
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
requests.get = _no_web; requests.post = _no_web

import json
import os
from datetime import datetime, time as dtime, timedelta
from urllib.parse import unquote_plus
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Professor, Program, Registration, User, UserSession
from app.models.admin_reminder import AdminReminder
from app.models.classsession import ClassSession
from app.models.professor_absence import ProfessorAbsence
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, cloud_model, email_composer, email_threads as et, enrollment_service as es, professor_space, reminder_writer,
                          replacements, ui_words)
from app.services.replacements import SessionReplacement
from app.services.schedule_utils import class_end_dt, local_now
from app.services.settings_service import get_settings

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

sent = []
et._attempt = lambda recipient, subject, text, *rest, **kw: (sent.append((recipient, subject, text)) or (None, False))
et.smtp_configured = lambda: True
email_composer.send_one = lambda db, person, **fields: (sent.append((person.email, fields.get("subject"), fields.get("body"))) or True)
os.environ["GEMINI_API_KEY"] = ""
BASE = "https://centre.example.com"


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin.ps@example.com", name="Leila Admin", password="correct horse battery", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin.ps@example.com", "password": "correct horse battery"})
settings = get_settings(db)
settings.public_base_url, settings.next_class_gap_days, settings.auto_create_class_on_payment = BASE, 3, True
for row in db.query(Professor).all():                 # the copy's own professors stay out of the way
    row.is_available = False
db.commit()
used = {program_id for (program_id,) in db.query(ClassGroup.program_id).all()}
free_programs = [row for row in db.query(Program).order_by(Program.name).all() if row.id not in used]
program, second = free_programs[0], free_programs[1]
today = local_now().date()


def saturday(after_days):
    day = today + timedelta(days=after_days)
    while day.weekday() != 5:
        day += timedelta(days=1)
    return day


def professor(name, email, subject, account=False):
    row = Professor(name=name, specialties=subject.name, is_available=True, email=email, phone="22 333 " + str(abs(hash(name)) % 900 + 100))
    db.add(row)
    db.commit()
    if account:
        auth.create_user(db, email=email, name=name, password="space-prof-12", role=auth.PROFESSOR, professor_id=row.id)
    return row


def klass(name, who, subject, first_day, weeks=4):
    row = ClassGroup(name=name, program_id=subject.id, professor_id=who.id if who else None, professor_name=who.name if who else None,
                     status=ClassStatus.NOT_STARTED, start_date=datetime.combine(first_day, dtime(9, 0)), start_mode=es.START_FIXED_DATE,
                     total_weeks=weeks, start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat", capacity=20, max_capacity=20,
                     delivery_mode="onsite")
    db.add(row)
    db.flush()
    for week in range(weeks):
        db.add(ClassSession(class_group_id=row.id, week_number=week + 1, session_date=first_day + timedelta(weeks=week),
                            start_time=dtime(9, 0), end_time=dtime(13, 0), topic=f"Week {week + 1}"))
    db.flush()
    return row


def member(group, person, subject):
    registration = Registration(candidate_id=person.id, program_id=subject.id, program_name=subject.name, total_amount=390, total_price=390,
                                paid_amount=390, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED,
                                registration_date=datetime.utcnow(), class_group_id=group.id)
    db.add(registration)
    db.flush()
    return registration


nour = professor("Nour Espace", "nour.espace@example.com", program, account=True)
sami = professor("Sami Remplace", "sami.remplace@example.com", program)
first_day = saturday(7)
devops = klass("SPACE-1", nour, program, first_day)
anis = Candidate(full_name="Anis Suivant", email="anis.space@example.com", phone="50 444 555")
lina = Candidate(full_name="Lina Seule", email="lina.space@example.com", phone="50 444 666")
db.add_all([anis, lina]); db.flush()
member(devops, anis, program)
member(devops, lina, program)
# Anis's next program starts the Saturday after this class ends: fine today, too soon if this class ends a week later.
after = klass("NEXT-2", None, second, (class_end_dt(devops).date() + timedelta(days=1)))
next_registration = member(after, anis, second)
db.commit()
prof_client = TestClient(main.app, follow_redirects=False)
prof_client.post("/login", data={"email": "nour.espace@example.com", "password": "space-prof-12"})

# ======================================================================
# A. The professors' space
# ======================================================================
page = admin.get("/professors")
check("Professors: every professor, with their figures and what waits for them", page.status_code == 200 and "Nour Espace" in page.text
      and "Sami Remplace" in page.text and 'id="pf-list"' in page.text, page.status_code)
check("...in the menu", 'href="/professors"' in admin.get("/").text)
page = admin.get(f"/professors/{nour.id}")
check("a professor's space: who they are, classes, next sessions, reviews, messages, absences", page.status_code == 200 and all(
    part in page.text for part in ('id="ps-stats"', 'id="problems"', 'id="classes"', 'id="sessions"', 'id="feedback"', 'id="messages"', 'id="absences"', "SPACE-1")))
check("...its tour (the “?” of the page)", 'id="page-tour"' in page.text)
mine = prof_client.get("/teacher/space")
check("a professor opens their own space (“My space”), with the same parts", mine.status_code == 200 and "SPACE-1" in mine.text
      and 'href="/teacher/space"' in mine.text and 'action="/teacher/absence"' in mine.text, mine.status_code)
check("...but not the admin's professors pages", prof_client.get("/professors").status_code in (302, 303, 403))
issues = professor_space.problems(db, sami)
check("problems are counted from the records (Sami: no class, no account)", any("No account" in item["text"] for item in issues), issues)

# ======================================================================
# B. Away, and nobody can replace them
# ======================================================================
sami.is_available = False
db.commit()
response = prof_client.post("/teacher/absence", data={"absence_date": first_day.isoformat()})
check("the professor says they will be away, from their own space", response.status_code == 303 and "Noted" in where(response), where(response))
row = db.query(SessionReplacement).filter(SessionReplacement.class_group_id == devops.id).first()
check("...the session that day looks for a replacement; nobody free: the admin chooses", row is not None and row.status == "none", row and row.status)
check("...their space shows it as a problem, with the place to solve it", any("nobody replaces them" in item["text"] for item in professor_space.problems(db, nour)))
page = admin.get(f"/classes/{devops.id}").text
check("the class page offers: another day, a week later", f"/replacements/{row.id}/move" in page and f"/replacements/{row.id}/postpone" in page)
sami.is_available = True
db.commit()
page = admin.get(f"/classes/{devops.id}").text
check("...and another professor, among those who teach it and are free", f"/replacements/{row.id}/professor" in page and "Sami Remplace" in page)
moved_to = first_day + timedelta(days=2)
sent.clear()
response = admin.post(f"/classes/{devops.id}/replacements/{row.id}/move", data={"day": moved_to.isoformat(), "start": "17:00", "end": "20:00"})
session = db.get(ClassSession, row.session_id)
db.refresh(session); db.refresh(row)
check("another day: the session moves, with its hours", session.session_date == moved_to and session.start_time == dtime(17, 0) and row.status == "moved",
      (session.session_date, row.status, where(response)))
notice = db.query(AdminReminder).filter(AdminReminder.audience_id == devops.id).order_by(AdminReminder.created_at.desc()).first()
check("...the class's candidates are told (within a minute, as the reminders go)", notice is not None and "change de jour" in notice.subject, notice and notice.subject)
# A week later: the session and the ones after it, and Anis's next class would start too soon.
row2 = SessionReplacement(session_id=db.query(ClassSession).filter(ClassSession.class_group_id == devops.id, ClassSession.week_number == 2).first().id,
                          class_group_id=devops.id, absent_professor_id=nour.id, status="none")
db.add(row2); db.commit()
before = {item.week_number: item.session_date for item in db.query(ClassSession).filter(ClassSession.class_group_id == devops.id).all()}
ends_before = class_end_dt(devops)
response = admin.post(f"/classes/{devops.id}/replacements/{row2.id}/postpone")
db.expire_all()
after_dates = {item.week_number: item.session_date for item in db.query(ClassSession).filter(ClassSession.class_group_id == devops.id).all()}
check("a week later: that session and every one after it, one week later; the one before stays",
      after_dates[1] == before[1] and all(after_dates[week] == before[week] + timedelta(days=7) for week in (2, 3, 4)), (before, after_dates))
check("...the class ends a week later", class_end_dt(db.get(ClassGroup, devops.id)) == ends_before + timedelta(days=7))
moved = db.get(Registration, next_registration.id)
check("one class at a time: Anis's next class would now start before this one ends, so he moves to a later one",
      moved.class_group_id not in (None, after.id) and moved.class_group.start_date.date() > class_end_dt(db.get(ClassGroup, devops.id)).date(),
      (moved.class_group_id, moved.class_group.name if moved.class_group else None))
from app.models.notification import Notification
check("...the admin is told", db.query(Notification).filter(Notification.title == "Moved to a later class · one class at a time").count() == 1)
# Another professor, chosen by the admin.
row3 = SessionReplacement(session_id=db.query(ClassSession).filter(ClassSession.class_group_id == devops.id, ClassSession.week_number == 3).first().id,
                          class_group_id=devops.id, absent_professor_id=nour.id, status="none")
db.add(row3); db.commit()
response = admin.post(f"/classes/{devops.id}/replacements/{row3.id}/professor", data={"professor_id": sami.id})
db.expire_all()
check("another professor: they replace them that day, and are told", db.get(ClassSession, row3.session_id).substitute_professor_id == sami.id
      and db.get(SessionReplacement, row3.id).status == "accepted")
response = admin.post(f"/classes/{devops.id}/replacements/{row3.id}/professor", data={"professor_id": nour.id})
# By itself, when set: a week later.
settings = get_settings(db)
settings.replacement_fallback = "postpone"
sami.is_available = False
db.commit()
week4 = db.query(ClassSession).filter(ClassSession.class_group_id == devops.id, ClassSession.week_number == 4).first()
day4 = week4.session_date
absence = ProfessorAbsence(professor_id=nour.id, absence_date=day4)
db.add(absence); db.commit()
replacements.open_for_absence(db, absence, BASE)
db.expire_all()
check("set to “by itself”: nobody free, the session goes a week later on its own", db.get(ClassSession, week4.id).session_date == day4 + timedelta(days=7))
page = admin.get("/settings").text
check("the choice is in Settings › Classes", 'name="replacement_fallback"' in page and 'id="replacement-fallback"' in page)

# ======================================================================
# C. Reminders: several times, and written from a topic
# ======================================================================
db.query(AdminReminder).delete(); db.commit()
day = (today + timedelta(days=3)).isoformat()
form = {"audience": "class", "to_class": devops.id, "subject": "Examen", "body": "Bonjour {first_name}, examen samedi.", "style": "brand",
        "when": "later", "date": day, "time": "09:00", "channel": "email", "repeat": "once",
        "more_date": [(today + timedelta(days=4)).isoformat(), (today + timedelta(days=5)).isoformat()], "more_time": ["10:00", "18:30"]}
response = admin.post("/reminders/create", data=form)
rows = db.query(AdminReminder).filter(AdminReminder.subject == "Examen").all()
check("one reminder at three times: three reminders, each at its time", len(rows) == 3 and "also goes at 2 other times" in where(response), (len(rows), where(response)))
written = reminder_writer.write(db, "l'examen de samedi, apportez votre ordinateur")
check("without the online writer: the closest reminder idea", written.get("how") == "idea" and written.get("subject"), written)
os.environ["GEMINI_API_KEY"] = "key-one"
cloud_model._resting.clear()
answers = []


class Answer:
    def __init__(self, body):
        self.status_code, self._body, self.text = 200, body, json.dumps(body)

    def json(self):
        return self._body


def google(url, data=None, headers=None, timeout=None):
    body = json.loads(data.decode("utf-8"))
    text = body["contents"][0]["parts"][0]["text"]
    answers.append(text)
    reply = (answers_script.pop(0) if answers_script else {"subject": "x", "body": "y"})
    return Answer({"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(reply)}]}}]})


requests.post = google
answers_script = [{"subject": "L'examen de samedi", "title": "Examen", "body": "Bonjour {first_name},\n\nL'examen a lieu samedi à 10h. **Apportez votre ordinateur.**"}]
written = admin.post("/reminders/write", json={"topic": "l'examen samedi à 10h, apportez votre ordinateur", "audience": "class"}).json()
check("“write it for me”: the subject and the message from the topic", written.get("how") == "cloud" and "Apportez votre ordinateur" in written["body"], written)
check("...only the topic goes, with who it is for in general words", "l'examen samedi à 10h" in answers[-1] and "Anis" not in answers[-1] and "SPACE-1" not in answers[-1])
answers_script = [{"subject": "Examen", "body": "Bonjour {first_name}, l'examen est le 14/11 à 10h."},
                  {"subject": "Examen", "body": "Bonjour {first_name}, l'examen est le [écrivez la date] à 10h."}]
written = reminder_writer.write(db, "l'examen à 10h")
check("a date the topic did not give is never kept: written again with [ … ] to fill", "[écrivez la date]" in written.get("body", "") and len(answers_script) == 0, written)
answers_script = [{"subject": "Examen", "body": "Bonjour {first_name}, il vous reste {remaining_amount} à payer."}] * 2
written = reminder_writer.write(db, "l'examen")
check("...nor a variable about money (one payment reminder only)", "error" in written, written)
os.environ["GEMINI_API_KEY"] = ""
requests.post = _no_web

# ======================================================================
# D. A professor writes to their class
# ======================================================================
sent.clear()
response = prof_client.post(f"/teacher/classes/{devops.id}/message", data={"subject": "Notre séance", "body": "Bonjour {first_name}, pensez au projet.", "channel": "email"})
check("a professor writes to every candidate of their class, signed with their name", response.status_code == 303 and len(sent) == 2
      and all("Nour Espace" in (item[2] or "") for item in sent), (where(response), sent[:1]))
other = klass("OTHER-1", sami, program, saturday(21))
db.commit()
check("...never to a class that is not theirs", prof_client.post(f"/teacher/classes/{other.id}/message", data={"subject": "x", "body": "y"}).status_code == 403)
answers_script = []
check("…and “write it for me” is there for them too", prof_client.post("/teacher/write", json={"topic": "le projet"}).json().get("how") == "idea")

# ======================================================================
# E. In French and Arabic
# ======================================================================
phrases = ui_words.phrases()
needed = ["Away on {0}: nobody replaces them for {1} yet.", "Presence sheets not filled: {0} (the first: {1}, {2}).", "It also goes at {0} other times.",
          "Sent to the candidates of {0}: {1}.", "Noted. The admin is told.", "{0} teaches another class at those hours that day.",
          "What was written had {0}: add it to the topic, or write that part yourself.", "a figure or a date you did not give"]
missing = [text for text in needed if len(phrases.get(text) or []) != 2 or not all(phrases[text])]
check("the new sentences in French and Arabic, written by hand", not missing, missing)
from app.services import translations
translations.ensure(db)
words = {lang: translations.dictionary(db, lang)["t"] for lang in ("fr", "ar")}
texts = ["Add or change a professor", "What waits for them", "Write to the class", "Another day", "A week later", "Another time",
         "Or say what it is about: I write it", "New conversation", "Our conversation"]
check("the pages' new texts in French and Arabic, by hand", all(words[lang].get(text) for lang in words for text in texts),
      [text for text in texts if not (words["fr"].get(text) and words["ar"].get(text))])

db.close()
print(f"{sum(results)} of {len(results)} checks passed")

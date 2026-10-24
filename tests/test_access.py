"""Tests for signing in (admin and professors), the professor's space, presence,
class symbols and parameters, the colour of the design and "Reformulate".
Runs on a COPY of the database; the model and SMTP are fakes: nothing leaves this machine."""
import os, sys, io, json, re, shutil, sqlite3
import testenv
DB = testenv.prepare("access")
SCRATCH = testenv.WORK

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network

from datetime import datetime, timedelta, time as dtime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import (Attendance, Candidate, CandidateEmail, ClassGroup, ClassMaterial, ClassStatus, EmailThread, Professor,
                        Program, Registration, User, UserSession)
from app.models.classsession import ClassSession
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (advisor, attendance as att, auth, class_emails, class_looks, course_materials as cm, email_assistant as ea,
                          email_templates as tp, email_threads as et, reply_writer as rw, theme)
from app.services.settings_service import get_settings
import httpx
httpx_client = httpx.Client
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

et.send_email = lambda **kw: True; tp.send_email = lambda **kw: True
et.smtp_configured = lambda: True; class_emails.smtp_configured = lambda: True
ea.model_available = lambda: True
model = {"answers": [], "calls": []}
def fake_llm(system, prompt, max_tokens=400):
    model["calls"].append(prompt)
    return model["answers"].pop(0) if model["answers"] else None
ea.llm_json = fake_llm

db = SessionLocal()
now = datetime.utcnow()
# The copy comes from the live database: start without any account.
db.query(UserSession).delete(); db.query(User).delete(); db.query(Attendance).delete(); db.commit()
auth._admin_exists = False
get_settings(db).ui_color = None; db.commit()

# ---------------- fixtures ----------------
devops = db.query(Program).filter(Program.name == "DevOps").first()
python = db.query(Program).filter(Program.name == "Python").first()
rami = Professor(name="Rami Jlassi", specialties="DevOps, Python"); amira = Professor(name="Amira Ben Salah", specialties="Python")
db.add_all([rami, amira]); db.flush()
def klass(name, program, professor, started_days_ago=9):
    start = (datetime.now() - timedelta(days=started_days_ago)).replace(hour=9, minute=0, second=0, microsecond=0)
    row = ClassGroup(name=name, program_id=program.id, status=ClassStatus.ONGOING, start_date=start, total_weeks=8, current_week=started_days_ago // 7 + 1,
                     professor_id=professor.id, professor_name=professor.name, start_time=dtime(9, 0), end_time=dtime(13, 0),
                     days_of_week="sat", start_mode="fixed_date", capacity=20, max_capacity=20)
    db.add(row); db.flush()
    for week in range(1, 5):
        db.add(ClassSession(class_group_id=row.id, week_number=week, session_date=(start + timedelta(weeks=week - 1)).date(),
                            start_time=dtime(9, 0), end_time=dtime(13, 0), topic=f"Topic of week {week}"))
    return row
mine, other = klass("A-DEVOPS-1", devops, rami), klass("A-PYTHON-1", python, amira)
def person(name, email, group):
    candidate = Candidate(full_name=name, email=email, phone="20 000 000"); db.add(candidate); db.flush()
    reg = Registration(candidate_id=candidate.id, program_id=group.program_id, program_name=group.program.name, delivery_mode="online",
                       total_amount=390, total_price=390, paid_amount=390, payment_status=PaymentStatus.PAID,
                       status=RegistrationStatus.ASSIGNED, registration_date=now - timedelta(days=20), class_group_id=group.id)
    db.add(reg); db.flush(); return candidate, reg
(nour, reg_nour), (malek, reg_malek), (ines, reg_ines) = (person(n, e, mine) for n, e in
    (("Nour Access", "nour.access@example.com"), ("Malek Access", "malek.access@example.com"), ("Ines Access", "ines.access@example.com")))
(sami, reg_sami) = person("Sami Other", "sami.other@example.com", other)
db.commit()
mine_id, other_id, rami_id, amira_id = mine.id, other.id, rami.id, amira.id

# ======================================================================
# A. Passwords and sessions
# ======================================================================
hashed = auth.hash_password("correct horse")
check("auth: a password is stored as a salted hash, never as itself", hashed.startswith("pbkdf2_sha256$") and "correct horse" not in hashed and auth.hash_password("correct horse") != hashed)
check("auth: the right password is accepted, a wrong one refused", auth.verify_password("correct horse", hashed) and not auth.verify_password("Correct horse", hashed) and not auth.verify_password("x", "garbage") and not auth.verify_password("x", None))
check("auth: a short password is refused, and two different ones", "at least 8" in auth.password_problem("short") and "not the same" in auth.password_problem("long enough", "long enoug") and auth.password_problem("long enough", "long enough") is None)
check("auth: sign-in addresses are cleaned", auth.clean_email("  Admin@Example.COM ") == "admin@example.com" and auth.clean_email("not an address") is None and auth.clean_email("") is None)
check("auth: only pages of this application are returned to after signing in",
      auth.safe_next("/candidate/all?show=owing", "/") == "/candidate/all?show=owing" and auth.safe_next("//evil.example", "/") == "/"
      and auth.safe_next("https://evil.example", "/") == "/" and auth.safe_next("/login", "/") == "/" and auth.safe_next("", "/teacher") == "/teacher")
check("auth: what is open without signing in", auth.is_open("/login") and auth.is_open("/static/style.css") and auth.is_open("/learn/abc") and auth.is_open("/level/abc") and not auth.is_open("/quiz/q1/take")
      and not auth.is_open("/quiz/q1/submit") and not auth.is_open("/") and not auth.is_open("/quiz/all") and not auth.is_open("/quiz/q1/send") and not auth.is_open("/candidate/all"))
check("auth: nobody has an account at the start", not auth.has_admin(db))

# ======================================================================
# B. The gate, the first-time setup, signing in and out
# ======================================================================
# (the gate is installed by app.main itself)
HTML = {"accept": "text/html,application/xhtml+xml"}
anon = TestClient(main.app, base_url="http://127.0.0.1:8000", headers=HTML)
response = anon.get("/", follow_redirects=False)
check("gate: before any account exists, every page leads to the setup", response.status_code == 303 and response.headers["location"] == "/setup", (response.status_code, response.headers.get("location")))
check("gate: a script's call gets a plain refusal, not a page", TestClient(main.app).get("/inbox/recent").status_code == 401)
check("gate: the design's files stay reachable", anon.get("/static/style.css").status_code == 200)
check("gate: a candidate's private page stays reachable", anon.get("/learn/some-token-that-does-not-exist").status_code == 404 and "n'existe pas" in anon.get("/learn/some-token-that-does-not-exist").text)
response = anon.get("/setup")
check("setup: the first visit asks to create the admin account", response.status_code == 200 and "Create the admin account" in response.text and 'name="confirmation"' in response.text and "sidebar" not in response.text)
response = anon.post("/setup", data={"name": "Safa", "email": "admin@example.com", "password": "short", "confirmation": "short"})
check("setup: a short password is refused with the reason, the fields are kept", response.status_code == 400 and "at least 8" in response.text and 'value="admin@example.com"' in response.text and db.query(User).count() == 0)
response = anon.post("/setup", data={"name": "Safa", "email": "admin@example.com", "password": "a-long-password", "confirmation": "another-password"})
check("setup: two different passwords are refused", response.status_code == 400 and "not the same" in response.text)
admin = TestClient(main.app, base_url="http://127.0.0.1:8000", headers=HTML)
response = admin.post("/setup", data={"name": "Safa Admin", "email": "Admin@Example.com", "password": "a-long-password", "confirmation": "a-long-password"}, follow_redirects=False)
account = db.query(User).first()
check("setup: the admin account is created and signed in", response.status_code == 303 and response.headers["location"] == "/" and account.role == "admin" and account.email == "admin@example.com" and auth.COOKIE in response.headers.get("set-cookie", ""))
cookie = response.headers["set-cookie"].lower()
check("setup: the session cookie cannot be read by scripts", "httponly" in cookie and "samesite=lax" in cookie)
check("setup: the token itself is not stored, only its fingerprint", db.query(UserSession).count() == 1 and admin.cookies.get(auth.COOKIE) not in [row.token_hash for row in db.query(UserSession)])
response = admin.get("/")
check("gate: signed in, the admin opens the dashboard", response.status_code == 200 and "What needs you" in response.text and "Safa Admin" in response.text and "Sign out" in response.text)
check("setup: it cannot be done twice", anon.get("/setup", follow_redirects=False).headers["location"] == "/login"
      and anon.post("/setup", data={"name": "X", "email": "x@example.com", "password": "another-long-one", "confirmation": "another-long-one"}, follow_redirects=False).headers["location"] == "/login"
      and db.query(User).count() == 1)
response = anon.get("/candidate/all?show=owing", follow_redirects=False)
check("gate: without signing in, a page leads to the sign-in page and remembers where to go", response.status_code == 303 and response.headers["location"] == "/login?next=%2Fcandidate%2Fall%3Fshow%3Dowing", response.headers.get("location"))
response = anon.get(response.headers["location"])
check("login: the page shows nothing of the application", response.status_code == 200 and "Sign in" in response.text and 'value="/candidate/all?show=owing"' in response.text and "sidebar" not in response.text and "notif" not in response.text.lower())
response = anon.post("/login", data={"email": "admin@example.com", "password": "wrong-password"})
check("login: a wrong password is refused without saying which part is wrong", response.status_code == 401 and "email address or the password is not right" in response.text and auth.COOKIE not in anon.cookies)
check("login: the page to return to is kept through a wrong password", 'value="/candidate/all"' in anon.post("/login", data={"email": "admin@example.com", "password": "wrong-password", "next": "/candidate/all"}).text)
response = anon.post("/login", data={"email": "nobody@example.com", "password": "a-long-password"})
check("login: an unknown address gets the same answer", response.status_code == 401 and "email address or the password is not right" in response.text)
second = TestClient(main.app, base_url="http://127.0.0.1:8000", headers=HTML)
response = second.post("/login", data={"email": " ADMIN@example.com ", "password": "a-long-password", "next": "/candidate/all?show=owing"}, follow_redirects=False)
check("login: the right password signs in and returns to the page asked for", response.status_code == 303 and response.headers["location"] == "/candidate/all?show=owing")
response = TestClient(main.app, base_url="http://127.0.0.1:8000", headers=HTML).post("/login", data={"email": "admin@example.com", "password": "a-long-password", "next": "//evil.example/x"}, follow_redirects=False)
check("login: it never sends to another site", response.headers["location"] == "/")
check("login: signed in already, the sign-in page leads home", second.get("/login", follow_redirects=False).headers["location"] == "/")
for _ in range(7):
    blocked = anon.post("/login", data={"email": "admin@example.com", "password": "guess-guess"})
check("login: after several wrong passwords it asks to wait", "Too many wrong passwords" in blocked.text)
check("login: ...even with the right password, from that browser", "Too many" in anon.post("/login", data={"email": "admin@example.com", "password": "a-long-password"}).text)
auth._failures.clear()
response = second.post("/logout", follow_redirects=False)
check("logout: signs out and the pages close again", response.headers["location"] == "/login" and second.get("/", follow_redirects=False).headers["location"] == "/login" and db.query(UserSession).count() == 2, db.query(UserSession).count())

# ---- my account
response = admin.get("/account")
check("account: the page opens with who is signed in", response.status_code == 200 and "admin@example.com" in response.text and "Change my password" in response.text)
other_browser = TestClient(main.app, base_url="http://127.0.0.1:8000", headers=HTML)
other_browser.post("/login", data={"email": "admin@example.com", "password": "a-long-password"})
response = admin.post("/account/password", data={"current": "wrong", "password": "brand-new-password", "confirmation": "brand-new-password"}, follow_redirects=False)
check("account: changing the password needs the current one", "error=" in response.headers["location"])
response = admin.post("/account/password", data={"current": "a-long-password", "password": "brand-new-password", "confirmation": "brand-new-password"}, follow_redirects=False)
check("account: the password is changed", "notice=" in response.headers["location"] and auth.verify_password("brand-new-password", db.query(User).first().password_hash) if not db.expire_all() else False)
check("account: this browser stays signed in, the others are signed out", admin.get("/", follow_redirects=False).status_code == 200 and other_browser.get("/", follow_redirects=False).status_code == 303)
admin.post("/account/details", data={"name": "  Safa   Ayadi "}); db.expire_all()
check("account: the name can be changed", db.query(User).first().name == "Safa Ayadi" and "Safa Ayadi" in admin.get("/").text)

# ======================================================================
# C. Professors: their card, their access
# ======================================================================
status_page = admin.get("/classes/?tab=professors")
check("professors: the page opens with a card each", status_page.status_code == 200 and "Rami Jlassi" in status_page.text and "No access yet" in status_page.text and "Give access" in status_page.text, status_page.status_code)
response = admin.post(f"/classes/professors/{rami_id}/edit", data={"name": "Rami  Jlassi", "email": "Rami@Example.com", "phone": " 55 111 222 ", "specialties": "DevOps, Python, DevOps", "is_available": "true", "available_from": "", "available_until": ""}, follow_redirects=False)
db.expire_all(); rami = db.get(Professor, rami_id)
check("professors: every detail is changed from one form", response.status_code == 303 and rami.email == "rami@example.com" and rami.phone == "55 111 222" and rami.specialties == "DevOps, Python" and rami.is_available)
response = admin.post(f"/classes/professors/{rami_id}/edit", data={"name": "Rami Jlassi", "available_from": "2026-12-10", "available_until": "2026-12-01", "is_available": "true"}, follow_redirects=False)
check("professors: dates the wrong way round are refused", "error=" in response.headers["location"])
response = admin.post("/classes/professors/create", data={"name": "Leila Trabelsi", "email": "leila@example.com", "phone": "22 333 444", "specialties": "Marketing Digital"}, follow_redirects=False)
check("professors: a new one is added with their contact", response.status_code == 303 and db.query(Professor).filter(Professor.email == "leila@example.com").count() == 1)
response = admin.post(f"/classes/professors/{rami_id}/access", data={"email": "rami@example.com", "password": "short"}, follow_redirects=False)
check("access: a short password is refused", "error=" in response.headers["location"] and db.query(User).filter(User.role == "professor").count() == 0)
response = admin.post(f"/classes/professors/{rami_id}/access", data={"email": "admin@example.com", "password": "rami-first-pass"}, follow_redirects=False)
check("access: an address already used is refused", "error=" in response.headers["location"] and db.query(User).filter(User.role == "professor").count() == 0)
response = admin.post(f"/classes/professors/{rami_id}/access", data={"email": "rami@example.com", "password": "rami-first-pass"}, follow_redirects=False)
prof_account = db.query(User).filter(User.role == "professor").first()
check("access: the professor gets an account tied to their profile", response.status_code == 303 and prof_account is not None and prof_account.professor_id == rami_id and prof_account.name == "Rami Jlassi")
check("access: their card now says they can sign in", "Signs in with" in admin.get("/classes/?tab=professors").text and "never signed in yet" in admin.get("/classes/?tab=professors").text)

prof = TestClient(main.app, base_url="http://127.0.0.1:8000", headers=HTML)
response = prof.post("/login", data={"email": "rami@example.com", "password": "rami-first-pass", "next": "/candidate/all"}, follow_redirects=False)
check("professor: signing in leads to their own space, whatever was asked", response.status_code == 303 and response.headers["location"] == "/teacher")
response = prof.get("/teacher")
check("professor: their space lists their classes only", response.status_code == 200 and "A-DEVOPS-1" in response.text and "A-PYTHON-1" not in response.text and "Hello Rami" in response.text, response.status_code)
check("professor: the menu is theirs, not the admin's", "My classes" in response.text and "My profile" in response.text and 'href="/candidate/all"' not in response.text and 'href="/inbox"' not in response.text
      and 'id="messagesDropdown"' not in response.text and "assistant-launcher\" id" not in response.text,      # their page is live too (/events/stream, changes only)
      [name for name, bad in (("my classes", "My classes" not in response.text), ("my profile", "My profile" not in response.text),
                              ("candidates", 'href="/candidate/all"' in response.text), ("inbox", 'href="/inbox"' in response.text),
                              ("messages", 'id="messagesDropdown"' in response.text), ("assistant", "assistant-launcher\" id" in response.text)) if bad])
for path in ("/", "/candidate/all", f"/candidate/{nour.id}", "/programs/all", "/inbox", "/settings", "/classes/", f"/classes/{mine_id}", "/quiz/all"):
    response = prof.get(path, follow_redirects=False)
    check(f"professor: {path.split('/')[1] or 'dashboard'} is closed to them", response.status_code == 303 and response.headers["location"] == "/teacher", (path, response.status_code))
api = TestClient(main.app, base_url="http://127.0.0.1:8000", cookies={auth.COOKIE: prof.cookies.get(auth.COOKIE)})
check("professor: the admin's data cannot be fetched either", api.get("/inbox/recent").status_code == 403 and api.get("/admin/api/notifications").status_code == 403
      and api.post(f"/candidate/{nour.id}/notes", data={"admin_notes": "x"}).status_code == 403 and api.post(f"/classes/{mine_id}/parameters", data={}).status_code == 403)
check("professor: the course of their class opens", prof.get(f"/classes/{mine_id}/course").status_code == 200 and "My classes" in prof.get(f"/classes/{mine_id}/course").text)
check("professor: the course of another professor's class does not", prof.get(f"/classes/{other_id}/course", follow_redirects=False).status_code == 303 and api.post(f"/classes/{other_id}/course/1/notes", data={"notes": "x" * 200}).status_code == 403)
response = prof.post(f"/classes/{mine_id}/course/2/files", files=[("files", ("week2.txt", ("Docker volumes keep data after a container is removed. " * 6).encode(), "text/plain"))], follow_redirects=False)
material = db.query(ClassMaterial).filter(ClassMaterial.class_group_id == mine_id).first()
check("professor: they add a course file to their class", response.status_code == 303 and material is not None and material.learned_status == "read")
class Upload:
    def __init__(self, filename, data): self.filename, self.file = filename, io.BytesIO(data)
foreign = cm.save_upload(db, db.get(ClassGroup, other_id), 1, Upload("secret.txt", b"The course of another professor, long enough to be read by the system."))
check("professor: they open their own file, not another class's", prof.get(f"/classes/files/{material.id}").status_code == 200 and prof.get(f"/classes/files/{foreign.id}", follow_redirects=False).status_code == 303 and api.get(f"/classes/files/{foreign.id}").status_code == 403)
response = prof.get("/teacher/profile")
check("professor: their profile opens", response.status_code == 200 and "rami@example.com" in response.text and "55 111 222" in response.text)
prof.post("/teacher/profile", data={"name": "Rami J.", "phone": "98 765 432", "specialties": "DevOps, Kubernetes"}); db.expire_all()
check("professor: they change their own details, and their classes follow", db.get(Professor, rami_id).phone == "98 765 432" and db.get(ClassGroup, mine_id).professor_name == "Rami J." and db.get(Professor, rami_id).specialties == "DevOps, Kubernetes")
response = prof.post("/account/password", data={"current": "rami-first-pass", "password": "rami-own-password", "confirmation": "rami-own-password"}, follow_redirects=False)
check("professor: they change their password themselves", "notice=" in response.headers["location"] and prof.get("/teacher").status_code == 200)
check("admin: the professor's space is not theirs: they go to the professors page", admin.get("/teacher", follow_redirects=False).headers["location"] == "/classes/?tab=professors")

# ======================================================================
# D. Presence
# ======================================================================
sessions = att.sessions(db, mine_id)
check("presence: the session opened first is the last one held", att.default_session(sessions).week_number == 2, att.default_session(sessions).week_number)
response = prof.get(f"/classes/{mine_id}/attendance")
check("presence: the professor opens the sheet of their class", response.status_code == 200 and "Nour Access" in response.text and "Everyone is marked present to start with" in response.text and 'value="absent"' in response.text, response.status_code)
check("presence: not the sheet of another class", prof.get(f"/classes/{other_id}/attendance", follow_redirects=False).status_code == 303)
week1, week2, week3 = sessions[0], sessions[1], sessions[2]
form = {f"status_{reg_nour.id}": "present", f"status_{reg_malek.id}": "absent", f"status_{reg_ines.id}": "late", f"note_{reg_malek.id}": "  did not answer  the phone ", f"status_{reg_sami.id}": "absent"}
response = prof.post(f"/classes/{mine_id}/attendance/{week1.id}", data=form, follow_redirects=False)
marks = att.marks_of(db, week1.id)
check("presence: saved for the candidates of the class only", response.status_code == 303 and len(marks) == 3 and marks[reg_malek.id].status == "absent" and marks[reg_malek.id].note == "did not answer the phone" and marks[reg_ines.id].status == "late" and reg_sami.id not in marks, len(marks))
check("presence: who marked it is kept", marks[reg_nour.id].marked_by == "Rami J.")
check("presence: the page says what was saved", "1+absent" in response.headers["location"])
prof.post(f"/classes/{mine_id}/attendance/{week1.id}", data={f"status_{reg_malek.id}": "present"}); db.expire_all()
check("presence: a mark can be corrected, without duplicates", att.marks_of(db, week1.id)[reg_malek.id].status == "present" and db.query(Attendance).filter(Attendance.session_id == week1.id).count() == 3)
prof.post(f"/classes/{mine_id}/attendance/{week1.id}", data={f"status_{reg_malek.id}": "absent"})
prof.post(f"/classes/{mine_id}/attendance/{week2.id}", data={f"status_{reg_nour.id}": "present", f"status_{reg_malek.id}": "absent", f"status_{reg_ines.id}": "present"})
response = prof.post(f"/classes/{mine_id}/attendance/{week3.id}", data={f"status_{reg_nour.id}": "present"}, follow_redirects=False)
check("presence: a session that has not taken place cannot be marked", "error=" in response.headers["location"] and not att.marks_of(db, week3.id))
check("presence: a session of another class cannot be used", api.post(f"/classes/{mine_id}/attendance/{att.sessions(db, other_id)[0].id}", data={f"status_{reg_nour.id}": "absent"}).status_code == 404)
db.expire_all()
summary = att.class_summary(db, db.get(ClassGroup, mine_id))
by_name = {person["name"]: person for person in summary["people"]}
check("presence: rates count late as present", by_name["Nour Access"]["rate"] == 100 and by_name["Ines Access"]["rate"] == 100 and by_name["Malek Access"]["rate"] == 0 and summary["rate"] == 67 and summary["sessions_marked"] == 2, {k: v["rate"] for k, v in by_name.items()})
check("presence: two absences in a row are noticed", by_name["Malek Access"]["absent_in_a_row"] == 2 and by_name["Nour Access"]["absent_in_a_row"] == 0)
check("presence: a candidate's own figures", att.candidate_summary(db, malek.id) == {"present": 0, "late": 0, "absent": 2, "marked": 2, "rate": 0} and att.candidate_summary(db, sami.id)["rate"] is None)
board = advisor.for_dashboard(db)
flag = next((item for item in board if "missed the last 2 sessions of A-DEVOPS-1" in item.title), None)
check("presence: the admin is advised to call who stopped coming", flag is not None and "Malek Access" in flag.title and flag.action["url"] == f"/classes/{mine_id}/attendance", [i.title for i in board if "missed" in i.title or "stopped" in i.title])
response = admin.get(f"/classes/{mine_id}/attendance?session={week1.id}")
check("presence: the admin sees the same sheet, already marked", response.status_code == 200 and "Already marked" in response.text and "absent 2 times in a row" in response.text and "A-DEVOPS-1" in response.text)
check("presence: it shows on the class page and on the candidate's file", "Absent twice in a row" in admin.get(f"/classes/{mine_id}").text and "Malek Access" in admin.get(f"/classes/{mine_id}").text
      and "2 absences in 2 sessions" in admin.get(f"/candidate/{malek.id}").text)
bare = ClassGroup(name="A-BARE-1", program_id=python.id, status=ClassStatus.ONGOING, professor_id=rami_id, professor_name="Rami J.", total_weeks=4, current_week=1, start_mode="min_enrollment")
db.add(bare); db.commit(); bare_id = bare.id
check("presence: a class without plan offers to start today's session", "Start today's session" in prof.get(f"/classes/{bare_id}/attendance").text)
response = prof.post(f"/classes/{bare_id}/attendance/today", follow_redirects=False)
check("presence: today's session is created once", response.status_code == 303 and "session=" in response.headers["location"] and len(att.sessions(db, bare_id)) == 1
      and prof.post(f"/classes/{bare_id}/attendance/today", follow_redirects=False).status_code == 303 and len(att.sessions(db, bare_id)) == 1)

# ======================================================================
# E. Symbols, colours and the parameters of a class
# ======================================================================
db.expire_all(); mine = db.get(ClassGroup, mine_id); other = db.get(ClassGroup, other_id)
check("looks: a class gets a symbol from its program until one is chosen", class_looks.look(mine)["symbol"] == "🐳" and class_looks.look(other)["symbol"] == "🐍" and class_looks.look(mine)["chosen_symbol"] is None)
check("looks: and a colour of its own, readable under white text", re.fullmatch(r"#[0-9a-f]{6}", class_looks.look(mine)["colour"]) and class_looks.look(mine)["soft"] != class_looks.look(mine)["colour"])
check("looks: only symbols of the list are accepted", class_looks.clean_symbol("🚀") == "🚀" and class_looks.clean_symbol("<script>") is None and class_looks.clean_symbol("") is None)
response = admin.get(f"/classes/{mine_id}/parameters-form")
check("parameters: the form of a class comes on its own, for the dialog", response.status_code == 200 and "<form" in response.text and "sidebar" not in response.text and 'name="symbol"' in response.text and 'name="back" value="list"' in response.text and "When it starts" in response.text)
base_form = {"name": "A-DEVOPS-1", "professor_id": rami_id, "capacity": "24", "total_weeks": "8", "start_mode": "fixed_date",
             "fixed_start_date": mine.start_date.strftime("%Y-%m-%d"), "days": "sat", "start_time": "09:00", "end_time": "13:00",
             "room": "Room 3", "reminder_days": "5, 2", "start_delay_days": "10", "min_students_to_start": "5", "min_start_notice_days": "3"}
response = admin.post(f"/classes/{mine_id}/parameters", data={**base_form, "symbol": "🚀", "color": "#1d4ed8", "back": "list"}, follow_redirects=False)
db.expire_all(); mine = db.get(ClassGroup, mine_id)
check("parameters: everything is saved from one form, symbol and colour included", response.status_code == 303 and mine.symbol == "🚀" and mine.color == "#1d4ed8" and mine.room == "Room 3" and mine.capacity == 24 and mine.reminder_days == "5,2")
check("parameters: saved from the list, the admin returns to the list", response.headers["location"].startswith("/classes/?tab=classes"))
admin.post(f"/classes/{mine_id}/parameters", data={**base_form, "symbol": "💣", "color": "red"}); db.expire_all(); mine = db.get(ClassGroup, mine_id)
check("parameters: an unknown symbol or colour falls back to the automatic one", mine.symbol is None and mine.color is None and class_looks.look(mine)["symbol"] == "🐳")
response = admin.post(f"/classes/{mine_id}/parameters", data={**base_form, "capacity": "1", "back": "list"}, follow_redirects=False)
check("parameters: a mistake comes back with the class's name and the reason", "error=A-DEVOPS-1" in response.headers["location"] and "enrolled" in response.headers["location"])
admin.post(f"/classes/{mine_id}/parameters", data={**base_form, "symbol": "🚀", "color": "#1d4ed8"})
page = admin.get("/classes/")
check("classes: the page shows each class with its symbol, places and buttons", page.status_code == 200 and "🚀" in page.text and 'class="cg-meter"' in page.text and 'aria-label="Parameters"' in page.text and f"/classes/{mine_id}/attendance" in page.text and "3 of 24 taken" in page.text)
check("classes: professors show what they teach and how much", "pf-card" in page.text and "Hours / week" in page.text and "98 765 432" in page.text)
check("classes: the class page shows its symbol and the same form", "🚀" in admin.get(f"/classes/{mine_id}").text and "data-class-parameters" in admin.get(f"/classes/{mine_id}").text)

# ======================================================================
# F. The colour of the design
# ======================================================================
check("theme: the original colour changes nothing", theme.palette(None) == {} and theme.palette("#a42c67") == {} and theme.css("") == "")
blue = theme.palette("#1d4ed8")
check("theme: one colour gives the whole look", blue["--primary-berry"] == "#1d4ed8" and set(blue) >= {"--berry-dark", "--berry-subtle", "--ink", "--ink-2", "--ink-3", "--canvas"} and blue["--berry-dark"] != blue["--primary-berry"])
check("theme: a very light colour is darkened so white text stays readable", theme.readable("#ffe066") != "#ffe066" and theme._hls(theme.readable("#ffe066"))[1] <= 0.43)
check("theme: only colours are accepted", theme.clean("1D4ED8") == "#1d4ed8" and theme.clean("blue") is None and theme.clean("#12345") is None and theme.clean("javascript:alert(1)") is None)
response = admin.post("/settings/appearance", data={"ui_color": "not-a-colour"}, follow_redirects=False)
check("theme: a wrong value is refused", "error=" in response.headers["location"])
response = admin.post("/settings/appearance", data={"ui_color": "#1d4ed8"}, follow_redirects=False); db.expire_all()
check("theme: the admin saves a colour", response.status_code == 303 and get_settings(db).ui_color == "#1d4ed8")
check("theme: every page of the application takes it", 'id="theme-colour"' in admin.get("/").text and "--primary-berry:#1d4ed8" in admin.get("/candidate/all").text and "--primary-berry:#1d4ed8" in prof.get("/teacher").text)
check("theme: the sign-in page takes it too", "--primary-berry:#1d4ed8" in anon.get("/login").text)
page = admin.get("/settings")
check("theme: Settings shows the colour chooser and who can sign in", 'id="appearance"' in page.text and 'value="#1d4ed8"' in page.text and "Who can sign in" in page.text and "admin@example.com" in page.text)
admin.post("/settings/appearance", data={"ui_color": "#a42c67"}); db.expire_all()
check("theme: choosing the original colour removes the override", get_settings(db).ui_color is None and 'id="theme-colour"' not in admin.get("/").text)

# ======================================================================
# G. Reformulate
# ======================================================================
check("reformulate: figures the admin never gave are found", rw.added_figures("200 dt puis 190", "200 DT maintenant, 190 DT ensuite, total 390 DT") == ["390"] and rw.added_figures("9h a 13h", "de 9h à 13h") == [])
check("reformulate: a meaningful word left out is found, a reworded one is not", rw.left_out("seulement samedi, je vous inscris", "Uniquement le samedi. Nous vous inscrirons.") == ["seulement"]
      and rw.left_out("la classe est complète", "La classe est complete.") == [])
check("reformulate: the model's own signature is removed", rw._without_signature("Bonjour,\n\nC'est noté.\n\nCordialement,\nL'équipe 9antra") == "Bonjour,\n\nC'est noté.")
check("reformulate: amounts stay in dinars", rw._amounts_in_dinars("200 dt et 190 dt", "Vous payez 200 € puis 190 dinars.") == "Vous payez 200 DT puis 190 DT.")
check("reformulate: the reply follows the candidate's language", rw.language_for("Hello, do I get a certificate?", "oui") == "English" and rw.language_for("Bonjour, est-ce possible ?", "yes") == "French")
model["answers"] = [{"reply": "Bonjour Nour,\\n\\nOui, c'est possible en 2 fois : 200 DT maintenant et 190 DT avant le début.\\n\\nCordialement,\\nL'équipe"}]
written = rw.reformulate("oui possible en 2 fois, 200 dt maintenant et 190 avant le debut", first_name="Nour", question="Bonjour, puis-je payer en deux fois ?")
check("reformulate: a short phrase becomes a full reply, without warning", written["warning"] is None and written["reply"].startswith("Bonjour Nour,\n\nOui") and "Cordialement" not in written["reply"] and "190 DT" in written["reply"], written)
check("reformulate: the model is told the candidate's question and name", "puis-je payer en deux fois" in model["calls"][-1] and "Nour" in model["calls"][-1] and "French" in model["calls"][-1])
model["calls"].clear()
model["answers"] = [{"reply": "Bonjour,\n\nLe prix est de 390 DT, payable en 2 fois."}, {"reply": "Bonjour,\n\nOui, le paiement en 2 fois est possible."}]
written = rw.reformulate("oui paiement possible en 2 fois")
check("reformulate: an invented figure triggers a second try that says what was wrong", written["warning"] is None and "390" not in written["reply"] and len(model["calls"]) == 2 and "added figures" in model["calls"][1] and "390" in model["calls"][1])
model["answers"] = [{"reply": "Bonjour,\n\nLe prix est de 390 DT."}, {"reply": "Bonjour,\n\nLe prix est de 350 DT."}]
written = rw.reformulate("le prix est le meme pour tous")
check("reformulate: if it still invents, the admin is warned", written is not None and "Check before sending" in written["warning"] and ("390" in written["warning"] or "350" in written["warning"]))
model["answers"] = []
check("reformulate: when the model does not answer, nothing is made up", rw.reformulate("oui possible") is None)

thread = EmailThread(candidate_id=nour.id, subject="Paiement", needs_admin=True, status="needs_admin", last_message_at=now)
db.add(thread); db.flush()
db.add(CandidateEmail(candidate_id=nour.id, direction="inbound", sender=nour.email, recipient="x", subject="Paiement", body="Bonjour, puis-je payer en deux fois ?",
                      status="needs_admin", requires_admin=True, received_at=now, author="candidate", thread_id=thread.id))
db.commit()
json_admin = TestClient(main.app, base_url="http://127.0.0.1:8000", cookies={auth.COOKIE: admin.cookies.get(auth.COOKIE)})
model["answers"] = [{"reply": "Bonjour Nour,\n\nOui, c'est possible en 2 fois."}]
response = json_admin.post(f"/inbox/thread/{thread.id}/reformulate", json={"text": "oui possible en 2 fois"})
check("reformulate: the inbox button gets the text back, nothing is sent", response.status_code == 200 and response.json()["reply"].startswith("Bonjour Nour") and response.json()["warning"] is None
      and db.query(CandidateEmail).filter(CandidateEmail.thread_id == thread.id, CandidateEmail.direction == "outbound").count() == 0, response.text[:200])
check("reformulate: it uses the candidate's last message", "puis-je payer en deux fois" in model["calls"][-1])
response = json_admin.post(f"/inbox/thread/{thread.id}/reformulate", json={"text": "oui"})
check("reformulate: when the model is down the page is told so", response.status_code == 503 and "assistant" in response.json()["detail"] and "Ollama" not in response.json()["detail"])
check("reformulate: an empty phrase is refused", json_admin.post(f"/inbox/thread/{thread.id}/reformulate", json={"text": ""}).status_code == 422)
check("reformulate: a professor cannot use the inbox", api.post(f"/inbox/thread/{thread.id}/reformulate", json={"text": "oui possible"}).status_code == 403)
page = admin.get(f"/inbox?thread={thread.id}")
check("reformulate: the button is under the reply box", page.status_code == 200 and 'id="reformulate-btn"' in page.text and "Put my words back" in page.text, page.status_code)

# ======================================================================
# H. Removing access, and the pages candidates open
# ======================================================================
response = admin.post(f"/classes/professors/{rami_id}/access", data={"email": "rami@example.com", "password": "set-by-the-admin"}, follow_redirects=False)
check("access: a new password from the admin signs the professor out", response.status_code == 303 and prof.get("/teacher", follow_redirects=False).headers["location"].startswith("/login"))
prof.post("/login", data={"email": "rami@example.com", "password": "set-by-the-admin"})
check("access: they sign in with the new one", prof.get("/teacher").status_code == 200)
admin.post(f"/classes/professors/{rami_id}/access/remove"); db.expire_all()
check("access: removed, they are out at once and cannot sign in again", prof.get("/teacher", follow_redirects=False).headers["location"].startswith("/login")
      and "not right" in prof.post("/login", data={"email": "rami@example.com", "password": "set-by-the-admin"}).text and db.query(User).filter(User.role == "professor").count() == 0)
from app.models import Registration
from app.models.registration import PaymentStatus
from app.services import level_check
paid = next(r for r in db.query(Registration).filter(Registration.payment_status == PaymentStatus.PAID).all() if level_check.can_have_one(r))
quiz = level_check.prepare(db, paid)
response = anon.get(f"/level/{quiz.access_token}")
check("candidates: the level check opens without signing in and shows nothing of the application", response.status_code == 200 and "sidebar" not in response.text and "Envoyer mes réponses" in response.text and "/inbox" not in response.text, response.status_code)
items = level_check.items_of(quiz)
response = anon.post(f"/level/{quiz.access_token}", data={f"q{i}": ("" if item["type"] == "note" else "0") for i, item in enumerate(items)})
check("candidates: after answering they get a page, not raw data", response.status_code == 200 and "Voici votre point de départ" in response.text and "sidebar" not in response.text)
check("candidates: the quiz center itself stays closed", anon.get("/quiz/all", follow_redirects=False).status_code == 303)
for path in ("/", "/candidate/all", f"/candidate/{nour.id}", "/programs/all", "/classes/", f"/classes/{mine_id}", f"/classes/{mine_id}/course", "/inbox", "/inbox/templates", "/settings", "/quiz/all", "/account"):
    check(f"admin: {path} opens", admin.get(path).status_code == 200, admin.get(path).status_code)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)

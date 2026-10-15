"""The program pages, the System page, and what a phone needs. Staging copy, COPY of the database."""
import testenv
testenv.prepare("pages")
check = testenv.Checks()

import json
from datetime import datetime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import ClassGroup, Professor, Program, User, UserSession
from app.scheduler import jobs
from app.services import auth, email_assistant as ea, email_templates as tp, email_threads as et, system_status as ss, brand

et.send_email = lambda **kw: True; tp.send_email = lambda **kw: True
ea.model_available = lambda: True
ea.llm_json = lambda *a, **k: None
brand.refresh_from_facebook = lambda force=False: "same"

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="pages-test-1", role=auth.ADMIN)
rami = db.query(Professor).filter(Professor.name == "rami").first()
auth.create_user(db, email="rami@test.local", name="rami", password="pages-test-2", role=auth.PROFESSOR, professor_id=rami.id)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "pages-test-1"})
teacher = TestClient(main.app, follow_redirects=False)
teacher.post("/login", data={"email": "rami@test.local", "password": "pages-test-2"})
nobody = TestClient(main.app, follow_redirects=False)
from urllib.parse import unquote_plus
def where(response): return unquote_plus(response.headers.get("location", ""))
def program(name): return db.query(Program).filter(Program.name == name).first()

# ======================================================================
# A. A program's page
# ======================================================================
mobile = program("Mobile Development (Flutter + Node/Spring Boot)")
page = admin.get(f"/programs/{mobile.id}")
text = page.text
check("the program's page opens", page.status_code == 200, page.status_code)
check("it has a cover with the program's picture and name", 'class="pd-hero"' in text and "pd-art-tile" in text and "<h1>Mobile Development (Flutter + Node/Spring Boot)</h1>" in text)
check("the facts are tiles with icons: duration, weeks, hours, prices", text.count('class="pd-fact ') == 5 and "8 weeks" in text and "6 h a week" in text and "390 DT" in text and "490 DT" in text)
check("what the website says is shown: level, mode, certificate", "Débutant à Intermédiaire" in text and "Hybride" in text and "Certificate" in text and "See it on 9antra.tn" in text)
check("the journey is drawn level by level, week by week", text.count('class="pd-level"') == 2 and text.count('class="pd-week"') == 8 and "Introduction to Flutter &amp; Dart" in text)
check("the level names lose their “Level 1:”", "Frontend Mobile (Flutter &amp; Dart)" in text and ">Level 1: Frontend" not in text)
check("the website's videos sit on their week", text.count('class="pd-video"') >= 3 and "drive.google.com/file" in text and 'rel="noopener"' in text, text.count('class="pd-video"'))
check("its classes are listed with their state", "Its classes" in text and "MD1" in text and "Running" in text)
check("who can teach it", "Who can teach it" in text and "rami" in text)
check("the level check is one click away", f"/quiz/preview/{mobile.id}" in text and "8 questions" in text)
check("the other programs are one click away, with their pictures", text.count('class="pd-other"') == db.query(Program).count() - 1)
check("the old page is gone", "Curriculum Breakdown" not in text and "All Program Titles" not in text and "accordion" not in text)
bare = program("Mobile Flutter")
page = admin.get(f"/programs/{bare.id}")
check("a program with nothing yet says so plainly", page.status_code == 200 and "No weekly plan yet" in page.text and "No description yet." in page.text and "No plan yet" in page.text, page.status_code)
bi = program("Business Intelligence (BI)")
page = admin.get(f"/programs/{bi.id}")
check("a program nobody can teach says its classes cannot start", "No professor has this program among their specialities" in page.text and "Give this speciality to a professor" in page.text)
check("an unknown program is not found", admin.get("/programs/nothing-here").status_code == 404)
listing = admin.get("/programs/all")
check("the list shows each program as a cover", listing.status_code == 200 and listing.text.count('class="pg-cover"') == db.query(Program).count() and "pg-cover-tile" in listing.text, listing.text.count('class="pg-cover"'))
check("each cover takes the program's own colour", listing.text.count('style="--tone:#') == db.query(Program).count())
check("the picture can still be changed and the program opened", listing.text.count("data-icon-for=") >= db.query(Program).count() and f'href="/programs/{mobile.id}"' in listing.text)

# ======================================================================
# B. How the application is working
# ======================================================================
page = admin.get("/system")
text = page.text
check("the System page opens", page.status_code == 200, page.status_code)
check("it shows the eleven things done by themselves", all(f'id="work-{item["key"]}"' in text for item in ss.WORK) and len(ss.WORK) == 11)
check("each says what it does and where its rule is changed", "Reads the emails candidates sent" in text and text.count("Change its rule") == 11)
check("it shows what the application is connected to", all(name in text for name in ("Sending emails", "Reading the mailbox", "The assistant", "Your website 9antra.tn", "WhatsApp", "Google Forms")))
check("without email it says what stops, in red", "Not working" in text and "no email can leave" in text and "need" in text)
check("it explains the phone", 'id="phone"' in text and "Add to Home screen" in text and any(words in text for words in ("start_for_phone.bat", "on the same Wi-Fi", "Connect this computer to your Wi-Fi", "from anywhere")), [w for w in ("id=\"phone\"", "Add to Home screen") if w not in text])
check("it lists what happened lately", 'id="system-news"' in text and "data-spot=" in text)
check("the scheduler names every piece of work", set(jobs.WATCHED) == {item["key"] for item in ss.WORK})
check("“Run now” exists only for what does not email candidates", set(jobs.RUN_NOW) == {item["key"] for item in ss.WORK if item["run"]} and "payments" not in jobs.RUN_NOW and "weekly" not in jobs.RUN_NOW)

@ss.watched("answers")
def clean(): return 7
@ss.watched("answers")
def caught():
    try: raise RuntimeError("the form could not be read")
    except Exception as exc: ss.problem("answers", exc)
def row(key): return next(item for item in ss.work(db) if item["key"] == key)
check("work that never ran says it starts in a moment", row("answers")["state"] == "waiting")
check("a job keeps its result", clean() == 7 and row("answers")["state"] == "ok" and row("answers")["runs"] == 1 and row("answers")["last"] == "just now")
caught()
check("a job that could not finish is shown, with why", row("answers")["state"] == "problem" and "the form could not be read" in row("answers")["problem"])
clean()
check("its next clean run clears it", row("answers")["state"] == "ok" and row("answers")["problem"] is None)
summary = ss.summary([{"state": "problem", "name": "Reads the mailbox"}], [{"state": "off", "name": "Sending emails"}, {"state": "partial", "name": "WhatsApp"}])
check("the top line counts what needs the admin", summary["tone"] == "bad" and "2 things need you" == summary["title"] and "Reads the mailbox, Sending emails" in summary["text"], summary)
fine = ss.summary([{"state": "ok", "name": "x"}], [{"state": "on", "name": "y"}, {"state": "partial", "name": "WhatsApp"}])
check("something not connected but not needed does not alarm", fine["tone"] == "ok" and fine["title"] == "Everything is running" and "1 connection is not complete" in fine["text"], fine)
before = row("classes")["runs"]
answer = admin.post("/system/run/classes")
check("a piece of work can be started by hand", answer.status_code == 303 and "was started" in where(answer) and row("classes")["runs"] == before + 1, (where(answer), row("classes")["runs"]))
check("but not the one that sends reminders", admin.post("/system/run/payments").status_code == 404 and admin.post("/system/run/nothing").status_code == 404)
check("a professor does not see the System page", teacher.get("/system").status_code in (302, 303, 403))
check("nor does somebody not signed in", nobody.get("/system").status_code in (302, 303, 401))
check("masked addresses do not show the whole mailbox name", ss._masked("someone.long@gmail.com") == "so…@gmail.com")
address = ss.lan_address()
check("the address for the phone is a network address or nothing", address is None or (address.count(".") == 3 and not address.startswith("127.")), address)

# ======================================================================
# C. On a phone
# ======================================================================
manifest = nobody.get("/app.webmanifest")
data = manifest.json() if manifest.status_code == 200 else {}
check("the home-screen file opens without signing in", manifest.status_code == 200 and data.get("display") == "standalone" and data.get("start_url") == "/", manifest.status_code)
check("it carries the application's name", bool(data.get("name")) and len(data.get("short_name", "")) <= 12, data.get("name"))
home = admin.get("/")
text = home.text
check("every page links it and has its bar for the thumb", 'rel="manifest" href="/app.webmanifest"' in text and 'class="tabbar"' in text and 'id="tabMore"' in text)
check("the bar holds the four pages used most", all(link in text for link in ('class="tab active" href="/"', 'href="/candidate/all"><i', 'href="/classes/"><i', 'href="/inbox"><i')), "tabs")
check("the inbox tab carries the count of waiting questions", 'id="tab-inbox-count"' in text and '"tab-inbox-count"' in text)
check("the side menu has the System page and the new name of quizzes", 'href="/system"' in text and "How it is working" in text and "Level checks" in text and ">Quizzes<" not in text)
mine = teacher.get("/teacher")
check("a professor's bar has their own pages only", mine.status_code == 200 and 'href="/teacher/profile"><i' in mine.text and 'href="/candidate/all"><i' not in mine.text, mine.status_code)
classes = admin.get("/classes/")
check("the Classes tab is lit on the classes page", 'class="tab active" href="/classes/"' in classes.text)
for path in ("/", "/candidate/all", "/classes/", "/programs/all", "/quiz/all", "/inbox", "/payments", "/website", "/settings", "/system", "/guide"):
    response = admin.get(path)
    check(f"{path} opens", response.status_code == 200, response.status_code)
db.close()
check.done()

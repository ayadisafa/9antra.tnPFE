"""Copies of the data and what protects the pages. COPY of the database, temporary folders only."""
import testenv
testenv.prepare("safety")
check = testenv.Checks()

import io, os, shutil, sqlite3, zipfile
from datetime import datetime, timedelta
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from urllib.parse import unquote_plus
from app.database import SessionLocal
from app.models import Candidate, User, UserSession
from app.scheduler import jobs
from app.services import auth, backups as bk, system_status as ss
from app.services.settings_service import get_settings

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="safety-test-1", role=auth.ADMIN)
auth.create_user(db, email="prof@test.local", name="Test Prof", password="safety-test-2", role=auth.PROFESSOR)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "safety-test-1"})
def where(response): return unquote_plus(response.headers.get("location", ""))
base = os.path.dirname(str(bk.database_file().resolve()))
shutil.rmtree(os.path.join(base, "data"), ignore_errors=True)
os.makedirs(os.path.join(base, "data", "email_files"), exist_ok=True)
open(os.path.join(base, "data", "email_files", "one.pdf"), "wb").write(b"%PDF-1.4 test")
open(os.path.join(base, ".env"), "a").close()

# ---- a copy
check("nothing is kept yet, so a copy is due", bk.listing() == [] and bk.due() and bk.database_file() is not None)
people = db.query(Candidate).count()
t0 = datetime(2026, 10, 1, 3, 0, 0)
made = bk.make(db, now=t0)
check("a copy is made, with its name and size", made["ok"] and made["name"] == "backup-20261001-030000.zip" and made["size"] > 10000 and made["problem"] is None, made)
archive = zipfile.ZipFile(bk.find(made["name"]))
names = archive.namelist()
check("it holds the database and the uploaded files", "app.db" in names and "data/email_files/one.pdf" in names, names[:6])
check("it holds no password and no key", not any(name.endswith((".env", ".json")) or "firebase" in name.lower() for name in names))
out = os.path.join(testenv.WORK, "safety_restored.db")
open(out, "wb").write(archive.read("app.db")); archive.close()
copy = sqlite3.connect(out)
check("the copy opens and holds every candidate", copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and copy.execute("select count(*) from candidates").fetchone()[0] == people, people)
copy.close(); os.remove(out)
check("it is not made again the same day", not bk.due(t0 + timedelta(hours=23)) and bk.make_if_due(db, t0 + timedelta(hours=5)) is None)
check("a day later it is", bk.due(t0 + timedelta(hours=24)) and bk.make_if_due(db, t0 + timedelta(hours=24))["ok"] and len(bk.listing()) == 2)
for day in range(2, 40):
    bk.make(db, now=t0 + timedelta(days=day))
kept = bk.listing()
recent = [item for item in kept if t0 + timedelta(days=39) - item["made"] <= timedelta(days=bk.KEEP_DAILY)]
check("two weeks of daily copies are kept, then one a week", len(recent) == bk.KEEP_DAILY + 1 and bk.KEEP_DAILY + 3 <= len(kept) <= bk.KEEP_DAILY + bk.KEEP_WEEKLY + 3 and kept[0]["made"] == t0 + timedelta(days=39), (len(recent), len(kept)))
check("only its own files can be asked for", bk.find("..\\..\\app.db") is None and bk.find("../.env") is None and bk.find("backup-x.zip") is None and bk.find(kept[0]["name"]) is not None)

# ---- a second place
second = os.path.join(testenv.WORK, "safety_second"); shutil.rmtree(second, ignore_errors=True)
check("a folder that is not a whole address is refused", bk.check_folder("copies")[1] is not None and bk.check_folder("")[0] is None and bk.check_folder("")[1] is None)
check("the first folder cannot be the second", bk.check_folder(str(bk.folder().resolve()))[1] is not None)
answer = admin.post("/system/backup-folder", data={"folder": second})
db.expire_all()
check("a second folder is saved and gets a copy at once", answer.status_code == 303 and "One was made there just now" in where(answer) and get_settings(db).backup_second_dir == second and len(bk.listing(bk.second_folder(db))) == 1, where(answer))
bk.make(db, now=t0 + timedelta(days=40))
view = bk.view(db)
check("the page knows both places are up to date", view["second"] == second and view["second_state"] == "ok" and view["last"] is not None)
shutil.rmtree(second); open(second, "w").close()          # the disk is "gone": a file where the folder was
gone = bk.make(db, now=datetime.now() + timedelta(days=1))
check("when the second disk is missing the copy is still made, and it says so", gone["ok"] and gone["second"] is None and "not in the second folder" in (gone["problem"] or ""), gone)
os.remove(second)
check("a folder that cannot be written is refused with a reason", "cannot be written" in where(admin.post("/system/backup-folder", data={"folder": "Q:\\nowhere\\x"})) or "cannot" in where(admin.post("/system/backup-folder", data={"folder": "Q:\\nowhere\\x"})))
answer = admin.post("/system/backup-folder", data={"folder": ""})
db.expire_all()
check("emptying it goes back to one folder", "one folder only" in where(answer) and get_settings(db).backup_second_dir is None)

# ---- the page and the job
page = admin.get("/system")
check("the System page shows the copies and how to bring one back", page.status_code == 200 and 'id="safety"' in page.text and "Last copy:" in page.text and "To bring a copy back" in page.text and f'/system/backups/{bk.latest()["name"]}' in page.text, page.status_code)
check("and says who can get in", "2 accounts can sign in" in page.text and "never put in the copies" in page.text)
down = admin.get(f"/system/backups/{bk.latest()['name']}")
check("a copy is downloaded as a zip", down.status_code == 200 and down.headers["content-type"] == "application/zip" and down.content[:2] == b"PK")
check("a copy that does not exist is not found", admin.get("/system/backups/backup-20200101-000000.zip").status_code == 404 and admin.get("/system/backups/app.db").status_code == 404)
guest = TestClient(main.app, follow_redirects=False)
prof = TestClient(main.app, follow_redirects=False); prof.post("/login", data={"email": "prof@test.local", "password": "safety-test-2"})
check("nobody else can take a copy", guest.get(f"/system/backups/{bk.latest()['name']}").status_code in (303, 401) and prof.get(f"/system/backups/{bk.latest()['name']}").status_code in (303, 403)
      and "PK" not in prof.get(f"/system/backups/{bk.latest()['name']}").text[:4])
check("the daily copy is one of the things the System page follows", jobs.WATCHED.get("backup") == "backup_tick" and "backup" in jobs.RUN_NOW and any(item["key"] == "backup" for item in ss.WORK))
before = len(bk.listing())
jobs.backup_tick()
check("the hourly look does nothing when a copy is recent", len(bk.listing()) == before)
bk.database_file = lambda: None
check("with another kind of database it says its copies are made elsewhere", not bk.make(db)["ok"] and "own tools" in bk.make(db)["problem"])

# ---- what protects the pages
for label, response in (("a page", page), ("the sign-in page", guest.get("/login")), ("a refusal", guest.get("/system"))):
    headers = {key.lower(): value for key, value in response.headers.items()}
    check(f"{label} tells the browser to be careful", headers.get("x-content-type-options") == "nosniff" and headers.get("x-frame-options") == "SAMEORIGIN" and headers.get("referrer-policy") == "same-origin", {k: v for k, v in headers.items() if k.startswith(("x-", "ref"))})
check("the sign-in cookie cannot be read by scripts", "httponly" in admin.post("/login", data={"email": "admin@test.local", "password": "safety-test-1"}).headers.get("set-cookie", "").lower())
bad = TestClient(main.app, follow_redirects=False)
for _ in range(12):
    last = bad.post("/login", data={"email": "admin@test.local", "password": "wrong"})
check("many wrong passwords pause signing in", "set-cookie" not in last.headers and bad.post("/login", data={"email": "admin@test.local", "password": "safety-test-1"}).headers.get("set-cookie") is None)
check.done()

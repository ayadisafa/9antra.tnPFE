"""Open every main page with the current code on a copy of TODAY's database.

The suites use the fixed 13 October database; this check uses the real data as
it is now, so it catches a page that breaks on records the suites never saw.
app.db is only read, to be copied; nothing is sent. Run it after any change:

    python tests/check_pages.py
"""
import os
import shutil
import testenv

testenv.prepare("pages_today", source=os.path.join(testenv.PROJECT, "app.db"))
# The program pictures, copied so that a page can never change the real folder.
real_icons = os.path.join(testenv.PROJECT, "data", "program_icons")
if os.path.isdir(real_icons):
    shutil.copytree(real_icons, os.environ["PROGRAM_ICONS_DIR"])

import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, ClassGroup, Program, User, UserSession
from app.models.quiz import Quiz
from app.services import auth, brand, email_assistant as ea, email_templates as tp, email_threads as et, level_check as lc

et.send_email = lambda **kw: True
tp.send_email = lambda **kw: True
ea.model_available = lambda: True
ea.llm_json = lambda *a, **k: None
brand.refresh_from_facebook = lambda force=False: "same"

db = SessionLocal()
# A sign-in that exists only in the copy.
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Page check", password="page-check-1", role=auth.ADMIN)
client = TestClient(main.app, follow_redirects=False)
client.post("/login", data={"email": "admin@test.local", "password": "page-check-1"})

programs = db.query(Program).all()
classes = db.query(ClassGroup).all()
paths = ["/", "/candidate/all", "/classes/", "/programs/all", "/quiz/all", "/inbox", "/payments", "/website", "/settings", "/system",
         "/guide", "/inbox/templates", "/programs/market", "/inbox/compose", "/reminders", "/reminders?audience=class&idea=bring",
         "/settings/translations", "/settings/translations?lang=ar", "/i18n/fr.json", "/i18n/ar.json", "/find?q=pay", "/data",
         "/settings/team", "/programs/documents", "/programs/summary.pdf", "/programs/catalogue.pdf", "/data/candidates.csv"]
paths += [f"/classes/{c.id}" for c in classes] + [f"/classes/{c.id}/attendance" for c in classes]
paths += [f"/programs/{p.id}" for p in programs] + [f"/quiz/preview/{p.id}" for p in programs]
paths += [f"/programs/{p.id}/pdf" for p in programs]
paths += [f"/programs/{p.id}/pdf/{look}" for p in programs for look in ("brochure", "classic", "one_page")]
paths += [f"/candidate/{c.id}" for c in db.query(Candidate).limit(40).all()]
paths += [f"/data/{key}" for key in ("candidates", "registrations", "payments", "programs", "levels", "weeks", "classes", "sessions",
                                     "attendance", "professors", "email_templates", "knowledge", "reminders", "website", "market",
                                     "notifications", "translations", "settings")]
paths += [f"/level/{q.access_token}" for q in db.query(Quiz).filter(Quiz.kind == "level").all()]

bad = []
for path in paths:
    try:
        status = client.get(path).status_code
    except Exception as error:
        status = f"{type(error).__name__}: {str(error)[:160]}"
    if status != 200:
        bad.append((path, status))
        print("FAIL", path, status)
print(f"\n{len(paths) - len(bad)} of {len(paths)} checks passed (pages opened on a copy of today's data)")
db.close()
os._exit(1 if bad else 0)

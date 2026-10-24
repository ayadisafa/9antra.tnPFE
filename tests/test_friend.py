"""Yasmine, the animated friend: she greets by name in the chosen language, tells the admin what is waiting,
explains the page, lets only the admin ask questions, and is on every signed-in page. Runs on a COPY."""
from datetime import datetime, timedelta

import testenv

testenv.prepare("friend")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import User, UserSession
from app.models.email_thread import EmailThread
from app.services import access, auth, friend

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="leila ben salah", password="friend-test-1", role=auth.ADMIN)
auth.create_user(db, email="sam.fr@test.local", name="Sam", password="sam-friend-12", role=auth.STAFF)
sam = db.query(User).filter(User.email == "sam.fr@test.local").one()
sam.permissions = access.dump({"candidates": "view"})
db.commit()
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "friend-test-1"})
staff = TestClient(main.app, follow_redirects=False)
staff.post("/login", data={"email": "sam.fr@test.local", "password": "sam-friend-12"})

# ---- A. She says hi, by name, in the chosen language ----
admin.cookies.set("lang", "fr")
said = admin.get("/account/friend", params={"path": "/"}).json()
check("hello: her name is Yasmine", said["name"] == "Yasmine")
check("hello: in French, with the first name", said["lang"] == "fr" and "Leila" in said["greeting"] and said["greeting"].startswith(("Bonjour", "Bonsoir")), said["greeting"])
check("hello: she introduces herself", "Yasmine" in said["intro"] and "questions" in said["intro"], said["intro"])
check("hello: she speaks French with a French voice", said["voice_lang"] == "fr-FR")
admin.cookies.set("lang", "en")
said = admin.get("/account/friend", params={"path": "/"}).json()
check("hello: in English when the interface is English", said["lang"] == "en" and "Leila" in said["greeting"] and said["voice_lang"] == "en-US", said["greeting"])
admin.cookies.set("lang", "ar")
said = admin.get("/account/friend", params={"path": "/"}).json()
check("hello: Arabic interface: she speaks Arabic", said["lang"] == "ar" and said["greeting"].startswith(("صباح الخير", "مساء الخير"))
      and said["voice_lang"] == "ar-SA" and said["words"]["ask"] == "اطرح عليّ سؤالا…", said["greeting"])
admin.cookies.set("lang", "en")

# ---- B. She tells the admin what is waiting ----
thread = db.query(EmailThread).first()
if thread is None:
    thread = EmailThread(subject="Friend test", candidate_email="friend@example.com")
    db.add(thread)
waiting_before = db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).count()
thread.needs_admin = True
db.commit()
waiting = db.query(EmailThread).filter(EmailThread.needs_admin.is_(True)).count()
said = admin.get("/account/friend", params={"path": "/"}).json()
emails = [line for line in said["lines"] if line["url"] == "/inbox?filter=todo"]
check("waiting: the emails waiting for an answer, counted from the records", emails and emails[0]["text"].startswith(f"{waiting} email"), said["lines"][:3])
check("waiting: the most pressing comes first", said["lines"][0]["url"] == "/inbox?filter=todo", said["lines"][0])
check("waiting: emails count as urgent (the badge)", said["urgent"] >= 1)
check("waiting: every line has a place to open or none", all(line["url"] is None or line["url"].startswith("/") for line in said["lines"]))
check("waiting: a cheering word", bool(said["cheer"]))
words = friend._WORDS["en"]
check("plural: one email waits", friend._plural(words, "emails", 1) == "1 email waits for your answer.")
check("plural: two emails wait", friend._plural(words, "emails", 2) == "2 emails wait for your answer.")
check("plural: French", friend._plural(friend._WORDS["fr"], "emails", 2) == "2 emails attendent votre réponse.")
check("plural: French owing", friend._plural(friend._WORDS["fr"], "owing", 1, amount="10 DT") == "1 candidat doit encore 10 DT au total.")

# ---- C. She explains the page ----
said = admin.get("/account/friend", params={"path": "/candidate/all"}).json()
check("page: a tip for the candidates' page", said["tip"] and said["tip"]["text"].startswith("On this page:") and said["tip"]["key"], said["tip"])
check("page: an unknown page has no tip, and nothing breaks", admin.get("/account/friend", params={"path": "/nowhere-at-all"}).json()["tip"] is None)
check("page: a strange address is read as the home page", admin.get("/account/friend", params={"path": "//evil.example.com"}).status_code == 200)

# ---- D. The team hears the greeting and the page, not the centre's counts ----
said = staff.get("/account/friend", params={"path": "/candidate/all"}).json()
check("team: a team member is greeted by name", "Sam" in said["greeting"])
check("team: no counts of the whole centre", said["lines"] == [] and said["urgent"] == 0)
check("team: everyone asks her, the team gets the guide and the admin the assistant", said["can_ask"] is True and said["ask_url"] == "/account/guide"
      and admin.get("/account/friend").json()["ask_url"] == "/assistant/chat")
check("signed out: no friend", TestClient(main.app, follow_redirects=False).get("/account/friend").status_code in (303, 401, 403))

# ---- E. She is on every signed-in page ----
for path in ("/", "/candidate/all", "/inbox", "/settings"):
    page = admin.get(path)
    check(f"page {path}: Yasmine is there", page.status_code == 200 and 'id="friend"' in page.text and "/account/friend" in page.text, page.status_code)
page = staff.get("/candidate/all")
check("page: she is there for the team too", page.status_code == 200 and 'id="friend"' in page.text, page.status_code)
check("page: news reaches her", 'new CustomEvent("friend:news"' in admin.get("/").text)
check("login page: no friend", 'id="friend"' not in TestClient(main.app).get("/login").text)

thread.needs_admin = False if waiting_before < waiting else thread.needs_admin
db.commit()
db.close()
check.done()

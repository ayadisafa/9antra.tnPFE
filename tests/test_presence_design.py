"""4 November (2): who is using the application, live, and signing people out (one browser, one person, everyone
else); Yasmine's livelier window; the dashboard and the long pages in tabs, every page opening with its moving picture.
Runs on a COPY; nothing is sent."""
from datetime import datetime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("presence_design")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import User, UserSession
from app.services import auth, presence, whats_new
from app.services.realtime import broadcaster

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Leila Admin", password="presence-1", role=auth.ADMIN)
auth.create_user(db, email="sami.team@example.com", name="Sami Team", password="presence-2", role=auth.STAFF)
auth.create_user(db, email="nour.team@example.com", name="Nour Team", password="presence-3", role=auth.STAFF)


def client(email, password, agent="Mozilla/5.0 (Windows NT 10.0) Chrome/120"):
    browser = TestClient(main.app, follow_redirects=False, headers={"user-agent": agent})
    browser.post("/login", data={"email": email, "password": password})
    return browser


admin = client("admin@test.local", "presence-1")
sami = client("sami.team@example.com", "presence-2")
sami_phone = client("sami.team@example.com", "presence-2", agent="Mozilla/5.0 (iPhone) Safari/604")
nour = client("nour.team@example.com", "presence-3")


def where(response):
    return unquote_plus(response.headers.get("location", ""))


# ======================================================================
# A. Who is connected
# ======================================================================
check("a page says it is here", sami.post("/account/here", json={"path": "/candidate/all"}).status_code == 200)
nour.post("/account/here", json={"path": "/payments"})
db.expire_all()
rows = presence.connected(db)
check("everyone signed in is listed, one line per browser", sorted(row["name"] for row in rows) == ["Leila Admin", "Nour Team", "Sami Team", "Sami Team"], rows)
sami_row = next(row for row in rows if row["name"] == "Sami Team" and row["path"] == "/candidate/all")
check("…with the page, the browser and the address", sami_row["page"] == "Candidates" and "Chrome" in sami_row["device"] and sami_row["ip"]
      and sami_row["state"] == "online", sami_row)
stale = db.query(UserSession).filter(UserSession.user_id == db.query(User).filter(User.email == "nour.team@example.com").one().id).one()
stale.last_seen_at = datetime.utcnow() - timedelta(minutes=8)
db.commit()
check("open but not looked at for a while: idle", next(row for row in presence.connected(db) if row["name"] == "Nour Team")["state"] == "idle")
page = admin.get("/settings/team").text
check("the team page shows who is connected now", 'id="connected"' in page and "Connected now" in page and "Sami Team" in page and "Sign out everyone else" in page)
check("the top bar shows who is online", 'class="who-online"' in admin.get("/payments").text)
live = admin.get("/settings/team/connected").json()
check("the live list", len(live["rows"]) == 4 and live["summary"]["people"] >= 2, live["summary"])
check("only the admin sees it", sami.get("/settings/team/connected").status_code in (303, 401, 403))
said = admin.post("/assistant/chat", json={"message": "Qui est connecté ?"}).json()
check("Yasmine says who is connected", "Sami Team" in said["reply"] and "connected" in str(said.get("links")), said["reply"])

# ======================================================================
# B. Signing out: one browser, one person, everyone else
# ======================================================================
sent = []
real_publish = broadcaster.publish
broadcaster.publish = lambda event, data: sent.append((event, data))
phone_key = next(row["key"] for row in presence.connected(db) if "iPhone" in row["device"])
answer = admin.post("/settings/team/sign-out", data={"key": phone_key})
check("one browser signed out", answer.status_code == 303 and "Signed out" in where(answer) and sami_phone.post("/account/here", json={"path": "/"}).status_code == 401
      and sami.post("/account/here", json={"path": "/"}).status_code == 200)
check("its page is told at once (live event)", sent and sent[-1][0] == "signed_out" and len(sent[-1][1]["tags"]) == 1)
check("a signed-out browser is sent to sign in, with why", "signed out by the admin" in admin.get("/login?out=1").text or "signed out by the admin" in
      TestClient(main.app).get("/login?out=1").text)
sami_id = db.query(User).filter(User.email == "sami.team@example.com").one().id
sami_phone = client("sami.team@example.com", "presence-2", agent="Mozilla/5.0 (iPhone) Safari/604")
answer = admin.post("/settings/team/sign-out", data={"user_id": sami_id})
db.expire_all()
check("one person signed out everywhere", "Sami Team" in where(answer) and db.query(UserSession).filter(UserSession.user_id == sami_id).count() == 0)
answer = admin.post("/settings/team/sign-out", data={"everyone": "true"})
db.expire_all()
admin_id = db.query(User).filter(User.email == "admin@test.local").one().id
check("everyone else signed out, never the admin's own browser", "everyone else" in where(answer)
      and db.query(UserSession).filter(UserSession.user_id != admin_id).count() == 0 and admin.get("/settings/team").status_code == 200)
nour = client("nour.team@example.com", "presence-3")
check("a team member cannot sign people out", nour.post("/settings/team/sign-out", data={"everyone": "true"}).status_code in (303, 401, 403))
broadcaster.publish = real_publish
check("every page listens for its own sign-out", 'data-session-tag="' in admin.get("/").text and '"signed_out"' in admin.get("/").text)

# ======================================================================
# C. The design: Yasmine's window, the dashboard and the long pages in tabs, a moving picture on every page
# ======================================================================
home = admin.get("/").text
check("Yasmine's window: a welcome with a moving picture and four things to ask", 'class="assistant-hero"' in home and home.count('class="assistant-tile"') == 4
      and 'id="assistant-dock"' in home and "assistant-typing" in home)
check("the dashboard in four tabs, what needs you always on top", home.count('class="db-tab"') == 4 and 'id="live-dashboard-advice"' in home
      and 'data-panel="people"' in home and home.count('class="db-intro"') == 4)
settings = admin.get("/settings").text
check("Settings in six tabs, the map still on top", settings.count('class="ui-tab"') == 6 and 'id="map"' in settings
      and settings.count('data-tab-of="settings"') >= 21)
website = admin.get("/website").text
check("the website in five tabs, each with its picture", website.count('class="ui-tab"') == 5 and website.count('class="ui-tab-intro"') == 5)
check("every page opens with its moving picture", 'id="page-art"' in admin.get("/payments").text and 'id="page-art"' in admin.get("/candidate/all").text)
check("what's new: told", any(release["key"] == "2026-11-04b" for release in whats_new.RELEASES))

db.close()
check.done()

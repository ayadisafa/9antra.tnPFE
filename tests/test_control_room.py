"""System › Control room: the live map counts what really flowed between the parts, every automatic functionality is
tracked, suggestions are said once (what only the admin can give is in "What I need from you"), what is new and every
functionality can be tested and marked. Runs on a COPY; nothing leaves this machine."""
from datetime import datetime

import testenv

testenv.prepare("control_room")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.test_mark import TestMark
from app.services import access, auth, control_room, page_tours, test_book

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.query(TestMark).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="control-room-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "control-room-1"})

# ---- A. The page ----
page = admin.get("/system/control")
check("the page opens", page.status_code == 200, page.status_code)
html = page.text
check("six views: live map, tracking, suggestions, what's new, every test, problems",
      all(f'data-cr-tab="{name}"' in html for name in ("map", "tracking", "ideas", "new", "tests", "problems")))
check("the live map: twelve parts, the lines between them, a canvas to light them", html.count('<a class="cr-node ') == 12 and 'id="cr-edges"' in html
      and 'id="cr-canvas"' in html)
check("in the menu, under System", 'href="/system/control"' in admin.get("/").text)
check("its page tour", page_tours.for_path("/system/control") is not None and 'id="page-tour"' in html)

# ---- B. What flowed is counted from the records ----
before = {edge["key"]: edge["count"] for edge in control_room.snapshot(db)["edges"]}
person = Candidate(full_name="Flow Tester", email="flow.tester@test.local")
db.add(person)
db.commit()
for kind in ("payment_reminder", "weekly_questions", "feedback_request"):
    db.add(CandidateEmail(candidate_id=person.id, direction="outbound", sender="centre@test.local", recipient=person.email, subject=kind,
                          body="x", intent=kind, author="system", status="sent", received_at=datetime.utcnow()))
db.add(CandidateEmail(candidate_id=person.id, direction="inbound", sender=person.email, recipient="centre@test.local", subject="q", body="Bonjour ?",
                      status="received", received_at=datetime.utcnow()))
db.commit()
after = {edge["key"]: edge["count"] for edge in control_room.snapshot(db)["edges"]}
check("a payment reminder sent: one more on Candidates → Payments", after["register>pay"] == before["register>pay"] + 1, (before["register>pay"], after["register>pay"]))
check("a review asked: one more on Classes → Reviews", after["classes>social"] == before["classes>social"] + 1)
check("a message in: one more on Inbox → Email assistant", after["inbox>assistant"] == before["inbox>assistant"] + 1)
data = admin.get("/system/control/data").json()
check("the page refreshes itself from the same figures", {edge["key"]: edge["count"] for edge in data["edges"]} == after and len(data["nodes"]) == 12
      and data["total"] == sum(after.values()) and all(node["figures"] for node in data["nodes"]), data["total"])

# ---- C. Tracking and suggestions ----
rows = control_room.tracking(db)
welcome = next(row for row in rows if row["key"] == "welcome")
check("every automatic functionality tracked: its state, last time, this week", len(rows) >= 20 and welcome["week"] is not None and welcome["last"])
needed = control_room.needs(db, "http://127.0.0.1:8000", True)
check("what is needed from the admin: the address candidates open (here, only this computer)", any("address" in item["title"].lower() for item in needed), [item["title"] for item in needed])
said = control_room.suggestions(db, None, needed)
given = {item["url"].split("#")[-1] for item in needed}
check("said once: nothing to look at points at what only the admin can give", not any(item["url"].split("#")[-1] in given for item in said),
      [item["text"] for item in said])
check("ideas from inside the application, each with why and what it needs", len(control_room.IDEAS) >= 5 and all(len(idea) == 5 and idea[3] and idea[4] for idea in control_room.IDEAS))

# ---- D. What is new, every test, and the marks ----
book = test_book.book(db)
every = [row for item in book["news"] for row in item["tests"]] + [row for group in book["pages"] for row in group["tests"]] + book["alone"] + book["yasmine"]
check("what is new: the latest deliveries, each with steps and what you should see", len(book["news"]) >= 4 and all(row["steps"] and row["expect"] for item in book["news"] for row in item["tests"]))
check("every page of the menu has its test", len([row for group in book["pages"] for row in group["tests"]]) >= 25)
check("everything it does by itself has a way to see it", len(book["alone"]) >= 20 and all(row["steps"][0] for row in book["alone"]))
check("Yasmine: each ability with a sentence to try", len(book["yasmine"]) >= 8 and all(row["steps"][0] for row in book["yasmine"]))
check("what sends to a person says so", any(row["sends"] for row in every) and not next(row for row in book["alone"] if row["key"] == "auto:backup")["sends"])
check("each test once", len({row["key"] for row in every}) == len(every) == book["progress"]["total"])
answer = admin.post("/system/control/mark", json={"key": "page:/inbox", "status": "ok"}).json()
check("“It works”: kept, counted", answer["status"] == "ok" and answer["progress"]["done"] == 1 and answer["progress"]["problems"] == 0, answer)
answer = admin.post("/system/control/mark", json={"key": "auto:welcome", "status": "problem", "note": "Nothing arrived after registering"}).json()
check("“Problem”, with what: kept, counted", answer["status"] == "problem" and answer["progress"]["problems"] == 1, answer)
html = admin.get("/system/control").text
check("the problems view lists it, with what", "Nothing arrived after registering" in html and 'class="cr-test is-problem"' in html)
check("…and the suggestions start with it", control_room.suggestions(db, test_book.book(db)["progress"])[0]["url"] == "#tab-problems")
answer = admin.post("/system/control/mark", json={"key": "auto:welcome", "status": "clear"}).json()
check("“Not tested again”: the mark is gone", answer["status"] is None and answer["progress"]["problems"] == 0)
check("an unknown result is refused", admin.post("/system/control/mark", json={"key": "page:/inbox", "status": "maybe"}).status_code == 400)

# ---- E. Languages and rights ----
admin.post("/language", data={"lang": "fr"})
data = admin.get("/system/control/data").json()
check("in French: the parts and what flows", any(node["title"] == "Candidats" for node in data["nodes"]) and any(edge["label"] == "avis demandés" for edge in data["edges"]))
french = admin.get("/system/control").text
check("in French: the tests are written in French", "Ouvrez Système › Salle de contrôle." in french)
admin.post("/language", data={"lang": "en"})
auth.create_user(db, email="viewer.cr@test.local", name="Vera Control", password="viewer-control-1", role=auth.STAFF)
viewer = db.query(User).filter(User.email == "viewer.cr@test.local").one()
viewer.permissions = access.dump({"payments": "view"})
db.commit()
looker = TestClient(main.app, follow_redirects=False)
looker.post("/login", data={"email": "viewer.cr@test.local", "password": "viewer-control-1"})
check("someone without the System page cannot open it", looker.get("/system/control").status_code in (302, 303, 403))
check("…nor mark a test", looker.post("/system/control/mark", json={"key": "page:/inbox", "status": "ok"}).status_code in (302, 303, 403))

# ---- F. What could be enhanced: ranked by the market by itself ----
from datetime import timedelta

from app.models.news_item import NewsItem
from app.services import enhancements, journal

check("the journal reads training and edtech news (five feeds)", "edtech" in journal.DOMAINS and sum(1 for feed in journal.FEEDS if feed[0] == "edtech") == 5)
db.query(NewsItem).delete()
db.commit()
now = datetime.utcnow()
for days, title in ((2, "Micro-credentials are reshaping bootcamps"), (5, "Digital badges and certificates employers trust"),
                    (9, "Les certificats numériques s'imposent en formation"), (12, "Why learners want a certificate at the end"),
                    (45, "Open badges in vocational training")):
    db.add(NewsItem(domain="edtech", source="EdSurge", title=title, url=f"https://example.org/{days}", summary="", published_at=now - timedelta(days=days)))
db.add(NewsItem(domain="cyber", source="BleepingComputer", title="Stolen credentials and expired TLS certificates", url="https://example.org/cyber", summary="",
                published_at=now - timedelta(days=1)))
db.add(NewsItem(domain="edtech", source="eLearning Industry", title="AI tutors in every course", url="https://example.org/ai", summary="", published_at=now - timedelta(days=3)))
for text in ("Bonjour, est-ce que j'aurai une attestation à la fin ?", "Est-ce qu'il y a un certificat ?"):
    db.add(CandidateEmail(candidate_id=person.id, direction="inbound", sender=person.email, recipient="centre@test.local", subject="q", body=text,
                          status="received", received_at=now))
db.commit()
market = enhancements.ranking(db)
first = market["ideas"][0] if market["ideas"] else {}
check("what is talked about and asked for, and missing, comes first: certificates", first.get("key") == "certificates", [item["key"] for item in market["ideas"]])
check("…with its evidence: articles (recent and older) and what candidates asked", first.get("recent") == 4 and first.get("older") == 1 and first.get("asked") == 2
      and len(first.get("examples", [])) == 3 and first.get("trend") == "up", {key: first.get(key) for key in ("recent", "older", "asked", "trend")})
check("security news does not count (“credentials”, “TLS certificates”)", all(example["source"] != "BleepingComputer" for item in market["ideas"] for example in item["examples"]))
check("what the centre already has is said apart: say it in your posts", any(item["key"] == "ai_tutor" for item in market["have"])
      and not any(item["key"] == "ai_tutor" for item in market["ideas"]))
check("what nobody talks about yet is watched, not pushed", any(item["key"] == "referral" for item in market["quiet"]))
html = admin.get("/system/control").text
check("the page shows the ranking with its evidence and the button to read again", "What could be enhanced, as the market moves" in html
      and "Micro-credentials are reshaping bootcamps" in html and 'action="/system/control/read-market"' in html and 'class="cr-rank-item' in html)
FEED = (b'<?xml version="1.0"?><rss><channel><item><title>Online payment links double course enrollments</title>'
        b'<link>https://example.org/pay</link><description>Payment links in reminders.</description></item></channel></rss>')
journal.fetch = lambda url: FEED if "edsurge" in url else None
before = next(item for item in enhancements.ranking(db)["ideas"] + enhancements.ranking(db)["quiet"] if item["key"] == "online_payment")["recent"]
answer = admin.post("/system/control/read-market")
db.expire_all()
after = next(item for item in enhancements.ranking(db)["ideas"] if item["key"] == "online_payment")
check("“Read the market again now”: the news is read, the ranking follows by itself", answer.status_code == 303 and before == 0 and after["recent"] == 1,
      (answer.status_code, before, after["recent"]))
admin.post("/language", data={"lang": "fr"})
french = admin.get("/system/control").text
check("in French: what each improvement is, and what it needs", "Un certificat PDF avec un QR code vérifiable" in french and "Articles qui en parlent en 60 jours : 5 (ces 30 derniers jours : 4)." in french)
admin.post("/language", data={"lang": "en"})

check.done()

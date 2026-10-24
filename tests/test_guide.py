"""The timetable's data (clashes said, finished classes left out), seeing the application as someone else (looking
only), professors reminded of their resources (words and timing changed by the admin, once per week), everything
the application does by itself, the guide for every role (steps, courses), and SMS tracked in the inbox.
Runs on a COPY; email, WhatsApp and SMS are fakes."""
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("guide")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Professor, Program, User, UserSession
from app.models.classsession import ClassSession
from app.models.course_material import ClassMaterial
from app.models.team_message import TeamMessage
from app.services import access, auth, automations, chatbot, email_threads as et, friend, howto, resource_reminders, whatsapp_inbox
from app.services.settings_service import get_settings

emails = []
et._attempt = lambda recipient, subject, *rest, **kw: (emails.append((recipient, subject, rest[0] if rest else "")) or (None, False))
et.smtp_configured = lambda: True


def where(response):
    return unquote_plus(response.headers.get("location", ""))


db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="guide-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "guide-test-1"})
program = db.query(Program).order_by(Program.name).first()
prof = Professor(name="Hamza Prof", specialties=program.name, email="hamza.prof@example.com", phone="22 555 666")
db.add(prof)
db.flush()
auth.create_user(db, email="hamza.login@example.com", name="Hamza Prof", password="hamza-prof-12", role=auth.PROFESSOR, professor_id=prof.id)
auth.create_user(db, email="lea.staff@test.local", name="Lea Staff", password="lea-staff-12", role=auth.STAFF)
lea = db.query(User).filter(User.email == "lea.staff@test.local").one()
lea.permissions = access.dump({"candidates": "view", "classes": "edit"})
db.commit()
hamza_user = db.query(User).filter(User.email == "hamza.login@example.com").one()

# ---- A. The timetable ----
start = datetime.combine(date.today() - timedelta(days=20), dtime(9, 0))
one = ClassGroup(name="TT-ONE", program_id=program.id, professor_id=prof.id, professor_name=prof.name, status=ClassStatus.ONGOING, start_date=start,
                 total_weeks=8, start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat")
two = ClassGroup(name="TT-TWO", program_id=program.id, professor_id=prof.id, professor_name=prof.name, status=ClassStatus.ONGOING, start_date=start,
                 total_weeks=8, start_time=dtime(10, 0), end_time=dtime(12, 0), days_of_week="sat")
old = ClassGroup(name="TT-OLD", program_id=program.id, professor_id=prof.id, professor_name=prof.name, status=ClassStatus.COMPLETED, start_date=start,
                 total_weeks=8, start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat")
db.add_all([one, two, old])
db.commit()
events = {event["className"]: event for event in admin.get("/api/dashboard/calendar/events").json() if event.get("className", "").startswith("TT-")}
check("timetable: each class with its name and program", events["TT-ONE"]["program"] == program.name)
check("timetable: the clash is said, with whom", events["TT-ONE"]["conflict"] and events["TT-ONE"]["clashesWith"] == ["TT-TWO"], events["TT-ONE"].get("clashesWith"))
check("timetable: a finished class clashes with nothing", not events["TT-OLD"]["conflict"])
page = admin.get("/").text
check("timetable: one view of the classes and their week", 'id="live-dashboard-classes"' in page and 'class="wb-week"' in page and "wb-now" in page
      and "Classes and weekly timetable" in page and "TT-ONE" in page and "TT-OLD" not in page)
check("timetable: the professor booked twice is said once, with both classes", page.count('class="wb-clash"') == 1 and "classes at the same time" in page
      and page.index("TT-ONE") < page.index('class="wb-week"') and "Hamza Prof" in page)
check("timetable: the hours shared by two classes", "09:00–13:00" in page and "10:00–12:00" in page)
from app.services import overview
from app.services.schedule_utils import local_now
loose = ClassGroup(name="TT-NODAY", program_id=program.id, status=ClassStatus.NOT_STARTED, total_weeks=8)
db.add(loose)
db.commit()
board = overview.week_board(db, local_now())
names = {card["name"] for card in board["cards"]}
saturday = next(row for row in board["rows"] if row["kind"] == "day" and row["index"] == 5)
mine = [slot for slot in saturday["slots"] if any(card["name"].startswith("TT-") for card in slot["classes"])]
check("board: each class once, a finished one left out", "TT-ONE" in names and "TT-TWO" in names and "TT-OLD" not in names
      and sum(1 for card in board["cards"] if card["name"] == "TT-ONE") == 1)
check("board: the classes of a day grouped by their hours, overlapping hours on two lanes",
      {slot["hours"] for slot in mine} >= {"09:00–13:00", "10:00–12:00"} and saturday["lanes"] >= 2
      and len({slot["lane"] for slot in saturday["bands"]}) >= 2, [(slot["hours"], slot.get("lane")) for slot in saturday["slots"]])
check("board: a class without a day is listed apart, not on a day", any(card["name"] == "TT-NODAY" for card in board["unplaced"])
      and not any(card["name"] == "TT-NODAY" for row in board["rows"] if row["kind"] == "day" for card in row["classes"]))
check("board: days without a class read as one line", all(row["first"]["index"] <= row["last"]["index"] for row in board["rows"] if row["kind"] == "free")
      and not any(a["kind"] == b["kind"] == "free" for a, b in zip(board["rows"], board["rows"][1:]))
      and sum((row["last"]["index"] - row["first"]["index"] + 1) if row["kind"] == "free" else 1 for row in board["rows"]) == 7)
hamza = next((clash for clash in board["clashes"] if clash["professor"] == "Hamza Prof"), None)
check("board: the professor booked twice, with both classes and when", hamza is not None and {item["name"] for item in hamza["classes"]} == {"TT-ONE", "TT-TWO"}
      and hamza["when"] == "Sat 10:00–12:00", hamza)
ones = next(card for card in board["cards"] if card["name"] == "TT-ONE")
check("board: a running class's weeks, places and hours", ones["running"] and len(ones["blocks"]) == 8 and ones["hours"] == "09:00–13:00"
      and ones["clash_with"] == ["TT-TWO"] and ones["days"] == [5], {key: ones[key] for key in ("week", "blocks", "hours", "clash_with", "days")})
check("board: the people fold holds the newest registrations and the programs", 'id="live-dashboard-registrations"' in page and 'id="live-dashboard-programs"' in page
      and page.index('id="live-dashboard-classes"') < page.index('id="live-dashboard-registrations"') < page.index('id="live-dashboard-programs"'))
db.delete(loose)
db.commit()

# ---- B. Seeing the application as someone else ----
response = admin.post(f"/settings/team/{lea.id}/view-as")
check("see as: the admin looks as a team member", response.status_code == 303 and auth.VIEW_AS_COOKIE in response.headers.get("set-cookie", ""))
page = admin.get("/candidate/all")
check("see as: their page, with the yellow bar", page.status_code == 200 and "You are seeing the application as Lea Staff" in page.text and "Back to my view" in page.text)
check("see as: their menu (no Settings, no Payments)", 'href="/payments"' not in page.text.split('<main')[0] and "Team and access" not in page.text.split('<main')[0])
check("see as: what they cannot open stays closed", admin.get("/payments", headers={"accept": "text/html"}).status_code in (303, 403))
before = get_settings(db).centre_phone
response = admin.post("/settings/numbers", data={"phone": "71 000 000", "chat": ""}, headers={"referer": "http://testserver/candidate/all"})
db.expire_all()
check("see as: nothing can be changed", response.status_code == 303 and "nothing can be changed" in where(response) and get_settings(db).centre_phone == before, where(response))
check("see as: “change what they can do” leads to their card", f"/account/view-as/stop?next=/settings/team%23person-{lea.id}" in page.text)
response = admin.get("/account/view-as/stop?next=/settings/team")
check("see as: back to my view", response.status_code == 303 and auth.VIEW_AS_COOKIE in response.headers.get("set-cookie", ""))
admin.cookies.delete(auth.VIEW_AS_COOKIE)
check("see as: the admin's pages again", "You are seeing the application as" not in admin.get("/").text and admin.get("/payments").status_code == 200)
admin.post(f"/settings/team/{hamza_user.id}/view-as")
page = admin.get("/teacher")
check("see as a professor: their own space", page.status_code == 200 and "You are seeing the application as Hamza Prof" in page.text)
check("see as a professor: the admin's pages closed", admin.get("/candidate/all", headers={"accept": "text/html"}).status_code in (303, 403))
admin.get("/account/view-as/stop")
admin.cookies.delete(auth.VIEW_AS_COOKIE)
staff = TestClient(main.app, follow_redirects=False)
staff.post("/login", data={"email": "lea.staff@test.local", "password": "lea-staff-12"})
check("see as: only an admin", staff.post(f"/settings/team/{hamza_user.id}/view-as").status_code in (303, 403))
check("see as: the button on the team page", f'/settings/team/{lea.id}/view-as' in admin.get("/settings/team").text)

# ---- C. Professors reminded of their resources ----
soon = date.today() + timedelta(days=1)
db.add(ClassSession(class_group_id=one.id, week_number=4, session_date=soon, start_time=dtime(9, 0), end_time=dtime(13, 0), topic="Docker networks"))
db.add(ClassSession(class_group_id=two.id, week_number=4, session_date=soon, start_time=dtime(10, 0), end_time=dtime(12, 0), topic="Volumes"))
db.add(ClassMaterial(class_group_id=two.id, week_number=4, filename="slides.pdf", stored_name="x", content_type="application/pdf", size=10, kind="document"))
db.commit()
due = [item for item in resource_reminders.due(db) if item["group"].name.startswith("TT-")]     # the copy's own classes may be due too
check("resources: a week without resources within two days is due", [(item["group"].name, item["week"]) for item in due] == [("TT-ONE", 4)],
      [(item["group"].name, item["week"]) for item in due])
response = admin.post("/classes/professors/resource-reminders", data={"on": "1", "days": "3", "text": "Salut {professor}, semaine {week} de {class_name} ({topic}) le {date} : {link}",
                                                                      "send_now": "1"})
db.expire_all()
sent = db.query(TeamMessage).filter(TeamMessage.person == f"professor:{prof.id}").all()
check("resources: sent now, with the admin's words filled", "sent now" in where(response) and "0 reminders" not in where(response)
      and any("Salut Hamza, semaine 4 de TT-ONE (Docker networks)" in row.body for row in sent)
      and not any("TT-TWO" in row.body for row in sent), (where(response), [row.body[:80] for row in sent]))
check("resources: in the application, by email and on WhatsApp", {row.channel for row in sent} >= {"app", "email", "whatsapp"})
check("resources: the timing kept", resource_reminders.settings_of(db)["days"] == 3)
check("resources: never twice", resource_reminders.send_due(db) == 0)
nobody = Professor(name="Nadia Nowhere", specialties=program.name)
db.add(nobody)
db.flush()
lost = ClassGroup(name="TT-LOST", program_id=program.id, professor_id=nobody.id, professor_name=nobody.name, status=ClassStatus.ONGOING, start_date=start,
                  total_weeks=8, start_time=dtime(15, 0), end_time=dtime(17, 0), days_of_week="sat")
db.add(lost)
db.flush()
db.add(ClassSession(class_group_id=lost.id, week_number=4, session_date=soon, start_time=dtime(15, 0), end_time=dtime(17, 0), topic="Logs"))
db.commit()
resource_reminders.send_due(db)
waiting = [item for item in resource_reminders.due(db) if item["group"].name == "TT-LOST"]
check("resources: a professor nobody can reach is not marked reminded", waiting and not waiting[0]["reachable"]
      and not db.query(TeamMessage).filter(TeamMessage.person == f"professor:{nobody.id}").count())
check("resources: and the card says so", "cannot be reached: add their email or phone" in admin.get("/classes/?tab=professors").text)
nobody.email = "nadia.nowhere@example.com"
db.commit()
check("resources: it goes once they can be reached", resource_reminders.send_due(db) >= 1
      and db.query(TeamMessage).filter(TeamMessage.person == f"professor:{nobody.id}", TeamMessage.channel == "email").count() == 1)
admin.post("/classes/professors/resource-reminders", data={"days": "2", "text": ""})
db.expire_all()
check("resources: can be stopped, and the words go back to the original", not resource_reminders.settings_of(db)["on"]
      and resource_reminders.settings_of(db)["text"] == resource_reminders.DEFAULT_TEXT)
check("resources: their card on the Professors tab", 'id="resources"' in admin.get("/classes/?tab=professors").text)

# ---- D. Everything it does by itself ----
found = {item["key"]: item for item in automations.listing(db)}
check("automatic: the list says what, when, and where to change it", {"welcome", "proofs", "resources", "recommendations", "sms", "ways"} <= set(found)
      and all(item["change"].startswith("/") and item["when"] for item in found.values()))
check("automatic: the resource reminders are off now, and said so", found["resources"]["state"] == "off")
page = admin.get("/system").text
check("automatic: on the System page, with their moving emojis", "Everything it does for you, by itself" in page and 'class="au-card' in page and 'class="emo emo-' in page)

# ---- E. The guide, for each role ----
check("guide: how to connect WhatsApp, for the admin", howto.task_for("how do I connect whatsapp business?", db.query(User).filter(User.email == "admin@test.local").one() and auth.CurrentUser(id="a", name="A", email="a", role="admin"))["key"] == "whatsapp-setup")
professor_user = auth.CurrentUser(id=hamza_user.id, name="Hamza Prof", email="h", role="professor", professor_id=prof.id)
staff_user = auth.CurrentUser(id=lea.id, name="Lea", email="l", role="staff", permissions=access.clean(lea.permissions))
check("guide: not for a professor", howto.task_for("how do I connect whatsapp business?", professor_user) is None)
check("guide: a professor learns to add their resources", howto.task_for("comment ajouter mes ressources pour la semaine ?", professor_user)["key"] == "resources-add")
check("guide: a team member only for their parts", howto.task_for("how do I change the welcome message?", staff_user) is None
      and howto.task_for("how do I add a professor to a program?", staff_user)["key"] == "professor-add")
check("guide: SMS from candidates", howto.task_for("how do I receive and track sms from candidates?", None)["key"] == "sms-setup")
professor_client = TestClient(main.app, follow_redirects=False)
professor_client.post("/login", data={"email": "hamza.login@example.com", "password": "hamza-prof-12"})
answer = professor_client.post("/account/guide", json={"message": "how do I mark presence?"}).json()
check("guide: Yasmine answers a professor, step by step", answer["reply"].startswith("Mark who was there") and "1. " in answer["reply"] and answer["links"][0]["label"] == "Take me there", answer)
answer = professor_client.post("/account/guide", json={"message": f"what is in week 1 of {program.name}?"}).json()
check("guide: what the courses teach", ("week 1" in answer["reply"].lower() or "semaine 1" in answer["reply"].lower() or program.name in answer["reply"]), answer["reply"][:200])
said = professor_client.get("/account/friend").json()
check("guide: Yasmine lets everyone ask (the guide for a professor)", said["can_ask"] and said["ask_url"] == "/account/guide")
check("guide: the admin's Yasmine asks the assistant", admin.get("/account/friend").json()["ask_url"] == "/assistant/chat")
found = chatbot.answer(db, "how do I set up sms from my android phone?", [], user=auth.CurrentUser(id="a", name="A", email="a", role="admin"))
check("assistant: “how do I…” gets the steps", "SMS Gateway for Android" in found["reply"] and "1. " in found["reply"], found["reply"][:200])

# ---- F. SMS tracked in the inbox ----
candidate = Candidate(full_name="Sonia Sms", email="sonia.sms@example.com", phone="98 777 666")
db.add(candidate)
db.commit()
whatsapp_inbox.receive_sms(db, phone="98777666", text="Bonjour, la séance est à quelle heure ?", sms_id="SMS-GUIDE-1")
db.commit()
page = admin.get("/inbox?filter=sms").text
check("sms: the inbox has its SMS filter, with the candidate's SMS", "/inbox?filter=sms" in page and "Sonia Sms" in page)

db.close()
check.done()

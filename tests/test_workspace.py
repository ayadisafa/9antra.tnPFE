"""Find anything, change any value, and a team with chosen access. Staging copy, COPY of the database; no mail leaves."""
import testenv
testenv.prepare("workspace")
check = testenv.Checks()

import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from urllib.parse import unquote_plus
from app.database import SessionLocal
from app.models import Candidate, EditLog, Professor, Registration, SystemSettings, User, UserSession
from app.models.registration import PaymentStatus
from app.services import access, auth, data_editor as de, email_templates as tp, email_threads as et, finder

et.send_email = lambda **kw: True; tp.send_email = lambda **kw: True
db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="workspace-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "workspace-1"})
def where(response): return unquote_plus(response.headers.get("location", ""))
person = Candidate(full_name="Yasmine Gharbi", email="yasmine.ws@test.local", phone="+21622333444", university="INSAT", city="Bizerte"); db.add(person); db.commit()

# ======================================================================
# A. Find anything
# ======================================================================
found = finder.find(db, "colour night")
check("a setting is found by what it does", any(item["url"] == "/settings#appearance" for group in found for item in group["items"]), found)
found = {group["group"]: group["items"] for group in finder.find(db, "yasmine")}
check("a candidate is found by name", found.get("Candidates", [{}])[0].get("url") == f"/candidate/{person.id}" and "INSAT" in found["Candidates"][0]["detail"], found)
check("and by phone or university", any(item["title"] == "Yasmine Gharbi" for group in finder.find(db, "22333444") + finder.find(db, "insat") for item in group["items"]))
check("an action is found in French words too", any(item["url"] == "/candidate/all?add=1" for group in finder.find(db, "ajouter candidat") for item in group["items"]))
check("the kinds of data are found as well", any(item["url"] == "/data/payments" for group in finder.find(db, "payments") for item in group["items"]))
check("one letter finds nothing, no error", finder.find(db, "y") == [] and finder.find(db, "") == [])
answer = admin.get("/find?format=json&q=yasmine").json()
check("the bar at the top asks for results while typing", answer["query"] == "yasmine" and any(group["group"] == "Candidates" for group in answer["groups"]))
page = admin.get("/find?q=backup")
check("the search has its own page", page.status_code == 200 and "Your data: copies and safety" in page.text, page.status_code)
home = admin.get("/").text
check("every page has the search bar, with Ctrl+K", 'id="find-form"' in home and 'action="/find"' in home and "Ctrl+K" in home and "See everything found" in home)

# ======================================================================
# B. Every value
# ======================================================================
page = admin.get("/data")
check("the data page lists every kind of record with how many there are", page.status_code == 200 and all(f'id="table-{key}"' in page.text for key in de.TABLES) and "Every value, in one place" in page.text)
page = admin.get("/data/candidates?q=bizerte")
check("a kind of record can be searched in every field", page.status_code == 200 and "Yasmine Gharbi" in page.text and "1 record matching" in page.text)
page = admin.get(f"/data/candidates/{person.id}")
check("a record shows all its values as a form", page.status_code == 200 and 'name="city" value="Bizerte"' in page.text and 'name="full_name"' in page.text and "password" not in page.text.lower().split("<form")[1].split("</form>")[0])
answer = admin.post(f"/data/candidates/{person.id}", data={"full_name": "Yasmine Gharbi", "email": "yasmine.ws@test.local", "phone": "+21622333444", "university": "INSAT", "city": "Sousse"})
db.expire_all()
log = db.query(EditLog).filter(EditLog.table == "candidates", EditLog.row_id == person.id).first()
check("any value can be changed", answer.status_code == 303 and db.get(Candidate, person.id).city == "Sousse" and "Saved" in where(answer))
check("the change is kept: who, what it was, what it became", log is not None and log.who == "Safa Admin" and '"city": ["Bizerte", "Sousse"]' in log.changes and "Bizerte → Sousse" in admin.get(f"/data/candidates/{person.id}").text)
check("a required value cannot be emptied", "cannot be empty" in admin.post(f"/data/candidates/{person.id}", data={"full_name": "", "email": ""}).text and db.get(Candidate, person.id).email == "yasmine.ws@test.local")
registration = db.query(Registration).first()
page = admin.get(f"/data/registrations/{registration.id}").text
check("a choice is offered as a list, a link to another record by its name", '<select class="form-select" id="f-payment_status"' in page and "PAID" in page and '<select class="form-select" id="f-candidate_id"' in page)
form = {field["name"]: de.form_value(getattr(registration, field["name"]), field["widget"]) for field in de.columns(Registration) if not field["readonly"]}
form["paid_amount"] = "abc"
check("a number written wrongly is refused with the field's name", "is not written the right way" in admin.post(f"/data/registrations/{registration.id}", data=form).text)
form["paid_amount"] = str(registration.paid_amount or 0)
form["payment_status"] = "PAID"
answer = admin.post(f"/data/registrations/{registration.id}", data=form)
db.expire_all()
check("a choice and dates come back as they were saved", answer.status_code == 303 and db.get(Registration, registration.id).payment_status == PaymentStatus.PAID, where(answer))
answer = admin.post("/data/professors/new", data={"name": "Mehdi Karoui", "email": "mehdi@test.local", "specialties": "Python, DevOps", "is_available": "yes"})
made = db.query(Professor).filter(Professor.name == "Mehdi Karoui").first()
check("a record can be added anywhere", answer.status_code == 303 and made is not None and made.is_available is True and where(answer).startswith(f"/data/professors/{made.id}"))
answer = admin.post(f"/data/professors/{made.id}/delete")
db.expire_all()
check("and removed, with a trace in the log", "Removed" in where(answer) and db.query(Professor).filter(Professor.name == "Mehdi Karoui").first() is None
      and db.query(EditLog).filter(EditLog.table == "professors", EditLog.action == "delete").count() == 1)
settings_row = db.query(SystemSettings).first()
answer = admin.post(f"/data/settings/{settings_row.id}", data={**{field["name"]: de.form_value(getattr(settings_row, field["name"]), field["widget"]) for field in de.columns(SystemSettings) if not field["readonly"]}, "app_name": "9antra Desk"})
db.expire_all()
check("even every setting has its value here", answer.status_code == 303 and db.query(SystemSettings).first().app_name == "9antra Desk", where(answer))
check("an unknown kind of record or record is not found", admin.get("/data/nothing").status_code == 404 and admin.get("/data/candidates/nope").status_code == 404)
check("the users and their passwords are not among the records", "users" not in de.TABLES and all(field["name"] not in ("password_hash", "token_hash") for spec in de.TABLES.values() for field in de.columns(spec[1])))

# ======================================================================
# C. The team
# ======================================================================
page = admin.get("/settings/team")
check("the team page lists who signs in", page.status_code == 200 and "Safa Admin" in page.text and "Who signs in, and what each one sees" in page.text and 'id="add"' in page.text, page.status_code)
answer = admin.post("/settings/team/add", data={"name": "Lina Mansour", "email": "lina@test.local", "password": "lina-pass-1", "role": "staff",
                                                "perm_candidates": "view", "perm_payments": "edit", "perm_dashboard": "none"})
lina = db.query(User).filter(User.email == "lina@test.local").first()
check("a team member is added with the access chosen for each part", answer.status_code == 303 and lina is not None and lina.role == "staff"
      and access.clean(lina.permissions) == {**{key: "none" for key, *_ in access.SECTIONS}, "candidates": "view", "payments": "edit"}, lina.permissions if lina else None)
staff = TestClient(main.app, follow_redirects=False)
staff.post("/login", data={"email": "lina@test.local", "password": "lina-pass-1"})
check("they land on the first part they may see", where(staff.get("/", headers={"accept": "text/html"})) == "/candidate/all")
check("they open what they may look at", staff.get("/candidate/all").status_code == 200 and staff.get(f"/candidate/{person.id}").status_code == 200)
page = staff.get("/candidate/all").text
check("their menu shows only their parts, marked when they can only look", 'href="/candidate/all" class="nav-link' in page and 'href="/payments" class="nav-link' in page and 'href="/settings" class="nav-link' not in page
      and 'href="/data" class="nav-link' not in page and "You can look, not change" in page)
answer = staff.post("/candidate/create", data={"full_name": "X", "email": "x@test.local"}, headers={"referer": "/candidate/all"})
check("looking is not changing: a form sends them back, told why", answer.status_code == 303 and "You can look at this part, but not change it" in where(answer) and db.query(Candidate).filter(Candidate.email == "x@test.local").first() is None, where(answer))
check("where they may change, they can", staff.get("/payments").status_code == 200)
for path in ("/settings", "/data", "/settings/team", "/system", "/reminders", "/website"):
    check(f"a part not given stays closed: {path}", staff.get(path, headers={"accept": "text/html"}).status_code in (303, 403) and "/settings" not in where(staff.get(path, headers={"accept": "text/html"})))
check("the admin's own pages stay closed even with every part given", not access.allows({key: "edit" for key, *_ in access.SECTIONS}, "/settings/team", "GET") and not access.allows({key: "edit" for key, *_ in access.SECTIONS}, "/system/backups/x.zip", "GET"))
check("the assistant, which knows everything, is not for them", staff.post("/assistant/chat", json={"message": "who has not paid?"}).status_code == 403)
results = staff.get("/find?format=json&q=settings colour").json()["groups"]
check("the search only finds what they may open", not any("/settings" in item["url"] for group in results for item in group["items"]), results)
check("but finds the candidates they may see", any(group["group"] == "Candidates" for group in staff.get("/find?format=json&q=yasmine").json()["groups"]))
admin.post(f"/settings/team/{lina.id}/access", data={"perm_candidates": "edit", "perm_settings": "view"})
check("a new access applies at once", staff.get("/settings").status_code == 200 and staff.post("/candidate/create", data={"full_name": "Nour Test", "email": "nour.ws@test.local"}).status_code == 303
      and db.query(Candidate).filter(Candidate.email == "nour.ws@test.local").first() is not None)
check("and what was taken away is closed again", staff.get("/payments", headers={"accept": "text/html"}).status_code in (303, 403))
answer = admin.post(f"/settings/team/{lina.id}/password", data={"password": "short"})
check("a password too short is refused", "at least" in where(answer))
answer = admin.post(f"/settings/team/{lina.id}/active")
db.expire_all()
check("stopping their access signs them out everywhere", "can no longer sign in" in where(answer) and db.get(User, lina.id).is_active is False and staff.get("/candidate/all", headers={"accept": "text/html"}).status_code == 303)
me = db.query(User).filter(User.email == "admin@test.local").first()
check("you cannot switch off yourself, nor change your own role", "cannot switch off your own" in where(admin.post(f"/settings/team/{me.id}/active")) and "own role" in where(admin.post(f"/settings/team/{me.id}/role", data={"role": "staff"})))
admin.post(f"/settings/team/{lina.id}/active")
admin.post(f"/settings/team/{lina.id}/role", data={"role": "admin"})
again = TestClient(main.app, follow_redirects=False); again.post("/login", data={"email": "lina@test.local", "password": "lina-pass-1"})
check("a team member can be made an admin", again.get("/settings").status_code == 200 and again.get("/data").status_code == 200)
check("only an admin manages the team", staff.get("/settings/team").status_code in (303, 401, 403))
check("the sections and their addresses agree", access.section_of("/candidate/all") == "candidates" and access.section_of("/") == "dashboard" and access.section_of("/programs/x/pdf") == "programs"
      and access.section_of("/payments") == "payments" and access.section_of("/nowhere") is None)
check.done()

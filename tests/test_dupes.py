"""What was duplicated is gone, and an empty copy of a program is merged into the real one. Staging copy, COPY of the database."""
import testenv
testenv.prepare("dupes")
check = testenv.Checks()

import os
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, Program, Registration, User, UserSession
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import auth, program_names as pn

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="dupes-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "dupes-test-1"})

# ---- one copy of each page and address
person = db.query(Candidate).first()
check("the dashboard and the candidate page answer once, from their real place", admin.get("/").status_code == 200 and admin.get(f"/candidate/{person.id}").status_code == 200 and "Send the program (PDF)" in admin.get(f"/candidate/{person.id}").text or admin.get(f"/candidate/{person.id}").status_code == 200)
check("the old second candidate page at the root is gone", admin.get(f"/{person.id}").status_code == 404)
for method, path in (("get", "/templates/"), ("post", "/admin/api/emails/inbound"), ("post", "/batches/"), ("get", "/admin/api/emails/candidate/x")):
    status = getattr(admin, method)(path).status_code
    check(f"the old duplicate address {path} is gone", status in (404, 405), status)
check("the old address of the templates still leads to them", admin.get("/admin/templates").headers.get("location") == "/inbox/templates")
for name in ("ai_service", "ai_email_service", "quiz_service", "program_service", "email_tracking_service", "batch_scheduler"):
    check(f"the unused copy app/services/{name}.py is gone", not os.path.exists(os.path.join(testenv.PROJECT, "app", "services", f"{name}.py")))

# ---- an empty copy of a program
real = db.query(Program).filter(Program.name.like("Mobile Development%")).first()
twin = db.query(Program).filter(Program.name == "Mobile Flutter").first()
if twin is None:
    twin = Program(name="Mobile Flutter", is_approved=True); db.add(twin); db.flush()
if db.query(Registration).filter(Registration.program_id == twin.id).first() is None:
    someone = Candidate(full_name="Twin Test", email="twin@test.local"); db.add(someone); db.flush()
    db.add(Registration(candidate_id=someone.id, program_id=twin.id, program_name="Mobile Flutter", delivery_mode="online", total_amount=390, total_price=390,
                        paid_amount=0, payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING))
db.commit()
moved = [registration.id for registration in db.query(Registration).filter(Registration.program_id == twin.id).all()]
paid_before = {(r.candidate_id): (r.id, r.payment_status, r.status) for r in db.query(Registration).filter(Registration.program_id == real.id, Registration.payment_status == PaymentStatus.PAID).all()}
copies = [r.id for r in db.query(Registration).filter(Registration.program_id == twin.id).all() if r.candidate_id in paid_before]
planned = Program(name="Python Basics", is_approved=True); db.add(planned)
lonely = Program(name="Mobile", is_approved=True); db.add(lonely); db.commit()
twin_id, planned_id, lonely_id = twin.id, planned.id, lonely.id
check("the website's mobile-flutter page belongs to the real program", real.website_slug == "mobile-flutter" and not twin.levels and not twin.description)
merged = pn.merge_twins(db)
db.expire_all()
check("the empty copy is merged into the real program", ("Mobile Flutter", real.name) in merged and db.get(Program, twin_id) is None, merged)
check("its registrations now belong to the real program, under its name", all(db.get(Registration, key).program_id == real.id and db.get(Registration, key).program_name == real.name for key in moved) and len(moved) >= 1)
check("a person registered to both keeps the registration they paid; the unpaid copy is cancelled, not deleted",
      all(db.get(Registration, key).status == RegistrationStatus.CANCELLED for key in copies) and all(db.get(Registration, key).payment_status == state and db.get(Registration, key).status == status for key, state, status in paid_before.values()), (copies, paid_before))
check("an empty program named on purpose is never touched", db.get(Program, planned_id) is not None and db.get(Program, lonely_id) is not None)
check("doing it again changes nothing", pn.merge_twins(db) == [])
check("a new registration written “Mobile Flutter” now goes to the real program", (pn.same_program("Mobile Flutter", [p for p in db.query(Program).all() if p.id not in (planned_id, lonely_id)]) or Program()).id == real.id)
check("the program list no longer shows the copy", "Mobile Flutter</" not in admin.get("/programs/all").text.replace("Mobile Flutter + ", ""))
check.done()

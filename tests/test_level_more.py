"""The level check, continued: the candidate's answers shown one by one with what they tell, and the one
reminder to those who do not answer, which the admin can stop. Staging copy, COPY of the database."""
import testenv
testenv.prepare("level_more")
check = testenv.Checks()

from datetime import datetime, timedelta, time as dtime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Program, Registration, User, UserSession
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, RegistrationStatus
from app.scheduler import jobs
from app.services import auth, email_assistant as ea, email_templates as tp, email_threads as et, level_check as lc
from app.services.settings_service import get_settings

sent_mail = []
def fake_send(**kw): sent_mail.append(kw); return True
et.send_email = fake_send; tp.send_email = fake_send
et.smtp_configured = lambda: True
ea.model_available = lambda: True
ea.llm_json = lambda *a, **k: None

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="level-more-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "level-more-1"})
public = TestClient(main.app, follow_redirects=False)
from urllib.parse import unquote_plus
def where(response): return unquote_plus(response.headers.get("location", ""))
lc.retire_old(db)
settings = get_settings(db); settings.public_base_url = "https://centre.example.com"; db.commit()
BASE = "https://centre.example.com"
devops = db.query(Program).filter(Program.name == "DevOps").first()
def person(name, email):
    one = Candidate(full_name=name, email=email, phone="50 123 456"); db.add(one); db.flush()
    reg = Registration(candidate_id=one.id, program_id=devops.id, program_name=devops.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=390,
                       payment_status=PaymentStatus.PAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow())
    db.add(reg); db.commit()
    return reg, lc.prepare(db, reg)
def subjects(): return [str(m.get("subject")) for m in sent_mail]
now = datetime.utcnow()

# ======================================================================
# A. One reminder, never a second
# ======================================================================
check("the column that remembers the reminder exists", hasattr(Quiz, "reminded_at") and db.query(Quiz).filter(Quiz.reminded_at.isnot(None)).count() == 0)
check("reminders are on unless the admin stops them", lc.reminders_on(db))
reg_a, quiz_a = person("Aya Silencieuse", "aya.remind@example.com")
reg_b, quiz_b = person("Bilel Recent", "bilel.remind@example.com")
reg_c, quiz_c = person("Chaima Jamais", "chaima.remind@example.com")       # prepared, never sent
reg_d, quiz_d = person("Dali Repondu", "dali.remind@example.com")
for quiz in (quiz_a, quiz_b, quiz_d):
    lc.send(db, quiz, BASE)
quiz_a.sent_at = now - timedelta(days=3); quiz_b.sent_at = now - timedelta(hours=20); quiz_d.sent_at = now - timedelta(days=4)
db.commit()
items = lc.items_of(quiz_d)
public.post(f"/level/{quiz_d.access_token}", data={f"q{i}": ("" if item["type"] == "note" else "0") for i, item in enumerate(items)})
db.expire_all()
sent_mail.clear()
count = lc.remind_due(db, BASE)
db.expire_all()
check("the one who got it three days ago and said nothing is reminded", count == 1 and len(sent_mail) == 1 and "cinq minutes pour nous dire où vous en êtes" in subjects()[0], subjects())
mail = " ".join(str(v) for v in sent_mail[0].values()) if sent_mail else ""
check("the reminder carries the same private link and says it is the only one", f"{BASE}/level/{quiz_a.access_token}" in mail and "seul rappel" in mail)
check("it goes to that candidate", "aya.remind@example.com" in mail)
check("it is remembered", db.get(Quiz, quiz_a.id).reminded_at is not None)
check("not yet for the one who got it yesterday", db.get(Quiz, quiz_b.id).reminded_at is None)
check("never for one that was not sent, nor for one who answered", db.get(Quiz, quiz_c.id).reminded_at is None and db.get(Quiz, quiz_d.id).reminded_at is None)
sent_mail.clear()
check("a second run sends nothing", lc.remind_due(db, BASE) == 0 and sent_mail == [])
check("nor a week later: once means once", lc.remind_due(db, BASE, now=now + timedelta(days=7)) == 1 and "bilel.remind@example.com" in " ".join(str(v) for v in sent_mail[0].values()) and len(sent_mail) == 1)
sent_mail.clear()
check("after that, nobody is left to remind", lc.remind_due(db, BASE, now=now + timedelta(days=30)) == 0 and sent_mail == [])

# Stopped by the admin.
reg_e, quiz_e = person("Emna Stop", "emna.remind@example.com")
lc.send(db, quiz_e, BASE); quiz_e.sent_at = now - timedelta(days=5); db.commit()
answer = admin.post("/quiz/reminders", data={"on": "0"})
check("the admin stops the reminders", answer.status_code == 303 and "stopped" in where(answer) and not lc.reminders_on(db), where(answer))
sent_mail.clear()
check("then nothing is sent, and nothing is marked", lc.remind_due(db, BASE) == 0 and sent_mail == [] and db.get(Quiz, quiz_e.id).reminded_at is None)
page = admin.get("/quiz/all").text
check("the page says they are stopped, for everyone and on the candidate", "Reminders are stopped" in page and "No reminder: you stopped them" in page and "Remind again" in page)
answer = admin.post("/quiz/reminders", data={"on": "1"})
check("the admin starts them again", "on again" in where(answer) and lc.reminders_on(db))
page = admin.get("/quiz/all").text
check("the page says who will be reminded and who was", "One reminder, by itself" in page and "One reminder in the next minutes" in page and "Reminded once" in page and "Stop the reminders" in page)
check("the wording of the reminder is kept when it is switched", tp.get(db, "level_reminder").subject == tp.AUTO_EMAILS["level_reminder"].subject)

# The link only opens on this computer: no reminder leaves.
settings = get_settings(db); settings.public_base_url = None; db.commit()
sent_mail.clear()
check("no reminder while candidates cannot open the link", lc.remind_due(db, "http://127.0.0.1:8000") == 0 and sent_mail == [] and db.get(Quiz, quiz_e.id).reminded_at is None)
settings = get_settings(db); settings.public_base_url = BASE; db.commit()

# Their class started meanwhile: reminding is no longer useful.
group = ClassGroup(name="REMIND-DEVOPS-1", program_id=devops.id, status=ClassStatus.ONGOING, start_date=datetime.now() - timedelta(days=1), total_weeks=8,
                   start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat", start_mode="fixed_date", capacity=10, max_capacity=10)
db.add(group); db.flush()
reg_e = db.get(Registration, reg_e.id); reg_e.class_group_id = group.id; reg_e.status = RegistrationStatus.ASSIGNED; db.commit()
sent_mail.clear()
check("a candidate whose class already started is not reminded", lc.remind_due(db, BASE) == 0 and sent_mail == [])
db.expire_all()
check("and is not looked at again", db.get(Quiz, quiz_e.id).reminded_at is not None)

# The scheduled work does it.
reg_f, quiz_f = person("Fares Tick", "fares.remind@example.com")
lc.send(db, quiz_f, BASE); quiz_f.sent_at = datetime.utcnow() - timedelta(days=2, hours=1); db.commit()
sent_mail.clear()
jobs.weekly_questions_tick()
db.expire_all()
check("the ten-minute work sends the reminder that is due", any("cinq minutes pour nous dire" in s for s in subjects()) and db.get(Quiz, quiz_f.id).reminded_at is not None, subjects())
sent_mail.clear()
jobs.weekly_questions_tick()
check("and only once", not any("cinq minutes pour nous dire" in s for s in subjects()))
templates_page = admin.get("/inbox/templates").text
check("the reminder is an email the admin can read, change and switch off", "Level check: the reminder" in templates_page)

# ======================================================================
# B. The answers, one by one, and what they tell
# ======================================================================
reg_g, quiz_g = person("Ghada Analyse", "ghada.remind@example.com")
items = lc.items_of(quiz_g)
data, position = {}, 0
for index, item in enumerate(items):
    if item["type"] == "experience": data[f"q{index}"] = "1"
    elif item["type"] == "goal": data[f"q{index}"] = "2"
    elif item["type"] == "topic": data[f"q{index}"] = "3" if index == 2 else "1"
    elif item["type"] == "check":
        # the three bases right, the first practice question wrong, the rest unknown
        data[f"q{index}"] = str(item["correct_answer"]) if position < 3 else (str((item["correct_answer"] + 1) % 4) if position == 3 else str(lc.DONT_KNOW))
        position += 1
    else: data[f"q{index}"] = ""
public.post(f"/level/{quiz_g.access_token}", data=data)
db.expire_all()
detail = lc.answers_of(db.get(Quiz, quiz_g.id))
states = [c["state"] for c in detail["checks"]]
check("each check question is right, wrong or not known", states == ["right"] * 3 + ["wrong"] + ["unknown"] * 4, states)
wrong = detail["checks"][3]
check("a wrong answer shows what they chose and what was right", wrong["given"] and wrong["right"] and wrong["given"] != wrong["right"] and wrong["step_name"] == "Practice")
check("“I don't know yet” has no chosen answer", detail["checks"][4]["given"] is None and detail["checks"][4]["right"])
check("who they are is in their own words", detail["you"][0]["answer"] == lc.EXPERIENCE[1] and detail["you"][1]["answer"] == lc.GOALS[2])
check("each topic says where they placed themselves", detail["topics"][0]["said"] == "Is at ease with it" and detail["topics"][1]["said"] == "Has heard of it")
reading = " ".join(detail["reading"])
check("the reading says how far they answer, what is a wrong idea and what is simply new",
      "up to “the bases” (3 of 8 questions)" in reading and "1 answered wrongly" in reading and "“I don't know yet” to 4" in reading and "Feels at ease with: DevOps Concepts" in reading, detail["reading"])
check("nothing for a form not answered yet", lc.answers_of(db.get(Quiz, quiz_c.id)) is None)
page = admin.get(f"/quiz/all?focus={quiz_g.id}").text
card = page[page.index(f'id="level-{quiz_g.id}"'):]
card = card[:card.index("</article>")]
check("the page shows what their answers tell", "What their answers tell" in card and "answered wrongly" in card)
check("and every answer, one by one", "Their answers, one by one" in card and card.count('class="lv-check is-right"') == 3 and card.count('class="lv-check is-wrong"') == 1 and card.count('class="lv-check is-unknown"') == 4)
check("with the right answer next to a wrong one", "Right answer:" in card and "Answered:" in card and "Who they are" in card and "The program, topic by topic" in card)
check("the candidate's own page still shows no right answers", "Right answer" not in public.get(f"/level/{quiz_g.access_token}").text)
check("the level checks page opens", admin.get("/quiz/all").status_code == 200)
check("the System page tells about the reminder", "reminds once those who have not answered their level check" in admin.get("/system").text)
db.close()
check.done()

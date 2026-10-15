"""Stage 2: the weekly check-in and its analysis, the Google Form -> Firebase -> here road
(Google and Firebase are stand-ins), WhatsApp sending, name and icon.
Runs on a COPY of the database; nothing leaves this machine."""
import os, sys, io, json, shutil, sqlite3
import testenv
DB = testenv.prepare("stage2")
SCRATCH = testenv.WORK

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network
import requests
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
requests.get = _no_web; requests.post = _no_web

from datetime import datetime, timedelta, time as dtime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Activity, Candidate, ClassGroup, ClassStatus, ClassWeek, Program, Quiz, Registration, User, UserSession, WeekQuestion
from app.models.classsession import ClassSession
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (advisor, answer_sync as sync, auth, brand, course_materials as cm, email_assistant as ea, email_templates as tp,
                          email_threads as et, google_forms as gf, week_insights as wi, weekly_questions as wq, whatsapp as wa)
from app.services.settings_service import get_settings

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

sent_mail = []
def fake_send(**kw): sent_mail.append(kw); return True
et.send_email = fake_send; tp.send_email = fake_send
et.smtp_configured = lambda: True
model = {"answers": []}
ea.model_available = lambda: True
ea.llm_json = lambda system, prompt, max_tokens=400: model["answers"].pop(0) if model["answers"] else None

db = SessionLocal()
now = datetime.utcnow()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False

# ======================================================================
# A. Reading words
# ======================================================================
tone = lambda text: wi.tone_of(text)["tone"]
cases = [
    ("Tout était clair, j'ai bien compris, merci !", "positive"),
    ("C'était trop rapide, je n'ai pas compris les volumes Docker et je suis perdu.", "negative"),
    ("Pas de problème cette semaine.", "positive"),
    ("Ce n'était pas facile, beaucoup de difficultés avec l'installation.", "negative"),
    ("The course was great but the exercises were confusing.", "mixed"),
    ("I am lost and stuck, it is too fast.", "negative"),
    ("s3ib barcha, mafhemtch el partie mta3 docker", "negative"),
    ("behi barcha, fhemt kol chay", "positive"),
    ("RAS", "neutral"),
    ("", "none"),
    ("Le cours n'est pas difficile.", "positive"),
]
wrong = [(text, want, tone(text)) for text, want in cases if tone(text) != want]
check(f"words: the tone of {len(cases)} sentences in French, English and Tunisian is read right", not wrong, wrong)
check("words: what the difficulty is about is found", set(wi.themes_of("Trop rapide, pas assez d'exercices, et l'installation de Docker ne marche pas sur mon pc")) >= {"pace", "practice", "tools"}, wi.themes_of("Trop rapide, pas assez d'exercices, et l'installation de Docker ne marche pas sur mon pc"))
check("words: a call for help and a wish to stop are seen", wi._ASKS_HELP.search(wi._plain("Pouvez-vous réexpliquer les volumes ?")) and wi._GIVING_UP.search(wi._plain("Je pense abandonner la formation")) and not wi._GIVING_UP.search(wi._plain("tout va bien")))
check("form words: what a Google Form sends back is understood", wi.from_words("mood", "😄 Très bien") == "5" and wi.from_words("pace", "Trop rapide") == "fast" and wi.from_words("confidence", "Oui, seul(e)") == "4" and wi.from_words("mood", "autre") is None)
clean = wi.clean_feedback({"mood": "9", "pace": "warp", "confidence": "2", "hard": "  x " * 900, "message": None})
check("check-in: unknown values are dropped and long texts cut", clean["mood"] is None and clean["pace"] is None and clean["confidence"] == 2 and len(clean["hard"]) <= wi.TEXT_LIMIT)

# ======================================================================
# B. A class, its week, its questions
# ======================================================================
devops = db.query(Program).filter(Program.name == "DevOps").first()
start = (datetime.now() - timedelta(days=16)).replace(hour=9, minute=0, second=0, microsecond=0)
group = ClassGroup(name="FEEL-DEVOPS-1", program_id=devops.id, status=ClassStatus.ONGOING, start_date=start, total_weeks=8, current_week=3,
                   start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat", start_mode="fixed_date", capacity=20, max_capacity=20, weekly_questions_count=3)
db.add(group); db.flush()
for week in (1, 2, 3):
    db.add(ClassSession(class_group_id=group.id, week_number=week, session_date=(start + timedelta(weeks=week - 1)).date(), start_time=dtime(9, 0), end_time=dtime(13, 0), topic=f"Docker, partie {week}"))
people = {}
for name, email, phone in (("Amal Calme", "amal@example.com", "50 100 200"), ("Bilel Perdu", "bilel@example.com", "50 100 201"), ("Chaima Sure", "chaima@example.com", "50 100 202"), ("Dali Doute", "dali@example.com", "+33 6 11 22 33 44")):
    person = Candidate(full_name=name, email=email, phone=phone); db.add(person); db.flush()
    reg = Registration(candidate_id=person.id, program_id=devops.id, program_name="DevOps", delivery_mode="online", total_amount=390, total_price=390, paid_amount=390,
                       payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, registration_date=now - timedelta(days=30), class_group_id=group.id)
    db.add(reg); db.flush(); people[name.split()[0]] = (person, reg)
def questions(week):
    for index in range(3):
        db.add(WeekQuestion(class_group_id=group.id, week_number=week, question=f"S{week} question {index + 1} sur les volumes Docker ?", options=json.dumps(["Bonne", "Fausse A", "Fausse B", "Fausse C"]),
                            correct_answer=0, explanation="Un volume garde les données.", status="ready", author="admin"))
    row = cm.get_week(db, group, week); row.questions_status = "ready"; db.commit(); return row
week1 = questions(1)
gf._state.update({"checked_at": 1.0, "forms": "off", "drive": "off", "problem": None})
check("road: without Google Forms the application's own page is used", not sync.ready())
sent_mail.clear()
outcome = wq.send_week(db, group, 1, base_url="https://centre.example.com", manual=True)
check("week 1: each candidate gets a private link of the application", outcome["sent"] == 4 and outcome["form"] is False and all("centre.example.com/learn/" in (m.get("text_body") or m.get("body") or "") for m in sent_mail), (outcome, [m.get("to_email") for m in sent_mail]))
quizzes = {q.candidate_id: q for q in db.query(Quiz).filter(Quiz.kind == "weekly", Quiz.class_group_id == group.id, Quiz.week_number == 1).all()}
client = TestClient(main.app, follow_redirects=False)
def token(name): return quizzes[people[name][0].id].access_token
page = client.get(f"/learn/{token('Amal')}")
check("candidate page: asks the questions, then how the week went (faces, pace, confidence, own words)", page.status_code == 200 and "passée cette semaine pour vous" in page.text and 'name="mood"' in page.text and 'name="pace"' in page.text and 'name="confidence"' in page.text and 'name="hard"' in page.text and "😄" in page.text)
def answer(name, picks, **said):
    return client.post(f"/learn/{token(name)}", data={**{f"q{i}": str(p) for i, p in enumerate(picks)}, **{k: str(v) for k, v in said.items()}})
answer("Amal", [0, 0, 0], mood=5, pace="right", confidence=4, hard="", message="Merci, c'était très clair !")
answer("Bilel", [1, 2, 0], mood=1, pace="fast", confidence=1, hard="C'était trop rapide, je n'ai pas compris les volumes Docker. Pouvez-vous réexpliquer ? Je pense abandonner.", message="")
answer("Chaima", [1, 1, 0], mood=4, pace="right", confidence=4, hard="Rien de spécial", message="")
answer("Dali", [0, 0, 0], mood=2, pace="fast", confidence=1, hard="J'ai du mal à suivre, pas assez d'exercices.", message="")
db.expire_all()
insights = {name: wi.insight_of(db.get(Quiz, quizzes[people[name][0].id].id)) for name in people}
check("check-in: what each candidate said is kept with their answers", wi.feedback_of(db.get(Quiz, quizzes[people['Bilel'][0].id].id))["mood"] == 1 and db.get(Quiz, quizzes[people['Bilel'][0].id].id).answer_source == "page")
print("     ", {name: (i.get("profile"), i.get("sentiment"), i["attention"]["level"], i["attention"]["points"]) for name, i in insights.items()})
check("analysis: understood and feels good -> on track, nothing to do", insights["Amal"]["profile"] == "on_track" and insights["Amal"]["sentiment"] == "positive" and insights["Amal"]["attention"]["level"] == "ok")
check("analysis: low result, low confidence, asks for help and talks of stopping -> needs you now", insights["Bilel"]["profile"] == "struggling" and insights["Bilel"]["sentiment"] == "negative" and insights["Bilel"]["attention"]["level"] == "act" and insights["Bilel"]["asks_help"] and insights["Bilel"]["giving_up"], insights["Bilel"]["attention"])
check("analysis: feels sure but answered wrong -> seen as overconfident", insights["Chaima"]["profile"] == "overconfident" and "thinks they understood, the answers say otherwise" in insights["Chaima"]["attention"]["reasons"], insights["Chaima"])
check("analysis: all right but does not feel ready -> knows more than they think", insights["Dali"]["profile"] == "underconfident" and insights["Dali"]["attention"]["level"] in ("watch", "act"), insights["Dali"]["attention"])
check("analysis: the difficulty is named (pace, practice) and the course words they use are kept", "pace" in insights["Bilel"]["themes"] and "practice" in insights["Dali"]["themes"] and "volumes" in [w.lower() for w in insights["Bilel"]["mentions"]], (insights["Bilel"]["themes"], insights["Bilel"]["mentions"]))
check("analysis: the summary is made of the facts", "very hard" in insights["Bilel"]["summary"] and "33%" in insights["Bilel"]["summary"] and "giving up" in insights["Bilel"]["summary"], insights["Bilel"]["summary"])
done = client.get(f"/learn/{token('Bilel')}")
check("candidate page: after answering, the correction and a thank-you for the check-in", done.status_code == 200 and "Merci aussi de nous avoir dit" in done.text and 'name="mood"' not in done.text)

feelings = wi.week_feelings(db, group, 1, hardest=wq.week_results(db, group, 1)["hardest"])
print("      next session:", [a["text"][:70] for a in feelings["advice"]])
check("class: faces, pace and confidence are counted", feelings["mood"]["counts"] == [1, 1, 0, 1, 1] and feelings["pace"]["fast"] == 2 and feelings["pace"]["right"] == 2 and feelings["confidence"] == 2.5 and feelings["mood"]["average"] == 3.0, (feelings["mood"], feelings["pace"], feelings["confidence"]))
check("class: who needs the professor comes first", feelings["people"][0]["name"] == "Bilel Perdu" and feelings["to_act"] >= 1)
check("class: it says what to change next session (pace too fast for half)", any("too fast" in a["text"] for a in feelings["advice"]), feelings["advice"])
check("class: what was hard is grouped, with their own words", any(t["key"] == "pace" and t["count"] == 2 and t["quotes"] for t in feelings["themes"]), [(t["key"], t["count"]) for t in feelings["themes"]])

auth.create_user(db, email="admin.stage2@example.com", name="Stage Admin", password="stage-password-2", role=auth.ADMIN)
client.post("/login", data={"email": "admin.stage2@example.com", "password": "stage-password-2"})
course = client.get(f"/classes/{group.id}/course?week=1")
check("course page: shows how the class lived the week, one by one", course.status_code == 200 and "How the class lived the week" in course.text and "Struggling, and knows it" in course.text and "Pouvez-vous réexpliquer" in course.text and "For the next session" in course.text, course.status_code)
check("course page: says answers go through the application's own page for now", "Private page of this application" in course.text and "Use Google Forms and Firebase instead" in course.text)
profile = client.get(f"/candidate/{people['Bilel'][0].id}")
check("candidate file: how the weeks went, with the reasons", profile.status_code == 200 and "How the weeks went for Bilel" in profile.text and "Needs you now" in profile.text and "Talks about giving up" in profile.text, profile.status_code)
board = advisor.for_dashboard(db)
talk = [a for a in board if a.key.startswith("feel:")]
print("      advice:", [(a.title, a.tone) for a in board if "feel" in a.key])
check("dashboard: advises to talk to the candidate who is giving up, with their words", any("Talk to Bilel" in a.title and a.tone == "urgent" and "abandonner" in a.why for a in talk), [(a.title, a.why[:80]) for a in talk])
check("dashboard: advises the class-level change before the next session", any(a.title == "Before the next session of FEEL-DEVOPS-1" for a in board), [a.title for a in board][:8])

# the model's second reading
model["answers"] = [{"tone": "negative", "difficulty": "Docker volumes, pace", "summary": "The learner found the week too fast and did not understand Docker volumes.", "quote": "je n'ai pas compris les volumes Docker"}]
bilel_quiz = quizzes[people["Bilel"][0].id].id
order = [q.id for q in db.query(Quiz).filter(Quiz.kind == "weekly", Quiz.completed_at.isnot(None), Quiz.insight.like('%"reading_state": "pending"%')).order_by(Quiz.completed_at.asc()).all()]
steps = 0
while wi.enrich_one(db) and steps < 12:
    steps += 1
    if not model["answers"]:
        model["answers"] = [{"tone": "positive", "summary": "Everything is wonderful and easy this week.", "quote": "words that are not in the message at all", "difficulty": ""}]
db.expire_all()
readings = {name: wi.insight_of(db.get(Quiz, quizzes[people[name][0].id].id)) for name in people}
first_read = wi.insight_of(db.get(Quiz, order[0]))
check("second reading: the model's summary is added when its quote is really in the message", first_read["reading"] is not None and first_read["reading_state"] == "done")
dali = readings["Dali"]
check("second reading: an invented quote is dropped", dali["reading"] is not None and dali["reading"]["quote"] == "", dali["reading"])
check("second reading: when it contradicts the rules, nothing is overruled: the answer is marked to be read", dali["reading"]["agrees"] is False and dali["sentiment"] == "mixed" and dali["text"]["tone"] == "negative" and any("read them" in r for r in dali["attention"]["reasons"]), (dali["sentiment"], dali["attention"]["reasons"]))
model["answers"] = []

# ======================================================================
# C. Google Form -> Firebase -> here (stand-ins for Google and Firebase)
# ======================================================================
google = {"forms": [], "answers": {}}
def fake_create(title, description, items):
    form_id = f"FORM{len(google['forms']) + 1}"
    google["forms"].append({"id": form_id, "title": title, "items": items})
    return {"form_id": form_id, "url": f"https://docs.google.com/forms/d/e/{form_id}/viewform", "ids": {item["key"]: format(4096 + index, "x") for index, item in enumerate(items)}}
gf.create = fake_create
gf.answers = lambda form_id, since=None: list(google["answers"].get(form_id, []))
gf._state.update({"checked_at": 1.0, "forms": "on", "drive": "on", "problem": None})
gf.project_id = lambda: "pfe2-test"
firebase = {}
def fake_put(path, value):
    node = firebase
    parts = path.split("/")
    for part in parts[:-1]: node = node.setdefault(part, {})
    node[parts[-1]] = value
def fake_read(path):
    node = firebase
    for part in path.split("/"):
        node = node.get(part) if isinstance(node, dict) else None
    return node
sync._put, sync._read = fake_put, fake_read
sync.firebase_ready = lambda: True
check("road: with Google Forms and Firebase connected it is ready", sync.ready() and sync.status()["google"]["project"] == "pfe2-test")
week2 = questions(2)
sent_mail.clear()
outcome = wq.send_week(db, group, 2, base_url="https://centre.example.com", manual=True)
db.refresh(week2)
form = google["forms"][0] if google["forms"] else {"items": []}
keys = [item["key"] for item in form["items"]]
check("week 2: one Google Form is created for the class", outcome["sent"] == 4 and outcome["form"] is True and len(google["forms"]) == 1 and week2.form_id == "FORM1", (outcome, len(google["forms"])))
check("form: it asks the address, the course questions, then how the week went", keys == ["email", "q0", "q1", "q2", "mood", "pace", "confidence", "hard", "message"] and form["items"][4]["options"][4] == "😄 Très bien", keys)
link = next((m.get("text_body") or "" for m in sent_mail if m["to_email"] == "amal@example.com"), "")
check("email: the link is the Google Form with the candidate's address already written", "docs.google.com/forms/d/e/FORM1/viewform?usp=pp_url&entry.4096=amal%40example.com" in link, link[-300:])
again = wq.send_week(db, group, 2, base_url="https://centre.example.com", manual=True)
check("form: sending again creates no second form", len(google["forms"]) == 1 and again["already"] == 4)
ids = json.loads(week2.form_map)["ids"]
def response(rid, email, picks, mood, pace, confidence, hard="", message="", at="2026-10-13T10:00:00Z"):
    options = ["Bonne", "Fausse A", "Fausse B", "Fausse C"]
    values = {ids["email"]: email, ids["mood"]: mood, ids["pace"]: pace, ids["confidence"]: confidence, ids["hard"]: hard, ids["message"]: message}
    values.update({ids[f"q{i}"]: options[p] for i, p in enumerate(picks)})
    return {"id": rid, "email": None, "at": at, "values": values}
google["answers"]["FORM1"] = [
    response("R1", "Amal@Example.com ", [0, 0, 0], "🙂 Bien", "Bon rythme", "Oui, avec de l'aide", message="Toujours bien, merci"),
    response("R2", "bilel@example.com", [0, 0, 1], "😐 Correcte", "Bon rythme", "Un peu", hard="Mieux que la semaine dernière mais les réseaux Docker restent flous"),
    response("R3", "stranger@example.com", [0, 0, 0], "😄 Très bien", "Bon rythme", "Oui, seul(e)"),
]
arrived = sync.tick(db)
db.expire_all()
check("step 2: the answers of the form are written to Firebase", set(firebase.get("weekly_answers", {}).get("FORM1", {})) == {"R1", "R2", "R3"} and firebase["weekly_answers"]["FORM1"]["R1"]["email"] == "amal@example.com" and firebase["weekly_answers"]["FORM1"]["R1"]["week"] == 2 and firebase["weekly_answers"]["FORM1"]["R1"]["answers"]["q0"] == "Bonne", firebase.get("weekly_answers"))
q2 = {q.candidate_id: q for q in db.query(Quiz).filter(Quiz.kind == "weekly", Quiz.class_group_id == group.id, Quiz.week_number == 2).all()}
amal2, bilel2 = q2[people["Amal"][0].id], q2[people["Bilel"][0].id]
check("step 3: they arrive here, graded, from the Google Form", arrived == 2 and amal2.completed_at is not None and amal2.score == 100.0 and amal2.answer_source == "google_form" and amal2.external_id == "R1" and round(bilel2.score) == 67, (arrived, amal2.score, amal2.answer_source, bilel2.score))
check("step 3: with what they said, read the same way", wi.feedback_of(amal2) == {"mood": 4, "pace": "right", "confidence": 3, "hard": "", "message": "Toujours bien, merci"} and wi.insight_of(bilel2)["trend"]["mood"] == 2 and wi.insight_of(bilel2)["trend"]["score"] > 0, (wi.feedback_of(amal2), wi.insight_of(bilel2)["trend"]))
db.refresh(week2)
shown = sync.form_of(week2)
check("an answer from an address nobody has in the class is set aside and shown", [u["email"] for u in shown["unmatched"]] == ["stranger@example.com"] and shown["answers"] == 3, shown)
check("the same answers read twice are imported once", sync.tick(db) == 0 and db.query(Quiz).filter(Quiz.external_id == "R1").count() == 1)
# an answer written to Firebase by something else than this application (the way the registration form does)
firebase["weekly_answers"]["FORM1"]["OTHER9"] = {"email": "chaima@example.com", "week": 2, "answers": {"q0": "Bonne", "q1": "Fausse A", "q2": "Bonne"}, "mood": "😕 Difficile", "pace": "Trop rapide", "confidence": "Pas du tout", "hard": "Je suis perdue"}
check("an answer written to Firebase by another tool is imported too", sync.import_pending(db) == 1 and db.query(Quiz).filter(Quiz.external_id == "OTHER9").first().answer_source == "google_form")
check("an answer for a form that is not ours is ignored", sync.import_record(db, {"form_id": "NOPE", "response_id": "X1", "email": "amal@example.com"}) == "no_form")
course2 = client.get(f"/classes/{group.id}/course?week=2")
check("course page: shows the road Google Form -> Firebase -> Here, the form's link and the stray answer", course2.status_code == 200 and "Open this week's form" in course2.text and "through the Google Form" in course2.text and "stranger@example.com" in course2.text and "Private page of this application" not in course2.text, course2.status_code)
bilel_file = client.get(f"/candidate/{people['Bilel'][0].id}").text
check("candidate file: two weeks side by side, the second better", "W1" in bilel_file and "W2" in bilel_file and "getting better" in bilel_file)
gf.create = lambda *a, **k: (_ for _ in ()).throw(gf.FormsError("Google did not create the form: quota exceeded"))
week3 = questions(3)
sent_mail.clear()
fallback = wq.send_week(db, group, 3, base_url="https://centre.example.com", manual=True)
db.refresh(week3)
check("when Google refuses, the week still goes out, on the application's own page, and the reason is kept", fallback["sent"] == 4 and fallback["form"] is False and "quota exceeded" in (week3.form_error or "") and all("/learn/" in (m.get("text_body") or "") for m in sent_mail), (fallback, week3.form_error))
check("prefilled link: a question id that is not a number leaves the plain link", gf.prefilled("https://x/viewform", "zz!", "a@b.c") == "https://x/viewform" and gf.prefilled("https://x/viewform", "1f", "a@b.c") == "https://x/viewform?usp=pp_url&entry.31=a%40b.c")

# ======================================================================
# D. Settings, name and icon, WhatsApp by itself
# ======================================================================
settings_page = client.get("/settings")
check("settings: name and icon, WhatsApp, and the Google Forms road with its two links", settings_page.status_code == 200 and 'id="identity"' in settings_page.text and "9antra Flow" in settings_page.text and 'id="whatsapp"' in settings_page.text and "+216" in settings_page.text and 'id="google"' in settings_page.text and "Test the connection" in settings_page.text, settings_page.status_code)
from PIL import Image
buffer = io.BytesIO(); Image.new("RGB", (300, 200), (164, 44, 103)).save(buffer, "PNG")
saved = client.post("/settings/identity", data={"name": "  9antra   Flow "}, files={"picture": ("logo.png", buffer.getvalue(), "image/png")})
db.expire_all()
check("identity: the name is saved and the picture becomes the icon in three sizes", saved.status_code == 303 and get_settings(db).app_name == "9antra Flow" and brand.source() == "upload" and all(os.path.exists(os.path.join(os.environ["BRAND_DIR"], n)) for n in brand.SIZES) and Image.open(os.path.join(os.environ["BRAND_DIR"], "icon-64.png")).size == (64, 64))
home = client.get("/")
check("identity: the menu shows the new name and the icon", "9antra Flow" in home.text and "/static/brand/icon-192.png" in home.text and 'rel="icon"' in home.text)
check("identity: the admin's own picture is not replaced by Facebook's by itself", brand.refresh_from_facebook() == "kept")
bad = client.post("/settings/identity", data={"name": "9antra"}, files={"picture": ("x.png", b"not a picture", "image/png")})
check("identity: a file that is not a picture is refused in plain words", "not+a+picture" in bad.headers.get("location", "") or "not%20a%20picture" in bad.headers.get("location", ""), bad.headers.get("location"))

calls = []
class FakeResponse:
    status_code = 200
    def json(self): return {"messages": [{"id": "wamid.1"}]}
    text = "{}"
def fake_post(url, json=None, headers=None, timeout=None):
    calls.append({"url": url, "json": json, "headers": headers}); return FakeResponse()
requests.post = fake_post
os.environ["WHATSAPP_TOKEN"] = "test-token"; os.environ["WHATSAPP_PHONE_ID"] = "12345"
amal = people["Amal"][0]
sent_mail.clear()
message = tp.send(db, "payment_reminder", candidate=amal, registration=people["Amal"][1], manual=True); db.commit()
check("whatsapp: with a business number, a reminder that is emailed also goes on WhatsApp, to +216",
      len(calls) == 1 and calls[0]["url"].endswith("/12345/messages") and calls[0]["json"]["to"] == "21650100200" and calls[0]["json"]["type"] == "text" and "Bonjour Amal Calme" in calls[0]["json"]["text"]["body"] and calls[0]["headers"]["Authorization"] == "Bearer test-token", calls[:1])
check("whatsapp: it is written in the candidate's history", db.query(Activity).filter(Activity.candidate_id == amal.id, Activity.event_type == "WHATSAPP_SENT").count() == 1)
calls.clear()
tp.send(db, "enrollment", candidate=amal, registration=people["Amal"][1], manual=True); db.commit()
check("whatsapp: an email that is not a reminder stays an email", not calls)
os.environ["WHATSAPP_TEMPLATE"] = "rappel_9antra"
tp.send(db, "payment_reminder", candidate=people["Dali"][0], registration=people["Dali"][1], manual=True); db.commit()
check("whatsapp: with an approved message it is sent as that message, to the foreign number as given", len(calls) == 1 and calls[0]["json"]["type"] == "template" and calls[0]["json"]["template"]["name"] == "rappel_9antra" and calls[0]["json"]["to"] == "33611223344" and "\n" not in calls[0]["json"]["template"]["components"][0]["parameters"][0]["text"], calls[:1])

print(f"\n{sum(results)}/{len(results)} passed")
db.close()
os._exit(0 if all(results) else 1)

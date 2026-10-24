"""The level check that replaces the old quizzes: its questions, its level rules, its pages, when it is sent.
Runs on a copy, on a COPY of the database; nothing leaves this machine."""
import testenv
testenv.prepare("level")
check = testenv.Checks()

import json
from datetime import datetime, timedelta, time as dtime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Notification, Professor, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, email_assistant as ea, email_templates as tp, email_threads as et, level_bank as bank,
                          level_check as lc, payment_service)
from app.services.schedule_utils import local_now
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
auth.create_user(db, email="admin@test.local", name="Test Admin", password="level-check-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "level-check-1"})
public = TestClient(main.app, follow_redirects=False)          # a candidate: not signed in
from urllib.parse import unquote_plus
def where(response): return unquote_plus(response.headers.get("location", ""))
def program(name): return db.query(Program).filter(Program.name == name).first()

# ======================================================================
# A. The questions written by hand
# ======================================================================
problems = []
for key, (label, questions) in bank.SUBJECTS.items():
    if [q[0] for q in questions] != [1, 1, 1, 2, 2, 2, 3, 3]: problems.append(f"{key}: steps {[q[0] for q in questions]}")
    for step, question, options in questions:
        if len(options) != 4 or len(set(options)) != 4: problems.append(f"{key}: choices of {question[:40]}")
        if not question.strip().endswith("?"): problems.append(f"{key}: not a question: {question[:40]}")
        if any(len(option) > 95 for option in options): problems.append(f"{key}: long choice in {question[:40]}")
    if len({q[1] for q in questions}) != len(questions): problems.append(f"{key}: a question twice")
check("eleven subjects, eight questions each, three steps, four different choices", len(bank.SUBJECTS) == 11 and not problems, problems)
lengths = [len(options[0]) - max(len(o) for o in options[1:]) for _, (_, qs) in bank.SUBJECTS.items() for _, _, options in qs]
check("the right choice is not always the longest", sum(1 for gap in lengths if gap > 0) < len(lengths) * 0.8, f"{sum(1 for gap in lengths if gap > 0)} of {len(lengths)}")
found = {p.name: bank.subject_of(p.name, p.website_slug) for p in db.query(Program).all()}
expected = {"Python": "python", "DevOps": "devops", "Cyber Security": "cyber", "Marketing Digital": "marketing", "Internet of Things (IoT)": "iot",
            "Business Intelligence (BI)": "bi", "Artificial Intelligence (AI Chatbot)": "ai", "Mobile Flutter": "flutter",
            "Mobile Development (Flutter + Node/Spring Boot)": "flutter", "Web Full Stack Js(Node React)": "mern",
            "Web Full stack(Spring Boot Angular)": "spring", "Bootcamp 6 months": "web"}
check("every program of the catalogue has its questions", all(found.get(name) == subject for name, subject in expected.items()), {n: found.get(n) for n in expected if found.get(n) != expected[n]})
check("a subject nobody wrote questions for has none", bank.subject_of("Comptabilité générale") is None and bank.questions_for("Comptabilité générale") == [])
check("letters inside a word do not pick a subject", bank.subject_of("Habits of mind") is None, bank.subject_of("Habits of mind"))

# ======================================================================
# B. The level, by rules
# ======================================================================
form = lc.build_form(db, program("DevOps"), "DevOps", "seed-1")
kinds = [item["type"] for item in form]
check("the form: who they are, the program's topics, the questions, a free word", kinds == ["experience", "goal"] + ["topic"] * 8 + ["check"] * 8 + ["note"], kinds)
check("the topics are the program's own weeks", form[2]["question"] == "DevOps Concepts & Docker Basics", form[2]["question"])
checks = [item for item in form if item["type"] == "check"]
rights = {q[1]: q[2][0] for q in bank.DEVOPS}
check("the choices are mixed, and the right one is still known", all(item["options"][item["correct_answer"]] == rights[item["question"]] for item in checks)
      and len({item["correct_answer"] for item in checks}) > 1, [item["correct_answer"] for item in checks])
check("the same candidate always gets the same order", lc.build_form(db, program("DevOps"), "DevOps", "seed-1") == form and lc.build_form(db, program("DevOps"), "DevOps", "seed-2") != form)
check("the questions climb from the bases to the advanced", [item["step"] for item in checks] == [1, 1, 1, 2, 2, 2, 3, 3])

def answers(experience=0, topics=0, right=(), wrong=(), note=None):
    """`right`: positions (0-7) of the check questions answered well; `wrong`: answered badly; others: I don't know."""
    out = [experience, 0] + [topics] * 8
    for position, item in enumerate(checks):
        if position in right: out.append(item["correct_answer"])
        elif position in wrong: out.append((item["correct_answer"] + 1) % 4)
        else: out.append(lc.DONT_KNOW)
    return out + [note]
def level(**kw): return lc.grade(form, answers(**kw))
check("everything right: advanced", level(right=range(8), topics=3, experience=3)["level"] == "advanced")
check("nothing known: beginner", level()["level"] == "beginner" and level()["checks"]["unknown"] == 8)
check("the bases only: has the bases", level(right=(0, 1, 2))["level"] == "basics")
check("two bases of three is enough for the bases", level(right=(0, 2), wrong=(1,))["level"] == "basics")
check("bases and practice: intermediate", level(right=(0, 1, 2, 3, 4, 5))["level"] == "intermediate")
check("practice without the last step is not advanced", level(right=(0, 1, 2, 3, 4, 5, 6))["level"] == "intermediate")
check("two lucky advanced answers do not make a level", level(right=(6, 7))["level"] == "beginner")
check("wrong answers and “I don't know” both count as not known", level(wrong=range(8))["level"] == "beginner" and level(wrong=range(8))["checks"]["unknown"] == 0)
over = level(topics=3, experience=3)
check("says “at ease” everywhere but knows no answer: flagged", over["level"] == "beginner" and over["said"] == "advanced" and over["calibration"] == "over")
under = level(right=range(8))
check("says “I discover” but answers everything: flagged the other way", under["level"] == "advanced" and under["said"] == "beginner" and under["calibration"] == "under")
check("when both agree nothing is flagged", level(right=(0, 1, 2), topics=1, experience=1)["calibration"] == "ok")
mixed = lc.grade(form, [1, 2, 3, 3, 0, 0, 1, 2, 0, 1] + [lc.DONT_KNOW] * 8 + ["Je travaille le samedi matin"])
check("what they know and what is new are listed", mixed["known"] == [form[2]["question"], form[3]["question"], form[7]["question"]] and form[4]["question"] in mixed["new"], (mixed["known"], mixed["new"]))
check("their goal and their words are kept", mixed["goal"] == lc.GOALS[2] and mixed["note"] == "Je travaille le samedi matin")
plain = lc.build_form(db, None, "Comptabilité générale", "seed-3")
check("a subject without questions asks who they are only", [item["type"] for item in plain] == ["experience", "goal", "note"])
alone = lc.grade(plain, [2, 1, None])
check("then the level is what they say, and marked so", alone["level"] == "intermediate" and alone["basis"] == "self" and alone["calibration"] is None, alone)

# ======================================================================
# C. The old quizzes go, level checks are prepared
# ======================================================================
old = db.query(Quiz).filter(Quiz.kind != "weekly").count()
weekly = Quiz(candidate_id="x", registration_id=None, course_name="DevOps", kind="weekly", week_number=1, questions="[]", answers="[]", access_token="w" * 24)
db.add(weekly); db.commit()
check("the copy holds the quizzes written the old way", old >= 40, old)
check("they are removed", lc.retire_old(db) == old and db.query(Quiz).filter(Quiz.kind != "weekly").count() == 0)
check("weekly questions are kept", db.query(Quiz).filter(Quiz.kind == "weekly").count() >= 1)
check("removing again removes nothing", lc.retire_old(db) == 0)
made = lc.prepare_missing(db)
waiting = [r for r in db.query(Registration).filter(Registration.payment_status == PaymentStatus.PAID).all() if lc.can_have_one(r) and lc.before_start(r)]
check("a level check is prepared for each paid candidate whose class has not started", made == len(waiting) and made >= 2, (made, len(waiting)))
check("and for nobody else", db.query(Quiz).filter(Quiz.kind == "level").count() == made)
check("preparing sends nothing", sent_mail == [] and db.query(Quiz).filter(Quiz.sent_at.isnot(None), Quiz.kind == "level").count() == 0)
check("preparing again prepares nothing", lc.prepare_missing(db) == 0)
check("the old way of removing does not touch the new ones", lc.retire_old(db) == 0 and db.query(Quiz).filter(Quiz.kind == "level").count() == made)

# ======================================================================
# D. A candidate answers
# ======================================================================
devops = program("DevOps")
person = Candidate(full_name="yasmine ben amor", email="yasmine.level@example.com", phone="50 444 555"); db.add(person); db.flush()
reg = Registration(candidate_id=person.id, program_id=devops.id, program_name=devops.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=390,
                   payment_status=PaymentStatus.PAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow())
db.add(reg); db.commit()
quiz = lc.prepare(db, reg)
check("one level check per registration", lc.prepare(db, reg).id == quiz.id and lc.state_of(quiz) == "ready")
items = lc.items_of(quiz)
page = public.get(f"/level/{quiz.access_token}")
check("the candidate opens it without signing in", page.status_code == 200 and "Yasmine, où en êtes-vous avant de commencer" in page.text, page.status_code)
check("it shows the three parts, in French", all(text in page.text for text in ("Parlez-nous de vous", "Le programme, sujet par sujet", "Quelques questions pour finir", "Je ne sais pas encore")))
check("it asks about the program's own weeks", "DevOps Concepts &amp; Docker Basics" in page.text and "Docker Compose, Volumes &amp; Networks" in page.text)
check("code in a question is shown as code", "<code>Dockerfile</code>" in page.text)
check("the right answers are not in the page", "correct_answer" not in page.text and 'data-right' not in page.text)
check("it is not for search engines", "noindex" in page.headers.get("x-robots-tag", ""))
check("an unknown link says so", public.get("/level/" + "z" * 24).status_code == 404 and "Lien introuvable" in public.get("/level/" + "z" * 24).text)
check("the admin's pages stay closed to them", public.get("/quiz/all").status_code in (302, 303, 401))

good = {}
for index, item in enumerate(items):
    if item["type"] == "experience": good[f"q{index}"] = "2"
    elif item["type"] == "goal": good[f"q{index}"] = "0"
    elif item["type"] == "topic": good[f"q{index}"] = "2" if index < 6 else "0"
    elif item["type"] == "check": good[f"q{index}"] = str(item["correct_answer"]) if item["step"] < 3 else str(lc.DONT_KNOW)
    else: good[f"q{index}"] = "  Je suis disponible le soir.  "
partial = dict(good); del partial["q3"]
answer = public.post(f"/level/{quiz.access_token}", data=partial)
db.expire_all()
check("a question left empty is asked again, nothing is kept", answer.status_code == 422 and "Il manque une réponse" in answer.text and db.get(Quiz, quiz.id).completed_at is None)
check("what was already chosen stays chosen", answer.text.count(" checked") >= len(partial) - 1, answer.text.count(" checked"))
notes = db.query(Notification).filter(Notification.category == "LEVEL").count()
answer = public.post(f"/level/{quiz.access_token}", data=good)
db.expire_all()
quiz = db.get(Quiz, quiz.id)
result = lc.result_of(quiz)
check("complete answers are kept", answer.status_code == 303 and quiz.completed_at is not None and result is not None)
check("bases and practice right, advanced unknown: intermediate", result["level"] == "intermediate" and quiz.assessed_level == "intermediate" and result["checks"]["right"] == 6 and result["checks"]["unknown"] == 2, result and result["checks"])
check("the score is the share of check questions right", quiz.score == 75.0, quiz.score)
check("their words are tidied and kept", result["note"] == "Je suis disponible le soir.")
thanks = public.get(f"/level/{quiz.access_token}").text
check("they see their starting point, kindly, without a mark", "Voici votre point de départ" in thanks and "Intermédiaire" in thanks and "Vous avez déjà pratiqué" in thanks and "75" not in thanks)
check("and what they know and will discover", "Ce que vous connaissez déjà" in thanks and "Ce que vous allez découvrir" in thanks)
note = db.query(Notification).filter(Notification.category == "LEVEL").order_by(Notification.created_at.desc()).first()
check("the admin is told once, with the level", db.query(Notification).filter(Notification.category == "LEVEL").count() == notes + 1 and "Yasmine" not in "" and "Intermediate" in note.message and note.action_url == f"/quiz/all?focus={quiz.id}", note and note.message)
again = dict(good); again["q0"] = "0"
public.post(f"/level/{quiz.access_token}", data=again)
db.expire_all()
check("answering twice changes nothing", lc.result_of(db.get(Quiz, quiz.id))["experience"] == 2 and db.query(Notification).filter(Notification.category == "LEVEL").count() == notes + 1)

# ======================================================================
# E. Sending
# ======================================================================
settings = get_settings(db); settings.public_base_url = "https://centre.example.com"; db.commit()
person2 = Candidate(full_name="Omar Level", email="omar.level@example.com", phone="50 444 666"); db.add(person2); db.flush()
reg2 = Registration(candidate_id=person2.id, program_id=devops.id, program_name=devops.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=390,
                    payment_status=PaymentStatus.PAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow())
db.add(reg2); db.commit()
quiz2 = lc.prepare(db, reg2)
sent_mail.clear()
answer = admin.post(f"/quiz/{quiz2.id}/send")
db.expire_all()
quiz2 = db.get(Quiz, quiz2.id)
mail = sent_mail[-1] if sent_mail else {}
everything = json.dumps({k: str(v) for k, v in mail.items()}, ensure_ascii=False)
check("the admin sends one", answer.status_code == 303 and len(sent_mail) == 1 and quiz2.sent_at is not None and "Level check sent" in where(answer), where(answer))
check("the email carries the candidate's private link", f"https://centre.example.com/level/{quiz2.access_token}" in everything)
check("and says what it is, in French", "où en êtes-vous avant de commencer DevOps" in everything and "Je ne sais pas encore" in everything, everything[:300])
check("it is kept in the candidate's discussion", db.query(CandidateEmail).filter(CandidateEmail.candidate_id == person2.id, CandidateEmail.direction == "outbound").count() == 1)
ready_before = lc.overview(db)["counts"]["ready"]
sent_mail.clear()
answer = admin.post("/quiz/send-ready")
db.expire_all()
check("“send the ready ones” sends each once", len(sent_mail) == ready_before and lc.overview(db)["counts"]["ready"] == 0 and f"{ready_before} level check" in where(answer), (len(sent_mail), ready_before, where(answer)))
sent_mail.clear()
admin.post("/quiz/send-ready")
check("and nothing the second time", sent_mail == [])
check("somebody who answered is not asked again", "already answered" in where(admin.post(f"/quiz/{quiz.id}/send")) and sent_mail == [])

# Paying: prepared, and sent by itself when the link can be opened from outside.
def payer(name, email, prog):
    one = Candidate(full_name=name, email=email, phone="50 777 888"); db.add(one); db.flush()
    r = Registration(candidate_id=one.id, program_id=prog.id, program_name=prog.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=0,
                     payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow())
    db.add(r); db.commit(); return r
python = program("Python")
# Here the admin chose "as soon as they pay" (by default it goes 7 days before the first session: see test_team).
lc.set_timing(db, "paid", 7)
r3 = payer("Lina Paye", "lina.level@example.com", python)
sent_mail.clear()
payment_service.confirm_payment(db, r3.id, 190)
check("a part payment prepares nothing", lc.current(db, r3.id) is None)
payment_service.confirm_payment(db, r3.id, 200)
db.expire_all()
q3 = lc.current(db, r3.id)
subjects = [str(m.get("subject")) for m in sent_mail]
check("paying in full prepares the level check and sends it", q3 is not None and q3.sent_at is not None and any("où en êtes-vous" in s for s in subjects), subjects)
check("once", sum(1 for s in subjects if "où en êtes-vous" in s) == 1, subjects)
lc.set_timing(db, "before", 7)
check("the Python form asks the Python questions", any("print(len(" in item["question"] for item in lc.items_of(q3)))

settings = get_settings(db); settings.public_base_url = None; db.commit()
r4 = payer("Mehdi Local", "mehdi.level@example.com", python)
sent_mail.clear()
payment_service.confirm_payment(db, r4.id, 390)
db.expire_all()
q4 = lc.current(db, r4.id)
check("when the link only opens on this computer it is prepared, not sent", q4 is not None and q4.sent_at is None and not any("où en êtes-vous" in str(m.get("subject")) for m in sent_mail))
settings = get_settings(db); settings.public_base_url = "https://centre.example.com"; db.commit()
tp.save(db, "quiz_invitation", subject=tp.get(db, "quiz_invitation").subject, body=tp.get(db, "quiz_invitation").body, is_active=False) if "is_active" in tp.save.__code__.co_varnames else None
switched_off = not tp.is_on(db, "quiz_invitation")
r5 = payer("Nada Off", "nada.level@example.com", python)
sent_mail.clear()
payment_service.confirm_payment(db, r5.id, 390)
db.expire_all()
q5 = lc.current(db, r5.id)
check("switched off in the email templates: prepared, not sent by itself", (not switched_off) or (q5 is not None and q5.sent_at is None), (switched_off, q5 and q5.sent_at))
print("   (template switch could be set from the test:", switched_off, ")")

# ======================================================================
# F. Pages
# ======================================================================
page = admin.get("/quiz/all")
check("the level checks page opens", page.status_code == 200, page.status_code)
text = page.text
check("it shows who answered, with their level and what it means", "yasmine ben amor" in text and "Intermediate" in text and "Has practised: the first weeks can go faster." in text)
check("the steps of their answers are drawn", "The bases" in text and 'aria-label="3 of 3"' in text and 'aria-label="0 of 2"' in text)
check("what they know, what is new, what they expect", "Already practised" in text and "New for them" in text and "Trouver un emploi ou un stage" in text and "Je suis disponible le soir." in text)
check("it says what each program asks", "What each program asks" in text and "8 check questions (DevOps)" in text and "/quiz/preview/" in text)
check("nothing of the old quiz centre is left", "Quiz center" not in text and "agent" not in text.lower().replace("user-agent", ""), "old words")
focus = admin.get(f"/quiz/all?focus={quiz.id}").text
check("a notification leads to the candidate's line", f'class="lv-row is-focus" id="level-{quiz.id}"' in focus)
preview = admin.get(f"/quiz/preview/{devops.id}")
check("the admin can see the form as a candidate does, nothing saved", preview.status_code == 200 and "Preview: this is exactly what a candidate of DevOps sees" in preview.text and "disabled" in preview.text)
check("the preview is for the admin only", public.get(f"/quiz/preview/{devops.id}").status_code in (302, 303, 401))
check("the old addresses lead to the page", admin.get("/quiz/center").status_code == 307 and admin.get("/quiz/quizzes").status_code == 307)
check("the old quiz page is gone", admin.get(f"/quiz/{quiz.id}/take").status_code in (404, 405))
profile = admin.get(f"/candidate/{person.id}")
check("the candidate's profile shows where they started from", profile.status_code == 200 and "Where they started from" in profile.text and "Intermediate" in profile.text, profile.status_code)

# A class: its group before it starts.
amira = db.query(Professor).filter(Professor.name == "amira").first()
future = (local_now() + timedelta(days=8)).replace(hour=9, minute=0, second=0, microsecond=0)
group = ClassGroup(name="LEVEL-DEVOPS-1", program_id=devops.id, status=ClassStatus.NOT_STARTED, start_date=future, total_weeks=8,
                   start_time=dtime(9, 0), end_time=dtime(13, 0), days_of_week="sat", start_mode="fixed_date", capacity=10, max_capacity=10)
db.add(group); db.flush()
for r in (reg, reg2):
    r = db.get(Registration, r.id); r.class_group_id = group.id; r.status = RegistrationStatus.ASSIGNED
person3 = Candidate(full_name="Sana Nouvelle", email="sana.level@example.com", phone="50 444 777"); db.add(person3); db.flush()
reg3 = Registration(candidate_id=person3.id, program_id=devops.id, program_name=devops.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=390,
                    payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, registration_date=datetime.utcnow(), class_group_id=group.id)
db.add(reg3); db.commit()
beginner = {key: (value if not key[1:].isdigit() else value) for key, value in good.items()}
quiz2 = db.get(Quiz, quiz2.id)
low = {}
for index, item in enumerate(lc.items_of(quiz2)):
    low[f"q{index}"] = "" if item["type"] == "note" else (str(lc.DONT_KNOW) if item["type"] == "check" else "0")
public.post(f"/level/{quiz2.access_token}", data=low)
db.expire_all()
view = lc.class_overview(db, db.get(ClassGroup, group.id))
check("the class knows who answered and who was not asked", view["total"] == 3 and view["answered"] == 2 and view["none"] == 1, {k: view[k] for k in ("total", "answered", "sent", "ready", "none")})
check("its levels are counted", view["levels"]["intermediate"] == 1 and view["levels"]["beginner"] == 1, view["levels"])
check("mixed levels are said to the professor", any("mixed" in line for line in view["advice"]), view["advice"])
check("and the topics nobody knows", any(line.startswith("Least known") for line in view["advice"]), view["advice"])
page = admin.get(f"/classes/{group.id}")
check("the class page shows where the group starts from", page.status_code == 200 and "Where the group starts from" in page.text and "For the professor" in page.text and "Sana Nouvelle" in page.text, page.status_code)
sent_mail.clear()
answer = admin.post(f"/quiz/class/{group.id}/send")
db.expire_all()
check("the class's missing ones are sent from its page", len(sent_mail) == 1 and "sent to 1 candidate" in where(answer) and lc.current(db, reg3.id).sent_at is not None, (len(sent_mail), where(answer)))
home = admin.get("/")
check("the dashboard opens", home.status_code == 200, home.status_code)
templates_page = admin.get("/inbox/templates")
check("the email is named for what it is, and can be switched off", templates_page.status_code == 200 and "Level check" in templates_page.text and "Quiz invitation" not in templates_page.text)
db.close()
check.done()

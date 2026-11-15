"""Reminder timers in any unit (seconds to months), the reminders that wait for something to happen, and the kinds
suggested from how the candidates behave. Staging copy, COPY of the database, no mail leaves this machine."""
import testenv
testenv.prepare("reminder_units")
check = testenv.Checks()

import json
from datetime import date, datetime, timedelta, time as dtime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from urllib.parse import unquote_plus
from app.database import SessionLocal
from app.models import AdminReminder, Candidate, ClassGroup, ClassStatus, Program, Registration, User, UserSession
from app.models.classsession import ClassSession
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.user import Attendance
from app.scheduler import jobs
from app.services import (auth, durations, email_composer, email_templates as tp, email_threads as et, reminder_board as rb,
                          reminder_kinds as rk, reminders as rm, timings)
from app.services.schedule_utils import local_now

sent = []
def fake_send(**kw):
    sent.append(kw); return True
et.send_email = fake_send; tp.send_email = fake_send

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
# The copy's own reminders stay out of the way: only this suite's go.
for old in db.query(AdminReminder).filter(AdminReminder.status.in_(("scheduled", "watching", "sending"))).all():
    old.status = "cancelled"
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="units-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "units-test-1"})
def where(response): return unquote_plus(response.headers.get("location", ""))
def to(email): return [mail for mail in sent if mail.get("to_email") == email]

# ======================================================================
# A. A time is a number and its unit, seconds to months
# ======================================================================
check("seconds, minutes, hours, days, weeks and months are read", [durations.parse(text, "hours").code for text in ("45s", "30 min", "24", "2 jours", "3 weeks", "1 mois")]
      == ["45s", "30min", "24h", "2d", "3w", "1mo"])
check("a month is a calendar month (31 January → the last day of February)", durations.parse("1mo", "days").after(datetime(2027, 1, 31, 9, 0)) == datetime(2027, 2, 28, 9, 0))
check("and counted back the same way", durations.parse("1mo", "days").before(datetime(2027, 3, 31, 9, 0)) == datetime(2027, 2, 28, 9, 0))
check("it is said in words", durations.of(1, "weeks").label() == "1 week" and durations.of(90, "seconds").label() == "90 seconds" and durations.of(2, "months").label("fr") == "2 mois")
check("what is not a time is refused", durations.parse("soon", "hours") is None and durations.parse("-3h", "hours") is None)
for label, args in (("too short", (durations.of(5, "seconds"), durations.of(1, "minutes"), None)), ("too long", (durations.of(400, "days"), None, durations.of(12, "months")))):
    try:
        durations.checked(args[0], least=args[1], most=args[2]); refused = False
    except ValueError:
        refused = True
    check(f"a time {label} is refused with a sentence", refused)
check("a list of times reads its units", [item.code for item in durations.parse_list("7d, 1d, 3h, 30min")] == ["7d", "1d", "3h", "30min"])

# ======================================================================
# B. Every automatic reminder takes its time in any unit
# ======================================================================
board = rb.items(db)
timed = [item for item in board if item.get("timing")]
check("every automatic reminder with a time has a number and a unit", timed and all(item["timing"]["rows"] and all({"value", "unit"} <= set(row) for row in item["timing"]["rows"]) for item in timed),
      [item["key"] for item in timed if not item["timing"].get("rows")])
check("the payment, session, link, level, resources and questions reminders are among them",
      {"payment_reminder", "session_reminder", "session_link", "level_reminder", "resources", "course_questions"} <= {item["key"] for item in timed}, [item["key"] for item in timed])
answer = admin.post("/reminders/auto/payment_reminder/timing", data={"value": "45", "unit": "minutes"})
db.expire_all()
check("a reminder's time is saved in minutes", answer.status_code == 303 and timings.get(db, "payment_reminder").code == "45min" and "45 minutes" in where(answer), where(answer))
answer = admin.post("/reminders/auto/session_reminder/timing", data={"value": "2", "unit": "days"})
db.expire_all()
check("in days", timings.get(db, "session_reminder").code == "2d")
answer = admin.post("/reminders/auto/session_link/timing", data={"value": "20", "unit": "seconds"})
db.expire_all()
check("a time below what makes sense is refused, and nothing changes", "error" in answer.headers.get("location", "") and timings.get(db, "session_link").code == "60min", where(answer))
answer = admin.post("/reminders/auto/session_link/timing", data={"value": "90", "unit": "seconds"})
db.expire_all()
check("90 seconds before is fine for the link", timings.get(db, "session_link").code == "90s", where(answer))
admin.post("/reminders/auto/session_link/reset")
answer = admin.post("/reminders/auto/course_questions/timing", data={"value": ["2", "1"], "unit": ["days", "days"], "field": ["remind", "admin"]})
db.expire_all()
check("the admin is never told before the professor is reminded", "error" in answer.headers.get("location", "") and timings.get(db, "question_admin").code == "48h", where(answer))
answer = admin.post("/reminders/auto/course_questions/timing", data={"value": ["12", "3"], "unit": ["hours", "days"], "field": ["remind", "admin"]})
db.expire_all()
check("both times of a question to the professor are saved", timings.get(db, "question_remind").code == "12h" and timings.get(db, "question_admin").code == "3d", where(answer))
answer = admin.post("/reminders/auto/training_reminder/timing", data={"value": ["3", "1", "2"], "unit": ["days", "days", "hours"]})
db.expire_all()
countdown = next(item for item in rb.items(db) if item["key"] == "training_reminder")
check("the countdown before the first session takes several times, each with its unit",
      [(row["value"], row["unit"]) for row in countdown["timing"]["rows"] if row["value"] != ""][:3] == [(3, "days"), (1, "days"), (2, "hours")], countdown["timing"]["rows"])
page = admin.get("/reminders")
check("the board shows each time with a unit to choose", page.status_code == 200 and page.text.count('<select name="unit"') >= len(timed) and '<option value="seconds"' in page.text
      and '<option value="months"' in page.text, page.status_code)
for key in ("payment_reminder", "session_reminder", "course_questions"):
    admin.post(f"/reminders/auto/{key}/reset")
db.expire_all()
check("putting a time back gives the usual one", timings.get(db, "payment_reminder").code == "24h" and timings.get(db, "question_admin").code == "48h")

# ======================================================================
# C. Again and again: every 30 seconds, every 2 weeks
# ======================================================================
program = Program(name="Units Program RM", is_active=True) if hasattr(Program, "is_active") else Program(name="Units Program RM")
db.add(program); db.flush()
soon = (local_now() + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
group = ClassGroup(name="UNITS-RM-1", program_id=program.id, professor_name="Nour", status=ClassStatus.NOT_STARTED, start_date=soon, total_weeks=8,
                   start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat", start_mode="fixed_date", capacity=12, max_capacity=12)
db.add(group); db.flush()
def member(name, email, class_group, registered=None):
    one = Candidate(full_name=name, email=email, phone="50 300 400"); db.add(one); db.flush()
    reg = Registration(candidate_id=one.id, program_id=class_group.program_id, program_name="Units Program RM", delivery_mode="online", total_amount=300, total_price=300,
                       paid_amount=300, payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, class_group_id=class_group.id)
    if registered is not None:
        reg.registration_date = registered
    db.add(reg); db.commit()
    return one, reg
lina, lina_reg = member("Lina Units", "lina.units@test.local", group)
sami, sami_reg = member("Sami Units", "sami.units@test.local", group)

answer = admin.post("/reminders/create", data={"audience": "class", "to_class": group.id, "subject": "Toutes les 30 secondes", "body": "Bonjour {first_name}", "style": "brand",
                                               "repeat": "every", "amount": "30", "unit": "seconds", "when": "now"})
fast = db.query(AdminReminder).filter(AdminReminder.subject == "Toutes les 30 secondes").first()
check("a reminder written to go every 30 seconds is refused (a minute at least)", fast is None and "error" in answer.headers.get("location", ""), where(answer))
answer = admin.post("/reminders/create", data={"audience": "class", "to_class": group.id, "subject": "Chaque minute", "body": "Bonjour {first_name}", "style": "brand",
                                               "repeat": "every", "amount": "1", "unit": "minutes", "when": "now"})
every = db.query(AdminReminder).filter(AdminReminder.subject == "Chaque minute").first()
check("every minute is planned", every is not None and every.repeat == "every" and every.every == "1min" and every.status == "scheduled", where(answer))
check("the notice says its rhythm", "every 1 minute" in where(answer), where(answer))
before = len(sent)
went = rm.send_scheduled(db)
db.refresh(every)
check("the quick pass sends it to each candidate of the class", went == 1 and len(sent) == before + 2 and {mail["to_email"] for mail in sent[before:]} == {"lina.units@test.local", "sami.units@test.local"})
check("and plans the next time one minute later", every.status == "scheduled" and timedelta(seconds=50) <= every.send_at - datetime.utcnow() <= timedelta(seconds=70), every.send_at)
check("nothing goes twice before its time", rm.send_scheduled(db) == 0 and len(sent) == before + 2)
rm._SENDING.acquire()
try:
    every.send_at = datetime.utcnow() - timedelta(seconds=1); db.commit()
    check("while one pass sends, the other waits (never twice)", rm.send_scheduled(db) == 0 and len(sent) == before + 2)
finally:
    rm._SENDING.release()
check("the quick pass runs every 10 seconds", any(getattr(job, "id", "") == "reminders_quick_tick" for job in getattr(jobs, "scheduler", None).get_jobs()) if getattr(jobs, "scheduler", None) and jobs.scheduler.get_jobs()
      else "seconds=10" in open(jobs.__file__, encoding="utf-8").read())
items = {item["key"]: item for item in rb.items(db)}
check("it sits with the automatic reminders, with its own timer", f"rule-{every.id}" in items and items[f"rule-{every.id}"]["timing"]["rows"][0]["unit"] == "minutes" and items[f"rule-{every.id}"]["when"].startswith("Every 1 minute"))
answer = admin.post(f"/reminders/auto/rule-{every.id}/timing", data={"value": "2", "unit": "weeks"})
db.refresh(every)
check("its timer changes to every 2 weeks", every.every == "2w", where(answer))
answer = admin.post(f"/reminders/auto/rule-{every.id}/switch", data={"on": "0"})
db.refresh(every)
check("it can be switched off: it waits", every.status == "paused" and "is off" in where(answer), where(answer))
every.send_at = datetime.utcnow() - timedelta(minutes=5); db.commit()
check("switched off, nothing goes", rm.send_scheduled(db) == 0 and len(sent) == before + 2)
admin.post(f"/reminders/auto/rule-{every.id}/switch", data={"on": "1"})
db.refresh(every)
check("switched on again, its next time is in the future (no burst of the missed ones)", every.status == "scheduled" and every.send_at > datetime.utcnow())
answer = admin.post(f"/reminders/{every.id}/words", data={"subject": "Chaque quinzaine", "body": "Bonjour {first_name}, à bientôt"})
db.refresh(every)
check("its words change from the board", every.subject == "Chaque quinzaine" and "Its words are saved" in where(answer))
check("an unknown word in braces is refused", "Unknown variable" in where(admin.post(f"/reminders/{every.id}/words", data={"subject": "x", "body": "Bonjour {prenom}"})))
admin.post(f"/reminders/{every.id}/cancel")
db.refresh(every)
check("it can be stopped for good", every.status == "cancelled" and f"rule-{every.id}" not in {item["key"] for item in rb.items(db)})
answer = admin.post("/reminders/create", data={"audience": "candidate", "to_candidate": lina.id, "subject": "Dans 10 minutes", "body": "Bonjour {first_name}", "style": "brand",
                                               "when": "in", "in_amount": "10", "in_unit": "minutes"})
later = db.query(AdminReminder).filter(AdminReminder.subject == "Dans 10 minutes").first()
check("“in 10 minutes” is planned 10 minutes from now", later is not None and timedelta(minutes=9) <= later.send_at - datetime.utcnow() <= timedelta(minutes=11), where(answer))
rm.cancel(db, later)

# ======================================================================
# D. Reminders that wait for something to happen
# ======================================================================
def watching(repeat, subject, offset, audience="class", audience_id=None):
    return rm.create(db, audience=audience, audience_id=audience_id if audience_id is not None else group.id, subject=subject, body="Bonjour {first_name}",
                     style="brand", repeat=repeat, offset=offset)
start_rule = watching("start", "Bientôt le début", "3d")
check("before their class starts: it waits", start_rule.status == "watching" and start_rule.offset == "3d")
before = len(sent)
went = rm.answer_events(db)
check("the class starts in 2 days: each candidate receives it", went == 2 and {mail["to_email"] for mail in sent[before:]} == {"lina.units@test.local", "sami.units@test.local"}, went)
check("once each, never twice", rm.answer_events(db) == 0 and len(sent) == before + 2)
newcomer, _ = member("Nadia Units", "nadia.units@test.local", group)
check("somebody who joins the class later still receives it, once", rm.answer_events(db) == 1 and len(to("nadia.units@test.local")) == 1)
far = watching("start", "Un jour avant", "1d")
check("one day before is not yet (the class starts in 2 days)", rm.answer_events(db) == 0)

ended_group = ClassGroup(name="UNITS-RM-OLD", program_id=program.id, professor_name="Nour", status=ClassStatus.COMPLETED,
                         start_date=(local_now() - timedelta(weeks=8, days=3)).replace(hour=9, minute=0, second=0, microsecond=0), total_weeks=8,
                         start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat", start_mode="fixed_date", capacity=12, max_capacity=12)
db.add(ended_group); db.flush()
old_one, old_reg = member("Rania Units", "rania.units@test.local", ended_group)
from app.services.schedule_utils import class_end_dt
ended_at = class_end_dt(ended_group)
after_end = watching("after_end", "Votre attestation", f"{max(1, (local_now() - ended_at).days - 1)}d", audience_id=ended_group.id)
before = len(sent)
went = rm.answer_events(db)
check("after their class ended: it goes once the time has passed", ended_at is not None and ended_at < local_now() - timedelta(days=1) and went >= 1 and len(to("rania.units@test.local")) == 1,
      (ended_at, went))
long_ago = watching("after_end", "Il y a longtemps", "6mo", audience_id=ended_group.id)
check("a time far in the future of an old class sends nothing now", rm.answer_events(db) == 0)
rm.cancel(db, after_end); rm.cancel(db, long_ago)

new_one, new_reg = member("Yassine Units", "yassine.units@test.local", group, registered=datetime.utcnow() - timedelta(hours=3))
registered = watching("registered", "Vos documents", "2h", audience="candidate", audience_id=new_one.id)
check("after they registered: 3 hours ago, with 2 hours to wait, it goes", rm.answer_events(db) >= 1 and any(mail["to_email"] == "yassine.units@test.local" and "Vos documents" in (mail.get("subject") or "") for mail in sent))
waiting = watching("registered", "Pas encore", "5h", audience="candidate", audience_id=new_one.id)
check("with 5 hours to wait, not yet", rm.answer_events(db) == 0)

quiz = Quiz(candidate_id=lina.id, registration_id=lina_reg.id, course_name="Units Program RM", kind="weekly", class_group_id=group.id, week_number=1,
            sent_at=datetime.utcnow() - timedelta(days=3))
db.add(quiz); db.commit()
unanswered = watching("unanswered", "Vos questions de la semaine", "2d", audience="candidate", audience_id=lina.id)
before = len(sent)
check("weekly questions left unanswered 3 days: it goes after 2 days", rm.answer_events(db) == 1 and len(sent) == before + 1)
quiz.completed_at = datetime.utcnow(); db.commit()
check("answered questions are never chased", rm.answer_events(db) == 0)

absence_rule = watching("absence", "Vous nous avez manqué", "2h")
absence_rule.created_at = datetime.utcnow() - timedelta(days=1); db.commit()
session = ClassSession(class_group_id=group.id, week_number=1, session_date=date.today(), topic="Introduction")
db.add(session); db.flush()
recent = Attendance(class_group_id=group.id, session_id=session.id, registration_id=sami_reg.id, candidate_id=sami.id, status="absent",
                    marked_at=datetime.utcnow() - timedelta(minutes=30))
db.add(recent); db.commit()
before = len(sent)
check("after each absence, 2 hours later: marked 30 minutes ago, not yet", rm.answer_absences(db) == 0 and len(sent) == before)
recent.marked_at = datetime.utcnow() - timedelta(hours=3); db.commit()
check("3 hours later, it goes to that candidate", rm.answer_absences(db) == 1 and to("sami.units@test.local")[-1]["subject"].startswith("Vous nous avez manqué"))
check("and once per absence", rm.answer_absences(db) == 0)

page = admin.get("/reminders")
check("the waiting reminders are on the board, each with its picture and words", page.status_code == 200 and f'id="auto-rule-{start_rule.id}"' in page.text
      and "before their class starts" in page.text.split(f'id="auto-rule-{start_rule.id}"', 1)[-1][:2500] and "after each absence" in page.text, page.status_code)
answer = admin.post(f"/reminders/auto/rule-{start_rule.id}/timing", data={"value": "36", "unit": "hours"})
db.refresh(start_rule)
check("its timer changes to 36 hours before", start_rule.offset == "36h", where(answer))
answer = admin.post(f"/reminders/auto/rule-{absence_rule.id}/timing", data={"value": "0", "unit": "minutes"})
db.refresh(absence_rule)
check("an absence reminder can go right away (0 minutes)", absence_rule.offset == "0min", where(answer))
for rule in (start_rule, far, registered, waiting, unanswered, absence_rule):
    rm.cancel(db, rule)

# ======================================================================
# E. The kinds of reminder, suggested from how the candidates behave
# ======================================================================
check("the catalogue covers the life of a training", len(rk.KINDS) >= 12 and {kind["trigger"] for kind in rk.KINDS} >= {"start", "session", "absence", "end", "after_end", "unanswered", "registered", "every", "once"})
check("each kind has a picture, its motion, a purpose and French words with no unknown variable",
      all(kind["icon"].startswith("bi-") and kind["motion"] and kind["purpose"] and kind["subject"] and "{first_name}" in kind["body"]
          and email_composer.unknown_variables(kind["subject"], kind.get("title") or "", kind["body"], extra=rm.SESSION_VARIABLES + (("start_date", "", ""), ("start_time", "", ""))) == []
          for kind in rk.KINDS), [kind["key"] for kind in rk.KINDS])
check("each kind says when it goes", all(kind["when"] for kind in rk.KINDS) and rk.get("first_session")["when"] == "2 days before their class starts" and rk.get("news")["when"] == "Every week")
for extra in range(3):
    one, reg = member(f"Absent {extra} Units", f"absent{extra}.units@test.local", group)
    db.add(Attendance(class_group_id=group.id, session_id=session.id, registration_id=reg.id, candidate_id=one.id, status="absent", marked_at=datetime.utcnow() - timedelta(days=2)))
db.commit()
ideas = rk.recommend(db, limit=20)
missed = next((item for item in ideas if item["kind"]["key"] == "missed"), None)
check("absences in the records put “after each absence” among the first suggestions", missed is not None and missed["seen"] and ideas.index(missed) < 3 and "absent in the last 30 days" in missed["why"],
      [(item["kind"]["key"], item["score"]) for item in ideas[:5]])
check("the class starting soon is named in its suggestion", any(item["kind"]["key"] == "first_session" and "UNITS-RM-1" in item["why"] for item in ideas))
check("what the records do not show is still offered, after, as what training centres do",
      all(not item["seen"] or item["score"] > 3 for item in ideas) and any(not item["seen"] and item["why"].startswith("Common in training centres") for item in ideas)
      and all(ideas[index]["score"] >= ideas[index + 1]["score"] for index in range(len(ideas) - 1)))
page = admin.get("/reminders")
check("the page shows “Suggested for your centre” with one-click buttons", 'id="suggested"' in page.text and "/reminders/kinds/missed/setup" in page.text and "Adjust first" in page.text
      and "From how your candidates behave" in page.text)
answer = admin.post("/reminders/kinds/missed/setup")
made = db.query(AdminReminder).filter(AdminReminder.kind == "missed", AdminReminder.status == "watching").first()
check("one click sets it up: for everyone, with its words and its timer", made is not None and made.repeat == "absence" and made.offset == "2h" and made.audience == "everyone"
      and where(answer).endswith(f"#auto-rule-{made.id}") and "is set up" in where(answer), where(answer))
page = admin.get("/reminders")
card = page.text.split(f'id="auto-rule-{made.id}"', 1)[-1][:1500]
check("it then sits with the automatic reminders, its moving picture on", f'id="auto-rule-{made.id}"' in page.text and rk.get("missed")["icon"] in card
      and f'm-{rk.get("missed")["motion"]}' in card)
check("and is no longer suggested", "missed" not in {item["kind"]["key"] for item in rk.recommend(db, limit=20)} and "/reminders/kinds/missed/setup" not in page.text)
check("the form marks it as set up", any(item["key"] == "missed" and item["running"] for item in rk.catalogue(db)))
check("a kind chip fills the form in place, with its trigger and its time", all({"amount", "unit", "trigger", "subject", "body"} <= set(item) for item in rk.catalogue(db))
      and next(item for item in rk.catalogue(db) if item["key"] == "last_week")["amount"] == 1 and 'data-kind="last_week"' in page.text)
plain = rm.create(db, audience="class", audience_id=group.id, subject="Absence au cours de {program_name}", body="Bonjour {first_name}", style="brand", repeat="absence")
shown = next(item for item in rb.items(db) if item["key"] == f"rule-{plain.id}")
check("a reminder without a kind is named by what it waits for, its variables shown as …", shown["name"] == "After each absence" and "{" not in shown["what"]
      and "Absence au cours de …" in shown["what"], (shown["name"], shown["what"]))
rm.cancel(db, plain)
answer = admin.post("/reminders/kinds/exam/setup")
check("a kind that needs a date opens the form instead", answer.status_code == 303 and where(answer) == "/reminders?kind=exam#new")
page = admin.get("/reminders?kind=exam")
check("the form opens filled with its words and its purpose", ("Jour d'évaluation" in page.text or "Jour d&#39;évaluation" in page.text) and rk.get("exam")["purpose"] in page.text and 'name="kind" value="exam"' in page.text)
page = admin.get("/reminders?kind=last_week")
check("a waiting kind opens with its trigger chosen and its time", 'name="repeat" value="end" checked' in page.text and 'name="amount" min="0" max="99999" value="1"' in page.text
      and '<option value="weeks" selected>' in page.text)
answer = admin.post("/reminders/create", data={"audience": "class", "to_class": group.id, "subject": "La dernière semaine", "body": "Bonjour {first_name}", "style": "brand",
                                               "repeat": "end", "amount": "10", "unit": "days", "kind": "last_week"})
made_end = db.query(AdminReminder).filter(AdminReminder.subject == "La dernière semaine").first()
check("written from the form, it waits with its kind and its time", made_end is not None and made_end.kind == "last_week" and made_end.offset == "10d" and made_end.status == "watching"
      and where(answer).endswith("#auto"), where(answer))
answer = admin.post("/reminders/create", data={"audience": "class", "to_class": group.id, "subject": "Trop loin", "body": "Bonjour {first_name}", "style": "brand",
                                               "repeat": "end", "amount": "9", "unit": "months"})
check("a waiting time longer than 6 months is refused", db.query(AdminReminder).filter(AdminReminder.subject == "Trop loin").first() is None and "error" in answer.headers.get("location", ""), where(answer))
rm.cancel(db, made); rm.cancel(db, made_end)
check("an unknown kind is not found", admin.post("/reminders/kinds/nothing/setup").status_code == 404)
check.done()

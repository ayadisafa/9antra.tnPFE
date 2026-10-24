"""The timer of the level check, the behaviour score and its parameters, a session's meeting with a link of
their own for each candidate, one program at a time, and notifications that carry their remedy.
Staging copy, COPY of the database; nothing leaves this machine."""
import testenv
testenv.prepare("behaviour")
check = testenv.Checks()

import json
from datetime import datetime, timedelta, time as dtime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import (Attendance, Candidate, ClassGroup, ClassSession, ClassStatus, Notification, Professor, Program, Registration,
                        SessionJoin, User, UserSession)
from app.models.candidate_email import CandidateEmail
from app.models.quiz import Quiz
from app.models.registration import PaymentStatus, RegistrationStatus
from app.scheduler import jobs
from app.services import (auth, behaviour as bh, email_assistant as ea, email_templates as tp, email_threads as et, enrollment_service as es,
                          level_check as lc, meetings as mt, notification_fixes as nf, payment_service)
from app.services.schedule_utils import local_now
from app.services.settings_service import get_settings

sent_mail = []
def fake_send(**kw): sent_mail.append(kw); return True
et.send_email = fake_send; tp.send_email = fake_send
et.smtp_configured = lambda: True
ea.model_available = lambda: True
ea.llm_json = lambda *a, **k: None
def subjects(): return [str(m.get("subject")) for m in sent_mail]
def all_text(mail): return " ".join(str(v) for v in mail.values())

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="behaviour-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "behaviour-1"})
public = TestClient(main.app, follow_redirects=False)
from urllib.parse import unquote_plus
def where(response): return unquote_plus(response.headers.get("location", ""))
lc.retire_old(db)
BASE = "https://centre.example.com"
settings = get_settings(db); settings.public_base_url = BASE; db.commit()
devops = db.query(Program).filter(Program.name == "DevOps").first()
python = db.query(Program).filter(Program.name == "Python").first()
marketing = db.query(Program).filter(Program.name == "Marketing Digital").first()
teacher = Professor(name="Nour Test", specialties="DevOps, Python, Marketing Digital"); db.add(teacher); db.flush()
now_local = local_now()

def klass(name, program, start, status=ClassStatus.ONGOING, days="sat", hours=(9, 13)):
    group = ClassGroup(name=name, program_id=program.id, professor_id=teacher.id, professor_name=teacher.name, status=status, start_date=start, total_weeks=8,
                       start_time=dtime(hours[0], 0), end_time=dtime(hours[1], 0), days_of_week=days, start_mode="fixed_date", capacity=10, max_capacity=10)
    db.add(group); db.flush(); return group
def person(name, email, program, group=None, paid=True):
    one = Candidate(full_name=name, email=email, phone="50 321 654"); db.add(one); db.flush()
    reg = Registration(candidate_id=one.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390, total_price=390,
                       paid_amount=390 if paid else 0, payment_status=PaymentStatus.PAID if paid else PaymentStatus.UNPAID,
                       status=RegistrationStatus.ASSIGNED if group else RegistrationStatus.PENDING, registration_date=datetime.utcnow(),
                       class_group_id=group.id if group else None)
    db.add(reg); db.commit(); return one, reg

# ======================================================================
# A. The timer of the level check
# ======================================================================
one, reg = person("Hela Chrono", "hela.b@example.com", devops)
quiz = lc.prepare(db, reg)
items = lc.items_of(quiz)
page = public.get(f"/level/{quiz.access_token}").text
check("the candidate sees a timer and is told what it is for", 'id="clock"' in page and "Le temps passé sur chaque question est noté" in page and "Il n'y a pas de limite" in page)
check("the form carries what the timer measured", 'name="timing" id="timing"' in page)
check_positions = [i for i, item in enumerate(items) if item["type"] == "check"]
data, per = {}, {}
for index, item in enumerate(items):
    if item["type"] == "check":
        place = check_positions.index(index)
        right = place in (0, 1)                                    # two right, the others wrong, all in a few seconds
        data[f"q{index}"] = str(item["correct_answer"] if right else (item["correct_answer"] + 1) % 4)
        per[str(index)] = 3.5 if place < 6 else 20
    else:
        data[f"q{index}"] = "" if item["type"] == "note" else "1"
        if item["type"] != "note": per[str(index)] = 6
data["timing"] = json.dumps({"total": 95, "per": {**per, "999": 4, "0": "x"}, "changes": {str(check_positions[0]): 2}, "away": 1, "away_seconds": 30})
public.post(f"/level/{quiz.access_token}", data=data)
db.expire_all()
quiz = db.get(Quiz, quiz.id)
stored = json.loads(quiz.analysis)["timing"]
check("only what makes sense is kept from the timer", "999" not in stored["per"] and "0" not in stored["per"] and stored["total"] == 95 and stored["away"] == 1, stored)
timing = bh.timing_of(quiz)
check("it knows the pace and what was rushed", timing["median"] == 3.5 and timing["fast_wrong"] == 4 and timing["fast_right"] == 2 and timing["changes"] == 2, timing)
detail = lc.answers_of(quiz)
check("each answer shows its seconds", detail["checks"][0]["seconds"] == 3.5 and detail["checks"][7]["seconds"] == 20)
check("the reading says they did not read", any("answered wrongly in under 8 seconds" in line for line in detail["reading"]), detail["reading"])
page = admin.get("/quiz/all").text
check("the level checks page shows how they answered", "How they answered" in page and "1 min 35 s in all" in page and "3.5 s per question" in page and "changed 2 answers" in page and "left the page 1 time" in page)
check("garbage from the timer is ignored", lc.read_timing(items, "not json") is None and lc.read_timing(items, json.dumps({"per": {}})) is None and lc.read_timing(items, json.dumps([1])) is None)

# ======================================================================
# B. Behaviour: a score, and the parameters behind it
# ======================================================================
fresh, _ = person("Nouveau Venu", "nouveau.b@example.com", devops, paid=False)
empty = bh.profile(db, fresh)
check("somebody new has no score yet, and it says why", empty["score"] is None and empty["headline"] == "Not enough to say yet" and empty["used"] == 0 and empty["of"] == 8)
check("the weights add up to 100", sum(bh.WEIGHTS.values()) == 100 and len(bh.WEIGHTS) == 8)

start = (now_local - timedelta(days=21)).replace(hour=9, minute=0, second=0, microsecond=0)
group = klass("BEHAVE-DEVOPS-1", devops, start)
reg = db.get(Registration, reg.id); reg.class_group_id = group.id; reg.status = RegistrationStatus.ASSIGNED
sessions = []
for week in range(4):
    session = ClassSession(class_group_id=group.id, week_number=week + 1, session_date=(start + timedelta(weeks=week)).date(), start_time=dtime(9, 0), end_time=dtime(13, 0), topic=f"Semaine {week + 1}")
    db.add(session); sessions.append(session)
db.flush()
for session, status, took_part, asked in zip(sessions[:3], ("present", "late", "present"), (3, 3, 2), (2, 1, 0)):
    db.add(Attendance(class_group_id=group.id, session_id=session.id, registration_id=reg.id, candidate_id=one.id, status=status, participation=took_part, questions=asked))
for week, (done, score) in enumerate(((True, 80.0), (False, 0.0)), start=1):
    db.add(Quiz(candidate_id=one.id, registration_id=reg.id, course_name="DevOps", kind="weekly", week_number=week, class_group_id=group.id, questions="[]", answers="[]",
                access_token=f"weekly-{week}-" + "x" * 16, sent_at=datetime.utcnow() - timedelta(days=10), completed_at=datetime.utcnow() - timedelta(days=9, hours=20) if done else None, score=score))
db.add(CandidateEmail(candidate_id=one.id, direction="inbound", sender=one.email, recipient="x@example.com", subject="Question", body="Bonjour, est-ce que la séance est enregistrée ?", status="answered", received_at=datetime.utcnow(), author="candidate"))
db.commit()
result = bh.profile(db, db.get(Candidate, one.id))
by = {param["key"]: param for param in result["params"]}
check("eight parameters, each with its value, its rule and its weight", len(result["params"]) == 8 and all(param["rule"] and param["value"] and param["weight"] for param in result["params"]))
check("presence counts the sessions attended", by["presence"]["score"] == 100 and "3 of 3 sessions" in by["presence"]["value"], by["presence"])
check("punctuality counts the late arrivals", by["punctuality"]["score"] == 67 and "Late 1 of 3" in by["punctuality"]["value"], by["punctuality"])
check("weekly questions: answered of sent, and the average", by["answers"]["score"] == 50 and by["understanding"]["score"] == 80, (by["answers"], by["understanding"]))
check("care: half the check questions were rushed", by["care"]["score"] == 50, by["care"])
check("participation comes from the professor's notes", by["participation"]["score"] == 88 and "Very active" in by["participation"]["value"], by["participation"])
check("questions: by email and in sessions", by["questions"]["score"] == 100 and "4 questions (1 by email, 3 in sessions)" in by["questions"]["value"], by["questions"])
expected = round(sum(param["score"] * param["weight"] for param in result["params"] if param["score"] is not None) / sum(param["weight"] for param in result["params"] if param["score"] is not None))
check("the score is the weighted average of what was measured", result["score"] == expected and result["used"] == 8 and 0 < result["score"] < 100, (result["score"], expected))
labels = [trait["label"] for trait in result["traits"]]
check("the traits say what was done, with the reason", "Rushes: fast but often wrong" in labels and "Asks a lot of questions" in labels and "Reliable: always there" in labels and all(trait["why"] for trait in result["traits"]), labels)
check("no trait judges the person", not any(word in " ".join(labels).lower() for word in ("lazy", "stupid", "bad student")))
page = admin.get(f"/candidate/{one.id}").text
check("the profile shows the score, the traits and every parameter", 'id="behaviour"' in page and "The parameters used" in page and f">{result['score']}<small>/100</small>" in page and "Care with questions" in page and "counts for" in page)
check("a profile with nothing measured still opens", admin.get(f"/candidate/{fresh.id}").status_code == 200)

# ======================================================================
# C. The meeting of a session
# ======================================================================
today = ClassSession(class_group_id=group.id, week_number=5, session_date=now_local.date(), start_time=(now_local - timedelta(minutes=20)).time().replace(microsecond=0),
                     end_time=(now_local + timedelta(hours=2)).time().replace(microsecond=0), topic="Kubernetes")
if (now_local + timedelta(hours=2)).date() != now_local.date() or (now_local - timedelta(minutes=20)).date() != now_local.date():
    today.start_time, today.end_time = dtime(0, 0), dtime(23, 59)
db.add(today); db.commit()
two, reg2 = person("Ilyes Lien", "ilyes.b@example.com", devops, group)
answer = admin.post(f"/classes/{group.id}/attendance/{today.id}/room", data={"meeting_url": "not a link"})
check("an address that is not one is refused", "Paste the meeting's address" in where(answer), where(answer))
answer = admin.post(f"/classes/{group.id}/attendance/{today.id}/room", data={"meeting_url": ""})
db.expire_all()
today = db.get(ClassSession, today.id)
check("with nothing pasted a private room is created, with a name nobody can guess", (today.meeting_url or "").startswith("https://meet.jit.si/BEHAVEDEVOPS1-") and len(today.meeting_url) > 45, today.meeting_url)
admin.post(f"/classes/{group.id}/attendance/{today.id}/room", data={"meeting_url": "https://meet.google.com/abc-defg-hij"})
db.expire_all()
today = db.get(ClassSession, today.id)
check("the professor's own Meet link is kept as it is", today.meeting_url == "https://meet.google.com/abc-defg-hij")
links = mt.links_for(db, group, today)
check("each candidate of the class has a link of their own", set(links) == {reg.id, reg2.id} and links[reg.id].token != links[reg2.id].token and len(links[reg.id].token) >= 20)
check("asking again gives the same links", mt.links_for(db, group, today)[reg.id].token == links[reg.id].token)
check("the door: early, open, closed", mt.door(sessions[0], group, datetime.combine(sessions[0].session_date, dtime(8, 0))) == "early"
      and mt.door(sessions[0], group, datetime.combine(sessions[0].session_date, dtime(8, 40))) == "open"
      and mt.door(sessions[0], group, datetime.combine(sessions[0].session_date, dtime(14, 0))) == "closed")
sent_mail.clear()
answer = admin.post(f"/classes/{group.id}/attendance/{today.id}/send-links")
db.expire_all()
check("each candidate is emailed their own link", len(sent_mail) == 2 and "2 candidates received their own link" in where(answer), (len(sent_mail), where(answer)))
mail = next(m for m in sent_mail if "ilyes.b@example.com" in all_text(m))
token2 = db.query(SessionJoin).filter(SessionJoin.session_id == today.id, SessionJoin.registration_id == reg2.id).first().token
check("the email carries their link, never the meeting's address", f"{BASE}/join/{token2}" in all_text(mail) and "meet.google.com" not in all_text(mail) and "votre lien pour la séance" in str(mail.get("subject")))
sent_mail.clear()
admin.post(f"/classes/{group.id}/attendance/{today.id}/send-links")
check("it is not sent twice", sent_mail == [])
admin.post(f"/classes/{group.id}/attendance/{today.id}/send-links", data={"again": "true"})
check("unless the professor asks to send it again", len(sent_mail) == 2)

page = public.get(f"/join/{token2}")
db.expire_all()
join2 = db.query(SessionJoin).filter(SessionJoin.token == token2).first()
check("their link opens the door during the session, without signing in", page.status_code == 200 and "Entrer dans la séance" in page.text and 'href="https://meet.google.com/abc-defg-hij"' in page.text)
check("their arrival is noted, with how late", join2.first_joined_at is not None and join2.joins == 1 and 15 <= (join2.minutes_late or 0) <= 25 or today.start_time == dtime(0, 0), join2.minutes_late)
mark = db.query(Attendance).filter(Attendance.session_id == today.id, Attendance.registration_id == reg2.id).first()
check("and marks them present or late by itself", mark is not None and mark.status in ("late", "present") and mark.marked_by == "their link", mark and mark.status)
public.get(f"/join/{token2}")
db.expire_all()
check("coming back counts as the same arrival", db.query(SessionJoin).filter(SessionJoin.token == token2).first().joins == 2 and db.query(Attendance).filter(Attendance.session_id == today.id, Attendance.registration_id == reg2.id).count() == 1)
unknown = public.get("/join/" + "z" * 24)
check("a link nobody was given opens nothing", unknown.status_code == 404 and "Ce lien n'est pas valable" in unknown.text and "meet.google.com" not in unknown.text)
old_token = mt.links_for(db, group, sessions[0])[reg2.id].token
sessions[0].meeting_url = "https://meet.google.com/old-old-old"; db.commit()
closed = public.get(f"/join/{old_token}")
check("after the session the link no longer gives the address", "Cette séance est terminée" in closed.text and "meet.google.com" not in closed.text)
later = ClassSession(class_group_id=group.id, week_number=6, session_date=(now_local + timedelta(days=7)).date(), start_time=dtime(9, 0), end_time=dtime(13, 0), topic="Ingress", meeting_url="https://meet.google.com/next-next")
db.add(later); db.commit()
early = public.get(f"/join/{mt.links_for(db, group, later)[reg2.id].token}")
check("nor before it", "C'est un peu tôt" in early.text and "meet.google.com" not in early.text)
moved, _ = person("Parti Ailleurs", "parti.b@example.com", devops, group)
gone_token = mt.links_for(db, group, today)[_.id].token
_.class_group_id = None; _.status = RegistrationStatus.PENDING; db.commit()
check("somebody no longer in the class cannot come in", "Ce lien n'est pas valable" in public.get(f"/join/{gone_token}").text)

page = admin.get(f"/classes/{group.id}/attendance?session={today.id}").text
check("the presence sheet shows the meeting and who came in", "The meeting of this session" in page and "Came in" in page and "Send each candidate their own link" in page and "In the session:" in page)
check("and each candidate's score", "/100</a>" in page)
answer = admin.post(f"/classes/{group.id}/attendance/{today.id}", data={
    f"status_{reg.id}": "present", f"participation_{reg.id}": "3", f"questions_{reg.id}": "4",
    f"status_{reg2.id}": "absent", f"participation_{reg2.id}": "2", f"questions_{reg2.id}": "1"})
db.expire_all()
seen = {row.registration_id: row for row in db.query(Attendance).filter(Attendance.session_id == today.id).all()}
check("the professor notes how each one took part", seen[reg.id].participation == 3 and seen[reg.id].questions == 4)
check("nothing is noted of somebody absent", seen[reg2.id].status == "absent" and seen[reg2.id].participation is None and seen[reg2.id].questions is None)
check("their own arrivals feed punctuality", mt.arrivals(db, two.id)["joined"] >= 1)

# Sent by itself, within the hour before a session, once.
soon = klass("SOON-PY-1", python, (now_local - timedelta(days=7)).replace(hour=9, minute=0, second=0, microsecond=0))
three, reg3 = person("Jihene Bientot", "jihene.b@example.com", python, soon)
begin = now_local + timedelta(minutes=40)
if begin.date() == now_local.date() and (begin + timedelta(hours=2)).date() == now_local.date():
    coming = ClassSession(class_group_id=soon.id, week_number=2, session_date=now_local.date(), start_time=begin.time().replace(microsecond=0), end_time=(begin + timedelta(hours=2)).time().replace(microsecond=0), topic="Listes")
    db.add(coming); db.commit()
    sent_mail.clear()
    count = mt.send_due(db, BASE)
    db.expire_all()
    check("an hour before a session each candidate gets their link by itself", count >= 1 and any("jihene.b@example.com" in all_text(m) for m in sent_mail) and db.get(ClassSession, coming.id).meeting_url, (count, subjects()))
    sent_mail.clear()
    check("once", mt.send_due(db, BASE) == 0 and sent_mail == [])
    check("never while the link only opens on this computer", mt.send_due(db, "http://127.0.0.1:8000") == 0)
else:
    print("   (skipped: too close to midnight to place a session in the next hour)")

# ======================================================================
# D. One program at a time
# ======================================================================
settings = get_settings(db); settings.auto_create_class_on_payment = True; db.commit()
four, reg4 = person("Karim Deux", "karim.b@example.com", devops, group)
second = Registration(candidate_id=four.id, program_id=marketing.id, program_name=marketing.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=0,
                      payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow())
db.add(second); db.commit()
classes_before = db.query(ClassGroup).count()
sent_mail.clear()
payment_service.confirm_payment(db, second.id, 390)
db.expire_all()
second = db.get(Registration, second.id)
check("somebody already in a class on the same hours is not put in a second one", second.class_group_id is None and second.status == RegistrationStatus.PENDING and db.query(ClassGroup).count() == classes_before)
note = db.query(Notification).filter(Notification.registration_id == second.id, Notification.category == "ASSIGNMENT_PENDING").all()
check("the admin is told once, with the reason", len(note) == 1 and note[0].title == "Waits for their current class to end" and "BEHAVE-DEVOPS-1" in note[0].message, [n.title for n in note])
asked = [m for m in sent_mail if "après DevOps" in str(m.get("subject"))]
check("the candidate is asked whether starting after the first one suits them", len(asked) == 1 and "karim.b@example.com" in all_text(asked[0]) and "Cela vous convient-il" in all_text(asked[0]), subjects())
sent_mail.clear()
es.assign_pending_paid_registrations(db); es.assign_pending_paid_registrations(db)
db.expire_all()
check("they are not asked again every minute", not any("après DevOps" in s for s in subjects()) and db.query(Notification).filter(Notification.registration_id == second.id, Notification.category == "ASSIGNMENT_PENDING").count() == 1)
answer = admin.post(f"/classes/registration/{second.id}/decision", data={"decision": "auto"})
check("“place automatically” explains why it waits", "one program at a time" in where(answer), where(answer))
fix = nf.fix_for(db, note[0])
check("the notification offers to start them now anyway", fix and fix["label"] == "Start them now anyway" and fix["fields"] == {"decision": "create_new"} and fix["confirm"])
group = db.get(ClassGroup, group.id)
es.complete_class(db, group)
sent_mail.clear()
es.assign_pending_paid_registrations(db)
db.expire_all()
second = db.get(Registration, second.id)
check("when their class ends they start the next program by themselves", second.class_group_id is not None and second.status == RegistrationStatus.ASSIGNED and second.class_group.program_id == marketing.id, second.status)
check("then the notification has nothing left to offer", nf.fix_for(db, db.get(Notification, note[0].id)) is None)
evening = klass("EVENING-PY-1", python, (now_local + timedelta(days=5)).replace(hour=18, minute=0, second=0, microsecond=0), status=ClassStatus.NOT_STARTED, days="tue", hours=(18, 20))
five, reg5 = person("Lina Soir", "lina.b@example.com", marketing, db.get(ClassGroup, second.class_group_id))
other = Registration(candidate_id=five.id, program_id=python.id, program_name=python.name, delivery_mode="online", total_amount=390, total_price=390, paid_amount=390,
                     payment_status=PaymentStatus.PAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow())
db.add(other); db.commit()
placed = es.enroll_paid_registration(db, other)
check("a second program waits too, even on other hours (one program at a time)", placed.class_group is None and placed.reason == "busy", placed.reason)

# ======================================================================
# E. Notifications that carry their remedy
# ======================================================================
lonely = klass("LONELY-PY-1", python, (now_local - timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0))
db.commit()
lonely_id = lonely.id
es.sync_class_lifecycle(db); db.expire_all()
held = db.query(Notification).filter(Notification.title == "Class on hold", Notification.message.like("LONELY-PY-1%")).first()
fix = nf.fix_for(db, held)
check("an empty class on hold can be removed from its notification", held is not None and fix and fix["label"] == "Remove this class" and fix["url"] == f"/classes/{lonely_id}/delete" and fix["method"] == "post" and fix["confirm"])
amira = db.query(Professor).filter(Professor.name == "amira").first()
iot_class = db.query(ClassGroup).filter(ClassGroup.name == "IOTI-20261017-1").first()
es.refresh_class_status(db, iot_class, now=iot_class.start_date + timedelta(days=1)); db.commit()
waits = db.query(Notification).filter(Notification.title == "Class cannot start", Notification.message.like("IOTI-20261017-1%")).first()
fix = nf.fix_for(db, waits)
check("a class waiting for its professor's speciality offers the one click", fix and fix["label"] == "Yes, amira teaches it" and fix["url"] == f"/classes/professors/{amira.id}/specialty" and fix["fields"] == {"program_id": iot_class.program_id}, fix)
api = admin.get("/admin/api/notifications?unread_only=true").json()
check("the bell receives the remedy with each notification", any(item.get("fix") and item["fix"]["label"] == "Remove this class" for item in api["notifications"]), [item.get("fix") for item in api["notifications"]][:4])
check("and can ask for it for one notification", admin.get(f"/admin/api/notifications/{held.id}/fix").json()["fix"]["label"] == "Remove this class")
home = admin.get("/").text
check("the button is inside the notification", 'class="notif-fix"' in home and "Remove this class" in home and f'action="/classes/{lonely_id}/delete"' in home and "solves it from here" in home)
system = admin.get("/system").text
check("and on the System page", "Remove this class" in system and 'class="notif-fix"' in system)
answer = admin.post(fix["url"], data=fix["fields"], headers={"referer": "http://testserver/"})
db.expire_all()
check("one click solves it", "now one of amira's specialities" in where(answer) and nf.fix_for(db, db.get(Notification, waits.id)) is None, where(answer))
answer = admin.post(f"/classes/{lonely_id}/delete")
db.expire_all()
check("a problem already solved offers nothing", db.get(ClassGroup, lonely_id) is None and nf.fix_for(db, db.get(Notification, held.id)) is None)
ordinary = db.query(Notification).filter(Notification.category == "NEW_REGISTRATION").first()
check("a notification that needs nothing has no button", ordinary is None or nf.fix_for(db, ordinary) is None)
for path in ("/", "/classes/", "/quiz/all", "/system", f"/classes/{group.id}/attendance", "/inbox/templates"):
    status = admin.get(path).status_code
    check(f"{path} opens", status == 200, status)
db.close()
check.done()

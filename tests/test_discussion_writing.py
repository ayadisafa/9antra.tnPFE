"""An open discussion says who the candidate is ("About") or only what to say ("What to say"), a suggestion goes into the
answer in one tap; every page's message box can be reworded (Tunisian words too, figures and {variables} checked);
Yasmine understands an order to write said any way ("write to wassim winik", "ekteb l wassim …", "اكتب لوسيم وينك"),
asks which one when several people have the name, takes the one on the page or chosen a moment ago, and sends.
Runs on a COPY; no email, WhatsApp or SMS leaves this machine; the model is a stand-in."""
import json
from datetime import datetime, timedelta, time as dtime

import testenv

testenv.prepare("discussion_writing")
STARTED = __import__("time").time()
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, EmailThread, Professor, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.classsession import ClassSession
from app.models.registration import PaymentStatus, RegistrationStatus
from app.models.user import Attendance
from app.services import (access, auth, chatbot_send, cloud_model, discussion_help, email_assistant as ea, email_threads as et,
                          enrollment_service as es, orchestrator, reminder_writer, rewording)
from app.services.schedule_utils import local_now

emails = []
et._attempt = lambda recipient, subject, *rest, **kw: (emails.append((recipient, subject, rest[0] if rest else "")) or (None, False))
et.smtp_configured = lambda: True
ea.model_available = lambda: True
MODEL = {"reply": "Bonjour Wassim,\n\nOù es-tu ?\n\nÀ bientôt.", "text": None}
prompts = []


def fake_model(system, prompt, **kw):
    prompts.append(prompt)
    if '{"text"' in prompt:
        return {"text": MODEL["text"]} if MODEL["text"] else None
    return {"reply": MODEL["reply"]} if MODEL["reply"] else None


ea.llm_json = fake_model
cloud_model.available = lambda: False          # the cloud writer is off here: the model of this computer rewords

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="writing-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "writing-test-1"})
row = db.query(User).filter(User.email == "admin@test.local").one()
me = auth.CurrentUser(id=row.id, name=row.name, email=row.email, role="admin")
check("the copy has nobody called Wassim (the suite's people are the only ones)",
      not any("wassim" in (item.full_name or "").lower() for item in db.query(Candidate).all()))

program = Program(name="Writing Test Program")
db.add(program)
db.flush()
teacher = Professor(name="Kamel Wassim", email="kamel.writing@test.local", specialties="Writing Test Program")
db.add(teacher)
db.flush()
start = (local_now() - timedelta(days=15)).replace(hour=9, minute=0, second=0, microsecond=0)
group = ClassGroup(name="WRITE-PY-1", program_id=program.id, professor_id=teacher.id, professor_name=teacher.name, status=ClassStatus.ONGOING,
                   start_date=start, total_weeks=8, current_week=3, start_time=dtime(9, 0), end_time=dtime(12, 0), days_of_week="sat,tue",
                   start_mode="fixed_date", capacity=12, max_capacity=12)
db.add(group)
db.flush()
# Its sessions, written here (the planning rules would move a start in the past): two gone, two to come.
today = local_now().date()
for week, days in ((1, -7), (2, -4), (3, 2), (4, 5)):
    db.add(ClassSession(class_group_id=group.id, week_number=week, session_date=today + timedelta(days=days), start_time=dtime(9, 0),
                        end_time=dtime(12, 0), topic=f"Writing week {week}"))
db.commit()


def person(name, email, *, paid=300, total=300, english=False, in_class=True):
    one = Candidate(full_name=name, email=email, phone="50 300 400")
    db.add(one)
    db.flush()
    reg = Registration(candidate_id=one.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=total,
                       total_price=total, paid_amount=paid, payment_status=PaymentStatus.PAID if paid >= total else PaymentStatus.PARTIAL,
                       status=RegistrationStatus.ASSIGNED if in_class else RegistrationStatus.PENDING, class_group_id=group.id if in_class else None,
                       registration_date=datetime.utcnow() - timedelta(days=30))
    db.add(reg)
    db.commit()
    return one, reg


kefi, kefi_reg = person("Wassim Kefi", "wassim.kefi@test.local", paid=200, total=490)
jlassi, jlassi_reg = person("Wassim Jlassi", "wassim.jlassi@test.local")
rania, _ = person("Rania Writing", "rania.writing@test.local", english=True)
fares_w, _ = person("Fares Writing", "fares.writing@test.local")
# Kefi missed the last session.
past = [session for session in db.query(ClassSession).filter(ClassSession.class_group_id == group.id).order_by(ClassSession.session_date).all()
        if session.session_date < local_now().date()]
db.add(Attendance(class_group_id=group.id, session_id=past[-1].id, registration_id=kefi_reg.id, candidate_id=kefi.id, status="absent"))
db.add(Attendance(class_group_id=group.id, session_id=past[-2].id, registration_id=kefi_reg.id, candidate_id=kefi.id, status="present"))
db.commit()


def discussion(candidate, body, *, language="fr", emotions=None, waiting=True):
    thread = EmailThread(candidate_id=candidate.id, subject="Question", channel="email", needs_admin=waiting, message_count=1, inbound_count=1,
                         last_message_at=datetime.utcnow(), last_direction="inbound", last_snippet=body[:80])
    db.add(thread)
    db.flush()
    message = CandidateEmail(candidate_id=candidate.id, direction="inbound", thread_id=thread.id, sender=candidate.email, recipient="centre@test.local",
                             subject="Question", body=body, language=language, status="needs_admin" if waiting else "received",
                             requires_admin=waiting, author="candidate", emotions=json.dumps(emotions) if emotions else None)
    db.add(message)
    db.commit()
    return thread, message


# ======================================================================
# A. The open discussion: about the candidate, or what to say
# ======================================================================
thread, asked = discussion(kefi, "Bonjour, je suis vraiment déçu, je n'ai pas compris la dernière séance.",
                           emotions={"emotions": [{"key": "disappointed", "label": "Disappointed", "words": ["déçu"]}]})
page = admin.get(f"/inbox?thread={thread.id}").text
check("the discussion opens with “Before you answer”: About / What to say", 'class="ix-help"' in page and 'data-ix-help="about"' in page
      and 'data-ix-help="say"' in page and "What to say" in page)
check("About: their program, what is left to pay and their class, said once (no more chips in the header)",
      "Writing Test Program" in page and "290 DT" in page and "WRITE-PY-1" in page and 'class="ix-context"' not in page)
check("About: next session and presence, from the records", "Next session" in page and "Presence" in page and "1/2" in page, page.count("ix-help-tile"))
check("About: what they felt, and their whole file", "Wrote they felt: disappointed" in page and f'href="/candidate/{kefi.id}"' in page)
said = discussion_help.to_say(db, kefi, asked, most=8)
keys = [item["key"] for item in said]
check("What to say: sorry first (they wrote they were disappointed)", keys[0] == "sorry" and "désolé" in said[0]["text"], keys)
check("What to say: we missed you at the last session (absent), with its date", "missed" in keys
      and "Nous ne vous avons pas vu à la séance de" in next(item["text"] for item in said if item["key"] == "missed"), keys)
check("What to say: their next session, and what is left to pay (the record's amount)", "next" in keys and "owes" in keys
      and "Il vous reste 290 DT à régler pour Writing Test Program." in next(item["text"] for item in said if item["key"] == "owes"))
check("What to say: always a closing line", keys[-1] == "soon" or "soon" in keys)
check("each suggestion is a button that fills the answer", page.count('class="ix-say"') >= 4 and "data-say-text=" in page
      and "Added to your answer" in page)
check("the choice About / What to say is remembered in the browser", "inbox-help" in page and "data-ixhelp" in page)
_, thanks = discussion(fares_w, "Merci beaucoup !", waiting=False)
check("a thank-you: answered with “Avec plaisir”", discussion_help.to_say(db, fares_w, thanks)[0]["key"] == "welcome"
      and "Avec plaisir, Fares" in discussion_help.to_say(db, fares_w, thanks)[0]["text"])
_, english = discussion(rania, "Hello, when is my next session please?", language="en")
check("someone who writes in English is answered in English", discussion_help.language_of(db, rania) == "en"
      and any(item["text"].startswith("Your next session is on") for item in discussion_help.to_say(db, rania, english)))
stranger = Candidate(full_name="Nobody Registered", email="nobody.writing@test.local")
db.add(stranger)
db.commit()
check("not registered yet: how to register, with the form's link", any(item["key"] == "register" and "http" in item["text"]
                                                                        for item in discussion_help.to_say(db, stranger)))
check("About a stranger says so", "Not registered yet" in admin.get(f"/inbox?thread={discussion(stranger, 'Bonjour')[0].id}").text)

print("time", round(__import__("time").time() - STARTED), "s", flush=True)
# ======================================================================
# B. Reword, on every page
# ======================================================================
check("figures: Tunisian letters are not figures, hours are", rewording.figures("ija ghodwa m3a 9h") == ["9"]
      and rewording.figures("3andi 2 cours, prix 390 dt") == ["2", "390"] and rewording.figures("09:00") == rewording.figures("9h"))
MODEL["reply"] = "Bonjour Sarra,\n\nOui, à demain 9h : pensez à ramener votre ordinateur."
answer = admin.post("/account/reword", json={"text": "ok pour demain 9h ramene ton pc", "kind": "message", "first_name": "Sarra"}).json()
check("a message: written as one, greeting with the first name, nothing changed that must not", answer["reply"].startswith("Bonjour Sarra")
      and answer["warning"] is None and answer["by"] == "local", answer)
MODEL["reply"] = "Bonjour Sarra,\n\nOui, à demain 10h."
answer = admin.post("/account/reword", json={"text": "ok pour demain 9h", "kind": "message", "first_name": "Sarra"}).json()
check("a figure changed: said, to read before sending", answer["warning"] and "10" in answer["warning"], answer)
MODEL["text"] = "Bonjour {first_name}, la séance de samedi est annulée : on se voit dimanche à 10h, salle 2."
answer = admin.post("/account/reword", json={"text": "Bonjour {first_name}, la seance de samedi est annulee, on se voit dimanche 10h salle 2", "kind": "text"}).json()
check("a template: said better, its {variables} kept, no greeting added", answer["reply"] == MODEL["text"] and answer["warning"] is None, answer)
MODEL["text"] = "Bonjour, la séance de samedi est annulée."
answer = admin.post("/account/reword", json={"text": "Bonjour {first_name}, la seance de samedi est annulee", "kind": "text"}).json()
check("a {variable} lost: said", answer["warning"] and "{first_name}" in answer["warning"], answer)
MODEL["reply"] = None
response = admin.post("/account/reword", json={"text": "winik", "kind": "message"})
check("the writer does not answer: said, the words stay", response.status_code == 503 and "did not answer" in response.json()["detail"])
MODEL["reply"] = "Bonjour Wassim,\n\nOù es-tu ?\n\nÀ bientôt."
real_cloud = (cloud_model.generate, cloud_model.json_of)
cloud_model.available = lambda: True
cloud_model.generate = lambda contents, **kw: {"asked": contents[0]["parts"][0]["text"]}
cloud_model.json_of = lambda content: {"text": "Bonjour Wassim, voici le lien : [link 1]. À samedi !"}
answer = admin.post("/account/reword", json={"text": "voila le lien https://meet.example.com/abc a samedi", "kind": "message", "first_name": "Wassim"}).json()
check("with the cloud writer: its text, the link put back exactly", answer["by"] == "cloud" and "https://meet.example.com/abc" in answer["reply"], answer)
cloud_model.available = lambda: False
cloud_model.generate, cloud_model.json_of = real_cloud
auth.create_user(db, email="prof.writing@test.local", name="Kamel Wassim", password="prof-writing-1", role=auth.PROFESSOR, professor_id=teacher.id)
professor = TestClient(main.app, follow_redirects=False)
professor.post("/login", data={"email": "prof.writing@test.local", "password": "prof-writing-1"})
check("professors may reword too", professor.post("/account/reword", json={"text": "ija ghodwa", "kind": "message"}).status_code == 200)
for address, label in ((f"/inbox/compose?candidate={kefi.id}", "compose"), ("/inbox/team", "team chat"), ("/reminders", "reminders"),
                       ("/inbox/templates?key=payment_reminder", "templates"), (f"/candidate/{kefi.id}/payment-reminder", "payment reminder"),
                       (f"/professors/{teacher.id}", "a professor's space")):
    shown = admin.get(address).text
    check(f"Reword on {label}: the bar's kit once, a box marked", shown.count('id="reword-kit"') == 1 and "data-reword=" in shown, address)
check("the team chat no longer has its own Reword button (the bar is the same everywhere)", "data-tm-reword" not in admin.get("/inbox/team").text)
mine = professor.get("/account/messages").text
check("a professor's messages: Reword under the box", 'data-reword="message"' in mine and 'data-reword-at="form-end"' in mine)

print("time", round(__import__("time").time() - STARTED), "s", flush=True)
# ======================================================================
# C. Yasmine writes: the order said any way
# ======================================================================
check("names in both scripts by their consonants", chatbot_send._skeleton("أحمد") == chatbot_send._skeleton("ahmed")
      and chatbot_send._skeleton("محمد") == chatbot_send._skeleton("mohamed") and chatbot_send._skeleton("وسيم") == chatbot_send._skeleton("wassim")
      and chatbot_send._skeleton("يوسف") == chatbot_send._skeleton("youssef") and chatbot_send._skeleton("وليد") == chatbot_send._skeleton("walid"))
check("the names first, what to say after", chatbot_send.split_names(db, "wassim kefi winik") == ("wassim kefi", "winik")
      and chatbot_send.split_names(db, "fares writing et wassim jlassi ija ghodwa") == ("fares writing et wassim jlassi", "ija ghodwa")
      and chatbot_send.split_names(db, "zorro here") == ("", "zorro here"))
check("Tunisian orders read: ekteb, 9oul, es2el; “9olli” is a question", chatbot_send.from_tunisian("ekteb l wassim kefi winik", db) == "envoie un message à wassim kefi winik"
      and chatbot_send.from_tunisian("9oul l fares ija ghodwa", db) == "envoie un message à fares ija ghodwa"
      and chatbot_send.from_tunisian("9olli chkoun ma khallasch", db) is None)
check("Arabic orders read", chatbot_send.from_tunisian("اكتب لوسيم كافي وينك", db) == "envoie un message à وسيم كافي وينك")


def ask(text, path=None, history=None):
    emails.clear()
    found = orchestrator.dispatch_chat(db, text, history or [], user=me, lang=None, path=path)
    db.expire_all()
    return found


found = ask("write to wassim winik")
labels = [option["label"] for option in found.get("options") or []]
check("several Wassims: she asks which, one tap each, nothing sent", found["path"][:2] == ["read", "write"] and not emails
      and len(labels) == 3 and any(label.startswith("Wassim Kefi") for label in labels) and any("Kamel Wassim" in label for label in labels), (found["reply"], labels))
pick = next(option["say"] for option in found["options"] if option["label"].startswith("Wassim Kefi"))
found = ask(pick)
check("chosen: the order goes on, reworded, sent to that one", any(item[0] == "wassim.kefi@test.local" for item in emails)
      and "✓ Wassim Kefi" in found["reply"] and found["agent"]["key"] == "writer", found["reply"])
check("she says what the Tunisian words meant", "« winik » = où es-tu" in found["reply"], found["reply"])
check("the rewording was given the meaning of “winik”", any("où es-tu" in prompt for prompt in prompts[-3:]))
MODEL["reply"] = "Bonjour Wassim,\n\nVenez demain."
found = ask("write to wassim ija ghodwa")
check("“Wassim” now means the one chosen a moment ago (and she says so)", any(item[0] == "wassim.kefi@test.local" for item in emails)
      and "tout à l'heure" in found["reply"], found["reply"])
MODEL["reply"] = "Bonjour Wassim,\n\nOù es-tu ?"
found = ask("write to wassim winik", path=f"/candidate/{jlassi.id}")
check("on Wassim Jlassi's page: that Wassim", any(item[0] == "wassim.jlassi@test.local" for item in emails) and "page est ouverte" in found["reply"], found["reply"])
found = ask("write to him winik", path=f"/inbox?thread={thread.id}")
check("“him”, in a discussion: its candidate", any(item[0] == "wassim.kefi@test.local" for item in emails), found["reply"])
found = ask("ekteblou winik", path=f"/candidate/{jlassi.id}")
check("“ekteblou” (write to him), on a page: that person", any(item[0] == "wassim.jlassi@test.local" for item in emails), found["reply"])
MODEL["reply"] = "Bonjour Fares,\n\nVenez demain."
found = ask("9oul l fares writing ija ghodwa")
check("“9oul l fares … ija ghodwa”: sent, and read as “venez demain”", any(item[0] == "fares.writing@test.local" for item in emails)
      and "« ija » = venez" in found["reply"], found["reply"])
found = ask("kteb l fares writing w wassim jlassi ija")
check("two people in one order", {item[0] for item in emails} == {"fares.writing@test.local", "wassim.jlassi@test.local"}, found["reply"])
chatbot_send._CHOSEN.clear()
found = ask("اكتب لوسيم وينك")
check("in Arabic letters: the Wassims are found and asked about", len(found.get("options") or []) == 3 and not emails, found["reply"])
MODEL["reply"] = "Hello Rania,\n\nWhere are you?"
amira_w, _ = person("Amira Writing", "amira.writing@test.local")
found = ask("اكتب لعمر وينك")
check("a name in Arabic letters read by its consonants (عمر could be Amira): asked, never sent on a guess",
      not emails and "Amira Writing" in [option["label"].split(" · ")[0] for option in found.get("options") or []], found["reply"])
check("even when only one person has those consonants", "c'est bien Amira Writing ?" in chatbot_send._which(
    "عمر", [item for item in chatbot_send._people(db) if item["name"] == "Amira Writing"], {}, None, True)["reply"])
found = ask(next(option["say"] for option in found["options"] if option["label"].startswith("Amira Writing")))
check("tapped: it goes to her", any(item[0] == "amira.writing@test.local" for item in emails), found["reply"])
MODEL["reply"] = "Hello Rania,\n\nWhere are you?"
found = ask("tell rania writing where are you")
check("an English order: answered in English; Rania writes English, so she is written to in English",
      "✓ Rania Writing" in found["reply"] and "Here is what happened" in found["reply"] and any("English" in prompt for prompt in prompts[-2:]), found["reply"])
found = ask("ask fares writing if he comes tomorrow")
check("“ask … if”: what to say is a question (the writer is given it so)", any("ask if he comes tomorrow" in prompt for prompt in prompts[-2:]), found["reply"])

chatbot_send._CHOSEN.clear()
found = ask("write to wassim")
check("“write to wassim”: which one first", len(found.get("options") or []) == 3, found["reply"])
found = ask(next(option["say"] for option in found["options"] if option["label"].startswith("Wassim Kefi")))
says = [option["say"] for option in found.get("options") or []]
check("then the email opens, with things to say to him, one tap each", "/inbox/compose?candidate=" + kefi.id in [link["url"] for link in found["links"]]
      and says and all(item.startswith("écris à Wassim Kefi : ") for item in says), (found["reply"], says))
MODEL["reply"] = "Bonjour Wassim,\n\n" + says[0].split(" : ", 1)[1]
found = ask(says[0])
check("a tapped idea goes, reworded", any(item[0] == "wassim.kefi@test.local" for item in emails), found["reply"])

for question in ("dis-moi qui n'a pas payé", "tell me a joke", "who has not paid?", "9olli chkoun ma khallasch"):
    found = ask(question)
    check(f"a question stays a question: “{question}”", "write" not in found["path"] and not emails, found["path"])
found = ask("send the writing test program to fares writing")
check("“send the … program to Fares”: the program's PDF, as before", "PDF" in found["reply"] and found["path"][:3] == ["read", "write", "tools"], (found["path"], found["reply"]))

admin.post("/assistant/auto-send", data={"on": "0"})
db.expire_all()
found = ask("write to fares writing winik")
check("“Send at once” off: nothing goes, one tap to send it", not emails and [option["say"] for option in found["options"]] in (["envoie-le", "non"], ["send it", "no"]), found["reply"])
found = ask(found["options"][0]["say"])
check("“envoie-le”: it goes", any(item[0] == "fares.writing@test.local" for item in emails), found["reply"])
found = ask("write to fares writing winik")
found = ask(found["options"][1]["say"])
check("“non”: nothing goes", not emails and ("rien n'est parti" in found["reply"] or "nothing was sent" in found["reply"]), found["reply"])
admin.post("/assistant/auto-send", data={"on": "1"})

auth.create_user(db, email="viewer.writing@test.local", name="Vera Writing", password="viewer-writing-1", role=auth.STAFF)
viewer = db.query(User).filter(User.email == "viewer.writing@test.local").one()
viewer.permissions = access.dump({"assistant": "view"})
db.commit()
looker = auth.CurrentUser(id=viewer.id, name=viewer.name, email=viewer.email, role="staff", permissions={"assistant": "view"})
emails.clear()
found = orchestrator.dispatch_chat(db, "write to fares writing winik", [], user=looker, lang=None)
check("someone who may only look cannot send", not emails and ("réservé" in found["reply"] or "for those who may change things" in found["reply"]), found["reply"])

check("the agents page draws the writing step and its agent", "write" in orchestrator.STEPS and "writer" in orchestrator.AGENTS
      and "write" in {step["key"] for row in orchestrator.drawing() for step in row["steps"]})
check("her window: a choice may show one thing and ask another", "option.say" in admin.get("/").text)

# ======================================================================
# D. “Send by itself when well reworded” (the inbox's answer)
# ======================================================================
address = f"/inbox/thread/{thread.id}/reformulate"
MODEL["reply"] = "Bonjour Wassim,\n\nOui, la séance de samedi est maintenue à 9h.\n\nÀ bientôt."
answer = admin.post(address, json={"text": "ey seance samedi maintenue 9h", "language": "fr"}).json()
check("well reworded: it may go by itself", answer["ready"] is True and not answer["why"], answer)
MODEL["reply"] = "Bonjour Wassim,\n\nOui, la séance de samedi est maintenue à 10h."
answer = admin.post(address, json={"text": "ey seance samedi maintenue 9h", "language": "fr"}).json()
check("a figure changed: it waits, and says why", answer["ready"] is False and "10" in (answer["warning"] or ""), answer)
MODEL["reply"] = "Bonjour Wassim,\n\nOui, Rami vous attend samedi à 9h."
answer = admin.post(address, json={"text": "ey on vous attend samedi 9h", "language": "fr"}).json()
check("a name the words did not give: it waits", answer["ready"] is False and "Rami" in (answer["warning"] or ""), answer)
MODEL["reply"] = "Bonjour Wassim,\n\nFares vous attend samedi à 9h."
answer = admin.post(address, json={"text": "ey on vous attend samedi 9h", "language": "fr"}).json()
check("someone of the centre named first in a sentence (Fares): it waits", answer["ready"] is False and "Fares" in (answer["warning"] or ""), answer)
MODEL["reply"] = "Bonjour Wassim Kefi,\n\nOui, on vous attend samedi à 9h."
answer = admin.post(address, json={"text": "ey on vous attend samedi 9h", "language": "fr"}).json()
check("…but not their own name", answer["ready"] is True, answer)
MODEL["reply"] = "Hello Wassim,\n\nYes, the Saturday session is still on at 9, see you there."
answer = admin.post(address, json={"text": "ey seance samedi maintenue 9h", "language": "fr"}).json()
check("not written in the language chosen: it waits", answer["ready"] is False and "language chosen" in (answer["warning"] or ""), answer)
draft = "Bonjour Wassim,\n\nVoici le lien de la séance : https://meet.example.com/xyz\n\nÀ samedi."
db.get(CandidateEmail, asked.id).suggested_reply = draft
db.commit()
before = len(prompts)
answer = admin.post(address, json={"text": draft, "language": "fr"}).json()
check("the system's own draft: ready as it is, never reworded", answer.get("kept") and answer["ready"] and answer["reply"] == draft and len(prompts) == before, answer)
db.get(CandidateEmail, asked.id).suggested_reply = None
db.commit()
admin.post("/inbox/reword-mode", data={"on": "1"})
page = admin.get(f"/inbox?thread={thread.id}").text
check("on: the button rewords and sends, “Send” sends as written", 'id="reply-form" class="is-auto"' in page and "Reword and send" in page
      and "Send as written" in page and 'id="reword-send" checked' in page)
check("the switch sits beside the button (not folded in Options)", page.index('id="reword-send"') < page.index("ix-options"))
check("well reworded: shown 3 seconds with Stop, then sent; a change while it waits stops it", 'id="reword-going"' in page and 'id="reword-stop"' in page
      and "requestSubmit" in page and "Stopped: read it" in page and "box.value.trim() !== text.trim()" in page)
MODEL["reply"] = "Bonjour Wassim,\n\nOui, Rami vous attend samedi à 9h."
emails.clear()
response = admin.post(f"/inbox/thread/{thread.id}/reply", data={"body": "ey on vous attend samedi 9h", "reword": "true", "reword_language": "fr"})
from urllib.parse import unquote_plus

check("without the page's script too: not well reworded, not sent", not emails and "Not sent" in unquote_plus(response.headers.get("location", "")),
      response.headers.get("location"))
admin.post("/inbox/reword-mode", data={"on": "0"})
check("off: Reformulate only fills the box", 'id="reply-form" class=""' in admin.get(f"/inbox?thread={thread.id}").text)

check.done()

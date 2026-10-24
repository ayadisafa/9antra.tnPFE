"""The pages in French and Arabic (every text translated by hand, the model's garbage refused, texts that change after
loading translated too, what people wrote never swapped), the registration form given to whoever asks how to register
(candidates and strangers, email, WhatsApp, SMS, the assistant, Yasmine), live pages for everyone, and a picture for
every program. Runs on a COPY; email, WhatsApp, SMS and the web are fakes."""
import asyncio
from datetime import datetime, timedelta, timezone

import testenv

testenv.prepare("live_i18n")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, Program, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.notification import Notification
from app.models.ui_translation import UiTranslation
from app.services import (access, auth, chatbot, email_assistant, email_threads as et, howto, program_icons, realtime,
                          registration_form, translations, whatsapp_inbox)
from app.services.settings_service import get_settings
from app.services.translation_seed import MORE, SEED

sent = []
et._attempt = lambda recipient, subject, text, *rest, **kw: (sent.append((recipient, subject, text)) or (None, False))
et.smtp_configured = lambda: True

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="live-i18n-1", role=auth.ADMIN)
auth.create_user(db, email="sam.staff@test.local", name="Sam Staff", password="sam-staff-12", role=auth.STAFF)
sam = db.query(User).filter(User.email == "sam.staff@test.local").one()
sam.permissions = access.dump({"candidates": "view", "classes": "view"})
db.commit()
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "live-i18n-1"})
staff = TestClient(main.app, follow_redirects=False)
staff.post("/login", data={"email": "sam.staff@test.local", "password": "sam-staff-12"})

# ---- A. Translations ----
texts = translations.sources(refresh=True)
by_hand = [text for text in texts if text in SEED or text in MORE]
check("translations: almost every text of the interface is translated by hand", len(by_hand) >= 0.98 * len(texts), (len(by_hand), len(texts)))
check("translations: the pages' everyday words are there", all(text in MORE or text in SEED for text in (
    "Weekly timetable", "Remind professors to add their resources", "Everything it does for you, by itself", "You are seeing the application as",
    "Payment proofs to check", "The registration form", "Reconnecting…", "Take me there")))
check("translations: no template code, no class names, no keyword lists", not any(
    marker in text for text in texts for marker in ("{%", "%}", "color-mix", "bg-warning-subtle", "j'ai tout compris", "spring ?boot")))
for row in db.query(UiTranslation).filter(UiTranslation.source == "Weekly timetable").all():
    row.text, row.origin = "Garbage {Emploi}", "model"          # what the small model had written
db.commit()
translations.ensure(db)
db.expire_all()
fr = translations.dictionary(db, "fr")["t"]
ar = translations.dictionary(db, "ar")["t"]
check("translations: the hand-made ones replace the model's", fr.get("Weekly timetable") == MORE["Weekly timetable"][0], fr.get("Weekly timetable"))
check("translations: French and Arabic for the menu and the new pages", fr.get("Change what they can do") == "Changer ce qu'il peut faire"
      and ar.get("Back to my view") == "العودة إلى عرضي" and ar.get("Remind professors to add their resources", "").startswith("تذكير"))
row = db.query(UiTranslation).filter(UiTranslation.lang == "fr", UiTranslation.source == "Back to my view").one()
translations.correct(db, "fr", "Back to my view", "Revenir à ma vue")
translations.ensure(db)
db.expire_all()
check("translations: the admin's own wording is never replaced", translations.dictionary(db, "fr")["t"]["Back to my view"] == "Revenir à ma vue")
check("translations: the model's garbage is refused", translations.acceptable("Error", "{Erreur }", "fr") is None
      and translations.acceptable("In the email you choose who receives it", "I can't help you with that.", "fr") is None
      and translations.acceptable("Remove this professor's access", "إزالة Zugriff على الدكتور", "ar") is None
      and translations.acceptable("Write your answer", "English", "fr") is None
      and translations.acceptable("Send me a test", "Envoyez-moi un essai", "fr") == "Envoyez-moi un essai")
db.add(UiTranslation(lang="fr", source_hash=translations.digest("Never shown anywhere"), source="Never shown anywhere"))
db.commit()
asked = []
email_assistant._chat = lambda system, text, **kw: asked.append(text) or "Jamais affiché"
translations._cache = None
translations.sources = (lambda original: (lambda refresh=False: original(refresh) + ["Never shown anywhere"]))(translations.sources)
translations.translate_batch(db, "fr")
check("translations: the model only gets texts a page showed", "Never shown anywhere" not in asked, asked[:3])
page = admin.get("/", cookies={"lang": "ar"}).text
check("translations: Arabic pages read right to left", 'dir="rtl"' in page and 'var lang = "ar"' in page)
check("translations: texts changed after loading are translated too", "characterData: true" in page and "mine.set(item" in page)
inbox = admin.get("/inbox/team").text
check("translations: what people wrote is never swapped word by word", 'class="tm-row-last" data-no-i18n' in inbox
      and 'item.setAttribute("data-no-i18n", "")' in page)

# ---- B. The registration form ----
link = registration_form.DEFAULT_URL
check("form: the questions that ask it", all(registration_form.asks_how(text) for text in (
    "Comment je peux m'inscrire ?", "How can I register?", "kifech nsajel?", "كيفاش نسجل؟", "le lien d'inscription svp", "I would like to register")))
check("form: and those that do not", not any(registration_form.asks_how(text) for text in (
    "How much is the registration?", "Je me suis inscrit hier, quand commence la classe ?", "comment annuler mon inscription")))
settings = get_settings(db)
settings.email_auto_reply_mode = "auto"
settings.whatsapp_auto_reply = "auto"
db.commit()
candidate = Candidate(full_name="Ines Form", email="ines.form@example.com", phone="98 111 222")
db.add(candidate)
db.commit()
email_assistant.ingest_inbound(db, sender="ines.form@example.com", subject="Inscription", body="Bonjour, comment je peux m'inscrire à une autre formation ?")
db.expire_all()
answer = db.query(CandidateEmail).filter(CandidateEmail.direction == "outbound", CandidateEmail.body.contains(link)).first()
check("form: a candidate who asks gets the link, with its source", answer is not None and "formulaire d'inscription" in answer.body
      and "Source : le formulaire d'inscription de 9antra." in answer.body, [row.body[:120] for row in db.query(CandidateEmail).filter(CandidateEmail.direction == "outbound")])
sent.clear()
email_assistant.ingest_inbound(db, sender="newcomer@example.com", subject="Question", body="Hello, how can I register for your courses?")
check("form: a stranger by email gets it too", any(to == "newcomer@example.com" and link in text and "registration form" in text for to, _, text in sent), sent)
check("form: and the admin is told", db.query(Notification).filter(Notification.title.like("Asked how to register: the form went by email%")).count() == 1)
before = len(sent)
email_assistant.ingest_inbound(db, sender="no-reply@shop.example.com", subject="Register now", body="How to register for our sale")
check("form: never to an automatic sender", len(sent) == before)
whatsapp_inbox.receive(db, phone="21698765432", text="Salam, kifech nsajel fil formation?", wa_id="WA-FORM-1", name="Rania")
db.commit()
said = db.query(Notification).filter(Notification.title.like("Asked how to register (WhatsApp)%")).first()
check("form: a stranger on WhatsApp: written for you when no Business number", said is not None and said.action_url.startswith("https://wa.me/21698765432?text=")
      and "docs.google.com" in said.action_url, said.action_url if said else None)
whatsapp_inbox.receive_sms(db, phone="98700700", text="Bonjour, comment s'inscrire ?", sms_id="SMS-FORM-1")
db.commit()
said = db.query(Notification).filter(Notification.title.like("Asked how to register (SMS)%")).first()
check("form: a stranger by SMS: the same, ready to send", said is not None and said.action_url.startswith("sms:+21698700700?body="))
whatsapp_inbox.receive(db, phone="21698765499", text="Bonjour, vous êtes ouverts samedi ?", wa_id="WA-FORM-2")
db.commit()
check("form: another question from a stranger stays a plain notice", db.query(Notification).filter(Notification.title == "WhatsApp from a number that is not a candidate").count() == 1)
response = admin.post("/settings/numbers", data={"phone": "", "chat": "", "form": "forms.gle/abc"})
check("form: the link must be a link", "starting with https://" in response.headers["location"].replace("+", " ").replace("%2F", "/").replace("%3A", ":"))
admin.post("/settings/numbers", data={"phone": "", "chat": "", "form": "https://forms.gle/new9antra"})
db.expire_all()
check("form: changed in Settings, used everywhere", registration_form.url(db) == "https://forms.gle/new9antra"
      and "https://forms.gle/new9antra" in registration_form.reply(db, "en") and 'name="form"' in admin.get("/settings").text)
admin.post("/settings/numbers", data={"phone": "", "chat": "", "form": "", "whole": "1"})
db.expire_all()
check("form: empty goes back to today's form", registration_form.url(db) == link)
check("form: the assistant knows it", registration_form.knowledge_chunk(db)["content"].count(link) == 1
      and link in __import__("app.services.chatbot_sources", fromlist=["about_text"]).about_text(db))
reply = chatbot.answer(db, "how does a candidate register?", [], user=auth.CurrentUser(id="a", name="A", email="a", role="admin"))["reply"]
check("form: the assistant gives it to the admin", link in reply and "Settings" in reply, reply[:200])
reply = chatbot.answer(db, "I want to register Ines Form to DevOps", [], user=auth.CurrentUser(id="a", name="A", email="a", role="admin"))["reply"]
check("form: an order to register someone is not taken for the question", link not in reply)
professor_user = auth.CurrentUser(id="p", name="P", email="p", role="professor", professor_id="x")
answer = howto.answer(db, "comment un candidat s'inscrit ?", professor_user)
check("form: Yasmine gives it to a professor too", link in answer["reply"] and answer["links"][0]["url"] == link and "Paramètres" not in answer["reply"])

# ---- C. Live pages for everyone ----
check("live: a team member and a professor may open the stream", access.allows(access.clean(sam.permissions), "/events/stream", "GET")
      and auth.professor_may_open(db, professor_user, "/events/stream"))
page = staff.get("/candidate/all").text
check("live: a team member's pages redraw themselves", 'new EventSource("/events/stream")' in page and 'data-live-topics="candidates,inbox"' in page)


async def first_message(notifications):
    class Request:
        async def is_disconnected(self):
            return False

    stream = realtime.event_stream(Request(), notifications=notifications)
    await stream.__anext__()
    realtime.broadcaster.publish("notification", {"title": "Private"})
    realtime.broadcaster.publish("change", {"topics": ["classes"]})
    got = await asyncio.wait_for(stream.__anext__(), 3)
    await stream.aclose()
    return got


check("live: the admin gets the notifications", asyncio.run(first_message(True)).startswith("event: notification"))
check("live: the others get only what changed, never the notifications' words", asyncio.run(first_message(False)).startswith("event: change"))
from app.models.resource_reminder import ResourceReminder
from app.models.feedback import CandidateFeedback

check("live: more changes are said", "course:C1" in realtime._topics_for(ResourceReminder(class_group_id="C1", week_number=1))
      and "reviews" in realtime._topics_for(CandidateFeedback(candidate_id="X")) and "team_people" in realtime._topics_for(User()))
check("live: the Payments page refreshes again", 'data-live-topics="candidates,payments"' in admin.get("/payments").text)
check("live: reminders, market and website pages too", 'data-live-topics="reminders"' in admin.get("/reminders").text
      and 'id="mk-grid" data-live-region' in admin.get("/programs/market").text and 'data-live-topics="programs,website"' in admin.get("/website").text)

# ---- D. A picture for every program ----
check("pictures: without the web, one is drawn", program_icons.picture_for("Quantum Basket Weaving")[2] == "offline")
program = Program(name="Quantum Basket Weaving", is_approved=True)
db.add(program)
db.commit()
db.expire_all()
program = db.get(Program, program.id)
check("pictures: a new program gets one at once", program_icons.has_icon(program) and program.icon_ref == program_icons.DRAWN, program.icon_ref)
check("pictures: a drawn one is looked for again later", not program_icons.needs_icon(program)
      and program_icons.needs_icon(program, now=datetime.now(timezone.utc) + timedelta(hours=13)))
program_icons.search = lambda words, sets=None, limit=48: [] if "quantum" in words.lower() or "basket" in words.lower() or "weaving" in words.lower() else ["fluent-emoji-flat:graduation-cap"]
program_icons.download = lambda ref: b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="15"/></svg>'
check("pictures: nothing fits the name: a general picture", program_icons.assign_from_web(db, program) == "nothing"
      and db.get(Program, program.id).icon_ref in program_icons.GENERIC)
program_icons.search = lambda words, sets=None, limit=48: ["logos:python"] if "python" in words.lower() else []
other = Program(name="Python for data", is_approved=True)
db.add(other)
db.commit()
db.expire_all()
check("pictures: a name that fits gets its own", db.get(Program, other.id).icon_ref == "logos:python", db.get(Program, other.id).icon_ref)
program_icons.remove(db, db.get(Program, other.id))
check("pictures: “show initials” is respected", not program_icons.needs_icon(db.get(Program, other.id)))

db.close()
check.done()

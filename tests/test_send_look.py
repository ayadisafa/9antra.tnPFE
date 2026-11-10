""""Send answer" that never sent (10 November): with "Reword and send at once" on, the system's own draft was reworded
again, its long form link came back changed, and every press stopped at "read it first". Now the draft goes as it is,
links reach the rewording as marks and come back exact, the press after a stop sends the box, and an error stays
until closed. The design chosen on one device reaches every open page at once (the "look" topic). A discussion is
readable on a phone (one column as wide as the screen, opened in view).
Runs on a COPY; mail is recorded, the model is faked: nothing leaves this machine."""
import testenv
DB = testenv.prepare("send_look")

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network
import requests
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
requests.get = _no_web; requests.post = _no_web

from urllib.parse import unquote_plus
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.services import auth, email_assistant as ea, email_templates as tp, email_threads as et, realtime, reply_writer
from app.services.settings_service import get_settings

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

sent = []
def fake_send(**kw):
    sent.append(kw)
    return True
et.send_email = fake_send
tp.send_email = fake_send
et.smtp_configured = lambda: True
ea.model_available = lambda: False
ea.llm_json = lambda *a, **k: None


def where(response):
    return unquote_plus(response.headers.get("location", ""))


def body_of(mail):
    return " ".join(str(mail.get(key) or "") for key in ("text_body", "body", "html_body"))


db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin.sl@example.com", name="Sl Admin", password="correct horse battery", role=auth.ADMIN)
client = TestClient(main.app, follow_redirects=False)
client.post("/login", data={"email": "admin.sl@example.com", "password": "correct horse battery"})
settings = get_settings(db)
settings.reword_and_send = True
db.commit()

LINK = "https://docs.google.com/forms/d/e/1FAIpQLSe_mb7BtCDgVVtHDhNMywq59VmyFRTh2AdteYWig4QlB4yLOg/viewform"
DRAFT = (f"Hello Fares,\n\nYou can register by filling out this registration form: {LINK}. Regarding the RIB, our team will "
         "answer you shortly with the details.\n\nYou can also ask your questions to your professor, Rami.")
fares = Candidate(full_name="Fares Sl", email="fares.sl@example.com"); db.add(fares); db.commit()
counter = [0]


def waiting(text):
    """A question from Fares that waits for the admin, with the system's draft."""
    counter[0] += 1
    ea.ingest_inbound(db, sender=fares.email, subject="inscription procedures", body=text, message_id=f"<sl-{counter[0]}@example.com>")
    db.commit()
    row = db.query(CandidateEmail).filter(CandidateEmail.message_id == f"<sl-{counter[0]}@example.com>").one()
    row.status, row.suggested_reply, row.language = "needs_admin", DRAFT, "en"
    db.commit()
    return row.thread_id


calls = []
real_reformulate = reply_writer.reformulate
reply_writer.reformulate = lambda *a, **k: calls.append(a) or {"reply": "Hello Fares, use docs.google.com/forms/x.", "warning": "Check before sending: x."}

# ======================================================================
# A. "Send answer"
# ======================================================================
thread = waiting("Hello i want to ask you about the inscription procedures can you send me your RIB thank you")
sent.clear()
answer = client.post(f"/inbox/thread/{thread}/reply", data={"body": DRAFT, "reword": "true", "reword_language": "en"})
check("the system's own draft goes as it is, even with “Reword and send at once” on", not calls and "Answer sent" in where(answer) and sent,
      (calls, where(answer)))
check("…its form link exactly as written", sent and LINK in body_of(sent[-1]), body_of(sent[-1])[:300] if sent else None)
sent.clear()
touched = DRAFT.replace("Regarding the RIB", "About the RIB")
thread = waiting("And when does it start?")
answer = client.post(f"/inbox/thread/{thread}/reply", data={"body": touched, "reword": "true", "reword_language": "en"})
check("…touched up a little, still the system's draft: sent as it is", not calls and sent and "About the RIB" in body_of(sent[-1]), (calls, where(answer)))

thread = waiting("Can I pay in two times?")
sent.clear()
answer = client.post(f"/inbox/thread/{thread}/reply", data={"body": "oui en 2 fois, 195 dt chaque mois", "reword": "true", "reword_language": "en"})
check("my own few words are reworded; a remark stops it, the reworded text comes back in the box", calls and not sent and "Not sent" in where(answer)
      and "as_is=1" in where(answer) and "draft=" in where(answer), where(answer))
check("…the stop says the next press sends the box", "now sends what is in the box, as it is" in where(answer))
page = client.get(answer.headers["location"]).text
check("…the page carries “as it is” for that next press", 'name="as_is" value="true"' in page)
calls.clear()
answer = client.post(f"/inbox/thread/{thread}/reply", data={"body": "Hello Fares, yes, in two payments of 195 DT.", "reword": "true",
                                                            "reword_language": "en", "as_is": "true"})
check("…and it sends what is in the box, without rewording again", not calls and sent and "two payments of 195 DT" in body_of(sent[-1]), where(answer))
reply_writer.reformulate = lambda *a, **k: None
thread = waiting("Is there a class on Saturday?")
sent.clear()
answer = client.post(f"/inbox/thread/{thread}/reply", data={"body": "oui samedi 10h", "reword": "true"})
check("the assistant silent: nothing sent, the words back, the next press sends them as they are",
      not sent and "as_is=1" in where(answer) and "now sends them as they are" in where(answer), where(answer))
check("“Send answer” says it is sending, and a second press waits", 'id="reply-sending"' in page and "button.disabled = true" in page)

# ======================================================================
# B. The rewording keeps web links exact
# ======================================================================
reply_writer.reformulate = real_reformulate
prompts = []


def model(system, prompt, **k):
    prompts.append(prompt)
    return {"reply": "Hello Fares,\n\nYou can register with this form: [link 1]\n\nSee you soon."}


ea.llm_json = model
out = reply_writer.reformulate(f"inscri avec ce formulaire {LINK} merci", first_name="Fares", language="en")
check("a link reaches the model as a mark, never as the long address", prompts and "[link 1]" in prompts[-1] and LINK not in prompts[-1], prompts[-1][-400:] if prompts else None)
check("…and comes back exact in the reply", out and LINK in out["reply"] and not out["warning"], out)
ea.llm_json = lambda system, prompt, **k: {"reply": "Hello Fares,\n\nUse https://docs.google.com/forms/d/e/1FAIpQLSe_mb7Bt/viewform please."}
out = reply_writer.reformulate(f"inscri avec ce formulaire {LINK} merci", first_name="Fares", language="en")
check("a link the model changed: said, and the admin's own words (with the exact link) are given back", out and out["warning"]
      and "lost a web link" in out["warning"] and LINK in out["reply"] and "1FAIpQLSe_mb7Bt/" not in out["reply"], out)
ea.llm_json = lambda *a, **k: None

# ======================================================================
# C. An error stays until it is closed
# ======================================================================
page = client.get("/inbox?error=Something+did+not+go").text
check("an error has its close button and does not leave by itself; a notice still does",
      'aria-label="Close"' in page and "document.querySelectorAll('.page-flash.alert-success')" in page)

# ======================================================================
# D. One design, on every device, at once
# ======================================================================
now = client.get("/account/look").json()
check("the design as it is now, for an open page", now["mode"] == "soft" and now["bar"] == "#f6f3fb" and "menu_side" in now, now)
published = []
real_publish = realtime.publish_change
realtime.publish_change = lambda *topics: published.append(set(topics)) or real_publish(*topics)
answer = client.post("/settings/appearance", data={"ui_color": "#0f766e", "ui_mode": "night", "ui_menu_side": "right", "ui_background": "plain"},
                     headers={"Accept": "application/json"})
look = answer.json() if answer.status_code == 200 else {}
check("a choice in Settings is saved at once, the page gets the new design back", look.get("mode") == "night" and look.get("bar") == "#110e17"
      and look.get("menu_side") == "right" and "#0f766e" in look.get("css", ""), (answer.status_code, look))
check("…and every open page is told (the “look” topic)", any("look" in topics for topics in published), published)
published.clear()
settings = get_settings(db)
db.refresh(settings)
settings.report_email_on = not bool(getattr(settings, "report_email_on", False))
db.commit()
check("…another setting changed: no “look” sent to every page", not any("look" in topics for topics in published), published)
realtime.publish_change = real_publish
page = client.get("/inbox").text
check("an open page takes it without reloading (and again when it comes back in sight)", "window.refreshLook" in page
      and 'topics.includes("look")' in page and "visibilitychange" in page)
check("the page in night mode, the phone's top bar in its colour", 'class="mode-night"' in page and '<meta name="theme-color" content="#110e17">' in page)
check("the home-screen app opens on the same background", client.get("/app.webmanifest").json()["background_color"] == "#110e17")
check("the form without its script still works (saved, back to Settings)",
      client.post("/settings/appearance", data={"ui_color": "#c1410c", "ui_mode": "soft"}).status_code == 303)
settings_page = client.get("/settings").text
check("Settings: no button to forget, it says it is saved and that every device changes",
      'id="appearance-saved"' in settings_page and 'data-look-say="done"' in settings_page and "on the phone and on the computer" in settings_page)

# ======================================================================
# E. Reading a discussion on a phone
# ======================================================================
check("the inbox in one column as wide as the screen (a long link wraps)", "grid-template-columns: minmax(0, 1fr)" in page and "overflow-wrap:anywhere" in page)
check("an opened discussion is brought into view on a phone, with the result of the last action above it", "section.before(flash)" in page)
check("the top bar slides away while reading down, comes back going up", ".topbar.is-away" in page and 'bar.classList.toggle("is-away", away)' in page)
check("the inbox switch fits a phone", ".ibx-switch a { flex:1 1 0; min-width:0;" in page)

db.close()
print(f"{sum(results)} of {len(results)} checks passed")

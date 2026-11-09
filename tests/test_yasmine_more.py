"""Yasmine, bigger (9 November): she keeps the conversation (from page to page and day to day, "where were we?"), takes
you to any page or file (and visits them all, by herself when asked), prepares an email in its page, says what she can
do (as cards to try) and what she cannot, tells jokes, draws her answers as moving cards (only what they say), asks back
when a question is unclear, and searches more (the records, the job market, the journal, the web without a person's
name). The cloud model is FAKED (a script plays it): nothing leaves this machine. Runs on a COPY of the database."""
import testenv
DB = testenv.prepare("yasmine_more")

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network
import requests
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
requests.get = _no_web; requests.post = _no_web

import json
import os
from datetime import datetime, time as dtime, timedelta
from urllib.parse import parse_qs, unquote_plus, urlsplit
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Professor, Program, Registration, User, UserSession
from app.models.chat_line import ChatLine
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (abilities, access, auth, cards, cloud_answer, cloud_model, conversation, email_assistant as ea, email_threads as et,
                          lookups, navigate, orchestrator, small_talk, ui_words)

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

et.smtp_configured = lambda: True
ea.model_available = lambda: False
ea.llm_json = lambda *a, **k: None
os.environ["GEMINI_API_KEY"] = ""
db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
admin_user = auth.create_user(db, email="admin.ym@example.com", name="Leila Admin", password="correct horse battery", role=auth.ADMIN)
client = TestClient(main.app, follow_redirects=False)
client.post("/login", data={"email": "admin.ym@example.com", "password": "correct horse battery"})
program = db.query(Program).filter(Program.name == "DevOps").first()
person = Candidate(full_name="Fares Benali", email="fares.ym@example.com", phone="50 111 222")
db.add(person)
db.flush()
group = ClassGroup(name="YM1", program_id=program.id, status=ClassStatus.NOT_STARTED, start_date=datetime.utcnow() + timedelta(days=9),
                   total_weeks=4, start_time=dtime(18, 0), end_time=dtime(20, 0), days_of_week="sat", capacity=10, max_capacity=10)
db.add(group)
db.flush()
db.add(Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390, paid_amount=390,
                    payment_status=PaymentStatus.PAID, status=RegistrationStatus.ASSIGNED, registration_date=datetime.utcnow(), class_group_id=group.id))
db.commit()


def ask(message, lang="fr", history=None, who=None, path="/"):
    response = (who or client).post("/assistant/chat", json={"message": message, "history": history or [], "lang": lang, "path": path})
    return response.json() if response.status_code == 200 else {"reply": "", "status": response.status_code}


# ======================================================================
# A. She keeps the conversation
# ======================================================================
db.query(ChatLine).delete(); db.commit()
first = ask("bonjour")
lines = client.get("/account/conversation").json()["lines"]
check("each question and answer is kept, in order", [line["role"] for line in lines] == ["user", "bot"] and lines[0]["text"] == "bonjour"
      and lines[1]["text"] == first["reply"], lines)
said = ask("ouvre les paiements")
lines = client.get("/account/conversation").json()["lines"]
check("a move to another page is kept without the move (an old answer never moves you), with where it led",
      "navigate" not in lines[-1] and lines[-1].get("went", {}).get("url") == "/payments", lines[-1])
check("...the window gets it back on any page, and the history for the agents", conversation.history(db, admin_user)[-1]["content"] == said["reply"])
said = ask("où en étions-nous ?")
check("“where were we?”: the last question before, and her answer", said["reply"].startswith("Nous parlions de : « ouvre les paiements »")
      and said["options"] == ["ouvre les paiements"], said)
check("...in English too", ask("where were we?", lang="en")["reply"].startswith("We were talking about:"))
client.post("/account/conversation/new")
check("a new conversation: the window starts empty", client.get("/account/conversation").json()["lines"] == [])
check("...and “where were we?” still remembers the one before", "ouvre les paiements" in ask("où en étions-nous ?")["reply"])
other = TestClient(main.app, follow_redirects=False)
auth.create_user(db, email="staff.ym@example.com", name="Sonia Staff", password="correct horse battery", role=auth.STAFF)
staff = db.query(User).filter(User.email == "staff.ym@example.com").first()
staff.permissions = access.dump({"candidates": "view", "assistant": "view"})
db.commit()
other.post("/login", data={"email": "staff.ym@example.com", "password": "correct horse battery"})
check("each person sees only their own conversation", other.get("/account/conversation").json()["lines"] == [])
old = ChatLine(user_id=admin_user.id, role="user", text="très vieux", created_at=datetime.utcnow() - timedelta(days=conversation.KEEP_DAYS + 2))
db.add(old); db.commit()
conversation.forget_old(db)
check("lines older than two months go", db.query(ChatLine).filter(ChatLine.text == "très vieux").count() == 0)

# ======================================================================
# B. She takes you there
# ======================================================================
said = ask("ouvre les paiements")
check("“ouvre les paiements”: she takes you to the payments page", said["navigate"]["url"] == "/payments" and said["reply"].startswith("Je vous emmène")
      and said["agent"]["key"] == "guide", said)
check("...and says on arrival what the page is for", said["navigate"]["arrived"].startswith("Nous y sommes : Paiements."), said["navigate"])
cases = {"Open the candidates page": "/candidate/all", "7ell les classes": "/classes/", "افتح الإعدادات": "/settings", "va au tableau de bord": "/",
         "emmène-moi sur la page des formateurs": "/classes/?tab=professors", "ouvre la fiche de Fares Benali": f"/candidate/{person.id}",
         "ouvre la classe YM1": f"/classes/{group.id}", "ouvre le cours de la classe YM1": f"/classes/{group.id}/course",
         "ouvre le programme DevOps": f"/programs/{program.id}", "ouvre les emails de Fares Benali": f"/inbox?candidate={person.id}",
         "take me to the job market": "/programs/market", "ouvre les professeurs": "/classes/?tab=professors", "tu peux ouvrir la page des rappels ?": "/reminders"}
wrong = {text: (ask(text, lang=None).get("navigate") or {}).get("url") for text in cases}
check("pages, files, classes, programs, in four languages", all(wrong[text] == url for text, url in cases.items()),
      {text: url for text, url in wrong.items() if url != cases[text]})
check("“reviens”: back to the page before", ask("reviens")["back"] is True)
said = ask("fais-moi visiter toute seule")
check("“fais-moi visiter toute seule”: the visit, page after page, by herself", said["tour"] == {"auto": True}, said.get("tour"))
check("“show me around”: the visit, one page at a time", ask("show me around", lang="en")["tour"] == {"auto": False})
said = ask("ouvre")
check("“ouvre” alone: which page? with the main ones to tap", said["reply"].startswith("Quelle page") and "Paiements" in said["options"], said)
said = ask("prépare un email pour Fares Benali : la séance de samedi commence à 18h, apportez votre ordinateur")
target = urlsplit(said["navigate"]["url"])
query = parse_qs(target.query)
check("“prépare un email pour Fares : …”: the email page, for him, with the words as typed",
      target.path == "/inbox/compose" and query["candidate"] == [person.id] and query["body"] == ["la séance de samedi commence à 18h, apportez votre ordinateur"]
      and "Rien ne part sans vous" in said["reply"], said)
page = client.get(said["navigate"]["url"]).text
check("...the email page opens with those words written, nothing sent", "apportez votre ordinateur" in page and "la séance de samedi commence à 18h" in page)
check("an order to send still goes to its own rules (not a move)", not navigate.wants("envoie un email à Fares pour lui dire que la séance est annulée"))
check("“ouvre le pdf …” stays a PDF (its own rules)", not navigate.wants("ouvre le pdf du programme DevOps"))
said = ask("ouvre les paiements", who=other)
check("a page someone may not open is never offered to them", "navigate" not in said or not said.get("navigate"), said)
check("...they are told so", said["reply"].startswith("Cette page ne vous est pas ouverte"), said["reply"])

# ======================================================================
# C. What she can do, and what she cannot
# ======================================================================
said = ask("qu'est-ce que tu peux faire ?")
grid = (said.get("cards") or [{}])[0]
check("“what can you do?”: every ability as a card with its moving picture and an example to try",
      grid.get("kind") == "abilities" and grid.get("covers") and len(grid["items"]) >= 14 and all(item["motion"] and item["ask"] for item in grid["items"]), grid)
check("...the short line above the cards, the points in the text (for her voice and the PDF)", said["short"].startswith("Voici ce que je sais faire")
      and said["reply"].count("•") >= 14)
check("...and what she does not do (yet), said plainly", grid["cannot"]["items"] and "voix" in grid["cannot"]["items"][0])
check("...in Tunisian, Arabic and English too", all(ask(text, lang=None).get("cards", [{}])[0].get("kind") == "abilities"
                                                     for text in ("chnowa tnajem ta3mel?", "ماذا يمكنك أن تفعلي؟", "what can you do?")))
said = ask("tu peux envoyer des sms ?")
check("“tu peux envoyer des sms ?”: yes, how, and one to try", said["reply"].startswith("Oui !") and said["options"] and "Fares" in said["options"][0], said)
said = ask("can you call candidates?", lang="en")
check("“can you call candidates?”: no, and what she does instead", said["reply"].startswith("No:") and "+216" in said["reply"], said["reply"])
check("“tu peux m'envoyer les programmes en pdf ?” is a request, done (not described)", not abilities.can_you("tu peux m'envoyer les programmes en pdf ?")
      and not abilities.wants("tu peux m'envoyer les programmes en pdf ?"))
check("“peux-tu me dire qui a payé ?” is a question about the records", not abilities.can_you("peux-tu me dire qui a payé ?"))
grid = ask("what can you do?", who=other, lang="en")["cards"][0]["items"]
check("a team member sees only the abilities they have", "system" not in {item["key"] for item in grid} and "figures" in {item["key"] for item in grid})

# ======================================================================
# D. Jokes, and playing
# ======================================================================
said = ask("raconte-moi une blague")
check("“raconte-moi une blague”: one of her jokes, and “Une autre !” to tap", any(joke in said["reply"] for joke in small_talk.JOKES["fr"])
      and said["options"] == ["Une autre !"] and said["agent"]["key"] == "talk", said)
history = [{"role": "user", "content": "raconte-moi une blague"}, {"role": "assistant", "content": said["reply"]}]
again = ask("Une autre !", history=history + [{"role": "user", "content": "Une autre !"}])
check("“Une autre !” right after: another joke, not the same", any(joke in again["reply"] for joke in small_talk.JOKES["fr"])
      and not any(joke in again["reply"] and joke in said["reply"] for joke in small_talk.JOKES["fr"]), again["reply"])
laugh = ask("haha", history=history + [{"role": "user", "content": "haha"}])
check("“haha” after a joke: she is glad, and offers another", laugh["reply"].startswith("Haha") or "J'en ai d'autres" in laugh["reply"], laugh["reply"])
check("a joke about what was named (“une blague sur Python”)", "Python" in ask("une blague sur python stp")["reply"])
told = {"en": ask("tell me a joke", lang="en")["reply"], "tn": ask("a3tini nokta", lang=None)["reply"], "ar": ask("احكي لي نكتة", lang=None)["reply"]}
check("in English, Tunisian and Arabic", all(any(joke in told[lang] for joke in small_talk.JOKES[lang]) for lang in told), told)
check("bored: a joke for the break", "pause" in ask("je m'ennuie")["reply"])
check("teased: she laughs it off and offers to try again", ask("tu es nulle")["reply"].startswith(("Aïe", "Oups")))
check("“tu es un robot ?”: a light answer", "code" in ask("tu es un robot ?")["reply"] or "numérique" in ask("tu es un robot ?")["reply"])
check("“tell me a joke” is not an order to send (“tell”)", small_talk.read("tell me a joke")["kinds"] == ["joke"])

# ======================================================================
# E. Cards: the kind of data drawn, only what the answer says
# ======================================================================
facts = {"overview": {"candidates": 4, "classes_running": 4, "programs": 12, "money_received_in_all": "1200 DT", "share_received": "60%"}}
drawn = cards.from_facts(facts, "Vous avez 4 candidats ; 1200 DT reçus, soit 60%.", "fr")
tiles = drawn[0]["items"]
check("figures said become tiles (in the answer's language), the ones not said do not",
      [tile["label"] for tile in tiles] == ["Candidats", "Reçu en tout"] and drawn[1]["kind"] == "ring" and drawn[1]["value"] == 60, drawn)
check("...a small figure counts only when what it counts is said (4 candidates, not the 4 classes)", "Classes en cours" not in [tile["label"] for tile in tiles])
owing = {"not_paid": {"not_paid_in_full": [{"candidate": "Ahmed Naji", "owes": "490 DT"}, {"candidate": "Sami Ben", "owes": "200 DT"},
                                           {"candidate": "Nour Ali", "owes": "90 DT"}], "people": 3, "owed_in_all": "780 DT"}}
drawn = cards.from_facts(owing, "Ahmed Naji doit 490 DT et Sami Ben 200 DT.", "fr")
bars = next(card for card in drawn if card["kind"] == "bars")
check("a list of amounts becomes bars, only the people the answer names", [item["label"] for item in bars["items"]] == ["Ahmed Naji", "Sami Ben"], drawn)
drawn = cards.from_reply("Ils doivent encore :\n- Ahmed Naji : 490 DT\n- Sami Ben : 200 DT")
check("an answer of the rules with a list of amounts is drawn too", drawn and drawn[0]["kind"] == "bars" and drawn[0]["items"][1]["display"] == "200 DT")
check("a list without amounts stays as it is written", cards.from_reply("Voici :\n- Ahmed\n- Sami\n- Nour") == [])

# ======================================================================
# F. The cloud reader asks back, searches more (faked model)
# ======================================================================
os.environ["GEMINI_API_KEY"] = "key-one"
cloud_model._resting.clear()
sent_bodies = []


class Answer:
    def __init__(self, body):
        self.status_code, self._body, self.text = 200, body, json.dumps(body)

    def json(self):
        return self._body


script = {}


def google(url, data=None, headers=None, timeout=None):
    body = json.loads(data.decode("utf-8"))
    sent_bodies.append(body)
    tools = body.get("tools") or []
    if tools and "google_search" in tools[0]:
        return Answer({"candidates": [{"content": {"role": "model", "parts": [{"text": "Les offres en data en Tunisie demandent surtout SQL et Power BI."}]},
                                       "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://example.org/emploi-data", "title": "example.org"}}]}}]})
    if tools:                                      # reading
        return Answer({"candidates": [{"content": {"role": "model", "parts": [{"functionCall": call} for call in script["read"]]}}]})
    return Answer({"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(script["write"])}]}}]})


requests.post = google
script["read"] = [{"name": "ask_back", "args": {"question": "Combien de quoi : de candidats, d'inscriptions ou de paiements ?",
                                                 "options": ["Candidats", "Inscriptions", "Paiements"]}}]
before = len(sent_bodies)
said = ask("combien ?")
check("too vague: she asks back, with the choices to tap", said["reply"].startswith("Combien de quoi") and said["options"] == ["Candidats", "Inscriptions", "Paiements"]
      and said["agent"]["key"] == "reader", said)
check("...in one call (no answer written from nothing)", len(sent_bodies) - before == 1, len(sent_bodies) - before)
script["read"] = [{"name": "candidate", "args": {"name": "Fares"}}]
script["write"] = {"language": "fr", "route": "answer", "sensitive": "none", "asks": [], "reply": "Lequel : Fares Benali ou Fares Trabelsi ?",
                   "clarify": {"question": "Lequel ?", "options": ["Fares Benali", "Fares Trabelsi"]}}
twin = Candidate(full_name="Fares Trabelsi", email="fares.t.ym@example.com"); db.add(twin); db.commit()
check("a name that fits several people: the lookup says so (never guessed)", "several_candidates" in lookups._person(db, "Fares"))
said = ask("combien doit Fares ?")
check("...and she asks which one, the names to tap", said["options"] == ["Fares Benali", "Fares Trabelsi"], said)
script["read"] = [{"name": "web_search", "args": {"query": "compétences demandées offres emploi data Tunisie 2026"}}]
script["write"] = {"language": "fr", "route": "answer", "sensitive": "none", "asks": [{"ask": "compétences", "answered": True, "evidence": ["SQL et Power BI"]}],
                   "reply": "Sur le web, les offres en data en Tunisie demandent surtout SQL et Power BI."}
said = ask("quelles compétences demande le marché en data en Tunisie ?")
check("the web for what the application cannot know: Google through the model, said as from the web", said["reply"].startswith("Sur le web")
      and any((body.get("tools") or [{}])[0].get("google_search") == {} for body in sent_bodies), said)
check("...its pages as links to open, and “The web (Google)” as the source", any(card["kind"] == "links" and card["items"][0]["url"] == "https://example.org/emploi-data"
                                                                                   for card in said["cards"]) and any(item["label"] == "The web (Google)" for item in said["sources"]), said.get("cards"))
check("a web search never carries someone of the centre", lookups._web(db, "Fares Benali salaire").get("error", "").startswith("Not searched")
      and lookups._web(db, "contact fares.ym@example.com").get("error", "").startswith("Not searched"))
script["read"] = [{"name": name, "args": {}} for name in ("job_market", "news", "waiting_today", "what_happened", "connected", "reminders")] + \
                 [{"name": "search", "args": {"words": "Fares"}}, {"name": "program_advice", "args": {"program": "DevOps"}}]
script["write"] = {"language": "fr", "route": "answer", "sensitive": "none", "asks": [], "reply": "Voici."}
before = len(sent_bodies)
ask("tout ce que tu sais")
written = sent_bodies[-1]["contents"][0]["parts"][0]["text"]
names = ("job_market", "news", "waiting_today", "what_happened", "connected", "reminders", "search(", "program_advice(")
check("the new lookups run on the records (search, job market, journal, advice, today, what happened, who is connected, reminders)",
      all(name in written for name in names) and "the lookup failed" not in written, [name for name in names if name not in written] or written[:600])
found = lookups._search(db, admin_user, "Fares")
check("“search” finds people in the records, with where to open them", any(item["url"] == f"/candidate/{person.id}" for item in found["found"]), found)
prompt = sent_bodies[-1]["systemInstruction"]["parts"][0]["text"]
check("the writer is told to answer exactly what was asked, nothing more", "nothing it did not ask" in prompt)
staff.permissions = access.dump({"candidates": "view", "assistant": "view", "payments": "view"})
db.commit()
script["read"] = [{"name": "overview", "args": {}}]
script["write"] = {"language": "fr", "route": "answer", "sensitive": "none", "asks": [], "reply": "Voici."}
check("a team member with the assistant is read by the cloud reader too", ask("comment va le centre ?", who=other).get("agent", {}).get("key") == "reader")
os.environ["GEMINI_API_KEY"] = ""
requests.post = _no_web

# ======================================================================
# G. The graph and her words
# ======================================================================
check("the graph has the new steps, drawn on the agents page", {"go", "abilities", "recall"} <= set(orchestrator.STEPS)
      and {"go", "abilities", "recall"} <= {step["key"] for line in orchestrator.drawing() for step in line["steps"]})
phrases = ui_words.phrases()
needed = ["The web (Google)", "Who is connected"] + [text for key in ("go", "abilities", "recall") for text in orchestrator.STEPS[key][1:]]
missing = [text for text in needed if len(phrases.get(text) or []) != 2 or not all(phrases[text])]
check("her new sentences in French and Arabic, written by hand", not missing, missing[:3])
check("her four languages for moves, abilities, jokes and “where were we”", all(set(table) == {"fr", "en", "ar", "tn"} for table in
      (navigate.SAY, abilities.HEAD, small_talk.JOKES, conversation._RECALL_SAY)))
page = client.get("/").text
check("her window: “New conversation”, the cards, the moves", 'id="assistant-new"' in page and "window.drawCards" in page and "assistant:arrived" in page
      and "startGrandTour" in page)

db.close()
print(f"{sum(results)} of {len(results)} checks passed")

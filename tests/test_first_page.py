"""The inbox made simple (three tabs, the rest behind "⋯"), Yasmine who says what came in and went out and shows the
application around, the journal beside every page (photos, the professors' corner), the first page's stories and
days in a row, and the hook style of the videos.
Runs on a COPY of the database; nothing leaves this machine."""
import json
import time
import testenv
DB = testenv.prepare("first_page")

import smtplib, imaplib
def _no_network(*a, **k): raise AssertionError("a test tried to open a real mail connection")
smtplib.SMTP_SSL = _no_network; smtplib.SMTP = _no_network; imaplib.IMAP4_SSL = _no_network
import requests
def _no_web(*a, **k): raise AssertionError("a test tried to reach the web")
requests.get = _no_web; requests.post = _no_web

from datetime import date, datetime, timedelta, time as dtime
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, ClassGroup, ClassStatus, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.news_item import NewsItem
from app.models.professor import Professor
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, chatbot, digest, email_threads as et, friend, grand_tour, journal, social_images, social_video,
                          stories)
from app.services.schedule_utils import local_now
from app.services.settings_service import get_settings

results = []
def check(label, condition, detail=""):
    results.append(bool(condition))
    print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

et.smtp_configured = lambda: True
db = SessionLocal()
now = datetime.utcnow()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
admin_user = auth.create_user(db, email="admin.fp@example.com", name="Fp Admin", password="correct horse battery", role=auth.ADMIN)
client = TestClient(main.app, follow_redirects=False)
client.post("/login", data={"email": "admin.fp@example.com", "password": "correct horse battery"})

# ======================================================================
# A. The inbox: what needs you, what needs no reply, every conversation
# ======================================================================
page = client.get("/inbox").text
check("inbox: three tabs, in plain words", all(word in page for word in ("Needs you", "No reply needed", "All conversations"))
      and 'href="/inbox?filter=todo' in page and 'href="/inbox?filter=aside' in page and 'href="/inbox?filter=conversations' in page)
check("inbox: the other filters wait under “More”", 'class="ix-more-filters"' in page and "/inbox?filter=sms" in page and "/inbox?filter=to_send" in page)
check("inbox: one line says what waits, one button writes", 'class="ix-hello"' in page and ("Nothing waits for you" in page or "for your answer" in page) and "Write a message" in page)
menu = page[page.index('id="ix-menu"'):page.index("</details>", page.index('id="ix-menu"'))]
check("inbox: how messages go, the figures and the tools are behind “⋯”", 'action="/inbox/channels"' in menu and 'id="live-inbox-kpis"' in menu
      and "Knowledge base" in menu and "Email templates" in menu and "Try with a sample email" in menu)
check("inbox: what works is not said; only what does not", "Assistant ready" not in page)
candidate = Candidate(full_name="Ines Inbox", email="ines.inbox@example.com"); db.add(candidate); db.flush()
from app.services import email_assistant as ea
ea.model_available = lambda: True
ea.llm_json = lambda *a, **k: None
message = ea.ingest_inbound(db, sender=candidate.email, subject="Prix", body="Bonjour, quel est le prix de la formation DevOps ?", message_id="<fp-1@example.com>")
db.commit()
inbound = db.get(CandidateEmail, message["email_id"])
page = client.get(f"/inbox?thread={inbound.thread_id}").text
check("inbox: the assistant speaks as Yasmine", "Yasmine" in page)
check("inbox: an answer's other tools are folded", 'class="ix-fold' in page)
check("inbox: the reply's options are folded, Reformulate stays in sight", 'ix-options' in page and 'id="reformulate-btn"' in page)

# ======================================================================
# B. Yasmine: what came in and went out, and the visit
# ======================================================================
since = datetime.utcnow() - timedelta(hours=3)
found = digest.counts(db, since)
check("digest: the message that came in is counted", found["came"]["messages"] >= 1, found)
check("digest: said in each language", all(digest.sentence(db, since, lang) for lang in ("fr", "en", "ar", "tn"))
      and "message" in digest.sentence(db, since, "fr") and digest.sentence(db, since, "en").startswith("While you were away"))
check("digest: nothing since a moment ago is said too", "Nothing new since." in digest.sentence(db, datetime.utcnow() + timedelta(minutes=1), "en"))
me = auth.user_for(db, client.cookies.get("session") or "") or type("U", (), {"is_admin": True, "name": "Fp Admin", "id": admin_user.id, "permissions": None})()
said = friend.talk(db, me, "/", "en", since=datetime.utcnow() - timedelta(hours=5))
check("Yasmine: back after a while, she says what happened", said["away"] and "While you were away" in said["away"] and said["away_links"], said.get("away"))
said = friend.talk(db, me, "/", "en", since=datetime.utcnow() - timedelta(minutes=10))
check("...not after ten minutes", said["away"] is None)
check("Yasmine: the visit, page after page, in her language", len(said["tour"]) == len(grand_tour.STEPS) and said["words"]["tour_start"] == "Show me around"
      and friend.talk(db, me, "/", "fr", guide_lang="tn")["words"]["tour_start"] == grand_tour.WORDS["tn"]["start"])
check("...every page of the visit opens", all(client.get(step["path"]).status_code == 200 for step in said["tour"]),
      [(step["path"], client.get(step["path"]).status_code) for step in said["tour"]])
response = client.get("/account/friend?path=/&since=" + str(int((time.time() - 4 * 3600) * 1000)))
check("Yasmine's address takes when this browser last heard her", response.status_code == 200 and "away" in response.json())
check("...and a wrong value is ignored", client.get("/account/friend?path=/&since=yesterday").status_code == 200)
frame = client.get("/").text
check("the page leads the visit: next page, the menu link that calls, the page tour that ends", "tourGo" in frame and "fr-next" in frame and "tour:closed" in frame)
answer = chatbot.answer(db, "Qu'est-ce qui s'est passé aujourd'hui ?", user=me)
reply = answer["reply"]
check("the chat: “what happened today?” is answered from the records", "Aujourd'hui" in reply or "Today" in reply, reply[:200])

# ======================================================================
# C. The journal beside every page
# ======================================================================
feed = b"""<rss xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><title>Agents everywhere</title><link>https://news.example/a</link><description>&lt;p&gt;Agents &lt;img src="https://img.example/a.jpg"&gt;&lt;/p&gt;</description></item>
<item><title>Docker news</title><link>https://news.example/b</link><media:thumbnail url="https://img.example/b.png"/></item>
<item><title>Kubernetes</title><link>https://news.example/c</link><enclosure url="https://img.example/c.webp" type="image/webp"/></item>
<item><title>No photo</title><link>https://news.example/d</link><description>Plain words</description></item>
<item><title>Video only</title><link>https://news.example/e</link><enclosure url="https://img.example/e.mp4" type="video/mp4"/></item>
</channel></rss>"""
items = journal.parse(feed)
check("journal: each article's photo is taken from its feed", [item["image"] for item in items] == ["https://img.example/a.jpg", "https://img.example/b.png",
                                                                                                 "https://img.example/c.webp", None, None], [item["image"] for item in items])
db.query(NewsItem).delete(); db.commit()
for item in items:
    journal.keep(db, "ai" if "Agents" in item["title"] else "devops", "Test feed", item)
db.commit()
check("journal: kept with the article", db.query(NewsItem).filter(NewsItem.url == "https://news.example/a").one().image_url == "https://img.example/a.jpg")
old = db.query(NewsItem).filter(NewsItem.url == "https://news.example/d").one()
journal.keep(db, "devops", "Test feed", {**items[3], "image": "https://img.example/d.jpg"}); db.commit()
check("journal: an article read before photos were kept gets its photo later", db.get(NewsItem, old.id).image_url == "https://img.example/d.jpg")
panel = client.get("/account/journal")
check("journal: the panel opens, with the articles and their moving photos", panel.status_code == 200 and "jr-card" in panel.text and "https://img.example/b.png" in panel.text
      and "jrKen" not in panel.text and "Agents everywhere" in panel.text, panel.status_code)
check("journal: an article without a photo gets a moving picture of its field", panel.text.count('class="jr-scene"') == panel.text.count('class="jr-card"'))
check("journal: on every page, its button and its panel", 'data-journal-open' in frame and 'id="journal-panel"' in frame and "jrKen" in frame)
check("journal: what arrived since it was last opened", client.get("/account/journal/new").json()["new"] >= 1
      and client.get("/account/journal/new?since=" + str(int((time.time() + 300) * 1000))).json()["new"] == 0)
check("journal: the page tour knows it", any(step["sel"] == "[data-journal-open]" for step in __import__("app.services.page_tours", fromlist=["FRAME"]).FRAME))

# The professors' corner
program = db.query(Program).filter(Program.name == "DevOps").first() or db.query(Program).first()
professor = Professor(name="Pierre Prof", email="pierre.prof@example.com"); db.add(professor); db.flush()
group = ClassGroup(name="FP-DEVOPS-1", program_id=program.id, status=ClassStatus.ONGOING, professor_id=professor.id, professor_name=professor.name,
                   start_date=now - timedelta(days=10), total_weeks=8, current_week=2, start_mode="fixed_date", capacity=10, max_capacity=10)
db.add(group); db.commit()
auth.create_user(db, email="pierre.prof@example.com", name="Pierre Prof", password="correct horse battery", role=auth.PROFESSOR, professor_id=professor.id)
teacher = TestClient(main.app, follow_redirects=False)
teacher.post("/login", data={"email": "pierre.prof@example.com", "password": "correct horse battery"})
corner = teacher.get("/account/journal")
check("professor: the journal opens for them, with their corner", corner.status_code == 200 and "Your corner" in corner.text and program.name in corner.text, corner.status_code)
check("professor: the button is on their pages too", "data-journal-open" in teacher.get("/teacher").text)
check("professor: their programs are found", [item.id for item in journal.professor_programs(db, professor.id)] == [program.id])

# ======================================================================
# D. The first page's stories and the days in a row
# ======================================================================
person = Candidate(full_name="Sarra Story", email="sarra.story@example.com"); db.add(person); db.flush()
db.add(Registration(candidate_id=person.id, program_id=program.id, program_name=program.name, delivery_mode="online", total_amount=390, total_price=390,
                    paid_amount=0, payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING, registration_date=now))
db.commit()
told = stories.for_dashboard(db)
keys = [story["key"] for story in told]
check("stories: only those with something in them, from the records", "people" in keys and "news" in keys and all(story["slides"] for story in told), keys)
people = next(story for story in told if story["key"] == "people")
check("stories: the new people, by first name", any(slide.get("big") == "Sarra" for slide in people["slides"]))
news = next(story for story in told if story["key"] == "news")
check("stories: the news with their photos", all(slide.get("image") for slide in news["slides"]) and news["image"])
check("stories: a signature that changes with their content (the ring comes back)", people["sig"] != stories._story("people", "x", "i", "c", people["slides"][:1])["sig"])
settings = get_settings(db); settings.active_days = None; db.commit()
first = stories.mark_today(db)
again = stories.mark_today(db)
check("days in a row: today counted once", first["new_today"] and not again["new_today"] and again["days"] == 1)
today = local_now().date()
check("days in a row: counted back from today", stories.run_of([(today - timedelta(days=k)).isoformat() for k in range(4)], today) == 4
      and stories.run_of([(today - timedelta(days=k)).isoformat() for k in (1, 2)], today) == 2
      and stories.run_of([(today - timedelta(days=k)).isoformat() for k in (2, 3)], today) == 0)
settings.active_days = json.dumps([(today - timedelta(days=k)).isoformat() for k in (1, 2, 3)]); db.commit()
board = client.get("/").text
check("dashboard: the stories open the page, with the days in a row", 'class="sx-strip"' in board and "days in a row" in board and 'id="sx-viewer"' in board)
check("dashboard: a new day in a row is celebrated, once a day", "streak-shown" in board and "4 days in a row!" in board)
check("dashboard: what was done today fills the ring", "done today" in board)

# ======================================================================
# E. The videos: the hook style
# ======================================================================
check("videos: the hook style is offered, and chosen first", "hook" in social_video.STYLES and 'name="style" value="hook" checked' in client.get("/social").text)
ideas = social_video.scenes(db, "program", "tn", "hook") or social_video.scenes(db, "success", "tn", "hook") or social_video.scenes(db, "both", "tn", "hook")
check("videos: a punch line first, and again at the end (it loops)", not ideas or (ideas[0]["key"] == "hook" and ideas[-1]["key"] == "hook-end"
      and ideas[0]["headline"] == social_video.HOOKS["tn"]), [item["key"] for item in ideas])
calm = social_video.scenes(db, "program", "tn") or social_video.scenes(db, "success", "tn") or social_video.scenes(db, "both", "tn")
check("videos: the calm style keeps its intro", not calm or calm[0]["key"] == "intro")
if ideas:
    pictures = [social_images.picture(item, "post", social_images.DEFAULT_LOOK) for item in ideas[:2]]
    made = list(social_video.frames(pictures, [0.3, 0.3], "hook"))
    width, height = social_images.SIZES["post"]
    check("videos: hook frames are drawn (zoom punch, flash at the cut)", len(made) == 2 * int(0.3 * social_video.FPS) and all(len(frame) == width * height * 3 for frame in made))
asset = social_video.make(db, kind="program", fmt="story", lang="tn", style="hook")
check("videos: the style is kept with the video", json.loads(asset.spec)["style"] == "hook" and json.loads(social_video.make(db, kind="program", style="zzz").spec)["style"] == "calm")

db.close()
print(f"{sum(results)} of {len(results)} checks passed")

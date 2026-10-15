"""Three later requests: the website's feedback and its analysis on the dashboard, a change taken from the
website shown as done (no button to press again), and an empty class that can be removed. Staging copy, COPY of the database."""
import testenv
testenv.prepare("more")
check = testenv.Checks()

import json
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import ClassGroup, ClassStatus, Program, User, UserSession
from app.models.website import WebsiteItem
from app.services import auth, brand, email_assistant as ea, email_templates as tp, email_threads as et, enrollment_service as es, feedback as fb, website_sync as ws

et.send_email = lambda **kw: True; tp.send_email = lambda **kw: True
ea.model_available = lambda: True
ea.llm_json = lambda *a, **k: None
brand.refresh_from_facebook = lambda force=False: "same"

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="more-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "more-test-1"})
from urllib.parse import unquote_plus
def where(response): return unquote_plus(response.headers.get("location", ""))
def card_of(page, title):
    from html import escape
    start = page.index('<div class="ws-title">' + escape(title, quote=False))
    return page[start:page.index('</article>', start)]

# ======================================================================
# A. The feedback of the website, on the dashboard, with its analysis
# ======================================================================
said = fb.overview(db)
home = admin.get("/")
text = home.text
check("the dashboard opens", home.status_code == 200, home.status_code)
check("the copy holds the feedback read from the website", said["total"] >= 29 and said["read"] >= 25, (said["total"], said["read"]))
check("every feedback is on the dashboard", text.count('class="db-said-card') == said["total"], (text.count('class="db-said-card'), said["total"]))
check("with who said it and what they said", all(row["name"] in text for row in said["rows"][:6] if row["name"]) and "“" in text)
check("with how each one sounds", text.count('class="db-said-who"') == said["total"] and "ui-chip ok ms-auto" in text)
check("and the analysis of all of them: share of happy, stars, what they talk about, the words that come back",
      f'{said["happy_share"]}%' in text and "are happy" in text and "stars on average" in text and "What they talk about" in text and "Words that come back" in text)
check("the themes are those found in the feedback", all(theme["label"] in text for theme in said["themes"][:3]), [t["label"] for t in said["themes"][:3]])
check("it leads to the Website page", 'href="/website#feedback"' in text)

# ======================================================================
# B. Taking the website's version: said once, then no button to press again
# ======================================================================
view = ws.overview(db)
followed = [entry for entry in view["formations"] if entry["program"]]
entry = next((e for e in followed if e["differences"]), None)
if entry is None:                                    # make one: the admin wrote their own hours
    entry = followed[0]
    entry["program"].hours_per_week = (entry["program"].hours_per_week or 0) + 3
    db.commit()
    entry = next(e for e in ws.overview(db)["formations"] if e["program"] and e["program"].id == entry["program"].id)
program, item = entry["program"], entry["item"]
difference = entry["differences"][0]
field, label = difference["field"], difference["label"]
page = admin.get("/website").text
check("a difference is offered with its button", "Where your program says something else" in page and f'name="field" value="{field}"' in page, field)
answer = admin.post(f"/website/programs/{program.id}/take", data={"field": field})
db.expire_all()
check("taking it changes the program", answer.status_code == 303 and "now as on the website" in where(answer), where(answer))
after = next(e for e in ws.overview(db)["formations"] if e["program"] and e["program"].id == program.id)
check("the difference is gone", field not in [d["field"] for d in after["differences"]])
check("and it is remembered as changed, with before and now", [c["field"] for c in after["changed"]][:1] == [field] and after["changed"][0]["was"] == difference["yours"] and after["changed"][0]["now"] == difference["website"] and after["changed"][0]["by"] == "you", after["changed"][:1])
page = admin.get("/website").text
card = card_of(page, item.title)
check("the page says it is already changed", "Already changed in your program" in card and f"<b>{label}</b>" in card and "Before:" in card and "Now:" in card)
check("with nothing to press for it again", f'name="field" value="{field}"' not in card and "Changed" in card)
answer = admin.post(f"/website/programs/{program.id}/take", data={"field": field})
db.expire_all()
check("asking again changes nothing and says so", "already says what the website says" in where(answer), where(answer))
again = next(e for e in ws.overview(db)["formations"] if e["program"] and e["program"].id == program.id)
check("and it is listed once", [c["field"] for c in again["changed"]].count(field) == 1)
admin.post(f"/website/programs/{program.id}/take", data={"field": "all"})
db.expire_all()
done = next(e for e in ws.overview(db)["formations"] if e["program"] and e["program"].id == program.id)
page = admin.get("/website").text
card = card_of(page, item.title)
check("taking everything leaves no difference and no button", not done["differences"] and "Use the website's" not in card and "Take all from the website" not in card)
check("and says the program is now the same as the website", "It now says the same as the website." in card or "says the same as the website" in card)
setattr(program, field, getattr(db.get(Program, program.id), field))
changed_back = db.get(Program, program.id)
if isinstance(getattr(changed_back, field), (int, float)):
    setattr(changed_back, field, getattr(changed_back, field) + 5)
else:
    setattr(changed_back, field, "Mon propre texte")
db.commit()
back = next(e for e in ws.overview(db)["formations"] if e["program"] and e["program"].id == program.id)
check("if the admin writes their own version afterwards, the choice is offered again", field in [d["field"] for d in back["differences"]] and field not in [c["field"] for c in back["changed"]])
same = next((e for e in ws.overview(db)["formations"] if e["program"] and not e["differences"] and not e["changed"] and not (e["plan"] and e["plan"]["site_weeks"] and not e["plan"]["from_site"])), None)
if same is not None:
    page = admin.get("/website").text
    card = card_of(page, same["item"].title)
    check("a program that already matches says so, without any button", "Your program says the same as the website" in card and "Use the website's" not in card)

# ======================================================================
# C. An empty class does not start, and can be removed
# ======================================================================
es.sync_class_lifecycle(db); db.expire_all()
empty = db.query(ClassGroup).filter(ClassGroup.name == "P-20261001-2").first()
full = db.query(ClassGroup).filter(ClassGroup.name == "MD1").first()
empty_id, full_id = empty.id, full.id
check("the empty class is not running", empty.status == ClassStatus.NOT_STARTED and es.enrolled_count(db, empty.id) == 0)
page = admin.get(f"/classes/{empty.id}").text
check("its page offers to remove it", "Remove this class" in page and f'action="/classes/{empty.id}/delete"' in page and "Nobody is in this class" in page)
listing = admin.get("/classes/").text
check("so does its card in the list", listing.count('title="Nobody is in this class: it can be removed"') == 2, listing.count('title="Nobody is in this class: it can be removed"'))
page = admin.get(f"/classes/{full.id}").text
check("a class with candidates has no such button", f'action="/classes/{full_id}/delete" data-confirm="Remove' not in page)
answer = admin.post(f"/classes/{full.id}/delete")
db.expire_all()
check("and cannot be removed while they are in it", db.get(ClassGroup, full_id) is not None and "Move or remove the enrolled candidates" in where(answer), where(answer))
answer = admin.post(f"/classes/{empty_id}/delete")
db.expire_all()
check("the empty class is removed", db.get(ClassGroup, empty_id) is None and "deleted" in where(answer), where(answer))
check("the classes page still opens", admin.get("/classes/").status_code == 200)
db.close()
check.done()

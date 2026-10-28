"""The tour of every page (the “?” at the top): each page has one, its parts are on the page, it comes in the page's
language, the first visit offers it; the assistant knows every page (never used to answer candidates); Yasmine says what
a page is for from its tour; “What the assistant knows” lists every program; the whole centre at a glance.
Runs on a COPY; nothing leaves this machine."""
import json
import re

import testenv

testenv.prepare("page_tours")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, ClassGroup, Program, User, UserSession
from app.models.knowledge import KnowledgeEntry
from app.services import auth, chatbot, email_assistant as ea, friend, knowledge_service, page_tours, translations

ea.llm_json = lambda *a, **k: None
db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="tours-test-12", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "tours-test-12"})
translations.ensure(db)

candidate = db.query(Candidate).first()
group = db.query(ClassGroup).first()
program = db.query(Program).first()
pages = ["/", "/candidate/all", f"/candidate/{candidate.id}", "/classes/", f"/classes/{group.id}", f"/classes/{group.id}/course",
         f"/classes/{group.id}/attendance", "/programs/all", f"/programs/{program.id}", f"/programs/{program.id}/pdf", "/programs/market",
         "/programs/documents", "/programs/videos", "/programs/expertise", "/quiz/all", "/inbox", "/inbox/compose", "/inbox/knowledge",
         "/inbox/team", "/inbox/templates", "/payments", f"/candidate/{candidate.id}/payment-reminder", "/reminders", "/website", "/system",
         "/settings", "/settings/team", "/settings/translations", "/data", "/data/candidates", "/find?q=python", "/guide", "/account"]


def present(selector: str, page: str) -> bool:
    """Whether the part a step names is in the page as written by the server (what the browser adds later is not)."""
    base = selector.split(":has(")[0]
    inner = re.search(r":has\(([^)]+)\)", selector)
    found = True
    for piece in re.findall(r"#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:=\"([^\"]*)\")?\]", base):
        ident, klass, attribute, value = piece
        if ident:
            found &= f'id="{ident}"' in page
        elif klass:
            found &= re.search(rf'class="[^"]*\b{re.escape(klass)}\b', page) is not None
        elif attribute:
            found &= (f'{attribute}="{value}"' in page) if value else (attribute in page)
    if inner:
        token = inner.group(1).lstrip(".#")
        found &= token in page
    return found


missing, without = [], []
for address in pages:
    response = admin.get(address)
    page = response.text
    tour = page_tours.for_path(address.split("?")[0])
    if response.status_code != 200 or tour is None or 'id="page-tour"' not in page or "data-tour-show" not in page:
        without.append((address, response.status_code, tour is not None))
        continue
    data = json.loads(re.search(r'<script type="application/json" id="page-tour">(.*?)</script>', page, re.S).group(1))
    frame = {step["sel"] for step in page_tours.FRAME}
    for step in data["steps"]:
        if step["sel"] in frame or step["sel"].startswith("#friend") or step.get("optional"):
            continue                       # the frame of every page, and Yasmine (drawn by her script)
        if not present(step["sel"], page):
            missing.append(f"{address} {step['sel']}")
check("every page has its tour, and its “?” opens it", not without, without)
check("every part a tour names is on its page", not missing, missing[:8])
check("every tour has parts that say what can be done", all(tour["steps"] and all(step["what"] for step in tour["steps"]) for _, tour in page_tours.every()))

# ---- In the page's language ----
admin.cookies.set("lang", "fr")
page = admin.get("/payments").text
data = json.loads(re.search(r'<script type="application/json" id="page-tour">(.*?)</script>', page, re.S).group(1))
check("French: the tour and its words in French", data["title"] == "Paiements" and data["labels"]["start"] == "Montrez-moi chaque partie"
      and data["steps"][0]["name"] == "Les chiffres", (data["title"], data["labels"]["start"], data["steps"][0]["name"]))
admin.cookies.set("lang", "ar")
page = admin.get("/classes/").text
data = json.loads(re.search(r'<script type="application/json" id="page-tour">(.*?)</script>', page, re.S).group(1))
check("Arabic: the tour in Arabic", data["labels"]["can"] == "يمكنك" and any("مكوّن" in step["what"] for step in data["steps"]), data["labels"]["can"])
admin.cookies.set("lang", "en")
check("the first visit offers the tour, once", "tour-seen:" in page and "hint_title" in page)

# ---- The assistant knows every page; candidates are never answered from them ----
knowledge_service.sync_knowledge(db)
kept = db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key.like("system:page:%")).count()
check("knowledge: every page is known", kept == len(page_tours.every()), (kept, len(page_tours.every())))
hits = knowledge_service.search(db, "what can I do on the payments page, record a payment, payment reminder", limit=5)
check("knowledge: “what can I do on the payments page” finds that page", any(hit["title"] == "Page: Payments" for hit in hits), [hit["title"] for hit in hits])
hits = knowledge_service.search(db, "what can I do on the payments page, record a payment, payment reminder", limit=5, skip_kinds=("page",))
check("knowledge: the candidates' answers never come from the pages", not any(hit["kind"] == "page" for hit in hits))
reply = chatbot.answer(db, "where do I change a class's hours and its professor?", [], user=auth.current_from_user(db.query(User).first())
                       if hasattr(auth, "current_from_user") else None)
check("assistant: answers how the application works from the pages", bool(reply.get("reply")), reply.get("reply", "")[:120])

# ---- Yasmine explains a page from its tour ----
user = db.query(User).filter(User.email == "admin@test.local").one()
said = friend.talk(db, auth.CurrentUser(id=user.id, name=user.name, email=user.email, role=user.role), "/payments", "en")
check("Yasmine: says what the payments page is for, from its tour", bool(said.get("tip")) and "pay" in said["tip"]["text"].lower(), said.get("tip"))

# ---- What the assistant knows, and the whole centre at a glance ----
page = admin.get("/programs/expertise")
check("“What the assistant knows”: every program, week by week", page.status_code == 200 and page.text.count('class="ex-card"') == db.query(Program).count()
      and "The course plan" in page.text and "Edit the weekly plan" in page.text, page.status_code)
check("Programs: its door to it", 'href="/programs/expertise"' in admin.get("/programs/all").text)
reply = chatbot.answer(db, "tell me everything about 9antra", [])
check("assistant: the whole centre at a glance", reply.get("reply", "").startswith("9antra at a glance:") and "Candidates:" in reply["reply"]
      and "Money:" in reply["reply"] and "Classes:" in reply["reply"], reply.get("reply", "")[:200])
reply = chatbot.answer(db, "où en est le centre ?", [])
check("assistant: the same in French words", reply.get("reply", "").startswith("9antra at a glance:"), reply.get("reply", "")[:80])

db.close()
check.done()

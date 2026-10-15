"""What the market asks of each program (counted from job offers), and the assistant that answers from the
application's own data. Staging copy, COPY of the database; the job board is a stand-in, nothing leaves this machine."""
import testenv
testenv.prepare("market_chat")
check = testenv.Checks()

import time
from datetime import datetime, timedelta
import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from app.database import SessionLocal
from app.models import Candidate, ClassGroup, MarketOffer, Program, Registration, User, UserSession
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, chatbot as cb, email_assistant as ea, email_templates as tp, email_threads as et, enrollment_service as es,
                          knowledge_service as ks, level_check as lc, market as mk)

et.send_email = lambda **kw: True; tp.send_email = lambda **kw: True
ea.model_available = lambda: True
ea.llm_json = lambda *a, **k: None

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="market-chat-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "market-chat-1"})
from urllib.parse import unquote_plus
def where(response): return unquote_plus(response.headers.get("location", ""))
devops = db.query(Program).filter(Program.name == "DevOps").first()
python = db.query(Program).filter(Program.name == "Python").first()

# ======================================================================
# A. Reading offers
# ======================================================================
check("an offer's field is read in its title", mk.fields_of("Senior DevOps Engineer (Remote)") == ["devops"] and "cyber" in mk.fields_of("Security Analyst"))
check("Java is not JavaScript", mk.fields_of("Java Developer") == ["spring"] and "spring" not in mk.fields_of("JavaScript Developer") and "mern" in mk.fields_of("JavaScript Developer"))
check("a title that says nothing is sorted by the skills the offer names", mk.fields_of("Projet de fin d'études", "Application avec React, Node.js, MongoDB et Express") == ["mern"])
check("but only with the skill that defines the field", mk.fields_of("Backend Engineer", "Go, SQL, Docker, REST API, Linux, AWS") == [] and mk.fields_of("Backend Engineer", "Python, Django, SQL, Docker") [:1] == ["python"])
check("a manager of something else is not a marketing or an AI offer", mk.fields_of("General Manager - Lifestyle Brand") == [] and mk.fields_of("Growth Manager (AI x Renewables)") == [] and mk.fields_of("AI Engineer") == ["ai"])
check("an offer of another trade belongs to no field", mk.fields_of("Office Assistant", "Excel, phone, filing") == [])
check("skills are whole words", mk.skills_in("We use Docker, k8s and Terraform on AWS.", "devops") == {"Docker", "Kubernetes", "Terraform", "AWS"}
      and "Java" not in mk.skills_in("JavaScript only", "spring") and "SOC" not in mk.skills_in("social media", "cyber"))
check("what every employer asks is found too", mk.common_in("Fluent English, Scrum team, Git") == {"English", "Agile / Scrum", "Git"})
site = mk.website_offers(db)
check("the offers of 9antra.tn are read from what is already kept", len(site) == 9 and all(offer["source"] == "9antra.tn" for offer in site) and sum(1 for offer in site if offer["fields"]) >= 6, [offer["fields"] for offer in site])

def job(key, title, text, tags=()):
    return {"id": key, "title": title, "description": f"<p>{text}</p>", "tags": list(tags), "company_name": "Acme", "candidate_required_location": "Worldwide",
            "url": f"https://remotive.com/remote-jobs/{key}", "publication_date": "2026-10-10T08:00:00"}
board = [
    job(1, "DevOps Engineer", "Docker and Kubernetes in production, Terraform, AWS, CI/CD with GitHub Actions. English required, Scrum team.", ["devops"]),
    job(2, "Senior DevOps Engineer", "Kubernetes, Helm, Terraform, Azure, Prometheus monitoring, Python scripting. English.", []),
    job(3, "Platform Engineer", "Docker, Kubernetes, ArgoCD, AWS, Linux, Bash.", []),
    job(4, "DevOps / SRE", "Kubernetes, Terraform, AWS, Jenkins, Linux. Agile.", []),
    job(5, "Cloud Engineer", "AWS, Terraform, Docker, Python.", []),
    job(6, "Office Assistant", "Excel, phone and filing.", ["excel"]),
    job(7, "Python Developer", "Django, PostgreSQL, Docker, REST API, pytest. English.", []),
    {"id": None, "title": "broken"},
    dict(job("devops-berlin-1", "DevOps Engineer (m/w/d)", "Docker, Kubernetes, Terraform, Azure. Deutsch und Englisch."), source="arbeitnow"),
]
mk.fetch_public = lambda: board
outcome = mk.read_public(db)
check("the job boards are read and only the offers of fields taught here are kept", outcome["state"] == "read" and outcome["new"] == 7 and db.query(MarketOffer).filter(MarketOffer.source == "remotive").count() == 6 and db.query(MarketOffer).filter(MarketOffer.source == "arbeitnow").count() == 1, outcome)
check("an offer of another trade is not kept", db.query(MarketOffer).filter(MarketOffer.title == "Office Assistant").count() == 0)
check("the layout of an offer is removed", "<p>" not in db.query(MarketOffer).filter(MarketOffer.external_id == "1").first().text)
check("it is not read again within six hours", mk.read_public(db)["state"] == "recent")
mk.fetch_public = lambda: board
quiet = mk.read_public(db, now=datetime.utcnow() + timedelta(hours=7))
check("a reading that brings nothing new still counts as a reading", quiet["state"] == "read" and quiet["new"] == 0 and mk.read_public(db, now=datetime.utcnow() + timedelta(hours=9))["state"] == "recent" and mk.read_if_due(db, now=datetime.utcnow() + timedelta(hours=20)) is None, quiet)
check("after six hours it is, without doubling anything", mk.read_public(db, now=datetime.utcnow() + timedelta(hours=14))["new"] == 0 and db.query(MarketOffer).count() == 7)
def fail(): raise RuntimeError("no network")
mk.fetch_public = fail
check("a job board that cannot be reached changes nothing", mk.read_public(db, now=datetime.utcnow() + timedelta(days=2))["state"] == "unreachable" and db.query(MarketOffer).count() == 7)
check("too little text is not an offer", mk.add_pasted(db, "DevOps") is None)
pasted = mk.add_pasted(db, "Ingénieur DevOps - Tunis\nNous cherchons un profil maîtrisant Docker, Kubernetes, GitLab CI, Ansible et Azure. Anglais courant, travail en équipe Scrum.")
check("an offer pasted from LinkedIn is kept, its first line as title", pasted is not None and pasted.title == "Ingénieur DevOps - Tunis" and pasted.source == "pasted")
check("pasting it twice keeps it once", mk.add_pasted(db, "Ingénieur DevOps - Tunis\nNous cherchons un profil maîtrisant Docker, Kubernetes, GitLab CI, Ansible et Azure. Anglais courant, travail en équipe Scrum.").id == pasted.id
      and db.query(MarketOffer).filter(MarketOffer.source == "pasted").count() == 1)

# ======================================================================
# B. A program against its field
# ======================================================================
result = mk.analysis(db, devops)
by = {item["label"]: item for item in result["skills"]}
total = result["offers"]
check("the offers of the program's field are counted, with where they come from", total >= 7 and result["sources"].get("Public job boards") == 6 and result["sources"].get("Pasted by you") == 1 and result["enough"], (total, result["sources"]))
kube = sum(1 for offer in mk.all_offers(db) if "devops" in offer["fields"] and "Kubernetes" in mk.skills_in(offer["text"], "devops"))
check("a percentage is the share of offers that name the skill", by["Kubernetes"]["count"] == kube and by["Kubernetes"]["pct"] == round(kube / total * 100), by["Kubernetes"])
check("what the program teaches is marked covered, what it does not is not", by["Docker"]["covered"] and by["Kubernetes"]["covered"] and by["Terraform"]["covered"] and not by["AWS"]["covered"], {k: v["covered"] for k, v in by.items()})
weight = sum(item["count"] for item in result["skills"])
check("the fit is the covered share of everything asked", result["fit"] == round(sum(item["count"] for item in result["skills"] if item["covered"]) / weight * 100) and 0 < result["fit"] < 100, result["fit"])
check("what to add is what is asked and not taught, most asked first", result["add"] and result["add"][0]["label"] == "AWS" and all(not item["covered"] and item["pct"] >= mk.WORTH_ADDING for item in result["add"]), [item["label"] for item in result["add"]])
check("the advice says it with its figures", any(line.startswith("Add AWS:") and f"({by['AWS']['count']} of {total})" in line for line in result["advice"]), result["advice"][:2])
check("and what to put forward", any("Put forward" in line and "Kubernetes" in line for line in result["advice"]))
check("what employers ask of everybody is counted", any(item["label"] == "English" and item["pct"] > 0 for item in result["employers"]), result["employers"])
check("a LinkedIn search is offered as a link for the admin, never read", result["search"]["linkedin"].startswith("https://www.linkedin.com/jobs/search/?keywords=DevOps") and "Tunisia" in result["search"]["linkedin"])
few = mk.analysis(db, python)
check("with few offers it says the figures are a hint", few["offers"] < mk.ENOUGH and not few["enough"] and any("take these percentages as a hint" in line for line in few["advice"]), few["advice"])
other = Program(name="Comptabilité générale", is_approved=True); db.add(other); db.commit()
check("a field no offer is sorted by says so", mk.analysis(db, other)["fit"] is None and "not one the offers are sorted by" in mk.analysis(db, other)["advice"][0])

page = admin.get("/programs/market")
check("the market page opens", page.status_code == 200 and "What employers ask, program by program" in page.text, page.status_code)
check("each program shows its fit and what to add", f'id="market-{devops.id}"' in page.text and f">{result['fit']}%</div>" in page.text and "AWS ·" in page.text)
check("it says where the offers come from", "From public job boards" in page.text and "Pasted by you" in page.text and "From 9antra.tn" not in page.text)
detail = admin.get(f"/programs/{devops.id}").text
check("the program's page has the detail", 'id="market"' in detail and "The skills offers ask for" in detail and "Add AWS:" in detail and "See these jobs on LinkedIn" in detail and "of what offers ask is already in the program" in detail)
check("a program with no offer of its field still opens", admin.get(f"/programs/{other.id}").status_code == 200)
# ---- what to teach for each missing skill
from app.services import market_content as mc, system_status as ss
from app.scheduler import jobs
check("every skill counted has its outline of what to teach", all(name in mc.MODULES for skills in mk.SKILLS.values() for name, _ in skills), [name for skills in mk.SKILLS.values() for name, _ in skills if name not in mc.MODULES])
content = result["content"]
check("each missing skill comes with what adding it means", content and content[0]["label"] == "AWS" and content[0]["module"] == mc.MODULES["AWS"] and all(not item["covered"] and item["module"] for item in content) and len(content) <= mk.CONTENT_IDEAS, [item["label"] for item in content])
check("there are more content ideas than the short list of what to add", len(content) >= len(result["add"]) and {item["label"] for item in result["add"]} <= {item["label"] for item in content})
check("the program's page shows them", "Content to add, from what offers ask" in detail and mc.MODULES["AWS"] in detail)
check("a skill without an outline still gets a sentence", "Quantum" in mc.module_for("Quantum"))
# ---- the fields of the trade nobody teaches here
check("a field not taught here is recognised by the title", mk.others_of("Senior Data Scientist") == ["datascience"] and mk.others_of("Senior .NET Developer") == ["dotnet"] and mk.others_of("QA Manager Pharma") == [] and mk.others_of("Office Assistant") == [])
more = [job(100 + i, "Data Scientist", "Python, SQL, machine learning, statistics and pandas. English.") for i in range(5)] + [job(120, "Senior .NET Developer", "C#, ASP.NET, Azure, SQL Server.")]
mk.fetch_public = lambda: board + more
later = datetime.utcnow() + timedelta(days=3)
out = mk.read_public(db, now=later)
check("offers of those fields are kept too", out["state"] == "read" and out["new"] == 6 and db.query(MarketOffer).filter(MarketOffer.title == "Senior .NET Developer").count() == 1, out)
trends = mk.trends(db)
rows = {row["key"]: row for row in trends}
counted = sum(1 for offer in mk.all_offers(db) if offer["fields"] or offer["others"])
check("the trade is ranked by the offers read, the most asked first", [row["offers"] for row in trends] == sorted((row["offers"] for row in trends), reverse=True) and rows["devops"]["offers"] == mk.analysis(db, devops)["offers"])
check("a field you teach says in which program", rows["devops"]["taught"] and "DevOps" in rows["devops"]["programs"] and rows["devops"]["advice"].startswith("You teach it"), rows["devops"])
ds = rows["datascience"]
check("a field nobody teaches here is shown with its share and its skills", not ds["taught"] and ds["offers"] == 5 and ds["pct"] == round(5 / counted * 100) and ds["skills"][0]["pct"] == 100 and {"Python", "SQL", "Machine learning"} <= {item["label"] for item in ds["skills"]}, ds)
check("with where it could start", ds["advice"].startswith("No program here teaches it. Most asked:") and ds["closest"] and "It could start as a module in" in ds["advice"], ds["advice"])
check("with too few offers it does not conclude", rows["dotnet"]["offers"] == 1 and "too few offers" in rows["dotnet"]["advice"])
page = admin.get("/programs/market")
check("the market page shows the trends", page.status_code == 200 and "What the trade asks most" in page.text and "Data science" in page.text and "No program yet" in page.text and "You teach it" in page.text, page.status_code)
said = cb.answer(db, "what is trending in the market?")
check("the assistant says what the trade asks most and what nobody teaches here", said["source"] == "system" and "Most asked in the trade:" in said["reply"] and "No program here yet: Data science (5)" in said["reply"], said["reply"][-260:])
said = cb.answer(db, "what should we add to DevOps?")
check("and what to teach for a missing skill", "Content to add:" in said["reply"] and mc.MODULES["AWS"] in said["reply"], said["reply"][-300:])
# ---- by itself, once a day
check("it is not read again by itself within a day", mk.read_if_due(db, now=later + timedelta(hours=7)) is None)
check("after a day it is", mk.read_if_due(db, now=later + timedelta(hours=25))["state"] == "read")
mk.fetch_public = fail
jobs.market_tick()
check("the daily job is one of the things the System page follows", "market" in jobs.WATCHED and "market" in jobs.RUN_NOW and any(item["key"] == "market" and item["every"] == "once a day" for item in ss.WORK))
page = admin.get("/system")
check("and it shows there", page.status_code == 200 and "Follows the job market" in page.text, page.status_code)
mk.fetch_public = lambda: board + [job(9, "DevOps Engineer II", "Docker, Kubernetes, AWS.")]
answer = admin.post("/programs/market/read")
check("the button reads the job board", "could not be reached" not in where(answer) and ("new offer" in where(answer) or "less than six hours" in where(answer)), where(answer))
answer = admin.post("/programs/market/paste", data={"text": "Community manager\nGestion des réseaux sociaux, Meta Ads, Canva, rédaction de contenu, SEO. Français et anglais."})
check("an offer is pasted from the page and counted for its field", "counted for: Marketing digital" in where(answer), where(answer))
answer = admin.post("/programs/market/paste", data={"text": "trop court"})
check("something that is not an offer is refused", "Paste the text of the offer" in where(answer))
answer = admin.post(f"/programs/market/offers/{pasted.id}/delete")
check("a pasted offer can be removed", "removed" in where(answer) and db.query(MarketOffer).filter(MarketOffer.id == pasted.id).count() == 0)
check("the programs page leads to it", "/programs/market" in admin.get("/programs/all").text)

# ======================================================================
# C. The assistant
# ======================================================================
ks.sync_knowledge(db)
es.sync_class_lifecycle(db); lc.retire_old(db); lc.prepare_missing(db); db.expire_all()
def ask(text):
    started = time.time()
    found = cb.answer(db, text)
    found["seconds"] = time.time() - started
    return found
unpaid = [r for r in db.query(Registration).filter(Registration.payment_status != PaymentStatus.PAID).all() if r.status != RegistrationStatus.CANCELLED]
people = {(r.candidate.full_name or r.candidate.email) for r in unpaid}
found = ask("Who has not paid?")
check("who has not paid: counted from the registrations", found["source"] == "system" and f"{len(people)} candidate" in found["reply"] and found["links"][0]["url"] == "/payments", found["reply"][:120])
check("and at once", found["seconds"] < 3, found["seconds"])
found = ask("Qui n'a pas encore payé ?")
check("in French when asked in French", "n'ont pas tout payé" in found["reply"], found["reply"][:100])
found = ask("Which classes cannot start?")
check("which classes cannot start, and why", "P-20261001-2" in found["reply"] and "no candidate is enrolled" in found["reply"] and found["links"][0]["url"] == "/classes/", found["reply"][:200])
found = ask("who can teach Python?")
check("who can teach a program", "amira" in found["reply"] and "Python" in found["reply"], found["reply"])
found = ask("How much is DevOps?")
check("the price of a program", "390 DT online" in found["reply"] and "490 DT on site" in found["reply"] and found["links"][0]["url"] == f"/programs/{devops.id}", found["reply"][:120])
found = ask("What should we add to DevOps?")
check("what to add to a program, from the offers", "Add AWS" in found["reply"] and f"/programs/{devops.id}#market" in [link["url"] for link in found["links"]], found["reply"][:160])
found = ask("how do I create a class?")
check("how to do something here, with the page", found["source"] == "guide" and "paid candidates are waiting" in found["reply"] and found["links"][0]["url"] == "/classes/")
found = ask("How do I use it on my phone?")
check("how to use it on the phone", "start_for_phone.bat" in found["reply"])
found = ask("level checks: who has not answered?")
check("where the level checks stand", "Level checks:" in found["reply"] and "ready to send" in found["reply"] and found["links"][0]["url"] == "/quiz/all", found["reply"][:120])
person = db.query(Candidate).filter(Candidate.full_name.isnot(None)).join(Registration, Registration.candidate_id == Candidate.id).first()
named = max(db.query(Candidate).all(), key=lambda c: len((c.full_name or "").split()))
found = ask(f"tell me about {named.full_name}")
check("a candidate by name: what they follow, what they paid", (named.full_name in found["reply"]) and found["links"][0]["url"] == f"/candidate/{named.id}", (named.full_name, found["reply"][:140]))
found = ask("Is everything working?")
check("whether everything is working", ("Everything is running" in found["reply"] or "need" in found["reply"]) and found["links"][0]["url"] == "/system", found["reply"][:140])
found = ask("what do candidates say about us?")
check("what candidates say, from the feedback read on the website", "feedback published on 9antra.tn" in found["reply"] and "% are happy" in found["reply"], found["reply"][:140])
found = ask("what is on our facebook page?")
check("Facebook: says honestly what is and is not read", "only reads its picture" in found["reply"] and found["links"][0]["url"] == "https://www.facebook.com/9antra.tn")
found = ask("comment se passe l'inscription et le paiement ?")
check("what it knows of the centre is found in its knowledge", found["source"] in ("knowledge", "website", "taught") and len(found["reply"]) > 60, (found["source"], found["reply"][:120]))
found = ask("j'ai une question sur la météo de demain matin")
check("French small words are not taken for a program", "Artificial Intelligence" not in found["reply"], found["reply"][:120])
found = ask("zzz qwerty plouf")
check("what it cannot answer it says, and offers what it can", found["source"] == "none" and "I do not have the answer" in found["reply"] and found.get("suggestions"), found["reply"][:100])
check("an empty message gets a hint", cb.answer(db, "   ")["source"] == "none")
api = admin.post("/assistant/chat", json={"message": "Who has not paid?", "history": []})
data = api.json()
check("the chat on every page answers with the reply and its links", api.status_code == 200 and "candidate" in data["reply"] and data["links"][0]["url"] == "/payments" and data["source"] == "system", data.get("reply", "")[:100])
check("it offers questions to start from", len(admin.get("/assistant/suggestions").json()["suggestions"]) >= 4)
home = admin.get("/").text
check("the chat window is ready for links and suggestions", "assistant-links" in home and "assistant-suggest" in home and "Who has not paid? Which classes cannot start?" in home)
check("somebody not signed in cannot ask it", TestClient(main.app, follow_redirects=False).post("/assistant/chat", json={"message": "Who has not paid?"}).status_code in (401, 302, 303))
db.close()
check.done()

"""Recommendations renewed every 24 hours from several sources: job offers (and Hacker News “Who is hiring”), the
developers' community (GitHub, dev.to), kept day after day; add / change / strengthen / reduce / keep, each with its
numbers and sources. Runs on a COPY; no source is read for real: they are given by the test."""
from datetime import date, datetime, timedelta

import testenv

testenv.prepare("reco")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Program, User, UserSession
from app.models.market_signal import MarketSignal
from app.models.program import ProgramLevel, WeeklyCurriculum
from app.services import auth, market, recommender

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="reco-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "reco-test-1"})

# Only this program: a DevOps course that teaches Docker (one week), Jenkins, Linux and SonarQube.
for other in db.query(Program).all():
    other.website_slug = None
program = Program(name="DevOps Lab", price_online=390, price_onsite=490, description="A DevOps course.")
db.add(program)
db.flush()
level = ProgramLevel(program_id=program.id, level_order=1, title="Level 1")
db.add(level)
db.flush()
for number, title in enumerate(["Docker basics", "Jenkins pipelines", "Linux and the shell", "SonarQube code quality"], start=1):
    db.add(WeeklyCurriculum(level_id=level.id, week_number=number, topic_title=title, learning_objectives=""))
db.commit()
db.refresh(program)

subjects = {}
for item in db.query(Program).all():
    from app.services import level_bank

    subjects[item.name] = level_bank.subject_of(item.name, item.website_slug)
check("the program's field is DevOps", subjects["DevOps Lab"] == "devops")


def offer(number, words):
    return {"source": "Public job boards", "title": f"DevOps engineer {number}", "text": "devops engineer " + " ".join(words), "company": "Co",
            "location": "Remote", "url": "", "posted": datetime.utcnow(), "fields": ["devops"]}


OFFERS = []
for number in range(40):
    words = ["docker"] if number < 30 else []
    words += ["kubernetes"] if number < 24 else []
    words += ["terraform"] if number < 10 else []
    words += ["github actions"] if number < 14 else []
    words += ["jenkins"] if number < 3 else []
    OFFERS.append(offer(number, words))
market.all_offers = lambda db_=None: list(OFFERS)
recommender.github_count = lambda topic, since: {"kubernetes": 4200, "docker": 9000}.get(topic, 120)
recommender.devto_count = lambda tag: {"kubernetes": 310}.get(tag, 25)
recommender.hn_posts = lambda limit=600: ["We hire a platform engineer: Kubernetes, Terraform, AWS. Remote."] * 7

today = date.today()
done = recommender.refresh(db, today, pause=False)
check("refresh: every skill of the field read today", done["fields"] >= 1 and done["skills"] >= 15 and done["hn_posts"] == 7, done)
kube = db.query(MarketSignal).filter(MarketSignal.day == today, MarketSignal.subject == "devops", MarketSignal.skill == "Kubernetes").one()
check("refresh: offers, Hacker News, GitHub and dev.to kept for the day", kube.offers == 24 and kube.total == 40 and kube.hn == 7 and kube.github == 4200 and kube.devto == 310,
      (kube.offers, kube.total, kube.hn, kube.github, kube.devto))
check("refresh: once a day", recommender.refresh_if_due(db, datetime.combine(today, datetime.min.time()) + timedelta(hours=10)) is None)
# A month ago Kubernetes was asked less: it is rising.
old_day = today - timedelta(days=30)
for row in db.query(MarketSignal).filter(MarketSignal.day == today).all():
    db.add(MarketSignal(day=old_day, subject=row.subject, skill=row.skill, offers=16 if row.skill == "Kubernetes" else row.offers,
                        total=row.total, github=(row.github or 0) - 300 if row.github else None, devto=row.devto, hn=row.hn))
db.commit()

found = recommender.recommend(db, program)
kinds = {item["kind"]: item for item in found["items"]}
by_skill = {item["skill"]: item for item in found["items"]}
check("recommend: compared with a month before", found["compared_with"] == old_day and found["day"] == today)
check("add: Kubernetes, asked by 60% (+20 points), not taught", by_skill.get("Kubernetes", {}).get("kind") == "add" and by_skill["Kubernetes"]["pct"] == 60.0
      and by_skill["Kubernetes"]["delta"] == 20.0, by_skill.get("Kubernetes"))
check("add: Terraform (25%)", by_skill.get("Terraform", {}).get("kind") == "add")
check("change: Jenkins for GitHub Actions (14 offers against 3)", any(item["kind"] == "change" and "Jenkins" in item["title"] and "GitHub Actions" in item["title"]
                                                                     for item in found["items"]), [item["title"] for item in found["items"]])
check("strengthen: Docker, asked by 75% and taught in one week", by_skill.get("Docker", {}).get("kind") == "strengthen", by_skill.get("Docker"))
check("reduce: SonarQube, taught and asked by nobody", by_skill.get("SonarQube", {}).get("kind") == "reduce", by_skill.get("SonarQube"))
evidence = " | ".join(source["label"] for source in by_skill["Kubernetes"]["evidence"])
check("sources: offers, Hacker News, GitHub, dev.to, with their numbers", "Job offers: 24 of 40" in evidence and "Hacker News" in evidence
      and "GitHub: 4 200 new public projects" in evidence and "dev.to: 310 articles" in evidence, evidence)
check("sources: links to check them", any(source.get("url", "").startswith("https://github.com/topics/kubernetes") for source in by_skill["Kubernetes"]["evidence"]))
check("each recommendation says why, with numbers", all(item["why"] and any(char.isdigit() for char in item["why"]) for item in found["items"]))
check("how they are made, said", "Every 24 hours" in found["how"] and "No model writes them" in found["how"])

page = admin.get(f"/programs/{program.id}")
check("program page: the recommendations, with their sources", page.status_code == 200 and "Recommendations for DevOps Lab" in page.text
      and "Renewed every 24 hours" in page.text and "Add Kubernetes" in page.text and "How these recommendations are made" in page.text, page.status_code)
recommender.refresh = lambda db_, today_=None, **kw: {"day": str(today_)}
response = admin.post("/programs/market/recommendations/refresh", data={"back": f"/programs/{program.id}#recommendations"})
check("read again now: in the background", response.status_code == 303 and "notice=" in response.headers["location"])

db.close()
check.done()

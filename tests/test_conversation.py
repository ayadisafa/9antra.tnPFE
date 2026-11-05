"""Yasmine talks like a person (4 November): how are you, a greeting, thanks, who she is, how one feels, a goodbye, in
the language written, with the person's name; she tells small talk from a real question, and answers both when a
message has both. Runs on a COPY; nothing is sent."""
import testenv

testenv.prepare("conversation")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Professor, User, UserSession
from app.services import auth, email_assistant as ea, email_templates as tp, email_threads as et, friend, orchestrator, small_talk, whats_new

et.send_email = lambda **kw: True
tp.send_email = lambda **kw: True
ea.model_available = lambda: False

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Leila Admin", password="conversation-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "conversation-1"})


def ask(text: str) -> dict:
    return admin.post("/assistant/chat", json={"message": text}).json()


# ======================================================================
# A. Small talk told apart from a question
# ======================================================================
read = small_talk.read
check("“ça va ?” is small talk", read("Salut Yasmine, ça va ?")["only"] and {"greeting", "how_are_you"} <= set(read("Salut Yasmine, ça va ?")["kinds"]))
check("“ça va les paiements ?” and “comment vont les paiements ?” are questions",
      not read("ça va les paiements ?")["kinds"] and not read("comment vont les paiements ?")["kinds"])
check("a greeting with a question: both, and only the question goes on", read("Salut ! combien de candidats ont payé ?")["kinds"] == ["greeting"]
      and read("Salut ! combien de candidats ont payé ?")["question"] == "combien de candidats ont payé ?")
check("an order to send is never small talk", read("envoie un email à Fares : bonjour, ça va ?")["kinds"] == [])
check("“merci de m'envoyer…” is a request, not thanks", "thanks" not in read("merci de m'envoyer le programme")["kinds"])
check("“je suis fatiguée aujourd'hui” is only how she feels", read("je suis fatiguée aujourd'hui")["only"])
check("the language of a short message", [small_talk.language_of(text) for text in ("merci beaucoup !", "how are you?", "chnowa a7welek", "كيف حالك؟")]
      == ["fr", "en", "tn", "ar"])

# ======================================================================
# B. She answers like a person, in the language written, with the name
# ======================================================================
said = ask("Salut Yasmine, ça va ?")
check("how are you: she says how she is, asks back, with the name", said["agent"]["key"] == "talk"
      and any(words in said["reply"] for words in ("Je vais", "Ça va", "Très bien")) and "Leila" in said["reply"] and "toi" in said["reply"], said["reply"])
check("…and what waits today, in the same language", "Côté travail" in said["reply"], said["reply"])
check("“vous” gets “vous”", "Et vous" in ask("Bonjour, comment allez-vous ?")["reply"] or "allez-vous" in ask("Bonjour, comment allez-vous ?")["reply"])
check("in English", ask("how are you?")["reply"].split("\n")[0].startswith(("I'm", "All good", "Great")))
check("in Arabic", "بخير" in ask("كيف حالك؟")["reply"] or "الحمد لله" in ask("كيف حالك؟")["reply"])
check("in Tunisian", any(word in ask("chnowa a7welek")["reply"] for word in ("Labes", "behya")))
check("never “I have no feelings”", all("ressens" not in ask(text)["reply"] and "feelings" not in ask(text)["reply"]
                                        for text in ("ça va ?", "how are you?", "كيف حالك؟")))
friend.save_look(db, {**friend.look(db), "name": "Salma"})
db.commit()
said = ask("tu es qui ?")
check("who she is: her own name, what she does", "Salma" in said["reply"] and "9antra" in said["reply"], said["reply"])
friend.save_look(db, {**friend.look(db), "name": "Yasmine"})
db.commit()
check("thanks", any(word in ask("merci beaucoup !")["reply"] for word in ("plaisir", "prie", "là pour")))
check("tired: kind words and what is most pressing", any(word in ask("je suis fatiguée aujourd'hui")["reply"] for word in ("courage", "Respire"))
      and "Le plus pressé" in ask("je suis fatiguée aujourd'hui")["reply"])
check("good night", "Bonne nuit" in ask("bonne nuit")["reply"])
check("a compliment", "Merci" in ask("tu es géniale")["reply"] or "merci" in ask("tu es géniale")["reply"])

# ======================================================================
# C. Small talk and a question: a word back, then the answer
# ======================================================================
said = ask("Salut ! qui a payé ?")
check("greeting + question: hello, then the figures", said["reply"].startswith(("Bonjour", "Bonsoir")) and "ont tout payé" in said["reply"], said["reply"])
check("“qui a payé ?” in French", "ont tout payé" in ask("qui a payé ?")["reply"])
check("“who has paid?” is not “who has not paid?”", "paid in full (" in ask("who has paid?")["reply"]
      and "not paid in full" in ask("who has not paid?")["reply"])
check("Tunisian “chkoun khallas ?”: who paid, not how to record a payment", "ont tout payé" in ask("chkoun khallas ?")["reply"])

# ======================================================================
# D. Professors too, without the centre's work; the agents page shows the step
# ======================================================================
teacher = Professor(name="Mourad Prof", email="mourad.conv@example.com")
db.add(teacher)
db.flush()
auth.create_user(db, email="mourad.conv@example.com", name="Mourad Prof", password="mourad-conv-1", role=auth.PROFESSOR, professor_id=teacher.id)
db.commit()
prof = TestClient(main.app, follow_redirects=False)
prof.post("/login", data={"email": "mourad.conv@example.com", "password": "mourad-conv-1"})
said = prof.post("/account/guide", json={"message": "Bonjour, ça va ?"}).json()
check("a professor gets an answer like a person, never the centre's work", "Mourad" in said["reply"] and "Côté travail" not in said["reply"], said["reply"])
check("the graph has its conversation step", "talk" in orchestrator.STEPS and any(step["key"] == "talk" for line in orchestrator.drawing() for step in line["steps"]))
check("trying “ça va ?” on the agents page lights it", admin.post("/assistant/agents/route", json={"message": "ça va ?"}).json()["step"] == "talk")
check("what's new: told", any(release["key"] == "2026-11-04" for release in whats_new.RELEASES))

db.close()
check.done()

"""Forgot password (a one-hour, one-use link by email, the admin told), the sign-in pages in three languages, notifications
that always lead somewhere and speak the page's language, the settings in folds with “Your centre”, cheques read and
checked against the centre's names, the Payments page's place to read one, and the automatic messages saying what can be
changed and restored. Runs on a COPY; nothing is sent (the mail server is replaced)."""
import io
import json
from datetime import datetime, timedelta

import testenv

testenv.prepare("access_payments")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Candidate, Notification, Program, Registration, User, UserSession
from app.models.payment_proof import PaymentProof
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import auth, dynamic_words, email_service, email_threads, password_reset, proof_reader
from app.services.settings_service import get_settings

sent = []
email_service.send_email = lambda **fields: (sent.append(fields) or True)
email_threads.smtp_configured = lambda: True
_reset_task = password_reset.send_task

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
admin_user = auth.create_user(db, email="admin@test.local", name="Safa Admin", password="first-pass-123", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "first-pass-123"})
guest = TestClient(main.app, follow_redirects=False)

# ---- A. Forgot password ----
page = guest.get("/login")
check("sign in: a link “Forgot your password?”", page.status_code == 200 and 'href="/login/forgot"' in page.text)
page = guest.get("/login/forgot")
check("forgot: the page asks for the address", page.status_code == 200 and 'action="/login/forgot"' in page.text and 'name="email"' in page.text)
sent.clear()
answer = guest.post("/login/forgot", data={"email": "admin@test.local"})
check("forgot: the same answer for everyone, and the email goes", answer.status_code == 200 and "Look in your mailbox" in answer.text
      and any(item["to_email"] == "admin@test.local" and "/login/reset/" in item["text_body"] for item in sent), [item.get("to_email") for item in sent])
link = next(item for item in sent if item["to_email"] == "admin@test.local")["text_body"]
token = link.split("/login/reset/")[1].split()[0]
sent.clear()
answer = guest.post("/login/forgot", data={"email": "nobody@nowhere.tn"})
check("forgot: an unknown address gets the same page, and nothing is sent", answer.status_code == 200 and "Look in your mailbox" in answer.text and not sent)
db.expire_all()
check("the admin is told someone asked for a new password", db.query(Notification).filter(Notification.category == "ACCOUNT",
                                                                                             Notification.title.like("%asked for a new password%")).count() == 1)
page = guest.get(f"/login/reset/{token}")
check("the link opens the page to choose a new password", page.status_code == 200 and "Choose a new password" in page.text and "admin@test.local" in page.text)
bad = guest.post(f"/login/reset/{token}", data={"password": "new-pass-456", "confirmation": "other-pass-456"})
check("two different passwords: refused, the link still works", bad.status_code == 400 and "not the same" in bad.text)
done = guest.post(f"/login/reset/{token}", data={"password": "new-pass-456", "confirmation": "new-pass-456"})
check("the new password is saved", done.status_code == 303 and done.headers["location"] == "/login?changed=1")
check("the link works once", guest.get(f"/login/reset/{token}").status_code == 410)
check("the old password no longer opens", auth.sign_in(db, "admin@test.local", "first-pass-123")[0] is None)
check("the new one does", auth.sign_in(db, "admin@test.local", "new-pass-456")[0] is not None)
db.expire_all()
check("the admin is told the password changed", db.query(Notification).filter(Notification.category == "ACCOUNT", Notification.title.like("%chose a new password%")).count() == 1)
user = db.query(User).filter(User.email == "admin@test.local").first()
user.reset_hash, user.reset_until = auth._fingerprint("x" * 40), datetime.utcnow() - timedelta(minutes=1)
db.commit()
check("a link older than an hour no longer works", guest.get("/login/reset/" + "x" * 40).status_code == 410)
french = guest.get("/login?lang=fr")
check("the sign-in page in French, chosen on it", "Mot de passe oublié ?" in french.text and french.cookies.get("lang") == "fr" or "Mot de passe oublié" in french.text)
arabic = TestClient(main.app, follow_redirects=False).get("/login?lang=ar")
check("and in Arabic, right to left", 'dir="rtl"' in arabic.text and "نسيت كلمة المرور؟" in arabic.text)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "new-pass-456"})
account = admin.get("/account")
check("my account: a link by email when the current password is forgotten", 'href="/login/forgot?email=' in account.text)

# ---- B. Notifications ----
check("a notification in French", dynamic_words.shown(db, "New Registration: Sana Ben Ali", "fr") == "Nouvelle inscription : Sana Ben Ali")
check("and in Arabic, the name kept", dynamic_words.shown(db, "Payment proof from Sana: Everything agrees", "ar").startswith("إثبات دفع من Sana:"))
check("written in French: shown in English to an English page",
      dynamic_words.shown(db, "Paiement confirmé — classe à assigner", "en") == "Payment confirmed, class to assign")
check("a sentence nobody wrote waits for the local model", dynamic_words.shown(db, "Something brand new happened today", "fr") == "Something brand new happened today"
      and ("fr", "Something brand new happened today") in dynamic_words._wanted)
lost = Notification(recipient_role="admin", category="EMAIL_CONFIG", title="Emails are not being sent", message="x", action_url=None)
db.add(lost)
db.commit()
check("a notification without a link still leads to its page", dynamic_words.target_of(lost) == "/settings#email")
admin.cookies.set("lang", "fr")
listed = admin.get("/admin/api/notifications").json()
mine = next(item for item in listed["notifications"] if item["id"] == lost.id)
check("the bell's list in the page's language, with where each leads", mine["title"] == "Les emails ne partent pas" and mine["action_url"] == "/settings#email")
admin.cookies.set("lang", "en")
home = admin.get("/")
check("no notification leads nowhere", 'href="#"' not in home.text.split('id="notif-dropdown-list"')[1].split("</div>\n")[0] if 'id="notif-dropdown-list"' in home.text else True)
check("a click opens the page at once (not after the “read” call)", "navigator.sendBeacon(`/admin/api/notifications/${notificationId}/read`)" in home.text
      and "event.currentTarget.getAttribute(\"href\");\n        if (href" not in home.text)

# ---- C. Settings in folds, and Your centre ----
page = admin.get("/settings")
check("settings: one column of folds, no two columns", page.status_code == 200 and page.text.count("data-fold") >= 14 and 'class="col-xl-4"' not in page.text)
check("“Your centre” is one of them", 'id="centre"' in page.text and 'name="beneficiaries"' in page.text and 'name="legal_name"' in page.text)
answer = admin.post("/settings/numbers", data={"whole": "1", "legal_name": "9antra Training Center SARL", "beneficiaries": "9antra\n9antra Training Center\n9antra",
                                               "ribs": "08 006 0101234567890 64", "tax_id": "1234567/A/M/000", "email": "contact@9antra.tn",
                                               "website": "9antra.tn", "phone": "", "chat": "", "address": "Tunis", "form": ""})
db.expire_all()
settings = get_settings(db)
check("the centre's details are saved (names once each, the site as a link)", answer.status_code == 303 and answer.headers["location"].endswith("#centre")
      and settings.centre_beneficiaries == "9antra\n9antra Training Center" and settings.centre_website == "https://9antra.tn"
      and settings.centre_tax_id == "1234567/A/M/000")
wrong = admin.post("/settings/numbers", data={"whole": "1", "ribs": "08 006 0101234567890 65"})
check("a wrong RIB is refused, back to Your centre", "#centre" in wrong.headers["location"] and "error=" in wrong.headers["location"])
check("the bank of the RIB is shown", "Banque Internationale Arabe de Tunisie" in admin.get("/settings").text)
from app.services import chatbot_sources

about = chatbot_sources.about_text(db)
check("the assistants know who to make a cheque out to, and the RIB", "à l'ordre de : 9antra" in about and "08 006 0101234567890 64" in about)

# ---- D. Cheques ----
TEXT = """BIAT Banque Internationale Arabe de Tunisie #390,000#
Payezcontre cecheque non endossable sauf au profit d'une banque
lasommede trois centquatre-vingt-dix dinars
a l'ordre de gantra Training Center
A Tunis, le 20/10/2026
M.AhmedBenSalah
1234567 08006010123456789064"""
found = proof_reader.read_words(TEXT)
check("a cheque is recognised", found["kind"] == "cheque")
check("who it is made out to", found["beneficiary"] == "gantra Training Center", found["beneficiary"])
check("the amount in figures and in words (glued words cut again)", found["figures"] == 390 and found["amount_words"] == 390, (found["figures"], found["amount_words"]))
check("who pays, read without its spaces", found["drawer"] == "Ahmed Ben Salah", found["drawer"])
check("the cheque's number, the place, the date", found["cheque_number"] == "1234567" and found["place"] == "Tunis" and str(found["date"]) == "2026-10-20")
names = ["9antra", "9antra Training Center", "9antra Training Center SARL"]
results = proof_reader.checks(found, owed=390, centre_ribs=[], centre_names=names, today=datetime(2026, 10, 22).date())
keys = {item["key"]: item["ok"] for item in results}
check("made out to the centre, same amount in words, consistent", keys.get("beneficiary") is True and keys.get("words") is True
      and proof_reader.verdict(results) == "consistent" and "receiver" not in keys, keys)
other = proof_reader.checks(proof_reader.read_words(TEXT.replace("gantra Training Center", "Mohamed Ali")), owed=390, centre_ribs=[], centre_names=names)
check("made out to someone else: doubtful", proof_reader.verdict(other) == "doubtful" and any(item["key"] == "beneficiary" and item["ok"] is False for item in other))
mismatch = proof_reader.checks(proof_reader.read_words(TEXT.replace("trois centquatre-vingt-dix", "trois cents")), owed=390, centre_ribs=[], centre_names=names)
check("words and figures that differ: doubtful", any(item["key"] == "words" and item["ok"] is False for item in mismatch))
old = proof_reader.checks(found, owed=390, centre_ribs=[], centre_names=names, today=datetime(2026, 11, 10).date())
check("a cheque more than 8 days old is said so", any(item["key"] == "date" and "8 days" in item["text"] for item in old))
check("French amounts in words", [proof_reader.words_amount_in(text)[0] for text in
                                  ("quatre-vingt-dix-neuf dinars", "soixante et onze dinars", "mille deux cents dinars", "cinq cents dinars et cinq cents millimes")]
      == [99, 71, 1200, 500.5])
program = db.query(Program).first()
ahmed = Candidate(full_name="Ahmed Ben Salah", email="ahmed.bs@example.com", phone="50 111 222")
db.add(ahmed)
db.flush()
db.add(Registration(candidate_id=ahmed.id, program_id=program.id, program_name=program.name, total_amount=390, total_price=390, paid_amount=0,
                    payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow() - timedelta(days=3)))
db.commit()
proof_reader.read_text = lambda data, content_type="": TEXT        # the picture's words, without the reading engine
answer = admin.post("/payments/proofs/upload", files={"files": ("cheque.jpg", b"\xff\xd8\xff fake picture", "image/jpeg")}, data={"candidate_id": ""})
db.expire_all()
proof = db.query(PaymentProof).order_by(PaymentProof.created_at.desc()).first()
check("Payments: a cheque dropped is read and kept", answer.status_code == 303 and proof is not None and proof.kind == "cheque" and proof.cheque_number == "1234567")
check("the one who paid is found from the name on it", proof.candidate_id == ahmed.id)
page = admin.get("/payments")
check("the cheque is shown like a cheque, with each check", 'class="pf-cq"' in page.text and "Pay to the order of" in page.text and "gantra Training Center" in page.text)
check("and the place to read another", 'action="/payments/proofs/upload"' in page.text and 'id="proof-drop"' in page.text)
admin.cookies.set("lang", "fr")
page = admin.get("/payments")
check("its checks in French", "« gantra Training Center » : le centre." in page.text)
admin.cookies.set("lang", "en")
nobody = PaymentProof(candidate_id=None, file_id=proof.file_id, source="upload", status="waiting", kind="cheque")
db.add(nobody)
db.commit()
linked = admin.post(f"/payments/proofs/{nobody.id}/candidate", data={"candidate_id": ahmed.id, "back": "/payments"})
db.expire_all()
check("who paid can be chosen when it was not found", linked.status_code == 303 and db.get(PaymentProof, nobody.id).candidate_id == ahmed.id)

# ---- E. The automatic messages ----
page = admin.get("/inbox/templates")
check("the messages say what can be changed in each", "In each message, you can" in page.text and "Restore the original text at any time" in page.text)
page = admin.get("/inbox/templates?key=welcome")
check("one message: each thing to change a click away, and restoring is always said", 'data-go="#template-subject"' in page.text and 'id="tp-restore"' in page.text)
admin.post("/inbox/templates/auto/welcome/save", data={"subject": "Bienvenue chez nous", "title": "", "body": "Bonjour {first_name}", "closing": "",
                                                        "style": "brand", "is_active": "true", "channel": "auto"})
page = admin.get("/inbox/templates")
check("a changed message has “Restore the original” on its card", 'action="/inbox/templates/auto/welcome/reset"' in page.text)
back = admin.post("/inbox/templates/auto/welcome/reset", data={"back": "list"})
check("restored from the list, back to the list", back.status_code == 303 and "key=" not in back.headers["location"])

db.close()
check.done()

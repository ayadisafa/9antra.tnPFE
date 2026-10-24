"""Payment proofs: a receipt photo or PDF is read (OCR), checked (RIB key, the centre's account, amount owed, date,
bank, sent twice, photo editor), matched with the registration it pays (or the candidate is asked which program),
and waits for the admin, who confirms (the payment is recorded) or refuses. Receipts here are drawn by the test.
Runs on a COPY; email and WhatsApp are fakes."""
import io
from datetime import date, datetime, timedelta
from urllib.parse import unquote_plus

import testenv

testenv.prepare("proofs")
check = testenv.Checks()

import app.main as main

main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from app.database import SessionLocal
from app.models import Candidate, Program, Registration, User, UserSession
from app.models.candidate_email import CandidateEmail
from app.models.email_thread import EmailThread
from app.models.notification import Notification
from app.models.payment_proof import PaymentProof
from app.models.registration import PaymentStatus, RegistrationStatus
from app.services import (auth, email_assistant as ea, email_threads as et, friend, payment_proofs, proof_reader as pr,
                          settings_guide, tunisian_banks as tb, whatsapp, whatsapp_inbox)
from app.services.settings_service import get_settings

emails = []
et._attempt = lambda recipient, subject, *rest, **kw: (emails.append((recipient, subject, rest[0] if rest else "")) or (None, False))
et.smtp_configured = lambda: True
ea.model_available = lambda: False


def where(response):
    return unquote_plus(response.headers.get("location", ""))


def rib_with_key(first18):
    return first18 + tb.rib_key(first18)


CENTRE = rib_with_key("080060101234567890")          # BIAT
OTHER = rib_with_key("040020202020202020")           # Attijari


def receipt(lines, rotate=1.5):
    try:
        big, small = ImageFont.truetype("arial.ttf", 30), ImageFont.truetype("arial.ttf", 24)
    except OSError:
        big = small = ImageFont.load_default()
    picture = Image.new("RGB", (900, 80 + 62 * len(lines)), "white")
    draw = ImageDraw.Draw(picture)
    for index, text in enumerate(lines):
        draw.text((40, 30 + 62 * index), text, fill="black", font=big if index in (0,) or text.startswith("Montant") else small)
    picture = picture.rotate(rotate, expand=True, fillcolor="white")
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def spaced(rib):
    return f"{rib[:2]} {rib[2:5]} {rib[5:18]} {rib[18:]}"


today = date.today()
GOOD = receipt(["BIAT - Avis de virement", f"Date : {today:%d/%m/%Y}", "Donneur d'ordre : Lina Preuve", "Beneficiaire : 9antra The Bridge",
                f"RIB beneficiaire : {spaced(CENTRE)}", "Montant : 390,000 TND", "Reference : VIR-2026-554433", "Motif : Formation Python"])

db = SessionLocal()
db.query(UserSession).delete()
db.query(User).delete()
db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Safa Admin", password="proofs-test-1", role=auth.ADMIN)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "proofs-test-1"})

# ---- A. The banks and the RIB ----
check("rib: a right key is accepted", tb.rib_ok(CENTRE) and tb.rib_ok(spaced(CENTRE)))
check("rib: a wrong key is refused", not tb.rib_ok(CENTRE[:-2] + ("00" if CENTRE[-2:] != "00" else "01")))
check("rib: its bank from its first two digits", tb.bank_of_rib(CENTRE)["short"] == "BIAT" and tb.bank_of_rib(OTHER)["short"] == "Attijari")
check("bank: read even when OCR writes BlAT", tb.banks_in("BlAT-Avisdevirement")[0]["short"] == "BIAT")
check("bank: the full name", tb.banks_in("Reçu de versement Attijari bank Tunis")[0]["short"] == "Attijari")
check("bank: “Banque de Tunisie et des Emirats” is BTE, not BT", [item["short"] for item in tb.banks_in("Banque de Tunisie et des Emirats")] == ["BTE"])
check("bank: a paying app", tb.apps_in("Paiement Flouci réussi") == ["Flouci"])
check("bank: the list has the banks of Tunisia", len(tb.listing()) >= 20 and {"BIAT", "BNA", "STB", "Zitouna", "La Poste"} <= {item["short"] for item in tb.listing()})

# ---- B. Reading a receipt ----
found = pr.read(GOOD, "image/png")
check("read: the amount (390,000 TND is 390 dinars)", found["amount"] == 390.0, found["amounts"])
check("read: the date", found["date"] == today, found["date"])
check("read: the RIB", CENTRE in found["ribs"], found["ribs"])
check("read: the bank", found["bank"] and found["bank"]["short"] == "BIAT", found["bank"])
check("read: the reference", found["reference"] == "VIR-2026-554433", found["reference"])
results = pr.checks(found, owed=390, centre_ribs=[spaced(CENTRE)])
check("checks: everything agrees", pr.verdict(results) == "consistent", results)
check("amounts: thousands and millimes", pr.amounts_in("Montant: 1 250,500 DT\nTel 98123456") == [1250.5])
wrong = dict(found, ribs=[CENTRE[:-2] + ("11" if CENTRE[-2:] != "11" else "12")])
check("checks: a RIB with a wrong key makes it doubtful", pr.verdict(pr.checks(wrong, owed=390, centre_ribs=[CENTRE])) == "doubtful")
check("checks: money to another account is doubtful", pr.verdict(pr.checks(dict(found, ribs=[OTHER]), owed=390, centre_ribs=[CENTRE])) == "doubtful")
check("checks: a date in the future is doubtful", pr.verdict(pr.checks(dict(found, date=today + timedelta(days=5)), owed=390, centre_ribs=[CENTRE])) == "doubtful")
check("checks: a date months ago is doubtful", pr.verdict(pr.checks(dict(found, date=today - timedelta(days=120)), owed=390, centre_ribs=[CENTRE])) == "doubtful")
check("checks: the same file twice is doubtful", pr.verdict(pr.checks(found, owed=390, centre_ribs=[CENTRE], seen_before=True)) == "doubtful")
check("checks: a photo editor in the file is doubtful", pr.verdict(pr.checks(dict(found, editor="photoshop"), owed=390, centre_ribs=[CENTRE])) == "doubtful")
check("checks: a part payment is to check, not doubtful", pr.verdict(pr.checks(dict(found, amount=200.0), owed=390, centre_ribs=[CENTRE])) == "to_check")
check("checks: an empty picture cannot be read", pr.verdict(pr.checks({"text": ""}, owed=390, centre_ribs=[])) == "unreadable")

# ---- C. The centre's RIB in Settings ----
response = admin.post("/settings/numbers", data={"phone": "", "chat": "", "ribs": "08 006 0101234567890 00"})
check("settings: a wrong RIB is refused", "is not right" in where(response) if not tb.rib_ok("080060101234567890" + "00") else True, where(response))
response = admin.post("/settings/numbers", data={"phone": "", "chat": "", "ribs": CENTRE})
db.expire_all()
check("settings: the centre's RIB is saved, spaced", get_settings(db).centre_ribs == spaced(CENTRE), get_settings(db).centre_ribs)

# ---- D. A candidate emails a receipt for the one program they owe ----
programs = db.query(Program).order_by(Program.name).all()
python, other_program = programs[0], programs[1]


def candidate_owing(name, email, *progs, phone=None):
    person = Candidate(full_name=name, email=email, phone=phone)
    db.add(person)
    db.flush()
    regs = []
    for prog in progs:
        reg = Registration(candidate_id=person.id, program_id=prog.id, program_name=prog.name, total_amount=390, total_price=390, paid_amount=0,
                           payment_status=PaymentStatus.UNPAID, status=RegistrationStatus.PENDING, registration_date=datetime.utcnow() - timedelta(days=1))
        db.add(reg)
        regs.append(reg)
    db.commit()
    return person, regs


lina, (lina_reg,) = candidate_owing("Lina Preuve", "lina.preuve@example.com", python)
result = ea.ingest_inbound(db, sender="lina.preuve@example.com", subject="Paiement", body="Bonjour, voici mon reçu de virement.",
                           message_id="<proof-1@example.com>", attachments=[("recu.png", "image/png", GOOD)])
db.expire_all()
proof = db.query(PaymentProof).filter(PaymentProof.candidate_id == lina.id).one_or_none()
check("email: the receipt becomes a proof", proof is not None and proof.status == "waiting" and proof.source == "email", result)
check("email: read and checked: everything agrees", proof and proof.verdict == "consistent" and proof.amount == 390 and proof.bank == "08 · BIAT", proof and (proof.verdict, proof.amount, proof.bank))
check("email: it knows which registration it pays", proof and proof.registration_id == lina_reg.id)
thread = db.query(EmailThread).filter(EmailThread.candidate_id == lina.id).first()
message = db.query(CandidateEmail).filter(CandidateEmail.message_id == "<proof-1@example.com>").one()
check("email: the admin is asked, with what was read", thread.needs_admin and "Payment proof received (390 DT, 08 · BIAT" in (message.escalation_reason or ""), message.escalation_reason)
check("email: a notification leads to it", db.query(Notification).filter(Notification.category == "PAYMENT_PROOF", Notification.action_url.like(f"%proof={proof.id}%")).count() == 1)
check("Yasmine: proofs to check", any(line["url"] == "/payments#proofs" for line in admin.get("/account/friend").json()["lines"]))
page = admin.get("/payments").text
check("payments page: the proof, its checks and the buttons", 'id="proofs"' in page and f'id="proof-{proof.id}"' in page and "Confirm: record it" in page and "Everything agrees" in page)
served = admin.get(f"/payments/proofs/{proof.id}/file")
check("the file: shown, never run", served.status_code == 200 and served.headers.get("x-content-type-options") == "nosniff" and served.headers["content-type"].startswith("image/png"))
response = admin.post(f"/payments/proofs/{proof.id}/confirm", data={"registration_id": lina_reg.id, "amount": "500", "back": "/payments"})
check("confirm: more than owed is refused", "more than the 390 DT" in where(response), where(response))
response = admin.post(f"/payments/proofs/{proof.id}/confirm", data={"registration_id": lina_reg.id, "amount": "390", "back": "/payments"})
db.expire_all()
reg = db.get(Registration, lina_reg.id)
check("confirm: the payment is recorded, the registration is paid", reg.payment_status == PaymentStatus.PAID and reg.paid_amount == 390 and "390 DT recorded" in where(response),
      (reg.payment_status, reg.paid_amount, where(response)))
check("confirm: the proof says who confirmed", db.get(PaymentProof, proof.id).status == "confirmed" and db.get(PaymentProof, proof.id).decided_by == "Safa Admin")

# ---- E. Two programs owed: the candidate is asked which one ----
sami, (sami_a, sami_b) = candidate_owing("Sami Deux", "sami.deux@example.com", python, other_program)
OTHER_RECEIPT = receipt(["Attijari bank", f"Date : {today:%d/%m/%Y}", f"RIB : {spaced(CENTRE)}", "Montant : 200,000 DT", "Ref : VERS-777888"])
emails.clear()
ea.ingest_inbound(db, sender="sami.deux@example.com", subject="reçu", body="voila le versement",
                  message_id="<proof-2@example.com>", attachments=[("versement.jpg", "image/png", OTHER_RECEIPT)])
db.expire_all()
proof2 = db.query(PaymentProof).filter(PaymentProof.candidate_id == sami.id).one()
check("two programs: it cannot say which one", proof2.registration_id is None)
check("two programs: the candidate is asked, with their programs", any(item[0] == "sami.deux@example.com" and "Pour quelle formation est ce paiement" in item[2]
                                                                     and python.name in item[2] for item in emails), [item[1] for item in emails])
check("two programs: the proof remembers it asked", proof2.asked_program_at is not None)
ea.ingest_inbound(db, sender="sami.deux@example.com", subject="Re: reçu", body=f"C'est pour {other_program.name}", message_id="<proof-3@example.com>")
db.expire_all()
check("two programs: their answer links the proof to the program", db.get(PaymentProof, proof2.id).registration_id == sami_b.id)
check("two programs: their answer needs no reply", db.query(CandidateEmail).filter(CandidateEmail.message_id == "<proof-3@example.com>").one().status == "no_reply")
response = admin.post(f"/payments/proofs/{proof2.id}/refuse", data={"note": "pas lisible", "back": "/payments"})
db.expire_all()
check("refuse: nothing is recorded", db.get(PaymentProof, proof2.id).status == "refused" and db.get(Registration, sami_b.id).paid_amount == 0)

# ---- F. The same receipt again, a picture that is not a payment, one added by hand ----
nadia, (nadia_reg,) = candidate_owing("Nadia Copie", "nadia.copie@example.com", python)
ea.ingest_inbound(db, sender="nadia.copie@example.com", subject="paiement", body="mon paiement",
                  message_id="<proof-4@example.com>", attachments=[("recu.png", "image/png", GOOD)])
db.expire_all()
copy = db.query(PaymentProof).filter(PaymentProof.candidate_id == nadia.id).one()
check("twice: the same receipt from someone else is doubtful", copy.verdict == "doubtful" and "already sent" in (copy.checks or ""), copy.checks)
blank = io.BytesIO()
Image.new("RGB", (400, 300), "white").save(blank, format="PNG")
ea.ingest_inbound(db, sender="nadia.copie@example.com", subject="photo", body="voici ma photo pour le badge",
                  message_id="<proof-5@example.com>", attachments=[("photo.png", "image/png", blank.getvalue())])
db.expire_all()
check("not a payment: no proof kept", db.query(PaymentProof).filter(PaymentProof.candidate_id == nadia.id).count() == 1)
check("not a payment: the admin is asked to look", "not a payment proof" in (db.query(CandidateEmail).filter(CandidateEmail.message_id == "<proof-5@example.com>").one().escalation_reason or ""))
response = admin.post(f"/candidate/{nadia.id}/proof", files={"file": ("scan.png", receipt(["STB", f"Date : {today:%d/%m/%Y}", f"RIB : {spaced(CENTRE)}",
                                                                                              "Montant : 390 DT", "Reference : STB-99-1234"], rotate=0), "image/png")})
db.expire_all()
check("by hand: the admin adds a receipt, it is read", response.status_code == 303 and db.query(PaymentProof).filter(PaymentProof.candidate_id == nadia.id, PaymentProof.source == "upload").count() == 1
      and "Payment proof received (390 DT" in where(response), where(response))
check("by hand: a file that is not a picture or a PDF is refused", "Add a photo" in where(admin.post(f"/candidate/{nadia.id}/proof", files={"file": ("x.txt", b"hello", "text/plain")})))
page = admin.get(f"/candidate/{nadia.id}").text
check("candidate file: the proofs and the reading form", 'id="proofs"' in page and f'action="/candidate/{nadia.id}/proof"' in page and "Confirm: record it" in page)

# ---- G. A photo sent on WhatsApp ----
payload = {"entry": [{"changes": [{"value": {"contacts": [{"wa_id": "21698765432", "profile": {"name": "Omar"}}],
                                             "messages": [{"from": "21698765432", "id": "wamid.PHOTO1", "timestamp": str(int(datetime.utcnow().timestamp())),
                                                           "type": "image", "image": {"id": "MEDIA1", "mime_type": "image/png", "caption": "mon reçu"}}]}}]}]}
items = whatsapp_inbox.from_webhook(payload)
check("whatsapp: a photo arrives with its caption and its file", items and items[0]["text"] == "mon reçu" and items[0]["media"]["id"] == "MEDIA1")
omar, (omar_reg,) = candidate_owing("Omar Photo", "omar.photo@example.com", python, phone="98 765 432")
whatsapp.download_media = lambda media_id: (receipt(["BNA", f"Date : {today:%d/%m/%Y}", f"RIB : {spaced(CENTRE)}", "Montant : 390,000 DT", "Ref : BNA-55-0001"]), "image/png") if media_id == "MEDIA1" else None
message = whatsapp_inbox.receive(db, **items[0])
db.commit()
ea.process_inbound(db, message)
db.commit()
db.expire_all()
wa_proof = db.query(PaymentProof).filter(PaymentProof.candidate_id == omar.id).one_or_none()
check("whatsapp: the photo is read as a proof", wa_proof is not None and wa_proof.source == "whatsapp" and wa_proof.amount == 390 and wa_proof.bank == "03 · BNA",
      wa_proof and (wa_proof.source, wa_proof.amount, wa_proof.bank))

words = " ".join(line["text"] for group in settings_guide.plain_words(db) for line in group["lines"])
check("plain words: proofs are read and checked against the RIB", "checked against the centre's RIB" in words)
db.close()
check.done()

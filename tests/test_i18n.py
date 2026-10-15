"""The language of the pages and the day / night looks. Staging copy, COPY of the database; the model is a stand-in."""
import testenv
testenv.prepare("i18n")
check = testenv.Checks()

import app.main as main
main.start_firebase_listener = lambda: None
main.start = lambda: None
from fastapi.testclient import TestClient
from urllib.parse import unquote_plus
from app.database import SessionLocal
from app.models import UiTranslation, User, UserSession
from app.scheduler import jobs
from app.services import auth, email_assistant as ea, system_status as ss, translations as tr
from app.services.settings_service import get_settings

asked = []
def fake_chat(system, prompt, as_json=False, max_tokens=60):
    asked.append((system, prompt))
    if "Arabic" in system:
        return {"Remove this class": "احذف هذا القسم", "Change the picture": "Change picture"}.get(prompt, "ترجمة " + "".join(ch for ch in prompt if ch.isdigit() or ch in "{}_abcdefghijklmnopqrstuvwxyz" and False))
    return {"Remove this class": "Supprimer cette classe\nExplication inutile", "Change the picture": '"Changer l\'image"'}.get(prompt, "Texte traduit")
ea._chat = fake_chat
ea.model_available = lambda: True

db = SessionLocal()
db.query(UserSession).delete(); db.query(User).delete(); db.commit()
auth._admin_exists = False
auth.create_user(db, email="admin@test.local", name="Test Admin", password="i18n-test-1", role=auth.ADMIN)
auth.create_user(db, email="prof@test.local", name="Test Prof", password="i18n-test-2", role=auth.PROFESSOR)
admin = TestClient(main.app, follow_redirects=False)
admin.post("/login", data={"email": "admin@test.local", "password": "i18n-test-1"})
def where(response): return unquote_plus(response.headers.get("location", ""))

# ---- which texts
texts = tr.sources()
check("the interface's own texts are listed from the pages", len(texts) > 1500 and len(set(texts)) == len(texts) and all(key in texts for key in ("Dashboard", "What needs you today", "Remove this class", "Change the picture")), len(texts))
check("what people type or receive is not among them", not any("@" in text or "://" in text for text in texts) and not any(text.startswith("Bonjour {first_name}") for text in texts))
check("sentences the services write are there too", any("copied once a day" in text for text in texts) or any("Protects your data" == text for text in texts))
added = tr.ensure(db)
check("each text has its line in French and in Arabic, once", added == 2 * len(texts) and tr.ensure(db) == 0 and db.query(UiTranslation).count() == 2 * len(texts), added)
french = tr.dictionary(db, "fr")
check("the common words are translated by hand from the start", french["t"]["Dashboard"] == "Tableau de bord" and tr.dictionary(db, "ar")["t"]["Candidates"] == "المترشحون" and len(french["t"]) == len(tr.SEED) and "Remove this class" in french["p"])
check("a language nobody chose is not worked on", tr.work(db) == 0 and asked == [])

# ---- choosing the language
home = admin.get("/")
check("every page has the language button", home.status_code == 200 and 'class="dropdown-menu dropdown-menu-end border-0 shadow rounded-4 p-2 lang-menu"' in home.text and 'action="/language"' in home.text and "العربية" in home.text and "Français" in home.text)
check("in English nothing is swapped", '<html lang="en" dir="ltr"' in home.text and "i18n:" not in home.text)
answer = admin.post("/language", data={"lang": "fr", "next": "/system"})
check("choosing French is remembered on this browser and comes back to the page", answer.status_code == 303 and where(answer) == "/system" and "lang=fr" in answer.headers.get("set-cookie", ""))
admin.cookies.set("lang", "fr")
home = admin.get("/")
check("the page then asks for the French texts", '<html lang="fr" dir="ltr"' in home.text and 'var lang = "fr"' in home.text and "/i18n/" in home.text)
check("an address that leaves the application is not followed", where(admin.post("/language", data={"lang": "fr", "next": "//evil.example"})) == "/" and where(admin.post("/language", data={"lang": "zz", "next": "/x"})) == "/x")
data = admin.get("/i18n/fr.json").json()
check("the dictionary is served: translated texts, and those still waiting", data["t"]["Settings"] == "Paramètres" and "Remove this class" in data["p"] and admin.get("/i18n/de.json").json() == {"t": {}, "p": []})
marked = admin.post("/i18n/seen", json={"lang": "fr", "texts": ["Remove this class", "Change the picture", "Safa Ayadi", "a private sentence somebody typed"]}).json()
check("a page says which texts it showed untranslated: only the interface's own count", marked == {"marked": 2}, marked)
check("the version changes when translations arrive", tr.version(db, "fr").startswith("fr-"))
before = tr.version(db, "fr")
kept = tr.work(db)
db.expire_all()
rows = {row.source: row for row in db.query(UiTranslation).filter(UiTranslation.lang == "fr", UiTranslation.origin == "model").all()}
check("the assistant translates what was seen first, a few at a time", kept == tr.BATCH and asked[0][1] in ("Remove this class", "Change the picture") and asked[1][1] in ("Remove this class", "Change the picture") and len(asked) == tr.BATCH, (kept, len(asked)))
check("its answer is tidied: one line, no quotes", rows["Remove this class"].text == "Supprimer cette classe" and rows["Change the picture"].text == "Changer l'image")
check("browsers are told there is something new", tr.version(db, "fr") != before and admin.get("/i18n/fr.json").json()["t"]["Remove this class"] == "Supprimer cette classe")
check("Arabic was not touched: nobody chose it", db.query(UiTranslation).filter(UiTranslation.lang == "ar", UiTranslation.origin == "model").count() == 0)
for source, text, lang, ok in (("Save {first_name}", "Enregistrer {first_name}", "fr", True), ("Save {first_name}", "Enregistrer {prenom}", "fr", False), ("7 days", "7 jours", "fr", True),
                               ("7 days", "sept jours", "fr", False), ("Open", "Open", "ar", False), ("Open", "افتح", "ar", True), ("Open", "افتح", "fr", False),
                               ("Open", "Translation: ouvrir", "fr", False), ("Open", "x" * 80, "fr", False), ("Open", None, "fr", False)):
    check(f"a translation is kept only when it can be one ({source!r} -> {str(text)[:18]!r}, {lang})", (tr.acceptable(source, text, lang) is not None) == ok)
admin.post("/i18n/seen", json={"lang": "ar", "texts": ["Remove this class", "Change the picture"]})
asked.clear(); tr.work(db); db.expire_all()
arabic = {row.source: row for row in db.query(UiTranslation).filter(UiTranslation.lang == "ar", UiTranslation.source.in_(["Remove this class", "Change the picture"])).all()}
check("in Arabic a good answer is kept and one that is not Arabic is refused", arabic["Remove this class"].text == "احذف هذا القسم" and arabic["Change the picture"].text is None and arabic["Change the picture"].tries == 1)
check("each text is asked one at a time, in the right language", all("Arabic" in system or "French" in system for system, _ in asked) and all("\n" not in prompt for _, prompt in asked))
admin.cookies.set("lang", "ar")
home = admin.get("/")
check("Arabic turns the page to the right", '<html lang="ar" dir="rtl"' in home.text and 'var lang = "ar"' in home.text)
admin.cookies.set("lang", "en")

# ---- correcting
page = admin.get("/settings/translations?lang=fr&only=model")
check("the translations can be read", page.status_code == 200 and "Supprimer cette classe" in page.text and "made by the assistant" in page.text and "Translations of the pages" in page.text, page.status_code)
answer = admin.post("/settings/translations/save", data={"lang": "fr", "source": "Remove this class", "text": "Retirer  cette classe"})
db.expire_all()
row = db.query(UiTranslation).filter(UiTranslation.lang == "fr", UiTranslation.source == "Remove this class").first()
check("and corrected: the admin's wording is kept", "Saved" in where(answer) and row.text == "Retirer cette classe" and row.origin == "admin")
tr.ensure(db); tr.work(db); db.expire_all()
check("it is never changed again", db.query(UiTranslation).filter(UiTranslation.lang == "fr", UiTranslation.source == "Remove this class").first().text == "Retirer cette classe")
check("an empty correction is refused", "Write the translation" in where(admin.post("/settings/translations/save", data={"lang": "fr", "source": "Remove this class", "text": " "})))
prof = TestClient(main.app, follow_redirects=False); prof.post("/login", data={"email": "prof@test.local", "password": "i18n-test-2"})
check("a professor can choose their language but not change the translations", prof.post("/language", data={"lang": "fr", "next": "/teacher"}).status_code == 303 and "lang=fr" in prof.post("/language", data={"lang": "fr", "next": "/teacher"}).headers.get("set-cookie", "")
      and prof.get("/i18n/fr.json").status_code == 200 and prof.get("/settings/translations").status_code in (303, 403))
page = admin.get("/settings")
check("Settings shows how far each language is", page.status_code == 200 and 'id="language"' in page.text and "Read and correct the translations" in page.text and f"of {len(texts)} texts" in page.text)
check("the job is followed on the System page", jobs.WATCHED.get("translations") == "translations_tick" and "translations" in jobs.RUN_NOW and any(item["key"] == "translations" for item in ss.WORK))
ea.model_available = lambda: False
asked.clear()
check("without the assistant nothing is asked, and nothing breaks", tr.work(db) == 0 and asked == [])

# ---- day or night
for mode, marker in (("night", 'class="mode-night"'), ("night-blue", 'class="mode-night-blue"'), ("colours", 'class="mode-colours"')):
    answer = admin.post("/settings/appearance", data={"ui_color": "#a42c67", "ui_mode": mode})
    db.expire_all()
    check(f"the look “{mode}” is saved and every page takes it", answer.status_code == 303 and get_settings(db).ui_mode == mode and marker in admin.get("/").text)
admin.post("/settings/appearance", data={"ui_color": "#a42c67", "ui_mode": "soft"})
db.expire_all()
check("back to the soft day look", get_settings(db).ui_mode is None and 'class=""' in admin.get("/").text.split("<head>")[0])
admin.post("/settings/appearance", data={"ui_color": "#a42c67", "ui_mode": "disco"})
db.expire_all()
check("a look that does not exist changes nothing", get_settings(db).ui_mode is None)
page = admin.get("/settings").text
check("Settings offers day, colours, night black and night blue", all(f'name="ui_mode" value="{mode}"' in page for mode in ("soft", "colours", "night", "night-blue")))
check.done()

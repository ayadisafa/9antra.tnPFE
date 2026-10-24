"""What every test does first: work on a throwaway copy, never on the real data.

prepare(tag) must be called before anything is imported from `app`. It

* copies a database into the Windows temp folder (WORK) and points the
  application at that copy: the real `app.db` is only ever read;
* empties every password and key (mail, WhatsApp, Facebook, Gemini) and points
  Firebase at a key file that does not exist, so nothing can leave the PC;
* sends every file the application would write (attachments, course files,
  pictures, copies of the data, the `.env` file) to WORK as well;
* makes any attempt to open a mail connection or reach the web fail loudly.

The suites were written against the database of 13 October 2026 (the baseline).
It is taken from `_backups/pre-level-check-20261013.zip` the first time and
kept in `tests/data/baseline.db`; neither is ever put in git (see .gitignore).
"""
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile

TESTS = os.path.dirname(os.path.abspath(__file__))
# The application folder: the one holding this tests folder (APP_FOLDER can say otherwise).
PROJECT = os.environ.get("APP_FOLDER") or os.path.dirname(TESTS)
# Where the copies and the files written during a test go: outside the project.
WORK = os.path.join(tempfile.gettempdir(), "9antra-tests")
BASELINE = os.path.join(TESTS, "data", "baseline.db")
BASELINE_ZIP = os.path.join(PROJECT, "_backups", "pre-level-check-20261013.zip")
BASELINE_SHA256 = "67de6f5bda6d995975182c96cad40156044b08f0f9f6945a67a01e962fa95c23"

SECRETS = ("SMTP_EMAIL", "SMTP_PASSWORD", "IMAP_EMAIL", "IMAP_PASSWORD", "WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID",
           "WHATSAPP_TEMPLATE", "FACEBOOK_PAGE_ACCESS_TOKEN", "GEMINI_API_KEY",
           "SMS_GATEWAY_URL", "SMS_GATEWAY_USER", "SMS_GATEWAY_PASSWORD", "SMS_SIGNING_KEY")
FOLDERS = ("EMAIL_FILES_DIR", "CLASS_FILES_DIR", "PROGRAM_ICONS_DIR", "BRAND_DIR", "FEEDBACK_DIR", "BACKUP_DIR")


def ensure_baseline() -> str:
    """The 13 October database the suites expect, taken out of its backup the first time."""
    if os.path.exists(BASELINE):
        return BASELINE
    if not os.path.exists(BASELINE_ZIP):
        sys.exit(f"The test database is missing. Put a copy of the 13 October database at {BASELINE}\n"
                 f"(it is the app.db inside {BASELINE_ZIP}).")
    os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
    partial = BASELINE + ".partial"
    with zipfile.ZipFile(BASELINE_ZIP) as archive, archive.open("app.db") as source, open(partial, "wb") as target:
        shutil.copyfileobj(source, target)
    with open(partial, "rb") as written:
        if hashlib.sha256(written.read()).hexdigest() != BASELINE_SHA256:
            os.remove(partial)
            sys.exit(f"The app.db inside {BASELINE_ZIP} is not the expected 13 October database.")
    os.replace(partial, BASELINE)
    return BASELINE


def move_to_postgres(database: str, tag: str) -> str:
    """The same suite on PostgreSQL (TEST_POSTGRES=postgresql://…/nantra_test): the copy is moved into a database of its
    own, made fresh for this suite, and the application is pointed at it."""
    import subprocess
    from urllib.parse import urlsplit

    import psycopg2

    address = os.environ["TEST_POSTGRES"]
    parts = urlsplit(address)
    name = f"nantra_t_{tag}".lower()
    admin = psycopg2.connect(dbname=parts.path.lstrip("/"), user=parts.username, password=parts.password,
                             host=parts.hostname, port=parts.port or 5432)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        cursor.execute(f"CREATE DATABASE \"{name}\" ENCODING 'UTF8' TEMPLATE template0")
    admin.close()
    target = address.rsplit("/", 1)[0] + "/" + name
    moved = subprocess.run([sys.executable, "-m", "app.postgres_move", database, target], cwd=PROJECT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env={**os.environ, "DATABASE_URL": target})
    if moved.returncode != 0:
        sys.exit("Moving the copy to PostgreSQL failed: " + moved.stdout[-3000:] + moved.stderr[-3000:])
    os.environ["DATABASE_URL"] = target
    return target


def prepare(tag: str, *, web: bool = False, source: str | None = None) -> str:
    """Point the application at a fresh copy of `source` (default: the baseline). Returns the copy's path."""
    os.makedirs(WORK, exist_ok=True)
    os.chdir(PROJECT)
    database = os.path.join(WORK, f"run_{tag}.db")
    for name in SECRETS:
        os.environ[name] = ""          # python-dotenv never overwrites a variable that exists, even empty
    os.environ["DATABASE_URL"] = "sqlite:///" + database.replace("\\", "/")
    os.environ["FIREBASE_CREDENTIALS_PATH"] = os.path.join(WORK, "no_such_key.json")
    os.environ["ENV_FILE"] = os.path.join(WORK, f"{tag}_env.txt")
    for name in FOLDERS:
        os.environ[name] = os.path.join(WORK, f"{tag}_{name.lower()}")
        shutil.rmtree(os.environ[name], ignore_errors=True)
    os.environ["BACKGROUND_TASKS_INLINE"] = "1"
    os.environ.setdefault("MEANING_OFF", "1")          # a suite about meaning sets it to "0" before importing the app
    if not web:
        os.environ["ICON_API_URL"] = "http://127.0.0.1:9"    # the icon catalogue (httpx) is never reached: pictures are drawn here
    if PROJECT not in sys.path:
        sys.path.insert(0, PROJECT)
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    if os.path.exists(database):
        os.remove(database)
    original = sqlite3.connect("file:" + (source or ensure_baseline()).replace("\\", "/") + "?mode=ro", uri=True)
    copy = sqlite3.connect(database)
    original.backup(copy)
    copy.close()
    original.close()
    if os.environ.get("TEST_POSTGRES"):
        move_to_postgres(database, tag)

    import imaplib
    import smtplib

    def no_mail(*args, **kwargs):
        raise AssertionError("a test tried to open a real mail connection")

    smtplib.SMTP_SSL = no_mail
    smtplib.SMTP = no_mail
    imaplib.IMAP4_SSL = no_mail
    if not web:
        import requests

        def no_web(*args, **kwargs):
            raise AssertionError("a test tried to reach the web")

        requests.get = no_web
        requests.post = no_web
    return database


class Checks:
    """check(label, condition, detail) prints PASS or FAIL; done() prints the count and ends the run."""

    def __init__(self):
        self.results = []

    def __call__(self, label, condition, detail=""):
        self.results.append(bool(condition))
        print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"   <-- {detail}"), flush=True)

    def done(self):
        failed = self.results.count(False)
        print(f"\n{len(self.results) - failed} of {len(self.results)} checks passed" + (f", {failed} FAILED" if failed else ""), flush=True)
        os._exit(1 if failed else 0)

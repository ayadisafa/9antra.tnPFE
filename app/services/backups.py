"""Copies of the data, made by themselves, so nothing is lost with a disk or a mistake.

Once a day the whole database is copied while the application keeps running
(SQLite's own backup, so the copy is never half-written), checked for damage,
and zipped with the files people uploaded. The copies stay in a folder next to
the application, and in a second folder the admin chooses (another disk, a
synchronised folder): a copy on the same disk does not survive that disk.

Passwords of the mailbox and the Firebase key are not put in the copies.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.database import DATABASE_URL

logger = logging.getLogger(__name__)

EVERY = timedelta(hours=24)
KEEP_DAILY = 14         # one a day for two weeks
KEEP_WEEKLY = 8         # then the Monday ones for two months
_NAME = re.compile(r"^backup-(\d{8})-(\d{6})\.zip$")
# Folders of uploaded files, next to the database.
_FILES = ("data",)


def folder() -> Path:
    path = Path(os.getenv("BACKUP_DIR") or "_backups/auto")
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_file() -> Path | None:
    """The SQLite file the application writes to; None with another kind of database."""
    if not DATABASE_URL.startswith("sqlite:///"):
        return None
    return Path(DATABASE_URL[len("sqlite:///"):])


def second_folder(db: Session) -> Path | None:
    from app.services.settings_service import get_settings

    value = (getattr(get_settings(db), "backup_second_dir", None) or "").strip()
    return Path(value) if value else None


def check_folder(value: str) -> tuple[str | None, str | None]:
    """(the folder as it will be kept, the problem if it cannot be used)."""
    value = (value or "").strip().strip('"')
    if not value:
        return None, None
    path = Path(value)
    if not path.is_absolute():
        return None, "Give the whole address of the folder, for example E:\\9antra-backups."
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return None, "This folder cannot be written to. Check that the disk is connected and the address is right."
    if path.resolve() == folder().resolve():
        return None, "This is already the first folder: choose one on another disk."
    return str(path), None


def _when(name: str) -> datetime | None:
    found = _NAME.match(name)
    if not found:
        return None
    try:
        return datetime.strptime(found.group(1) + found.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def listing(where: Path | None = None) -> list[dict]:
    """The copies kept, the latest first: {"name", "made", "size", "path"}."""
    where = where or folder()
    found = []
    try:
        entries = list(where.iterdir())
    except OSError:
        return []
    for entry in entries:
        made = _when(entry.name)
        if made is not None and entry.is_file():
            found.append({"name": entry.name, "made": made, "size": entry.stat().st_size, "path": entry})
    return sorted(found, key=lambda item: item["made"], reverse=True)


def latest() -> dict | None:
    kept = listing()
    return kept[0] if kept else None


def due(now: datetime | None = None) -> bool:
    last = latest()
    return last is None or (now or datetime.now()) - last["made"] >= EVERY


def _prune(where: Path, now: datetime, keep_name: str | None = None) -> int:
    """Keep one a day for two weeks, then one a week for two months. The copy just made (`keep_name`) always stays."""
    removed, days, weeks = 0, set(), set()
    for item in listing(where):
        age = now - item["made"]
        day, week = item["made"].date(), item["made"].isocalendar()[:2]
        keep = False
        if item["name"] == keep_name:
            keep = True
        elif age <= timedelta(days=KEEP_DAILY) and day not in days:
            keep = True
        elif age <= timedelta(weeks=KEEP_WEEKLY + 2) and week not in weeks and len(weeks) < KEEP_WEEKLY + 2:
            keep = True
        days.add(day)
        weeks.add(week)
        if not keep:
            try:
                item["path"].unlink()
                removed += 1
            except OSError:
                logger.warning("An old copy could not be removed: %s", item["name"])
    return removed


def make(db: Session | None = None, now: datetime | None = None) -> dict:
    """Make a copy now. Returns {"ok", "name", "size", "second", "problem"}."""
    now = now or datetime.now()
    source = database_file()
    if source is None:
        return {"ok": False, "name": None, "size": 0, "second": None,
                "problem": "The data is not in a SQLite file: its copies are made with the database's own tools."}
    if not source.is_file():
        return {"ok": False, "name": None, "size": 0, "second": None, "problem": f"The database file {source} was not found."}
    name = f"backup-{now:%Y%m%d-%H%M%S}.zip"
    target = folder() / name
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "app.db"
        live = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30)
        out = sqlite3.connect(str(copy))
        try:
            live.backup(out)
            verdict = out.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            out.close()
            live.close()
        if verdict != "ok":
            return {"ok": False, "name": None, "size": 0, "second": None,
                    "problem": f"The database did not pass its check ({str(verdict)[:120]}). No copy was kept; the earlier ones are untouched."}
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(copy, "app.db")
            base = source.resolve().parent
            for part in _FILES:
                root = base / part
                if not root.is_dir():
                    continue
                for path in root.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(base).as_posix())
    result = {"ok": True, "name": name, "size": target.stat().st_size, "second": None, "problem": None}
    _prune(folder(), now, keep_name=name)
    other = second_folder(db) if db is not None else None
    if other is not None:
        try:
            other.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, other / name)
            _prune(other, now, keep_name=name)
            result["second"] = str(other)
        except OSError as exc:
            result["problem"] = f"The copy was made, but not in the second folder ({other}): {exc.__class__.__name__}. Is the disk connected?"
    return result


def make_if_due(db: Session, now: datetime | None = None) -> dict | None:
    return make(db, now) if due(now) else None


def find(name: str) -> Path | None:
    """A copy by its name, for downloading. Only names this module wrote are found."""
    if _when(name) is None:
        return None
    path = folder() / name
    return path if path.is_file() else None


def view(db: Session) -> dict:
    """What the System page shows: the copies, where they go, and whether the last one is recent."""
    kept = listing()
    other = second_folder(db)
    second_latest = (listing(other) or [None])[0] if other is not None else None
    last = kept[0] if kept else None
    age = datetime.now() - last["made"] if last else None
    source = database_file()
    return {
        "handled": source is not None,
        "database": {"size": source.stat().st_size if source is not None and source.is_file() else 0,
                     "kind": "SQLite" if source is not None else DATABASE_URL.split(":")[0]},
        "copies": kept[:6],
        "count": len(kept),
        "last": last,
        "state": "none" if last is None else "old" if age > EVERY + timedelta(hours=6) else "ok",
        "folder": str(folder().resolve()),
        "second": str(other) if other is not None else None,
        "second_last": second_latest,
        "second_state": None if other is None else "ok" if second_latest and last and second_latest["name"] == last["name"] else "behind",
    }

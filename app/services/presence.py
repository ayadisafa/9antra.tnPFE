"""Who is using the application right now, and signing people out.

Every open page says "I'm here" every 45 seconds while it is visible (POST /account/here): its session's last
moment, the page and the address it comes from are kept. From that the admin sees, live, who is connected: online
(seen in the last ONLINE minutes), idle (the page is open but not looked at), or away (signed in, not seen for a while);
on which page, from which browser and address, since when.

The admin can sign out one browser, one person (all their browsers) or everyone else at once. The session is removed
at once, and a live event tells the page it is gone: it goes to the sign-in page within a second, without waiting for
its next click.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.user import User, UserSession

ONLINE = timedelta(minutes=2)
IDLE = timedelta(minutes=15)
BEAT_EVERY = timedelta(seconds=20)       # writes to the database at most this often per browser


def key_of(token_hash: str) -> str:
    """The short key the admin's buttons use for one browser."""
    return token_hash[:16]


def tag_of(token_hash: str) -> str:
    """What a page is told to listen for when its session ends (not the session's own key)."""
    return hashlib.sha256(f"signed-out:{token_hash}".encode()).hexdigest()[:20]


def beat(db: Session, token_hash: str, path: str | None, ip: str | None) -> bool:
    """A page is open and looked at. False when its session no longer exists (it was signed out)."""
    session = db.get(UserSession, token_hash)
    now = datetime.utcnow()
    if session is None or session.expires_at < now:
        return False
    if session.last_seen_at is None or now - session.last_seen_at >= BEAT_EVERY or (path and path != session.last_path):
        session.last_seen_at = now
        session.last_path = (path or "")[:200] or session.last_path
        session.ip = (ip or "")[:64] or session.ip
        db.commit()
    return True


def state_of(last_seen: datetime | None, now: datetime) -> str:
    if last_seen is None:
        return "away"
    gone = now - last_seen
    return "online" if gone <= ONLINE else "idle" if gone <= IDLE else "away"


_PAGES = {"/": "Dashboard", "/candidate": "Candidates", "/classes": "Classes", "/programs": "Programs", "/inbox": "Inbox",
          "/payments": "Payments", "/reminders": "Reminders", "/settings": "Settings", "/social": "Social media", "/teacher": "Their space",
          "/assistant": "The assistant", "/quiz": "Level checks", "/website": "Website", "/system": "How it is working",
          "/workspace": "All the data", "/account": "Their account", "/whats-new": "What's new", "/guide": "How it works"}


def page_name(path: str | None) -> str:
    if not path:
        return ""
    if path == "/":
        return _PAGES["/"]
    best = max((prefix for prefix in _PAGES if prefix != "/" and path.startswith(prefix)), key=len, default=None)
    return _PAGES.get(best, path) if best else path


def connected(db: Session, my_token_hash: str | None = None, now: datetime | None = None) -> list[dict]:
    """Every browser signed in, the most recently seen first: who, how, where, since when."""
    from app.services.schedule_utils import utc_to_local

    now = now or datetime.utcnow()
    people = {user.id: user for user in db.query(User).all()}
    rows = db.query(UserSession).filter(UserSession.expires_at >= now).all()
    found = []
    for row in rows:
        user = people.get(row.user_id)
        if user is None:
            continue
        seen = row.last_seen_at or row.created_at
        found.append({
            "key": key_of(row.token_hash), "user_id": user.id, "name": user.name or user.email, "email": user.email, "role": user.role,
            "device": row.device or "A browser", "ip": row.ip or "", "since": row.created_at, "last_seen": seen,
            "since_local": utc_to_local(row.created_at), "seen_local": utc_to_local(seen),
            "state": state_of(seen, now), "page": page_name(row.last_path), "path": row.last_path or "",
            "is_me": row.token_hash == my_token_hash,
        })
    order = {"online": 0, "idle": 1, "away": 2}
    return sorted(found, key=lambda item: (order[item["state"]], -(item["last_seen"] or now).timestamp()))


def summary(rows: list[dict]) -> dict:
    """{"online": people online now (one line each), "count": browsers online, "people": distinct people online}."""
    online = [row for row in rows if row["state"] == "online"]
    people = {}
    for row in online:
        people.setdefault(row["user_id"], row)
    return {"online": list(people.values()), "count": len(online), "people": len(people)}


def _tell(tags: list[str]) -> None:
    """The pages of these sessions go to the sign-in page at once."""
    if not tags:
        return
    from app.services.realtime import broadcaster

    broadcaster.publish("signed_out", {"tags": tags})


def disconnect(db: Session, *, key: str | None = None, user_id: str | None = None, everyone: bool = False,
               keep: str | None = None) -> int:
    """Sign out one browser (`key`), one person (`user_id`, all their browsers) or `everyone` else; never the browser
    `keep` (the admin's own). Returns how many browsers were signed out."""
    query = db.query(UserSession)
    if key:
        query = query.filter(UserSession.token_hash.like(key.replace("%", "").replace("_", "") + "%"))
    elif user_id:
        query = query.filter(UserSession.user_id == user_id)
    elif not everyone:
        return 0
    rows = [row for row in query.all() if row.token_hash != keep]
    for row in rows:
        db.delete(row)
    db.commit()
    _tell([tag_of(row.token_hash) for row in rows])
    return len(rows)

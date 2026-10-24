"""What the assistant learns from a page or a text the admin gives it: 9antra on Google, LinkedIn or Facebook, a
partner's site, an article, the centre's own documents.

Only on the admin's request, one page at a time. LinkedIn, Facebook and Google do not let applications read their pages
(and forbid it), so for them the admin pastes the text: nothing is fetched there. Other pages are fetched once, only
from public addresses (never this computer or the local network), and their text is kept as knowledge with its source.
The assistant then answers from it (candidates and the admin), saying where it comes from.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from sqlalchemy.orm import Session

MAX_BYTES = 2_000_000
CHUNK = 900
MAX_CHUNKS = 40
_CLOSED = re.compile(r"(^|\.)(linkedin\.com|facebook\.com|fb\.com|instagram\.com|google\.[a-z.]+|goo\.gl|g\.page)$", re.I)
_WALL = re.compile(r"\b(sign in|log in|se connecter|connectez-vous|join now|create an account|enable javascript|activez javascript)\b", re.I)


def _where(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def _public(url: str) -> str:
    """The address, when it is a public web page; ValueError otherwise."""
    parts = urlparse((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Give a web address that starts with https://")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))}
    except OSError:
        raise ValueError("This address could not be found. Check it, or paste the page's text instead.")
    for address in addresses:
        found = ipaddress.ip_address(address.split("%")[0])
        if found.is_private or found.is_loopback or found.is_link_local or found.is_reserved or found.is_multicast or found.is_unspecified:
            raise ValueError("Only public web pages can be read, not this computer or the local network.")
    return parts.geturl()


def page_text(raw: str) -> tuple[str, str]:
    """(title, readable text) of an HTML page."""
    title = html.unescape(re.sub(r"\s+", " ", (re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I) or [None, ""])[1])).strip()
    body = re.sub(r"<(script|style|noscript|svg|template|head)\b.*?</\1>", " ", raw, flags=re.S | re.I)
    body = re.sub(r"<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article|header|footer)>", "\n", body, flags=re.I)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    lines = [" ".join(line.split()) for line in body.splitlines()]
    return title, "\n".join(line for line in lines if len(line) > 1)


def fetch(url: str) -> tuple[str, str]:
    """Read a public page once: (title, text). ValueError with a message for the admin when it cannot be read."""
    import requests

    if _CLOSED.search(_where(url)):
        raise ValueError("LinkedIn, Facebook and Google do not let applications read their pages. Open the page, copy its text and paste it below.")
    address = _public(url)
    for _ in range(4):                          # redirects are followed by hand: each one must stay public
        try:
            response = requests.get(address, timeout=15, stream=True, allow_redirects=False,
                                    headers={"User-Agent": "9antra-training-centre (reads one page for its assistant)"})
        except requests.RequestException:
            raise ValueError("The page could not be reached. Check the address and the internet connection, or paste its text.")
        if response.is_redirect and response.headers.get("location"):
            address = _public(urljoin(address, response.headers["location"]))
            if _CLOSED.search(_where(address)):
                raise ValueError("This page leads to LinkedIn, Facebook or Google, which do not let applications read them. Paste its text below.")
            continue
        break
    else:
        raise ValueError("The page sends to too many other addresses. Paste its text instead.")
    if response.status_code >= 400:
        raise ValueError(f"The site answered with an error ({response.status_code}). Paste the page's text instead.")
    kind = (response.headers.get("content-type") or "").lower()
    if "html" not in kind and "text" not in kind:
        raise ValueError("This address is not a web page (a PDF or a picture?). Paste its text instead.")
    data = b""
    for piece in response.iter_content(65536):
        data += piece
        if len(data) > MAX_BYTES:
            break
    raw = data.decode(response.encoding or "utf-8", "ignore")
    title, text = page_text(raw) if "html" in kind else ("", raw)
    if len(text) < 300 or (_WALL.search(text[:3000]) and len(text) < 2500):
        raise ValueError("This page does not show its text to applications (it asks to sign in, or it is drawn by a script). Open it, copy its text and paste it below.")
    return title or _where(address), text


def _chunks(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\n", text) if part.strip()]
    found, current = [], ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 1 > CHUNK and current:
            found.append(current)
            current = ""
        current = (current + "\n" + paragraph).strip() if current else paragraph[:CHUNK * 2]
    if current:
        found.append(current)
    return [chunk for chunk in found if len(chunk) >= 40][:MAX_CHUNKS]


def _key(source: str) -> str:
    return "import:" + hashlib.sha1(source.strip().lower().encode("utf-8")).hexdigest()[:12]


def learn(db: Session, *, title: str, text: str, source: str) -> int:
    """Keep a page's or a text's content as knowledge (replacing what the same source gave before). Returns how many parts."""
    from app.models.knowledge import KnowledgeEntry
    from app.services import knowledge_service

    text = (text or "").strip()
    if len(text) < 40:
        raise ValueError("There is not enough text to learn from.")
    parts = _chunks(text)
    if not parts:
        raise ValueError("There is not enough text to learn from.")
    prefix = _key(source)
    forget(db, prefix, commit=False)
    where = _where(source) if source.startswith("http") else source
    label = f"{(title or where).strip()[:150]} — {where}" if where and where not in (title or "") else (title or where).strip()[:180]
    rows = []
    for index, part in enumerate(parts):
        row = KnowledgeEntry(origin="system", kind="imported", source_key=f"{prefix}:{index}", program_id=None, title=label[:240],
                             content=part, content_hash=hashlib.sha1(part.encode("utf-8")).hexdigest(), is_active=True)
        db.add(row)
        rows.append(row)
    db.flush()
    try:
        vectors = knowledge_service._embed([f"{row.title}\n{row.content}" for row in rows])
        for row, vector in zip(rows, vectors or []):
            row.embedding = json.dumps(vector)
    except Exception:
        pass                                    # found by its words meanwhile; embedded at the next rebuild
    db.commit()
    return len(rows)


def forget(db: Session, prefix: str, commit: bool = True) -> int:
    from app.models.knowledge import KnowledgeEntry

    if not prefix.startswith("import:"):
        return 0
    rows = db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key.like(prefix + ":%")).all()
    for row in rows:
        db.delete(row)
    db.flush()                                  # gone before the new parts take the same keys
    if commit:
        db.commit()
    return len(rows)


def sources(db: Session) -> list[dict]:
    """What was learnt this way: one line per page or text."""
    from app.models.knowledge import KnowledgeEntry

    found: dict[str, dict] = {}
    for row in (db.query(KnowledgeEntry).filter(KnowledgeEntry.source_key.like("import:%")).order_by(KnowledgeEntry.created_at.desc()).all()):
        prefix = row.source_key.rsplit(":", 1)[0]
        item = found.setdefault(prefix, {"key": prefix, "title": row.title, "parts": 0, "when": row.created_at})
        item["parts"] += 1
    return list(found.values())

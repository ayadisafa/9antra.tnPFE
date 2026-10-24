"""The 9antra page on LinkedIn: its posts, read with LinkedIn's own interface (never by reading its web pages, which
LinkedIn forbids and hides behind a sign-in), with the website every six hours, analysed and learnt like the Facebook
posts (website_sync, social_analysis).

LinkedIn gives a page's posts only to an application it has approved (its “Community Management API”), with a token made
by an admin of the page: LINKEDIN_ACCESS_TOKEN and LINKEDIN_ORGANIZATION_ID (the page's number), kept in the .env file
like the other accounts. Without them nothing is read; a post can still be pasted in Inbox › Knowledge.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

ENV_NAMES = ("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_ORGANIZATION_ID")
VERSION = "202406"          # the version of LinkedIn's interface the requests are written for
API = "https://api.linkedin.com/rest/posts"


def configured() -> bool:
    token = (os.getenv("LINKEDIN_ACCESS_TOKEN") or "").strip()
    organization = (os.getenv("LINKEDIN_ORGANIZATION_ID") or "").strip()
    return len(token) >= 40 and organization.isdigit()


def save_account(values: dict[str, str]) -> None:
    """Keep what LinkedIn gave in the .env file (never in the database) and use it at once. Empty values keep the old ones."""
    from app.services import email_account

    kept = {name: (values.get(name) or "").strip() for name in ENV_NAMES if (values.get(name) or "").strip()}
    if "LINKEDIN_ORGANIZATION_ID" in kept:
        digits = re.sub(r"\D", "", kept["LINKEDIN_ORGANIZATION_ID"])
        if not digits:
            raise ValueError("The page's number is the digits at the end of urn:li:organization:12345678.")
        kept["LINKEDIN_ORGANIZATION_ID"] = digits
    if kept:
        email_account._write(kept)
        os.environ.update(kept)


def fetch_posts(limit: int = 30) -> list[dict]:
    """The page's latest posts, as LinkedIn gives them; [] when it is not connected or does not answer."""
    import requests

    if not configured():
        return []
    organization = os.getenv("LINKEDIN_ORGANIZATION_ID").strip()
    try:
        response = requests.get(API, timeout=30, params={"q": "author", "author": f"urn:li:organization:{organization}", "count": limit,
                                                         "sortBy": "LAST_MODIFIED"},
                                headers={"Authorization": f"Bearer {os.getenv('LINKEDIN_ACCESS_TOKEN').strip()}", "LinkedIn-Version": VERSION,
                                         "X-Restli-Protocol-Version": "2.0.0"})
    except requests.RequestException as error:
        logger.warning("LinkedIn could not be reached: %s", error)
        return []
    if response.status_code != 200:
        logger.warning("LinkedIn answered %s: %s", response.status_code, response.text[:300])
        return []
    elements = (response.json() or {}).get("elements") or []
    return [{**row, "_linkedin": True} for row in elements if isinstance(row, dict)]


def normalise(raw: dict) -> dict | None:
    """A LinkedIn post in the shape the website's things are kept in (website_sync)."""
    from datetime import datetime, timezone

    text = " ".join(str(raw.get("commentary") or "").split())
    urn = str(raw.get("id") or "").strip()
    if not urn or not text:
        return None
    moment = raw.get("publishedAt") or raw.get("createdAt")
    when = datetime.fromtimestamp(int(moment) / 1000, tz=timezone.utc).replace(tzinfo=None) if str(moment or "").isdigit() else None
    article = ((raw.get("content") or {}).get("article") or {}) if isinstance(raw.get("content"), dict) else {}
    return {
        "external_id": "li:" + urn[-70:], "slug": None, "category": "linkedin", "starts_at": when, "volatile": {},
        "title": text[:140], "summary": text[:4000], "url": f"https://www.linkedin.com/feed/update/{urn}",
        "data": {"message": text[:6000], "link": str(article.get("source") or "")[:500], "link_title": str(article.get("title") or "")[:200]},
    }

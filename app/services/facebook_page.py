"""The 9antra page on Facebook: the page's own access token and number, given in Settings › Facebook.

With them, the page's posts, videos and reviews are read with the website every six hours (website_sync._facebook),
analysed and learnt. They are kept in the .env file (never in the database), like the other accounts, and used at once.
Without them only the page's public picture is taken (the application's icon). Facebook's pages themselves are never
read: only Meta's own interface (the Graph API), with the token an admin of the page made.
"""
from __future__ import annotations

import os
import re

ENV_NAMES = ("FACEBOOK_PAGE_ACCESS_TOKEN", "FACEBOOK_PAGE_ID")
GRAPH = "https://graph.facebook.com/v20.0"


def configured() -> bool:
    from app.services import website_sync

    return website_sync.facebook_connected()


def page_id() -> str:
    return (os.getenv("FACEBOOK_PAGE_ID") or "").strip()


def has_token() -> bool:
    return len((os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN") or "").strip()) >= 60


def save_account(values: dict[str, str]) -> None:
    """Keep the token and the page's number in the .env file and use them at once. Empty values keep the old ones."""
    from app.services import email_account

    kept = {name: (values.get(name) or "").strip() for name in ENV_NAMES if (values.get(name) or "").strip()}
    if "FACEBOOK_PAGE_ID" in kept:
        digits = re.sub(r"\D", "", kept["FACEBOOK_PAGE_ID"])
        if not digits:
            raise ValueError("The page's number is only digits (Facebook page → About → Page transparency → Page ID).")
        kept["FACEBOOK_PAGE_ID"] = digits
    if "FACEBOOK_PAGE_ACCESS_TOKEN" in kept and len(kept["FACEBOOK_PAGE_ACCESS_TOKEN"]) < 60:
        raise ValueError("This is not a page access token: it is a long text (more than 60 characters) that starts with EAA.")
    if kept:
        email_account._write(kept)
        os.environ.update(kept)


def test_connection() -> tuple[bool, str]:
    """Ask Facebook for the page's name with the token: it answers only when both are right."""
    import requests

    if not configured():
        return False, "Write the page access token and the page's number first."
    try:
        response = requests.get(f"{GRAPH}/{page_id()}", params={"fields": "name,fan_count", "access_token": os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN").strip()},
                                timeout=20)
    except requests.RequestException as exc:
        return False, f"Facebook did not answer ({type(exc).__name__})."
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200:
        reason = (data.get("error") or {}).get("message") or response.text[:160]
        return False, f"Facebook refused: {reason}"
    return True, f"Connected to the page “{data.get('name') or page_id()}”: its posts, videos and reviews are read with the website."

"""The cloud model (Gemini) that reads questions for Yasmine and the email assistant: one client, several keys.

GEMINI_API_KEY in .env holds one key or several separated by commas. Google counts the free quota per key and per
model: when one is used up (HTTP 429) that key rests for that model only (until Google's daily reset for a daily
quota, else the delay Google gives) and the next key takes over, then the next model; a key refused (403) rests a
day for every model. Nothing here decides anything: it sends what it is given and returns what the model said, or
None when no key or model answered (the callers then do as before).

CLOUD_MODEL=off in .env switches it off for everyone.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

READING = ["gemini-3.5-flash-lite", "gemini-3.5-flash"]      # picking the lookups: the lighter model first
WRITING = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]      # writing the answer
MODELS = WRITING
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT = 40
_resting: dict[tuple[str, str], float] = {}   # (key, model or "*") -> until when it rests (time.time())
_used: dict[str, int] = {}                    # "YYYY-MM-DD" -> calls that got an answer
_lock = threading.Lock()


def keys() -> list[str]:
    return [part.strip().strip('"').strip("'") for part in (os.getenv("GEMINI_API_KEY") or "").split(",") if part.strip()]


def available() -> bool:
    return (os.getenv("CLOUD_MODEL") or "on").lower() != "off" and bool(keys())


def used_today() -> int:
    return _used.get(date.today().isoformat(), 0)


def _next_reset() -> float:
    """Google's daily quotas start again at midnight, Pacific time (08:00 UTC in winter): a little after it."""
    now = datetime.now(timezone.utc)
    reset = now.replace(hour=8, minute=5, second=0, microsecond=0)
    return (reset if reset > now else reset + timedelta(days=1)).timestamp()


def _rest(key: str, model: str, response) -> None:
    text = response.text or ""
    if response.status_code == 403:
        until, model = time.time() + 24 * 3600, "*"
    elif "PerDay" in text:
        until = _next_reset()
    else:
        waited = re.search(r'"retryDelay":\s*"(\d+)', text)
        until = time.time() + (int(waited.group(1)) + 2 if waited else 60)
    with _lock:
        _resting[(key, model)] = until


def _ready_keys(model: str) -> list[str]:
    now = time.time()
    return [key for key in keys() if _resting.get((key, model), 0) <= now and _resting.get((key, "*"), 0) <= now]


def resting() -> dict:
    """How many keys may still be used now, per model (for the System page)."""
    return {model: len(_ready_keys(model)) for model in WRITING}


def generate(contents: list[dict], *, system: str | None = None, tools: list[dict] | None = None,
             schema: dict | None = None, temperature: float = 0.2, models: list[str] | None = None) -> dict | None:
    """One answer of the model: its content ({"role": "model", "parts": [...]}) or None.

    `contents` are Gemini's turns; `tools` function declarations; `schema` asks for JSON of that shape; `models` in
    the order to try (WRITING by default)."""
    if not available():
        return None
    import requests

    body: dict = {"contents": contents, "generationConfig": {"temperature": temperature}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        body["tools"] = [{"functionDeclarations": tools}]
    if schema:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = schema
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    for model in models or WRITING:
        for key in _ready_keys(model):
            try:
                response = requests.post(URL.format(model=model), data=payload, timeout=TIMEOUT,
                                         headers={"Content-Type": "application/json", "x-goog-api-key": key})
            except Exception as error:                      # no connection: no point trying the other keys
                logger.warning("Cloud model unreachable: %s", type(error).__name__)
                return None
            if response.status_code == 200:
                try:
                    content = response.json()["candidates"][0]["content"]
                except (KeyError, IndexError, ValueError):
                    logger.warning("Cloud model gave no content (%s)", model)
                    break                                   # blocked or empty: the next model
                with _lock:
                    _used[date.today().isoformat()] = used_today() + 1
                return content
            if response.status_code in (429, 403):
                _rest(key, model, response)
                continue                                    # this key is spent for this model: the next key
            if response.status_code in (404, 500, 503):
                break                                       # this model is missing or busy: the next model
            logger.warning("Cloud model refused the request (%s): %s", response.status_code, response.text[:200])
            return None
    return None


def text_of(content: dict | None) -> str:
    return "".join(part.get("text", "") for part in (content or {}).get("parts", []) if "text" in part).strip()


def calls_of(content: dict | None) -> list[dict]:
    """The functions the model asks to call: [{"name", "args"}]."""
    return [part["functionCall"] for part in (content or {}).get("parts", []) if "functionCall" in part]


def json_of(content: dict | None) -> dict | None:
    text = text_of(content)
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        found = json.loads(text)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None

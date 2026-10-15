"""Google Forms, with the Google account of the Firebase project.

The key file the application already uses for Firebase (firebase_key.json)
belongs to a Google project. When that project's "Google Forms API" and
"Google Drive API" are switched on (two clicks in the Google console, see
Settings), the application can create a form for a class week, open it to
anyone who has the link, and read the answers.

Nothing here runs while a page is being shown: the state is checked in the
background and pages read what was found.
"""
from __future__ import annotations

import json
import logging
import os
import time
from urllib.parse import quote

logger = logging.getLogger(__name__)

KEY_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH", "app/firebase_key.json")
SCOPES = [
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
    "https://www.googleapis.com/auth/drive",
]
FORMS = "https://forms.googleapis.com/v1/forms"
DRIVE = "https://www.googleapis.com/drive/v3"
_TIMEOUT = 30

_session = None
_state: dict = {"checked_at": 0.0, "forms": "unknown", "drive": "unknown", "problem": None}


class FormsError(Exception):
    """Google refused, in words a person can act on."""


def project_id() -> str | None:
    try:
        with open(KEY_PATH, encoding="utf-8") as handle:
            return json.load(handle).get("project_id")
    except (OSError, ValueError):
        return None


def session():
    """A connection signed with the project's key. Raises FormsError without a key."""
    global _session
    if _session is None:
        try:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
            _session = AuthorizedSession(credentials)
        except Exception as exc:
            raise FormsError(f"The Google key file could not be used ({type(exc).__name__}).") from exc
    return _session


def _reason(response) -> str:
    try:
        return response.json().get("error", {}).get("message") or response.text[:200]
    except ValueError:
        return response.text[:200]


def _is_switched_off(response) -> bool:
    text = response.text
    return response.status_code == 403 and ("SERVICE_DISABLED" in text or "has not been used" in text or "is disabled" in text)


def refresh_status() -> dict:
    """Ask Google whether both services are switched on. Reads only; changes nothing."""
    found = {"checked_at": time.time(), "forms": "unknown", "drive": "unknown", "problem": None}
    if project_id() is None:
        found["problem"] = "The Firebase key file (app/firebase_key.json) is missing."
        _state.update(found)
        return status()
    try:
        connection = session()
        # A form that does not exist: "not found" means the service is on.
        probe = connection.get(f"{FORMS}/0000000000000000000000000000000000000000000", timeout=_TIMEOUT)
        found["forms"] = "off" if _is_switched_off(probe) else "on" if probe.status_code in (200, 404) else "unknown"
        probe = connection.get(f"{DRIVE}/about", params={"fields": "user(emailAddress)"}, timeout=_TIMEOUT)
        found["drive"] = "off" if _is_switched_off(probe) else "on" if probe.status_code == 200 else "unknown"
    except FormsError as exc:
        found["problem"] = str(exc)
    except Exception as exc:        # no network, Google unreachable
        found["problem"] = f"Google could not be reached ({type(exc).__name__})."
    _state.update(found)
    return status()


def status() -> dict:
    """What was found the last time Google was asked (see refresh_status)."""
    project = project_id()
    console = "https://console.developers.google.com/apis/api/{api}/overview?project=" + (project or "")
    return {
        "key": project is not None, "project": project,
        "forms": _state["forms"], "drive": _state["drive"], "problem": _state["problem"],
        "ready": project is not None and _state["forms"] == "on" and _state["drive"] == "on",
        "checked": bool(_state["checked_at"]),
        "forms_link": console.format(api="forms.googleapis.com"),
        "drive_link": console.format(api="drive.googleapis.com"),
    }


def ready() -> bool:
    return status()["ready"]


# ------------------------------------------------------------------
# Creating a form and reading its answers
# ------------------------------------------------------------------

def _question(item: dict) -> dict:
    kind = item.get("kind", "choice")
    if kind == "choice":
        body = {"choiceQuestion": {"type": "RADIO", "options": [{"value": option} for option in item["options"]]}}
    else:
        body = {"textQuestion": {"paragraph": kind == "paragraph"}}
    return {
        "title": " ".join(item["title"].split())[:900],
        "questionItem": {"question": {"required": bool(item.get("required")), **body}},
    }


def create(title: str, description: str, items: list[dict]) -> dict:
    """Create a form anyone with the link can answer.

    `items` are {"key", "title", "kind": "choice" | "text" | "paragraph",
    "options", "required"}. Returns {"form_id", "url", "ids": {key: question id}}.
    """
    connection = session()
    made = connection.post(FORMS, json={"info": {"title": title[:200], "documentTitle": title[:200]}}, timeout=_TIMEOUT)
    if made.status_code != 200:
        raise FormsError("Google did not create the form: " + _reason(made))
    form = made.json()
    form_id = form["formId"]

    requests_ = [{"updateFormInfo": {"info": {"description": description[:3000]}, "updateMask": "description"}}]
    requests_ += [{"createItem": {"item": _question(item), "location": {"index": index}}} for index, item in enumerate(items)]
    filled = connection.post(f"{FORMS}/{form_id}:batchUpdate", json={"requests": requests_}, timeout=_TIMEOUT)
    if filled.status_code != 200:
        raise FormsError("Google did not accept the questions: " + _reason(filled))
    replies = filled.json().get("replies", [])[1:]
    ids = {}
    for item, reply in zip(items, replies):
        question_ids = (reply.get("createItem") or {}).get("questionId") or []
        if not question_ids:
            raise FormsError("Google did not say which question is which.")
        ids[item["key"]] = question_ids[0]

    # Forms made by a program start closed: open this one, to anyone who has the link.
    published = connection.post(
        f"{FORMS}/{form_id}:setPublishSettings", timeout=_TIMEOUT,
        json={"publishSettings": {"publishState": {"isPublished": True, "isAcceptingResponses": True}}},
    )
    if published.status_code not in (200, 400):     # 400: an older form that is already open
        raise FormsError("Google did not open the form to answers: " + _reason(published))
    shared = connection.post(
        f"{DRIVE}/files/{form_id}/permissions", timeout=_TIMEOUT,
        json={"type": "anyone", "role": "reader", "view": "published"},
    )
    if shared.status_code != 200:
        raise FormsError("Google did not open the form to people who have the link: " + _reason(shared))
    return {"form_id": form_id, "url": form.get("responderUri") or f"https://docs.google.com/forms/d/{form_id}/viewform", "ids": ids}


def answers(form_id: str, since: str | None = None) -> list[dict]:
    """The answers of a form, oldest first: {"id", "at", "values": {question id: text}}."""
    connection = session()
    found, token = [], None
    while True:
        params = {"pageSize": 200}
        if since:
            params["filter"] = f"timestamp > {since}"
        if token:
            params["pageToken"] = token
        page = connection.get(f"{FORMS}/{form_id}/responses", params=params, timeout=_TIMEOUT)
        if page.status_code != 200:
            raise FormsError("Google did not give the answers: " + _reason(page))
        body = page.json()
        for response in body.get("responses", []):
            values = {}
            for question_id, answer in (response.get("answers") or {}).items():
                texts = [entry.get("value", "") for entry in (answer.get("textAnswers") or {}).get("answers", [])]
                values[question_id] = texts[0] if texts else ""
            found.append({
                "id": response.get("responseId"), "email": response.get("respondentEmail"),
                "at": response.get("lastSubmittedTime") or response.get("createTime"), "values": values,
            })
        token = body.get("nextPageToken")
        if not token:
            break
    return sorted(found, key=lambda item: item["at"] or "")


def prefilled(url: str, question_id: str | None, value: str) -> str:
    """The form's address with one answer already written (the candidate's email).
    Google names a question in such a link by the decimal of its identifier."""
    try:
        entry = int(str(question_id), 16)
    except (TypeError, ValueError):
        return url
    return f"{url}{'&' if '?' in url else '?'}usp=pp_url&entry.{entry}={quote(value)}"


def delete(form_id: str) -> None:
    try:
        session().delete(f"{DRIVE}/files/{form_id}", timeout=_TIMEOUT)
    except Exception:
        logger.warning("The form %s could not be removed.", form_id)


def trial() -> list[dict]:
    """Try the whole road once with a small form, then remove it.
    Returns the steps: {"label", "ok", "detail"}."""
    steps = []
    def step(label: str, ok: bool, detail: str = "") -> bool:
        steps.append({"label": label, "ok": ok, "detail": detail})
        return ok

    found = refresh_status()
    if not step("The Firebase key file is there", found["key"], found["problem"] or ""):
        return steps
    if not step("Google Forms is switched on for the project", found["forms"] == "on",
                "Open the link below and press “Enable”, then wait a minute." if found["forms"] == "off" else found["problem"] or "Google did not say."):
        return steps
    if not step("Google Drive is switched on for the project", found["drive"] == "on",
                "Open the link below and press “Enable”, then wait a minute." if found["drive"] == "off" else found["problem"] or "Google did not say."):
        return steps
    try:
        form = create(
            "9antra : test de connexion (à ignorer)", "Formulaire créé pour vérifier la connexion. Il est supprimé aussitôt.",
            [{"key": "email", "title": "Votre adresse e-mail", "kind": "text", "required": True},
             {"key": "q0", "title": "Tout fonctionne ?", "kind": "choice", "options": ["Oui", "Non"], "required": True}],
        )
    except FormsError as exc:
        step("A form can be created and opened to candidates", False, str(exc))
        return steps
    step("A form can be created and opened to candidates", True, form["url"])
    try:
        step("Its answers can be read", answers(form["form_id"]) == [], "")
    except FormsError as exc:
        step("Its answers can be read", False, str(exc))
    delete(form["form_id"])
    return steps

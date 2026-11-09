"""Yasmine's answers drawn as moving cards, easy to read: figures as tiles that count up, comparisons as bars that grow, a
share as a ring that fills, people as chips, dates on a line, articles and web pages as links.

The kind of card comes from the kind of data (an amount, a share, a list of people, dates), read from what the lookups
returned (lookups.py), never from a model. And only what the answer says is drawn, nothing less and nothing more: a
figure becomes a tile only when the answer gives it, a person or a class is drawn only when the answer names them.
"""
from __future__ import annotations

import re
import unicodedata

MOST_CARDS = 3
MOST_ITEMS = 8

# key of a lookup's figure -> ({lang: label}, icon, tone)
FIGURES = {
    "candidates": ({"fr": "Candidats", "en": "Candidates", "ar": "المترشحون", "tn": "Candidats"}, "bi-people-fill", "blue"),
    "new_candidates_this_week": ({"fr": "Nouveaux cette semaine", "en": "New this week", "ar": "جدد هذا الأسبوع", "tn": "Jdod el jem3a hedhi"}, "bi-person-plus-fill", "berry"),
    "registrations": ({"fr": "Inscriptions", "en": "Registrations", "ar": "التسجيلات", "tn": "Inscriptions"}, "bi-journal-check", "violet"),
    "paid_in_full": ({"fr": "Payées en entier", "en": "Paid in full", "ar": "مدفوعة بالكامل", "tn": "Mkhalsin el kol"}, "bi-check-circle-fill", "green"),
    "money_received_in_all": ({"fr": "Reçu en tout", "en": "Received in all", "ar": "المقبوض جملة", "tn": "Dkhal el kol"}, "bi-cash-coin", "green"),
    "money_still_due": ({"fr": "Encore dû", "en": "Still due", "ar": "المتبقي", "tn": "Mazel"}, "bi-hourglass-split", "amber"),
    "candidates_who_owe": ({"fr": "Candidats qui doivent", "en": "Candidates who owe", "ar": "مترشحون عليهم مستحقات", "tn": "Candidats 3lihom flous"}, "bi-exclamation-circle-fill", "amber"),
    "classes_running": ({"fr": "Classes en cours", "en": "Classes running", "ar": "أقسام جارية", "tn": "Classes ye5dmou"}, "bi-easel2-fill", "blue"),
    "classes_about_to_start": ({"fr": "Classes à venir", "en": "Classes to come", "ar": "أقسام قادمة", "tn": "Classes jeyin"}, "bi-calendar-plus", "violet"),
    "sessions_a_week": ({"fr": "Séances par semaine", "en": "Sessions a week", "ar": "حصص في الأسبوع", "tn": "Séances fil jem3a"}, "bi-calendar-week", "blue"),
    "professors": ({"fr": "Formateurs", "en": "Professors", "ar": "الأساتذة", "tn": "Profs"}, "bi-person-video3", "violet"),
    "professors_available": ({"fr": "Formateurs disponibles", "en": "Professors available", "ar": "أساتذة متاحون", "tn": "Profs disponibles"}, "bi-person-check-fill", "green"),
    "programs": ({"fr": "Programmes", "en": "Programs", "ar": "البرامج", "tn": "Programmes"}, "bi-journal-bookmark-fill", "berry"),
    "emails_waiting_for_your_answer": ({"fr": "Emails qui vous attendent", "en": "Emails waiting for you", "ar": "رسائل تنتظرك", "tn": "Emails yestannew fik"}, "bi-envelope-exclamation-fill", "amber"),
    "received": ({"fr": "Reçu", "en": "Received", "ar": "المقبوض", "tn": "Dkhal"}, "bi-cash-coin", "green"),
    "payments": ({"fr": "Paiements", "en": "Payments", "ar": "الدفعات", "tn": "Paiements"}, "bi-receipt", "blue"),
    "still_to_come_in_all": ({"fr": "Encore à venir", "en": "Still to come", "ar": "ما زال منتظرا", "tn": "Mazel ijiw"}, "bi-hourglass-split", "amber"),
    "people": ({"fr": "Personnes", "en": "People", "ar": "أشخاص", "tn": "Nes"}, "bi-people", "amber"),
    "owed_in_all": ({"fr": "Dû en tout", "en": "Owed in all", "ar": "المستحق جملة", "tn": "3lihom el kol"}, "bi-wallet2", "amber"),
    "free_count": ({"fr": "Libres ce jour-là", "en": "Free that day", "ar": "متفرغون ذلك اليوم", "tn": "Fer8in nhar"}, "bi-calendar-check", "green"),
    "waiting_for_your_answer": ({"fr": "En attente de réponse", "en": "Waiting for your answer", "ar": "تنتظر ردك", "tn": "Yestannew jweb"}, "bi-envelope-exclamation-fill", "amber"),
    "complaints_or_unhappy": ({"fr": "Mécontents", "en": "Unhappy", "ar": "غير راضين", "tn": "Mech far7anin"}, "bi-emoji-frown-fill", "red"),
    "sent_by_the_assistants_today": ({"fr": "Envoyés seuls aujourd'hui", "en": "Sent alone today", "ar": "أرسلت وحدها اليوم", "tn": "Mchew wa7adhom lyoum"}, "bi-send-check-fill", "green"),
    "enrolled": ({"fr": "Inscrits", "en": "Enrolled", "ar": "المسجلون", "tn": "Msajlin"}, "bi-people-fill", "blue"),
    "seats_left": ({"fr": "Places libres", "en": "Seats left", "ar": "مقاعد شاغرة", "tn": "Blays fer8a"}, "bi-grid-3x3-gap", "green"),
    "stars": ({"fr": "Étoiles", "en": "Stars", "ar": "النجوم", "tn": "Njoum"}, "bi-star-fill", "amber"),
}
# The words that say what a figure counts: a small figure ("4") becomes a tile only when the answer also says what it
# counts, so "4 candidates" does not draw the 4 classes too (read on folded words: no accents, Arabic without hamza).
CONCEPTS = {
    "candidates": r"candidat|candidate|etudiant|student|مترشح", "new_candidates_this_week": r"nouveau|nouvel|new|jdod|جدد|جديد",
    "registrations": r"inscri|registr|تسجيل", "paid_in_full": r"pay|paid|khall|regl|دفع|خلص",
    "money_received_in_all": r"recu|received|encaiss|collect|dkhal|مقبوض|dt|dinar", "received": r"recu|received|encaiss|collect|dkhal|مقبوض|dt|dinar",
    "money_still_due": r"du |due|reste|owe|doi|mazel|متبق|مستحق|dt|dinar", "still_to_come_in_all": r"venir|come|reste|mazel|متبق|dt|dinar",
    "owed_in_all": r"du |due|owe|doi|reste|3lih|مستحق|dt|dinar", "candidates_who_owe": r"doi|owe|3lih|عليهم|candidat|personne|people",
    "people": r"personne|people|candidat|nes|اشخاص|مترشح", "classes_running": r"cours|running|jari|جاري|classe|class",
    "classes_about_to_start": r"venir|start|commenc|come|قادم|classe|class", "sessions_a_week": r"seance|session|حص",
    "professors": r"formateur|prof|teacher|trainer|استاذ|اساتذة", "professors_available": r"formateur|prof|teacher|trainer|استاذ|اساتذة",
    "programs": r"programme|program|formation|برنامج|برامج", "emails_waiting_for_your_answer": r"email|mail|message|رسال",
    "waiting_for_your_answer": r"email|mail|message|attend|wait|رسال", "payments": r"paiement|payment|versement|دفع",
    "free_count": r"libre|free|fer8|disponible|available|متفرغ", "complaints_or_unhappy": r"mecontent|unhappy|plain|complain|راض",
    "sent_by_the_assistants_today": r"envoy|sent|mchew|ارسل", "enrolled": r"inscrit|enrolled|msajl|مسجل|candidat",
    "seats_left": r"place|seat|blays|مقعد", "stars": r"etoile|star|njoum|نجم|★",
}
SHARES = {
    "share_received": {"fr": "Part reçue", "en": "Share received", "ar": "النسبة المقبوضة", "tn": "9addech dkhal"},
    "present_share": {"fr": "Présence", "en": "Presence", "ar": "الحضور", "tn": "Présence"},
    "happy_share": {"fr": "Contents", "en": "Happy", "ar": "راضون", "tn": "Far7anin"},
}
TITLES = {
    "owes": {"fr": "Ce qui reste à payer", "en": "Still to pay", "ar": "ما بقي للدفع", "tn": "Chnowa b9a ytkhallas"},
    "paid": {"fr": "Derniers paiements", "en": "Latest payments", "ar": "آخر الدفعات", "tn": "Akher el paiements"},
    "registered": {"fr": "Inscriptions", "en": "Registrations", "ar": "التسجيلات", "tn": "Inscriptions"},
    "classes": {"fr": "Classes", "en": "Classes", "ar": "الأقسام", "tn": "Classes"},
    "sessions": {"fr": "Séances", "en": "Sessions", "ar": "الحصص", "tn": "Séances"},
    "professors": {"fr": "Formateurs", "en": "Professors", "ar": "الأساتذة", "tn": "Profs"},
    "risk": {"fr": "Risque d'abandon", "en": "Risk of dropping out", "ar": "خطر الانقطاع", "tn": "Risque ybattlou"},
    "best": {"fr": "Présence des meilleurs", "en": "Presence of the best", "ar": "حضور الأفضل", "tn": "Présence mta3 el a7sen"},
    "messages": {"fr": "Messages", "en": "Messages", "ar": "الرسائل", "tn": "Messages"},
    "programs": {"fr": "Inscriptions par programme", "en": "Registrations per program", "ar": "التسجيلات حسب البرنامج", "tn": "Inscriptions 3la kol programme"},
    "market": {"fr": "Offres d'emploi par domaine", "en": "Job offers per field", "ar": "عروض الشغل حسب المجال", "tn": "Offres 3la kol domaine"},
    "news": {"fr": "À lire", "en": "To read", "ar": "للقراءة", "tn": "Bech te9ra"},
    "web": {"fr": "Sur le web", "en": "On the web", "ar": "على الإنترنت", "tn": "3al web"},
    "found": {"fr": "Trouvé", "en": "Found", "ar": "وُجد", "tn": "L9it"},
    "online": {"fr": "En ligne", "en": "Online", "ar": "متصلون", "tn": "Connectés"},
    "candidates": {"fr": "Candidats", "en": "Candidates", "ar": "المترشحون", "tn": "Candidats"},
}
_STATES = {"running": "green", "not started": "violet", "completed": "grey"}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    kept = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " " + " ".join(re.sub(r"[^\w]+", " ", kept.lower()).split()) + " "


def _numbers(text: str) -> list[str]:
    from app.services.cloud_answer import _numbers

    return _numbers(str(text))


def _said(reply: str, value) -> bool:
    """The answer gives this figure (its digits, whatever its spelling: "490 DT", "490 dinars")."""
    wanted = _numbers(value)
    if not wanted:
        return False
    given = _numbers(reply)
    return all(number in given for number in wanted[:1])


def _named(reply_folded: str, name) -> bool:
    """The answer names this person, class or program (the whole name, or its first word when it is distinctive)."""
    whole = _fold(str(name or "")).strip()
    if not whole:
        return False
    if f" {whole} " in reply_folded:
        return True
    first = whole.split()[0]
    return len(first) >= 3 and not first.isdigit() and f" {first} " in reply_folded


def _amount(value) -> float | None:
    found = re.search(r"\d+(?:[.,]\d+)?", str(value or ""))
    return float(found.group(0).replace(",", ".")) if found else None


def _initials(name: str) -> str:
    parts = [part for part in re.split(r"\s+", (name or "").strip()) if part]
    return ("".join(part[0] for part in parts[:2]) or "?").upper()


def _lang(lang: str | None) -> str:
    return lang if lang in ("fr", "en", "ar", "tn") else "fr"


def _tiles(facts: dict, reply: str, lang: str) -> list[dict]:
    """Every figure the answer gives, as a tile (its label in the answer's language)."""
    tiles, seen = [], set()
    folded = _fold(reply)
    for result in facts.values():
        if not isinstance(result, dict):
            continue
        for key, value in result.items():
            if key in FIGURES and isinstance(value, (int, float, str)) and not isinstance(value, bool) and _numbers(value):
                if key in seen or not _said(reply, value):
                    continue
                if key in CONCEPTS and not re.search(CONCEPTS[key], folded):
                    continue                    # the figure is said, but not what it counts: it may be another one
                seen.add(key)
                label, icon, tone = FIGURES[key]
                tiles.append({"label": label[lang], "value": str(value), "number": _amount(value), "icon": icon, "tone": tone})
    return tiles[:6]


def _rings(facts: dict, reply: str, lang: str) -> list[dict]:
    rings = []
    for result in facts.values():
        if not isinstance(result, dict):
            continue
        for key, value in list(result.items()) + list((result.get("presence") or {}).items() if isinstance(result.get("presence"), dict) else []):
            if key in SHARES and isinstance(value, str) and value.endswith("%") and _said(reply, value):
                rings.append({"kind": "ring", "label": SHARES[key][lang], "value": _amount(value), "display": value})
    return rings[:2]


def _list(rows, reply: str, name_key: str):
    folded = _fold(reply)
    named = [row for row in rows or [] if isinstance(row, dict) and _named(folded, row.get(name_key))]
    return named


def _people(title: str, rows: list[dict], name_key: str, detail, tone=None) -> dict:
    items = [{"name": str(row.get(name_key)), "initials": _initials(str(row.get(name_key))), "detail": detail(row),
              "tone": tone(row) if tone else None} for row in rows[:MOST_ITEMS]]
    return {"kind": "people", "title": title, "items": items, "more": max(0, len(rows) - MOST_ITEMS)}


def _bars(title: str, rows: list[dict], name_key: str, value_key: str, tone: str = "berry") -> dict | None:
    items = [{"label": str(row.get(name_key)), "value": _amount(row.get(value_key)), "display": str(row.get(value_key))}
             for row in rows if _amount(row.get(value_key)) is not None][:MOST_ITEMS]
    if len(items) < 1:
        return None
    return {"kind": "bars", "title": title, "tone": tone, "items": items, "most": max(item["value"] for item in items) or 1}


def _timeline(title: str, rows: list[dict], when, head, detail) -> dict:
    return {"kind": "timeline", "title": title, "items": [{"when": when(row), "title": head(row), "detail": detail(row)} for row in rows[:MOST_ITEMS]],
            "more": max(0, len(rows) - MOST_ITEMS)}


def _lists(name: str, result: dict, reply: str, lang: str) -> list[dict]:
    """The lists a lookup returned, drawn by their kind, keeping only what the answer names."""
    t = {key: value[lang] for key, value in TITLES.items()}
    found = []
    if name == "not_paid":
        rows = _list(result.get("not_paid_in_full"), reply, "candidate")
        bar = _bars(t["owes"], rows, "candidate", "owes", tone="amber")
        if bar:
            found.append(bar)
    elif name == "money_received":
        rows = _list(result.get("latest"), reply, "candidate")
        if rows:
            found.append(_people(t["paid"], rows, "candidate", lambda row: " · ".join(filter(None, (row.get("amount"), row.get("on")))), lambda row: "green"))
    elif name == "registrations":
        rows = _list(result.get("list"), reply, "candidate")
        if rows:
            found.append(_timeline(t["registered"], rows, lambda row: row.get("on"), lambda row: row.get("candidate"), lambda row: row.get("program")))
    elif name == "classes":
        rows = _list(result.get("classes"), reply, "class")
        if rows:
            found.append({"kind": "classes", "title": t["classes"], "items": [
                {"title": row.get("class"), "detail": " · ".join(filter(None, (row.get("program"), row.get("days_and_hours"), row.get("professor")))),
                 "state": row.get("state"), "tone": _STATES.get(row.get("state"), "grey"), "when": row.get("first_session"),
                 "enrolled": row.get("enrolled"), "capacity": row.get("capacity")} for row in rows[:MOST_ITEMS]]})
    elif name == "class_detail" and isinstance(result.get("candidates"), list):
        people = [person for person in result["candidates"] if _named(_fold(reply), person)]
        if people:
            found.append(_people(t["candidates"], [{"name": person} for person in people], "name", lambda row: ""))
    elif name == "sessions":
        folded = _fold(reply)
        given = _numbers(reply)
        rows = [row for row in result.get("sessions") or [] if _named(folded, row.get("class"))
                or any("".join(_numbers(row.get("date") or "")[:1])[:4] in number for number in given if len(number) >= 3)]
        if rows:
            found.append(_timeline(t["sessions"], rows, lambda row: " ".join(filter(None, (row.get("date"), row.get("from")))),
                                   lambda row: row.get("class"), lambda row: " · ".join(filter(None, (row.get("topic"), row.get("replaced_by") or row.get("professor"))))))
    elif name == "professors":
        rows = _list(result.get("professors"), reply, "professor")
        if rows:
            found.append(_people(t["professors"], rows, "professor", lambda row: row.get("teaches") or "",
                                 lambda row: {True: "green", False: "red"}.get(row.get("free_that_day"))))
    elif name == "may_drop_out":
        rows = _list(result.get("may_drop_out"), reply, "candidate")
        bar = _bars(t["risk"], rows, "candidate", "score", tone="red")
        if bar:
            found.append(bar)
    elif name == "best_candidates":
        rows = _list(result.get("best"), reply, "candidate")
        bar = _bars(t["best"], rows, "candidate", "presence", tone="green")
        if bar:
            found.append(bar)
    elif name == "messages":
        for key, who in (("messages", "from"), ("not_reached", "to"), ("latest_from_candidates", "from")):
            rows = _list(result.get(key), reply, who)
            if rows:
                found.append(_people(t["messages"], rows, who, lambda row: row.get("subject") or row.get("why") or row.get("what") or "",
                                     lambda row: "amber" if row.get("waiting_for_you") else None))
                break
    elif name == "programs":
        rows = _list(result.get("programs_most_chosen_first"), reply, "program")
        bar = _bars(t["programs"], rows, "program", "registrations")
        if bar and len(bar["items"]) >= 2:
            found.append(bar)
    elif name == "job_market":
        rows = _list(result.get("fields"), reply, "field")
        bar = _bars(t["market"], rows, "field", "share_of_offers", tone="teal")
        if bar:
            found.append(bar)
    elif name in ("news", "search"):
        folded = _fold(reply)
        rows = [row for row in result.get("articles") or result.get("found") or []
                if isinstance(row, dict) and (_named(folded, row.get("title")) or len(set(_fold(row.get("title")).split()) & set(folded.split()) - _SMALL) >= 3)]
        if rows:
            found.append({"kind": "links", "title": t["news" if name == "news" else "found"],
                          "items": [{"title": row.get("title"), "detail": row.get("source") or row.get("detail") or "", "url": row.get("url")}
                                    for row in rows[:5] if row.get("url")]})
    elif name == "web_search" and result.get("sources"):
        found.append({"kind": "links", "title": t["web"], "items": [{"title": row.get("title"), "detail": row.get("site") or "", "url": row.get("url")}
                                                                   for row in result["sources"][:5] if row.get("url")]})
    elif name == "connected":
        rows = _list(result.get("online"), reply, "name")
        if rows:
            found.append(_people(t["online"], rows, "name", lambda row: row.get("page") or "", lambda row: "green"))
    return [card for card in found if card.get("items")]


_SMALL = {"the", "and", "les", "des", "pour", "with", "dans", "une", "est", "for", "que", "qui", "sur", "you", "your", "vous"}


def from_facts(facts: dict | None, reply: str | None, lang: str | None = None) -> list[dict]:
    """The cards of an answer written from the lookups' facts (the cloud reader)."""
    if not facts or not reply:
        return []
    lang = _lang(lang)
    cards = []
    tiles = _tiles(facts, reply, lang)
    if len(tiles) >= 2 or (tiles and not any(isinstance(value, dict) and any(isinstance(item, list) and item for item in value.values())
                                              for value in facts.values())):
        cards.append({"kind": "tiles", "items": tiles})
    cards += _rings(facts, reply, lang)
    for label, result in facts.items():
        if isinstance(result, dict) and not result.get("error"):
            cards += _lists(label.split("(", 1)[0], result, reply, lang)
    return cards[:MOST_CARDS]


_ITEM = re.compile(r"^\s*(?:[-•*·▪◦]|\d+[.)])\s+(?P<line>.+)$")
_LABEL = re.compile(r"^(?P<label>[^:—–(]{2,48}?)\s*(?::|—|–|\(| - )\s*(?P<rest>.*)$")
_VALUE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(DT|dt|TND|%|h|/100)")


def from_reply(reply: str | None, lang: str | None = None) -> list[dict]:
    """The cards of an answer written by the application's rules: a list whose lines each give an amount or a share
    ("• Ahmed Naji : 490 DT") is drawn as bars. Anything else stays as it is written."""
    rows = []
    for line in (reply or "").split("\n"):
        item = _ITEM.match(line)
        if not item:
            continue
        parts = _LABEL.match(item.group("line"))
        value = _VALUE.search(parts.group("rest")) if parts else None
        if parts and value:
            rows.append({"label": parts.group("label").strip(" *"), "value": float(value.group(1).replace(",", ".")),
                         "display": value.group(0)})
    if len(rows) < 2 or len({row["display"].split()[-1] if " " in row["display"] else re.sub(r"[\d.,]", "", row["display"]) for row in rows}) > 1:
        return []
    return [{"kind": "bars", "title": "", "tone": "berry", "items": rows[:MOST_ITEMS], "most": max(row["value"] for row in rows) or 1,
             "from_text": True}]


def lists_drawn(cards: list[dict]) -> bool:
    """Whether a card already draws a list of the answer (then the answer shows its first line, the rest one tap away)."""
    return any(card.get("kind") in ("bars", "people", "timeline", "classes", "links", "abilities") for card in cards or [])

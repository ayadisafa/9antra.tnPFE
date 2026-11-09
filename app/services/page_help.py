"""Yasmine is the guide of every page: "explique cette page", "what can I do here?", "how does the payments page work?",
"chnowa najjem na3mel lehna?", "اشرح لي هذه الصفحة". She says what the page is for and every part of it (what it shows,
what can be done there, what can be changed), from the page's own tour (page_tours.py), in the language asked, and
offers to show each part on the page itself.

Read with word lists, never by a model; the page is the one named, else the one her window is open on.
"""
from __future__ import annotations

import re

from app.services import navigate

_ASKS = navigate._rx(
    r"(?<![\w])(?:"
    r"(?:explique|expliquez|explain|decris|decrivez|describe|presente|presentez|guide)(?:\s*moi|\s*me)?\s+(?:cette|la|this|the|ma|my)?\s*page"
    r"|(?:a quoi sert|c est quoi|what is|what s|what does)\s+(?:cette page|this page|la page|the page|ici|here)"
    r"|(?:que|qu est ce que|quoi)\s+(?:je peux|puis je|on peut|peut on)\s+faire\s+(?:ici|sur cette page|dans cette page|sur la page|dans la page|avec cette page)"
    r"|what can (?:i|we) do (?:here|on this page|on the|with this page|in this page)"
    r"|how (?:does|do) (?:this|the) .{0,30}page work|how (?:do|can) i use (?:this|the) .{0,30}page"
    r"|comment (?:marche|fonctionne|utiliser|on utilise) (?:cette|la) .{0,30}page"
    r"|(?:montre|show)(?:\s*moi|\s*me)? (?:les parties|chaque partie|each part|the parts) de (?:cette|la) page|(?:montre|show)(?:\s*moi|\s*me)? (?:each part|every part)"
    r"|chnowa (?:najjem|nnajem|najem|nejjem|nnajjem) na3mel (?:lehna|houni|hne|fil page)|(?:fasserli|fassarli|ochrahli|achrahli|fassrli) (?:el )?page"
    r"|(?:اشرح|اشرحي)\s+(?:لي\s+)?(?:هذه\s+)?الصفحة|ماذا (?:يمكنني|استطيع) ان افعل (?:هنا|في هذه الصفحة)|شنوة (?:نعمل|ننجم نعمل) (?:هنا|في الصفحة)"
    r")(?![\w])")
_SUMMARY = navigate._rx(r"(?<![\w])(?:resume|resumer|summari[sz]e|sum up|lakhes|لخص)(?![\w])")

SAY = {
    "fr": {"you_can": "Vous pouvez", "change": "Vous pouvez changer", "show": "Montrez-moi chaque partie sur la page", "open": "Ouvrez-la et montrez-moi",
           "none": "Je n'ai pas encore le guide de cette page. Le guide de l'application dit comment faire chaque chose :",
           "parts": "Ses parties :"},
    "en": {"you_can": "You can", "change": "You can change", "show": "Show me each part on the page", "open": "Open it and show me",
           "none": "I don't have this page's guide yet. The application's guide says how to do each thing:", "parts": "Its parts:"},
    "ar": {"you_can": "يمكنك", "change": "يمكنك تغيير", "show": "أريني كل جزء في الصفحة", "open": "افتحيها وأريني",
           "none": "ليس لدي دليل هذه الصفحة بعد. دليل التطبيق يشرح كيف تفعل كل شيء:", "parts": "أجزاؤها:"},
    "tn": {"you_can": "Tnajjem", "change": "Tnajjem tbaddel", "show": "Warrini kol parti fil page", "open": "7ellha w warrini",
           "none": "Mazel ma 3andich el guide mta3 el page hedhi. El guide mta3 l'application y9oul kifech ta3mel kol chay:",
           "parts": "El parties mte3ha:"},
}


def wants(message: str) -> bool:
    text = navigate._fold(message)
    return bool(text) and len(text) <= 200 and not _SUMMARY.search(text) and bool(_ASKS.search(text))


def _words(db, lang: str) -> dict:
    if lang == "en":
        return {}
    from app.services import translations

    try:
        return translations.dictionary(db, "fr" if lang == "tn" else lang)["t"]
    except Exception:
        return {}


def guide(db, path: str, lang: str) -> dict | None:
    """The page's tour in the language asked: {"title", "purpose", "parts": [{"icon", "name", "what", "do", "change"}]}."""
    from app.services import page_tours

    tour = page_tours.for_path((path or "/").split("?", 1)[0].split("#", 1)[0])
    if tour is None:
        return None
    say = _words(db, lang)
    tr = (lambda text: say.get(text, text)) if say else (lambda text: text)
    frame = {step["sel"] for step in page_tours.FRAME}
    parts = [{"icon": step["icon"], "name": tr(step["name"]), "what": tr(step["what"]), "do": [tr(item) for item in step["do"]],
              "change": [tr(item) for item in step["change"]]} for step in tour["steps"] if step["sel"] not in frame]
    return {"title": tr(tour["title"]), "purpose": tr(tour["purpose"]), "parts": parts}


def answer(db, message: str, user=None, lang: str | None = None, path: str | None = None) -> dict:
    lang = lang if lang in SAY else "fr"
    words = SAY[lang]
    text = navigate._fold(message)
    named = navigate._page(re.sub(r"(?<![\w])(?:page|pages|cette|this|la|the|de|des|du)(?![\w])", " ", text), lang)
    here = (path or "/").split("?", 1)[0]
    target = named[0] if named and navigate.may_open(db, user, named[0]) else here
    found = guide(db, target, lang)
    if found is None:
        return {"reply": words["none"], "links": [{"label": "Guide", "url": "/guide"}], "source": "guide", "sources": [], "resources": [], "scene": "assistant"}
    lines = [f"{found['title']} — {found['purpose']}", "", words["parts"]]
    for part in found["parts"]:
        line = f"• {part['name']} — {part['what']}"
        if part["do"]:
            line += f" {words['you_can']} : " if lang == "fr" else f" {words['you_can']}: "
            line += "; ".join(part["do"]) + "."
        if part["change"]:
            line += (f" {words['change']} : " if lang == "fr" else f" {words['change']}: ") + "; ".join(part["change"]) + "."
        lines.append(line)
    same = target.split("#", 1)[0].rstrip("/") == here.rstrip("/")
    return {"reply": "\n".join(lines), "short": f"{found['title']} — {found['purpose']}", "source": "guide", "links": [], "sources": [],
            "resources": [], "scene": "assistant",
            "cards": [{"kind": "steps", "covers": True, "items": [{"icon": part["icon"], "title": part["name"], "text": part["what"],
                                                                   "do": part["do"][:3]} for part in found["parts"]]}],
            "page_tour": {"url": target, "here": same, "label": words["show"] if same else words["open"]}}

"""Where each answer of the assistant comes from, said every time; and the questions it answers from the programs'
content, from what is known about 9antra itself, and (last) from the local model reading the extracts it was given.

The sources are worked out from what the answer was built with: the application's records, a program's weeks,
9antra.tn, a guide, an answer the admin taught. The model never invents a source: it only says which of the numbered
extracts it used, and those are the ones shown.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session

_STOP = set("""
the a an of in on for and or to is are does do did what which who whom how why when where it its this that these those with about
est ce que qui quoi les des une un le la du de dans pour avec sur il elle on y a t au aux ou et en se sa son ses leur leurs ne pas
program programs programme programmes formation formations course courses cours week weeks semaine semaines module modules
cover covers covered teach teaches taught learn learns learned contain contains include includes included aborde abordes apprend
apprennent enseigne enseignes contenu content topic topics sujet sujets chapitre chapitres talk talks parle there any some all
vous nous je tu me my your our 9antra please svp merci thanks est-ce t-il quelle quel quels quelles
""".split())
_CONTENT_WORDS = (r"cover\w*|teach\w*|learn\w*|contain\w*|includ\w*|inclu\w*|abord\w*|apprend\w*|enseign\w*|contenu|content|topics?|"
                  r"sujets?|modules?|chapitres?|week \d+|semaine \d+|what is in|qu y a t il|parle de|talks? about|programme de|curriculum")
_ABOUT_WORDS = (r"who (is|are) 9antra|about 9antra|qui (est|sont) 9antra|c est quoi 9antra|presente(r)? 9antra|9antra c est|"
                r"where (are you|is (the )?(centre|center|9antra))|ou (etes vous|se trouve|est (situe|le centre))|adresse|address|located|situe\w*|"
                r"(your|vos|votre) (phone|numero|telephone|contact)|how (can|do) (i|we) (reach|contact)|comment (vous )?(joindre|contacter)|"
                r"centres?|locaux|horaires d ouverture|opening hours")


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", text)


def link(label: str, url: str | None = None) -> dict:
    return {"label": label, "url": url} if url else {"label": label}


# ------------------------------------------------------------------
# The source of a piece of knowledge
# ------------------------------------------------------------------

def of_entry(db: Session, entry) -> dict:
    """Where a knowledge entry comes from, with the page to check it."""
    from app.models.website import WebsiteItem
    from app.services import website_sync

    key = entry.source_key or ""
    if entry.origin != "system":
        return link(f"An answer you taught: “{(entry.title or '')[:70]}”", "/inbox/knowledge")
    if key.startswith("website:"):
        parts = key.split(":")
        item = (db.query(WebsiteItem).filter(WebsiteItem.kind == parts[1], WebsiteItem.external_id == parts[2]).first()
                if len(parts) >= 3 else None)
        return link(f"9antra.tn: {item.title if item else (entry.title or '').split(' — ')[0]}", (item.url if item and item.url else website_sync.SITE))
    if key.startswith("program:"):
        return link(f"The program in the application: {(entry.title or '').split(' — ')[0]}", f"/programs/{key.split(':')[1]}")
    if key.startswith("fact:"):
        return link(f"What you told the assistants: “{(entry.title or '')[:70]}”", "/inbox/knowledge#facts")
    if key.startswith("program-file:"):
        return link(f"A file of the program: {(entry.title or '')[:70]}", "/programs/expertise")
    if key == "system:about":
        return link("About 9antra (Settings and the welcome message)", "/settings#centre")
    return link(f"The application: {(entry.title or '')[:70]}", "/guide")


def for_answer(db: Session, found: dict) -> list[dict]:
    """The sources of an answer that did not say them itself, from what it was built with."""
    kind = found.get("source") or "none"
    links = found.get("links") or []
    if kind == "system":
        return [link(f"The application's records: {item['label']}", item.get("url")) for item in links[:3]] or [link("The application's records")]
    if kind == "guide":
        return [link("The application's guide", (links[0].get("url") if links else "/guide"))]
    if kind == "action":
        return [link("Prepared by the application from its records")] + [link(item["label"], item.get("url")) for item in links[:2]]
    if kind in ("taught", "website", "knowledge"):
        return [link(item["label"], item.get("url")) for item in links[:2]]
    return []


# ------------------------------------------------------------------
# What a program teaches
# ------------------------------------------------------------------

def _weeks_of(db: Session, program) -> list[dict]:
    """Every week of a program: its number, title, objectives, and what 9antra.tn says of it."""
    from app.models.website import WebsiteItem

    site = {}
    page = None
    for item in db.query(WebsiteItem).filter(WebsiteItem.kind == "formation", WebsiteItem.program_id == program.id,
                                             WebsiteItem.gone_at.is_(None)).all():
        page = page or item
        for week in item.details.get("weeks") or []:
            if isinstance(week, dict) and str(week.get("week", "")).isdigit():
                site[int(week["week"])] = week
    found, number = [], 0
    for level in program.levels:
        for week in level.weeks:
            number += 1
            web = site.get(number) or {}
            found.append({"number": number, "title": week.topic_title or "", "objectives": week.learning_objectives or "",
                          "site": " ".join(str(web.get(key) or "") for key in ("phase", "content", "video_title")).strip()})
    for number_, web in site.items():
        if number_ > number:
            found.append({"number": number_, "title": str(web.get("phase") or ""), "objectives": "", "site": str(web.get("content") or "")})
    return found, page


def _asked_words(text: str, program=None) -> list[str]:
    own = set(_plain(program.name).split()) if program is not None else set()
    return [word for word in _plain(text).split() if len(word) >= 3 and word not in _STOP and word not in own and not word.isdigit()]


def program_content(db: Session, text: str, program, fr: bool) -> dict | None:
    """“Does DevOps cover Kubernetes?”, “what is in week 3 of Python?”, “which program teaches React?”."""
    from app.models import Program
    from app.services import website_sync

    plain = _plain(text)
    if not re.search(_CONTENT_WORDS, plain) and program is None:
        return None
    week_asked = re.search(r"(?:week|semaine)\s*(\d{1,2})", plain)
    programs = [program] if program is not None else db.query(Program).order_by(Program.name).all()
    if program is not None and week_asked:
        weeks, page = _weeks_of(db, program)
        chosen = next((week for week in weeks if week["number"] == int(week_asked.group(1))), None)
        if chosen is None:
            return None
        parts = [f"{program.name}, {'semaine' if fr else 'week'} {chosen['number']} : {chosen['title']}".rstrip(" :")]
        if chosen["objectives"]:
            parts.append(("Objectifs : " if fr else "Objectives: ") + chosen["objectives"])
        if chosen["site"]:
            parts.append(("Sur 9antra.tn : " if fr else "On 9antra.tn: ") + chosen["site"][:400])
        sources = [link(f"The program in the application: {program.name}", f"/programs/{program.id}")]
        if page is not None:
            sources.append(link(f"9antra.tn: {page.title}", page.url or website_sync.SITE))
        return {"reply": "\n".join(parts), "links": [{"label": program.name, "url": f"/programs/{program.id}"}], "source": "knowledge", "sources": sources}
    words = _asked_words(text, program)
    if not words or not re.search(_CONTENT_WORDS, plain):
        return None
    hits = []
    for one in programs:
        weeks, page = _weeks_of(db, one)
        matched = [week for week in weeks if any(word in _plain(" ".join((week["title"], week["objectives"], week["site"]))) for word in words)]
        described = any(word in _plain(one.description or "") for word in words)
        if matched or described:
            hits.append((one, matched, page))
    asked = " ".join(words[:3])
    if program is not None and not hits:
        reply = (f"Les semaines de « {program.name} » ne parlent pas de « {asked} »." if fr
                 else f"None of the weeks of “{program.name}” mention “{asked}”.")
        return {"reply": reply, "links": [{"label": program.name, "url": f"/programs/{program.id}"}], "source": "knowledge",
                "sources": [link(f"The program in the application: {program.name} (every week read)", f"/programs/{program.id}")]}
    if not hits:
        return None
    lines, sources = [], []
    for one, matched, page in hits[:5]:
        if matched:
            weeks_text = "; ".join(f"{'sem.' if fr else 'week'} {week['number']} {week['title']}".strip() for week in matched[:4])
            lines.append(f"• {one.name} : {weeks_text}")
        else:
            lines.append(f"• {one.name} : {'dans sa présentation' if fr else 'in its description'}")
        sources.append(link(f"The program in the application: {one.name}", f"/programs/{one.id}"))
        if page is not None and matched and any(week["site"] for week in matched):
            sources.append(link(f"9antra.tn: {page.title}", page.url or website_sync.SITE))
    head = (f"« {asked} » est abordé ici :" if fr else f"“{asked}” is covered here:")
    return {"reply": head + "\n" + "\n".join(lines), "links": [{"label": one.name, "url": f"/programs/{one.id}"} for one, _, _ in hits[:3]],
            "source": "knowledge", "sources": sources[:6]}


# ------------------------------------------------------------------
# 9antra itself
# ------------------------------------------------------------------

def about_text(db: Session) -> str:
    """What is known of 9antra: its name, its site and page, its numbers, the words of the welcome message, its programs."""
    from app.models import Program
    from app.services import brand, email_templates, website_sync
    from app.services.settings_service import get_settings

    settings = get_settings(db)
    name = brand.view(settings)["name"]
    lines = [f"{name} (9antra.tn - The Bridge) est un centre de formation : formations, stages et encadrement.",
             f"Site : {website_sync.SITE} · Page Facebook : https://www.facebook.com/{website_sync.FACEBOOK_PAGE}"]
    if getattr(settings, "centre_phone", None) or getattr(settings, "centre_whatsapp", None):
        lines.append("Contact : " + " · ".join(part for part in (
            f"téléphone {settings.centre_phone}" if settings.centre_phone else "",
            f"WhatsApp {settings.centre_whatsapp}" if settings.centre_whatsapp else "") if part))
    # Who the centre is on payments (Settings › Your centre): what a cheque is made out to, where a transfer goes.
    legal = (getattr(settings, "centre_legal_name", None) or "").strip()
    if legal:
        lines.append(f"Nom officiel : {legal}" + (f" · matricule fiscal {settings.centre_tax_id}" if getattr(settings, "centre_tax_id", None) else ""))
    names = [line.strip() for line in (getattr(settings, "centre_beneficiaries", None) or "").splitlines() if line.strip()]
    if names or legal:
        lines.append("Chèque ou virement à l'ordre de : " + " ou ".join(dict.fromkeys(names + ([legal] if legal else []))))
    ribs = [line.strip() for line in (getattr(settings, "centre_ribs", None) or "").splitlines() if line.strip()]
    if ribs:
        from app.services import tunisian_banks

        shown = []
        for rib in ribs:
            bank = tunisian_banks.bank_of_rib(rib)
            shown.append(f"{rib} ({bank['name']})" if bank else rib)
        lines.append("RIB pour payer par virement : " + " ; ".join(shown))
    for label, value in (("Email", getattr(settings, "centre_email", None)), ("Site", getattr(settings, "centre_website", None)),
                         ("Adresse", getattr(settings, "centre_address", None))):
        if value:
            lines.append(f"{label} : {value}")
    from app.services import registration_form

    lines.append(f"Inscription : le formulaire d'inscription {registration_form.url(db)}")
    welcome = email_templates.get(db, "welcome")
    said = "\n".join(line.strip() for line in f"{welcome.body}\n{welcome.closing}".splitlines()
                     if line.strip() and "{" not in line and not line.strip().lower().startswith(("bonjour", "cordialement")))
    if said:
        lines.append("Ce que dit le message de bienvenue :\n" + said)
    programs = [program.name for program in db.query(Program).order_by(Program.name).all()]
    if programs:
        lines.append(f"{len(programs)} formations : " + ", ".join(programs) + ".")
    return "\n".join(lines)


def about(db: Session, text: str, fr: bool) -> dict | None:
    if not re.search(_ABOUT_WORDS, _plain(text)):
        return None
    from app.services import website_sync

    return {"reply": about_text(db), "links": [{"label": "9antra.tn", "url": website_sync.SITE}], "source": "system",
            "sources": [link("Settings: your centre", "/settings#centre"),
                        link("The welcome message (its words)", "/inbox/templates?key=welcome"),
                        link("9antra.tn", website_sync.SITE), link("The programs", "/programs/all")]}


def about_chunk(db: Session) -> dict:
    """The same, for what the assistant knows (and answers candidates with)."""
    return {"source_key": "system:about", "kind": "process", "program_id": None,
            "title": "À propos de 9antra : qui nous sommes, où, comment nous joindre",
            "content": "Qui est 9antra, où se trouve le centre, adresse, contact, téléphone, WhatsApp (about us, address, contact) : "
                       + about_text(db)}


# ------------------------------------------------------------------
# The local model, reading numbered extracts
# ------------------------------------------------------------------

def grounded(db: Session, message: str, fr: bool, facts: str) -> dict | None:
    """Last resort: the model answers from the closest extracts of what the application knows, and says which it used."""
    from app.models.knowledge import KnowledgeEntry
    from app.services import email_assistant, knowledge_service

    if not email_assistant.model_available():
        return None
    try:
        hits = knowledge_service.search(db, message, limit=5)
    except Exception:
        hits = []
    extracts = []
    for hit in hits:
        if (hit.get("quality") or 0) < 0.2:
            continue
        entry = db.get(KnowledgeEntry, hit["id"])
        if entry is not None and (entry.content or "").strip():
            extracts.append(entry)
    numbered = "\n\n".join(f"[{index + 1}] {entry.title}\n{entry.content.strip()[:900]}" for index, entry in enumerate(extracts))
    data = email_assistant.llm_json(
        "You are the assistant of 9antra, a training centre, inside its management application. Answer in one to four short "
        "sentences, in " + ("French" if fr else "English") + ", only from the numbered extracts and the figures given. "
        "If they do not answer the question, reply exactly: UNKNOWN. Say which extracts you used. "
        'Answer with one JSON object: {"reply": "...", "used": [1, 2]}',
        f"Figures of the application: {facts}\n\nExtracts:\n{numbered or '(none)'}\n\nQuestion: {message}",
        max_tokens=260,
    )
    reply = str((data or {}).get("reply") or "").strip()
    if not reply or "UNKNOWN" in reply.upper() or len(reply) < 8:
        return None
    used = []
    for number in (data or {}).get("used") or []:
        try:
            index = int(number) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(extracts) and extracts[index] not in used:
            used.append(extracts[index])
    sources = [of_entry(db, entry) for entry in used] or [link("The application's figures")]
    sources.append(link("Written by the local model from these sources: check it"))
    return {"reply": reply, "links": [], "source": "model", "sources": sources}


# ------------------------------------------------------------------
# The resources about a question: where to read, watch or download more
# ------------------------------------------------------------------

def resources_for(db: Session, message: str, found: dict | None = None, limit: int = 6) -> list[dict]:
    """The programs, videos, 9antra.tn pages, PDFs and guides a question is about (besides the answer's own links)."""
    from app.models import Program
    from app.models.knowledge import KnowledgeEntry
    from app.services import knowledge_service, videos, website_sync

    plain = _plain(message)
    words = [word for word in plain.split() if len(word) >= 4 and word not in _STOP]
    if not words:
        return []
    already = {item.get("url") for item in (found or {}).get("links") or []} | {item.get("url") for item in (found or {}).get("sources") or []}
    picked: list[dict] = []

    def add(label: str, url: str, kind: str) -> None:
        if url and url not in already and all(item["url"] != url for item in picked):
            picked.append({"label": label, "url": url, "kind": kind})

    named = [program for program in db.query(Program).order_by(Program.name).all()
             if any(part in plain.split() for part in _plain(program.name).split() if len(part) >= 4)]
    for program in named[:2]:
        add(f"{program.name}: its page", f"/programs/{program.id}", "program")
        add(f"{program.name}: the PDF", f"/programs/{program.id}/pdf", "pdf")
        if videos.of_program(db, program):
            add(f"{program.name}: its videos", f"/programs/videos#p-{program.id}", "video")
    for group in videos.library(db)["programs"]:
        for video in group["videos"]:
            if any(word in _plain(video["title"]) for word in words):
                add(f"Video: {video['title'][:60]}", f"/programs/videos#p-{group['program'].id}", "video")
        if len(picked) >= limit:
            break
    try:
        hits = knowledge_service.search(db, message, limit=4)
    except Exception:
        hits = []
    for hit in hits:
        if (hit.get("quality") or 0) < 0.3:
            continue
        entry = db.get(KnowledgeEntry, hit["id"])
        if entry is not None:
            source = of_entry(db, entry)
            if source.get("url"):
                add(source["label"], source["url"], "page")
    if re.search(r"site|website|9antra tn|events?|evenements?|offres?|stages?|internships?", plain):
        add("9antra.tn", website_sync.SITE, "page")
    return picked[:limit]

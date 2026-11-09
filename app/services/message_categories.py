"""What a message or a request is about: its category, which is also the agent that handles it (orchestrator.py).

    programs      Programs and courses     the content, the level, the course files, the certificate
    planning      Planning                 days, hours, start dates, sessions, absences, places
    payment       Payment                  prices, what is owed, receipts, refunds
    registration  Registration             registering, cancelling, changing program
    technical     Technical help           a link that does not open, a password, the platform, a video, Meet
    complaint     Complaint                someone unhappy: always for the admin
    trends        Trends and ideas         what is new in a field, the market, services to add, names
    records       The centre's figures     who paid, who is absent, lists (the admin's questions)
    general       General                  thanks, greetings, anything else

Read by fixed word lists (French, English, Tunisian in Latin letters and Arabic); the multilingual model is only asked
when no list recognises anything, and it can only pick a category, never answer.
"""
from __future__ import annotations

import re
import unicodedata

CATEGORIES = {
    "programs": ("Programs and courses", "bi-journal-bookmark", "#7c3aed"),
    "planning": ("Planning", "bi-calendar-week", "#0ea5e9"),
    "payment": ("Payment", "bi-cash-coin", "#16a34a"),
    "registration": ("Registration", "bi-person-plus", "#db2777"),
    "technical": ("Technical help", "bi-tools", "#f59e0b"),
    "complaint": ("Complaint", "bi-emoji-frown", "#e11d48"),
    "trends": ("Trends and ideas", "bi-graph-up-arrow", "#0d9488"),
    "records": ("The centre's figures", "bi-clipboard-data", "#475569"),
    "general": ("General", "bi-chat-dots", "#6b7280"),
}
# When two lists tie, the one first here wins (what most needs a person first).
_PRIORITY = ["complaint", "technical", "payment", "registration", "planning", "programs", "trends", "records", "general"]

_WORDS = {
    "complaint": r"plainte|reclamation|arnaque|inadmissible|scandale|inacceptable|complaint|scam|unacceptable|lawyer|avocat|"
                 r"pas content|mecontent|decu|disappointed|nul\b|n'importe quoi|chkoun ya3mel|mouch normal|شكوى|غير مقبول",
    "technical": r"lien|link|ne marche pas|marche pas|ne fonctionne pas|fonctionne pas|bug|erreur|error|acces|access|connexion|"
                 r"connecter|login|mot de passe|password|identifiant|plateforme|platform|application|appli|site web|meet|zoom|jitsi|"
                 r"camera|micro|audio|son\b|video ne|la video|telecharg|download|upload|s'ouvre pas|n'ouvre pas|page blanche|ecran|"
                 r"navigateur|browser|wifi|internet|mayemchich|matemchich|ma3adech yemchi|mafamech|moch khadem|bloque|blocked|"
                 r"لا يعمل|رابط|كلمة السر|الدخول|مشكل تقني",
    "payment": r"pai?ement|payer|paye|payé|prix|tarif|cout|coût|combien|montant|reste a payer|solde|virement|cheque|chèque|recu|"
               r"facture|rembours|refund|price|cost|fee|pay|owe|balance|invoice|9adech|9addech|flous|khalas|5alas|dinar|dt\b|"
               r"الدفع|السعر|ثمن|فلوس|خلاص",
    "registration": r"inscri|inscription|s'inscrire|register|registration|sign up|enrol|annul|cancel|changer de formation|"
                    r"changer de programme|desinscri|formulaire|nsajjel|tsajjel|التسجيل|سجل|نسجل",
    "planning": r"horaire|emploi du temps|planning|calendrier|seance|séance|session|cours de (?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)|"
                r"lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|quand|date|heure|demain|commence|debut|début|start|"
                r"schedule|timetable|when|absent|absence|retard|en retard|reporter|report|decal|en ligne|sur place|adresse|"
                r"lieu|ou se trouve|wa9tech|wa9teh|ghodwa|el wa9t|نهار|وقتاش|موعد|الحصة|غياب",
    "programs": r"programme|formation|contenu|module|semaine \d|support|cours|chapitre|niveau|prerequis|certificat|attestation|"
                r"diplome|syllabus|exercice|projet|curriculum|course|lesson|content|certificate|level|prerequisite|"
                r"dars|el cours|الدرس|البرنامج|الشهادة|التكوين",
    "trends": r"tendance|trend|trending|nouveaute|nouveautes|actualite|news|marche du travail|market|offres? d'emploi|jobs?\b|"
              r"quoi de neuf|what'?s new|inspir|quel nom|nom de|name for|service a ajouter|services a ajouter|ajouter comme service|"
              r"what (?:service|program)s? (?:to|should|could)|idee|idees|ideas?|innov|futur|avenir|journal|linkedin|recommand|"
              r"(?:services?|programmes?|formations?|offres?)\W+(?:\w+\W+){0,3}(?:ajouter|add|proposer|offrir|lancer|creer)|"
              r"(?:ajouter|add|proposer|offrir|lancer|creer)\W+(?:\w+\W+){0,3}(?:services?|programmes?|formations?)",
    "records": r"qui n.?a pas pay|who (?:has not|hasn.?t) paid|combien de candidats|how many|liste des|list of|impayes|unpaid|"
               r"qui est absent|who is absent|chiffres|figures|statisti|rapport|report",
}
_COMPILED = {key: re.compile(r"(?<![a-z])(?:" + pattern + r")", re.IGNORECASE) for key, pattern in _WORDS.items()}
_THANKS = re.compile(r"^\W*(merci|thanks|thank you|ok|d'accord|bien recu|parfait|super|top|c'est note|3aychek|aychek|يعطيك الصحة|شكرا)\b[^?]{0,80}$",
                     re.IGNORECASE)


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", (text or "").lower()).encode("ascii", "ignore").decode() + " " + (text or "").lower()


def label_of(key: str | None) -> dict:
    name, icon, colour = CATEGORIES.get(key or "general", CATEGORIES["general"])
    return {"key": key if key in CATEGORIES else "general", "label": name, "icon": icon, "colour": colour}


def categorize(text: str, *, intent: str | None = None, for_admin: bool = False, use_meaning: bool = True) -> dict:
    """{"key", "label", "icon", "colour", "why": the words that decided, "by": words | intent | meaning | none}."""
    plain = _plain(text)
    if _THANKS.match((text or "").strip()):
        return {**label_of("general"), "why": [], "by": "words"}
    counts: dict[str, list[str]] = {}
    for key, pattern in _COMPILED.items():
        found = [match.group(0).strip() for match in pattern.finditer(plain)]
        if found:
            counts[key] = list(dict.fromkeys(found))
    if not for_admin:
        counts.pop("records", None)            # a candidate's message is never about the centre's figures or the market
        counts.pop("trends", None)
    # The reading of the email assistant (its intent) counts as one more word for its category.
    from_intent = {"complaint": "complaint", "refund": "payment", "cancellation": "registration", "payment": "payment", "pricing": "payment",
                   "certificate": "programs", "course_content": "programs", "schedule": "planning", "location": "planning",
                   "registration": "registration"}.get(intent or "")
    if from_intent:
        counts.setdefault(from_intent, []).append(f"({intent})")
    if counts:
        best = max(counts, key=lambda key: (len(counts[key]) + (2 if key == "complaint" else 0), -_PRIORITY.index(key)))
        return {**label_of(best), "why": counts[best][:4], "by": "words"}
    if use_meaning:
        found = _by_meaning(text, for_admin)
        if found:
            return found
    return {**label_of("general"), "why": [], "by": "none"}


def backfill(db, most: int = 400) -> int:
    """The messages that came before categories, or that nobody sorted, get theirs (by their words only). Returns how many."""
    from app.models.candidate_email import CandidateEmail

    rows = (db.query(CandidateEmail).filter(CandidateEmail.direction == "inbound", CandidateEmail.category.is_(None))
            .order_by(CandidateEmail.received_at.desc()).limit(most).all())
    for row in rows:
        row.category = categorize(f"{row.subject or ''}\n{row.body or ''}", intent=row.intent, use_meaning=False)["key"]
    if rows:
        db.commit()
    return len(rows)


def counts(db, since=None) -> dict[str, int]:
    """How many incoming messages of each category (since a date, when given)."""
    from sqlalchemy import func

    from app.models.candidate_email import CandidateEmail

    query = db.query(CandidateEmail.category, func.count(CandidateEmail.id)).filter(CandidateEmail.direction == "inbound")
    if since is not None:
        query = query.filter(CandidateEmail.received_at >= since)
    return {key or "general": count for key, count in query.group_by(CandidateEmail.category).all()}


_EXAMPLES = {
    "programs": ["Qu'est-ce qu'on apprend dans cette formation ?", "What does the course cover?", "Je voudrais le support du cours"],
    "planning": ["À quelle heure est la séance ?", "When does my class start?", "Je serai absent samedi"],
    "payment": ["Combien coûte la formation ?", "How much do I still owe?", "J'ai fait le virement"],
    "registration": ["Je veux m'inscrire", "I would like to cancel my registration", "Comment s'inscrire ?"],
    "technical": ["Le lien ne marche pas", "I cannot log in", "La vidéo ne s'ouvre pas"],
    "complaint": ["Je ne suis pas satisfait du tout", "This is unacceptable", "Je veux faire une réclamation"],
    "trends": ["Qu'est-ce qui est tendance en intelligence artificielle ?", "What services could we add?", "Quoi de neuf en DevOps ?"],
}


def _by_meaning(text: str, for_admin: bool) -> dict | None:
    """The multilingual model, only when no word list recognised the message: the closest category, when it is clearly
    the closest."""
    try:
        from app.services import meaning

        if not meaning.available():
            return None
        bank = {key: value for key, value in _EXAMPLES.items() if for_admin or key != "trends"}
        found = meaning.closest(text, bank, floor=0.45, margin=0.05)
    except Exception:
        return None
    if not found:
        return None
    return {**label_of(found["key"]), "why": [found["example"]], "by": "meaning"}

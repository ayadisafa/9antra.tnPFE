"""What came in and what went out since a moment: Yasmine's "while you were away", and the chat's "what happened today?".

Everything is counted from the records (the messages, the registrations, the payments, the reviews), never guessed:
what came in (messages from candidates, registrations, payments, payment proofs, reviews) and what went out (answers
the assistant sent alone, automatic messages, messages written by the team, and what did not go out).
The sentences are written here in the four languages Yasmine speaks.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

AWAY_AFTER_HOURS = 2            # back after this long: Yasmine says what happened meanwhile

# key: (where it leads, {lang: (one, many)})
_CAME = {
    "messages": ("/inbox?filter=conversations", {
        "fr": ("1 message reçu", "{n} messages reçus"), "en": ("1 message received", "{n} messages received"),
        "ar": ("رسالة واحدة وصلت", "{n} رسائل وصلت"), "tn": ("message wa7ed wsel", "{n} messages wslou")}),
    "registrations": ("/candidate/all", {
        "fr": ("1 nouvelle inscription", "{n} nouvelles inscriptions"), "en": ("1 new registration", "{n} new registrations"),
        "ar": ("تسجيل جديد واحد", "{n} تسجيلات جديدة"), "tn": ("inscription jdida", "{n} inscriptions jdod")}),
    "payments": ("/payments", {
        "fr": ("1 paiement enregistré", "{n} paiements enregistrés"), "en": ("1 payment recorded", "{n} payments recorded"),
        "ar": ("دفعة واحدة سُجّلت", "{n} دفعات سُجّلت"), "tn": ("khlas wa7ed tsajjel", "{n} khlasat tsajjlou")}),
    "proofs": ("/payments#proofs", {
        "fr": ("1 preuve de paiement à vérifier", "{n} preuves de paiement à vérifier"),
        "en": ("1 payment proof to check", "{n} payment proofs to check"),
        "ar": ("إثبات دفع واحد للتثبت منه", "{n} إثباتات دفع للتثبت منها"), "tn": ("preuve de paiement wa7da lazem tchoufha", "{n} preuves de paiement lazem tchoufhom")}),
    "reviews": ("/website#feedback", {
        "fr": ("1 avis reçu", "{n} avis reçus"), "en": ("1 review received", "{n} reviews received"),
        "ar": ("رأي واحد وصل", "{n} آراء وصلت"), "tn": ("ra2y wa7ed wsel", "{n} ara2 wslou")}),
}
_WENT = {
    "answered": ("/inbox?filter=auto", {
        "fr": ("1 réponse envoyée seule", "{n} réponses envoyées seules"), "en": ("1 answer sent alone", "{n} answers sent alone"),
        "ar": ("رد واحد أُرسل تلقائيا", "{n} ردود أُرسلت تلقائيا"), "tn": ("jweb wa7ed tba3ath wa7dou", "{n} ajwba tba3thou wa7adhom")}),
    "sent": ("/inbox?filter=sent", {
        "fr": ("1 message automatique envoyé", "{n} messages automatiques envoyés"), "en": ("1 automatic message sent", "{n} automatic messages sent"),
        "ar": ("رسالة آلية واحدة أُرسلت", "{n} رسائل آلية أُرسلت"), "tn": ("message automatique wa7ed tba3ath", "{n} messages automatiques tba3thou")}),
    "by_team": ("/inbox?filter=conversations", {
        "fr": ("1 message écrit par l'équipe", "{n} messages écrits par l'équipe"), "en": ("1 message written by the team", "{n} messages written by the team"),
        "ar": ("رسالة واحدة كتبها الفريق", "{n} رسائل كتبها الفريق"), "tn": ("message wa7ed ktbou l'équipe", "{n} messages ktbethom l'équipe")}),
    "failed": ("/inbox/not-reached", {
        "fr": ("1 message n'est pas parti", "{n} messages ne sont pas partis"), "en": ("1 message did not go out", "{n} messages did not go out"),
        "ar": ("رسالة واحدة لم تُرسل", "{n} رسائل لم تُرسل"), "tn": ("message wa7ed ma tba3athch", "{n} messages ma tba3thouch")}),
}
WORDS = {
    "fr": {"came": "Arrivé", "went": "Parti", "away": "Pendant votre absence", "today": "Aujourd'hui", "nothing": "Rien de nouveau depuis."},
    "en": {"came": "Came in", "went": "Went out", "away": "While you were away", "today": "Today", "nothing": "Nothing new since."},
    "ar": {"came": "ما وصل", "went": "ما أُرسل", "away": "خلال غيابك", "today": "اليوم", "nothing": "لا جديد منذ ذلك الوقت."},
    "tn": {"came": "Chnowa wsel", "went": "Chnowa tba3ath", "away": "Wa9t elli ma kontech lehna", "today": "Lyoum", "nothing": "Ma famma chay jdid."},
}


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def counts(db: Session, moment: datetime) -> dict:
    """{"came": {key: n}, "went": {key: n}} since `moment` (UTC)."""
    from app.models.candidate_email import CandidateEmail
    from app.models.feedback import CandidateFeedback
    from app.models.payment import Payment
    from app.models.payment_proof import PaymentProof
    from app.models.registration import Registration

    since = _naive(moment)
    out = db.query(CandidateEmail).filter(CandidateEmail.direction == "outbound", CandidateEmail.received_at > since)
    return {
        "came": {
            "messages": db.query(CandidateEmail.id).filter(CandidateEmail.direction == "inbound", CandidateEmail.received_at > since).count(),
            "registrations": db.query(Registration.id).filter(Registration.registration_date > since).count(),
            "payments": db.query(Payment.id).filter(Payment.created_at > since).count(),
            "proofs": db.query(PaymentProof.id).filter(PaymentProof.created_at > since).count(),
            "reviews": db.query(CandidateFeedback.id).filter(CandidateFeedback.status == "received", CandidateFeedback.submitted_at > since).count(),
        },
        "went": {
            "answered": out.filter(CandidateEmail.author == "system", CandidateEmail.intent == "reply", CandidateEmail.status == "sent").count(),
            "sent": out.filter(CandidateEmail.author == "system", CandidateEmail.intent != "reply", CandidateEmail.status == "sent").count(),
            "by_team": out.filter(CandidateEmail.author == "admin", CandidateEmail.status.in_(("sent", "to_send"))).count(),
            "failed": out.filter(CandidateEmail.status == "failed").count(),
        },
    }


def lines(db: Session, moment: datetime, lang: str = "fr") -> list[dict]:
    """[{"text", "url", "side": "came" | "went"}]: only what happened, what needs a look ("failed") first."""
    lang = lang if lang in WORDS else "fr"
    found = counts(db, moment)
    shown = []
    for side, table in (("went", _WENT), ("came", _CAME)):
        for key, (url, said) in table.items():
            n = found[side][key]
            if n:
                one, many = said[lang]
                shown.append({"text": one if n == 1 else many.format(n=n), "url": url, "side": side, "key": key})
    shown.sort(key=lambda item: item["key"] != "failed")
    return shown


def sentence(db: Session, moment: datetime, lang: str = "fr", heading: str = "away") -> str:
    """"While you were away — came in: 3 messages received, 1 new registration. Went out: 2 answers sent alone." """
    lang = lang if lang in WORDS else "fr"
    words = WORDS[lang]
    found = lines(db, moment, lang)
    if not found:
        return f"{words[heading]} : {words['nothing']}" if lang == "fr" else f"{words[heading]}: {words['nothing']}"
    parts = []
    for side in ("came", "went"):
        items = [item["text"] for item in found if item["side"] == side]
        if items:
            parts.append(f"{words[side]} : " + ", ".join(items) if lang == "fr" else f"{words[side]}: " + ", ".join(items))
    joined = ". ".join(parts) + "."
    return f"{words[heading]} — {joined}"

"""A candidate asks to follow online instead of on site (or the other way): the application changes it by itself.

Read in every message a registered candidate sends (email, WhatsApp, SMS), by word rules in French, English, Tunisian
and Arabic, never by a model: a wish or a change ("je préfère passer en présentiel", "can I switch to online",
"nheb nwalli online", "أريد التحويل إلى الحضوري") next to a way (online / on site). A question about the ways alone
("c'est en ligne ou en présentiel ?") is not a change.

Then, for their registration (the one named, or the only one still running):
- its way changes (registrations.delivery_mode); their meeting links follow (only those online get them);
- the price of a month follows the new way: from the next month when they already paid this one, at once when they
  have paid nothing yet (services/monthly.py);
- their class: when it has no way of its own, or is "mixed", nothing else to do. When it is online only (or on site
  only) and they now want the other, the admin gets one notification (MODE_CHANGE) with a button to make it mixed;
- they get an answer saying what changed, in their language; the admin is told (Activity, notification).
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.notification import Notification
from app.models.registration import RegistrationStatus

ONLINE, ONSITE = "online", "onsite"

_ONLINE = (r"en\s+ligne|online|on\s*line|a\s+distance|distanciel|a\s+domicile|remote(ly)?|zoom|google\s+meet|visio|"
           r"3an\s+bo3d|3an\s+bou3d|عن\s*بعد|اونلاين|أونلاين|أون\s*لاين")
_ONSITE = (r"presentiel(le)?|en\s+presence|sur\s+place|au\s+centre|dans\s+(le|votre)\s+centre|on[\s-]*site|in\s+person|"
           r"at\s+the\s+cent(er|re)|in\s+class|fel\s+centre|f\s+centre|حضوري|حضورياً|حضوريا|في\s+المركز|بالحضور")
# A wish or a change, said in the same sentence as the way.
_CHANGE = re.compile(
    r"\b(passer|basculer|changer|change|switch(ing)?|move|transfer(er)?|transferer|mettre|convertir|"
    r"prefere(rais)?|prefer(ed)?|plutot|instead|au\s+lieu|rather|"
    r"(je\s+)?(veux|voudrais|souhaite|souhaiterais|aimerais|peux|pourrais|pourrai)\s+(le\s+|la\s+|les\s+)?(suivre|faire|continuer|assister|etudier)|"
    r"(want|would\s+like|wish|can\s+i|could\s+i)\s+(to\s+)?(attend|follow|study|continue|do\s+it|take\s+it)|"
    r"nheb\s+n(walli|badel|9ra|qra|kammel|kamel|tab3)|nbadel|nwalli|najjem\s+n(badel|walli|9ra|qra)|bech\s+n(walli|badel)|"
    r"أريد|اريد|أود|نحب|نبدل|نبدّل|تحويل|التحويل|الانتقال|أنتقل|انتقل|تغيير|بدل)\b|"
    r"أريد|اريد|أود|نحب|نبدل|تحويل|التحويل|الانتقال|تغيير|بدل",
    re.IGNORECASE)
# The way just after the verb or the preposition: the one they go to ("passer EN ligne", "switch TO online").
_TO = re.compile(r"(?:\ben\b|\bto\b|\bpour\b|\bvers\b|\bau\b|\bl\b|\blel\b|\bfil\b|إلى|الى|ل)\s*(?:le\s+|la\s+|mode\s+|format\s+|cours\s+)?(?:ال)?"
                 r"(?P<way>" + _ONLINE + "|" + _ONSITE + r")", re.IGNORECASE)
_QUESTION_ONLY = re.compile(r"\b(ou|or|wala|walla|أو)\b", re.IGNORECASE)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in text if not unicodedata.combining(char)).lower()


def _way(word: str) -> str:
    return ONLINE if re.fullmatch(_ONLINE, word, re.IGNORECASE) else ONSITE


def wanted(text: str, current: str | None = None) -> str | None:
    """The way they ask to change to (online / onsite), or None when the message does not ask for a change."""
    plain = _plain(text)
    for sentence in re.split(r"(?<=[.!?\n؟])\s*", plain):
        online = re.search(_ONLINE, sentence, re.IGNORECASE)
        onsite = re.search(_ONSITE, sentence, re.IGNORECASE)
        if not (online or onsite) or not _CHANGE.search(sentence):
            continue
        if online and onsite and _QUESTION_ONLY.search(sentence) and not _TO.search(sentence):
            continue                        # "en ligne ou en présentiel ?": a question about the ways
        target = _TO.search(sentence)
        if target is not None and not (online and onsite and target.group("way") and _way(target.group("way")) == _norm(current)):
            return _way(target.group("way"))
        if online and onsite:
            # From one to the other: the one they are not in yet.
            return ONSITE if _norm(current) == ONLINE else ONLINE
        return ONLINE if online else ONSITE
    return None


def _norm(mode: str | None) -> str:
    return ONSITE if (mode or "").strip().lower() in ("onsite", "on-site", "presentiel", "présentiel", "sur place") else ONLINE


def _registration(candidate, text: str):
    """The registration it is about: the program named, else the only one still running. None when unsure."""
    running = [item for item in candidate.registrations
               if item.status not in (RegistrationStatus.CANCELLED, RegistrationStatus.COMPLETED)]
    if len(running) <= 1:
        return running[0] if running else None
    plain = _plain(text)
    named = [item for item in running if item.program_name and _plain(item.program_name) in plain
             or (item.program is not None and _plain(item.program.name) in plain)]
    return named[0] if len(named) == 1 else None


def _class_way(db: Session, group) -> str | None:
    return group.delivery_mode if group is not None and group.delivery_mode in ("online", "onsite", "mixed") else None


WORDS = {
    "fr": {"done": "C'est fait : vous suivez désormais « {program} » {way}.",
           "online": "en ligne", "onsite": "en présentiel",
           "same": "Vous suivez déjà « {program} » {way} : rien à changer.",
           "price_now": "Le tarif mensuel {way} est de {price} : c'est le montant à régler pour commencer.",
           "price_next": "Le tarif mensuel {way} est de {price} : il s'applique à partir du mois prochain (le mois déjà réglé ne change pas).",
           "links": "Vous recevrez votre lien personnel avant chaque séance.",
           "place": "Rendez-vous au centre pour vos séances{where}.",
           "class": "Notre équipe confirme votre place {way} dans votre classe et revient vers vous très vite.",
           "which": "Bien sûr ! Pour quelle formation souhaitez-vous changer : {list} ?"},
    "en": {"done": "Done: you now follow “{program}” {way}.",
           "online": "online", "onsite": "on site",
           "same": "You already follow “{program}” {way}: nothing to change.",
           "price_now": "The monthly price {way} is {price}: that is the amount to pay to start.",
           "price_next": "The monthly price {way} is {price}: it applies from next month (the month already paid does not change).",
           "links": "You will receive your personal link before each session.",
           "place": "See you at the centre for your sessions{where}.",
           "class": "Our team confirms your place {way} in your class and gets back to you very soon.",
           "which": "Of course! For which program would you like to change: {list}?"},
    "ar": {"done": "تمّ: أصبحت تتابع « {program} » {way}.",
           "online": "عن بعد", "onsite": "حضورياً",
           "same": "أنت تتابع « {program} » {way} من قبل: لا شيء يتغيّر.",
           "price_now": "السعر الشهري {way} هو {price}: هذا هو المبلغ المطلوب للبدء.",
           "price_next": "السعر الشهري {way} هو {price}: يُطبَّق ابتداءً من الشهر القادم (الشهر المدفوع لا يتغيّر).",
           "links": "ستصلك روابطك الشخصية قبل كل حصة.",
           "place": "نلتقي في المركز لحصصك{where}.",
           "class": "سيؤكّد فريقنا مكانك {way} في قسمك ويعود إليك قريباً جداً.",
           "which": "بكل سرور! لأي تكوين تريد التغيير: {list}؟"},
    "tn": {"done": "Tamm: walit taba3 « {program} » {way}.",
           "online": "online", "onsite": "présentiel",
           "same": "Enti déjà taba3 « {program} » {way}: ma fama chay bech yetbaddel.",
           "price_now": "Soum chhar {way} howa {price}: hadha elli tkhallsou bech tebda.",
           "price_next": "Soum chhar {way} howa {price}: yebda men chhar el jey (chhar elli khallastou ma yetbaddelch).",
           "links": "Bech ywasslek lien mte3ek 9bal kol séance.",
           "place": "Nchoufouk fel centre lel séances{where}.",
           "class": "L'équipe bech t2akked blastek {way} fel classe w terja3lek fissa3.",
           "which": "Akid! Ama formation t7eb tbaddel: {list}?"},
}


def handle(db: Session, candidate, text: str, language: str = "fr") -> dict | None:
    """When the message asks to change way: change it and say so. Returns {"text", "changed", "registration", "note"}
    (the answer to send, in their language), or None when the message does not ask for that."""
    from app.services import monthly
    from app.services.program_names import price_of
    from app.services.settings_service import get_settings

    words = WORDS.get(language) or WORDS["fr"]
    running = [item for item in candidate.registrations if item.status not in (RegistrationStatus.CANCELLED, RegistrationStatus.COMPLETED)]
    if not running:
        return None
    registration = _registration(candidate, text)
    target = wanted(text, registration.delivery_mode if registration is not None else None)
    if target is None:
        return None
    if registration is None:
        names = ", ".join(f"« {item.program_name} »" for item in running if item.program_name)
        return {"text": words["which"].format(list=names), "changed": False, "registration": None,
                "note": "Asked to change way (online / on site), for which program?"}
    program = registration.program_name or (registration.program.name if registration.program else "")
    way = words[target]
    if _norm(registration.delivery_mode) == target:
        return {"text": words["same"].format(program=program, way=way), "changed": False, "registration": registration,
                "note": f"Asked to follow {target}: they already do."}
    before = _norm(registration.delivery_mode)
    registration.delivery_mode = target
    price = float(price_of(registration.program, target)) if registration.program is not None else monthly.month_price(registration)
    lines = [words["done"].format(program=program, way=way)]
    nothing_paid = not (registration.paid_amount or 0) and monthly.counted(registration) <= 1
    if price:
        registration.month_price = price
        if nothing_paid:
            registration.total_amount = registration.total_price = price
            monthly._recompute(registration)
            lines.append(words["price_now"].format(way=way, price=monthly.money(price)))
        elif monthly.counted(registration) < monthly.months_of(registration):
            lines.append(words["price_next"].format(way=way, price=monthly.money(price)))
    group = registration.class_group
    class_way = _class_way(db, group)
    conflict = class_way in (ONLINE, ONSITE) and class_way != target
    if conflict:
        lines.append(words["class"].format(way=way))
    elif group is not None:
        if target == ONLINE:
            lines.append(words["links"])
        else:
            address = (get_settings(db).centre_address or "").strip()
            lines.append(words["place"].format(where=f" ({address})" if address else ""))
    who = candidate.full_name or candidate.email
    db.add(Activity(candidate_id=candidate.id, event_type="MODE_CHANGED",
                    details=f"{program}: from {before} to {target}, asked in their message" + (f"; month price {monthly.money(price)}" if price else "")))
    if conflict:
        db.add(Notification(recipient_role="admin", category="MODE_CHANGE", candidate_id=candidate.id, registration_id=registration.id,
                            program_id=registration.program_id, title=f"{who} now follows {target}",
                            message=f"{who} asked to follow {program} {target}; their class {group.name} is {class_way} only. "
                                    "Make it mixed, or move them to another class.",
                            action_url=f"/classes/{group.id}", payload={"class_id": group.id, "registration_id": registration.id, "way": target}))
    else:
        db.add(Notification(recipient_role="admin", category="MODE_CHANGE_DONE", candidate_id=candidate.id, registration_id=registration.id,
                            title=f"{who} now follows {target}", message=f"{program}: changed from {before} to {target} by itself, from their message.",
                            action_url=f"/candidate/{candidate.id}#programs", is_read=True, status="told"))
    db.flush()
    return {"text": "\n\n".join(lines), "changed": True, "registration": registration, "conflict": conflict,
            "note": f"Changed from {before} to {target}, as they asked" + ("; their class is not of that way: you were told." if conflict else ".")}


def settled(db: Session, notification: Notification) -> bool:
    """A MODE_CHANGE waits until their class takes their way (mixed, or theirs), or they are elsewhere."""
    from app.models import ClassGroup
    from app.models.registration import Registration

    payload = notification.payload if isinstance(notification.payload, dict) else {}
    registration = db.get(Registration, notification.registration_id) if notification.registration_id else None
    group = db.get(ClassGroup, payload.get("class_id") or "")
    if registration is None or group is None or registration.class_group_id != group.id:
        return True
    return _class_way(db, group) in (None, "mixed", _norm(registration.delivery_mode))


def fix(db: Session, notification: Notification) -> dict | None:
    """The one button of a MODE_CHANGE: make their class mixed."""
    if settled(db, notification):
        return None
    payload = notification.payload if isinstance(notification.payload, dict) else {}
    return {"label": "Make the class mixed (online and on site)", "url": f"/classes/{payload.get('class_id')}/mode", "method": "post",
            "fields": {"mode": "mixed"}, "confirm": None}

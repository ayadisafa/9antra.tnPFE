"""Reading a payment proof: a photo or a PDF of a transfer receipt, a deposit slip, an invoice, a payment app's screen.

The words are read by OCR on this computer (RapidOCR; a PDF's own text first). Then fixed rules find the amount,
the date, the RIBs, the bank, the reference, and check them. No language model takes part, and nothing here
decides: the admin confirms or refuses (services/payment_proofs.py).

What the checks can and cannot say: a photo cannot prove a receipt is genuine. They say whether what it shows is
consistent: a RIB whose key is right, the centre's own account as the receiver, the amount the candidate owes, a
recent date, a bank that matches the RIB, the same receipt not sent twice, no trace of a photo editor in the file.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import date, datetime, timedelta

from app.services import tunisian_banks as banks

logger = logging.getLogger(__name__)

_ENGINE = None
EDITORS = ("photoshop", "gimp", "canva", "picsart", "snapseed", "lightroom", "pixlr", "paint.net", "affinity", "illustrator", "inkscape")
MOST_DAYS_OLD = 60


# ------------------------------------------------------------------
# The words
# ------------------------------------------------------------------

def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
    return _ENGINE


def ocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _ocr_image(data: bytes) -> str:
    from PIL import Image, ImageOps
    import numpy

    picture = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    if max(picture.size) > 2200:                 # big phone photos: smaller is as readable and much faster
        picture.thumbnail((2200, 2200))
    result, _ = _engine()(numpy.array(picture)[:, :, ::-1])
    lines = []
    for box, text, score in result or []:
        if score >= 0.5:
            lines.append((box[0][1], box[0][0], text))
    lines.sort(key=lambda item: (round(item[0] / 14), item[1]))       # top to bottom, then left to right
    return "\n".join(text for _, _, text in lines)


def read_text(data: bytes, content_type: str = "") -> str:
    """Every word of the file. A PDF's own text when it has some, otherwise its pages read as pictures."""
    kind = (content_type or "").lower()
    if kind == "application/pdf" or data[:5] == b"%PDF-":
        text = ""
        try:
            from pypdf import PdfReader

            text = "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages[:3])
        except Exception:
            text = ""
        if len(text.strip()) >= 20:
            return text
        try:
            import pypdfium2

            document = pypdfium2.PdfDocument(data)
            parts = []
            for index in range(min(len(document), 2)):
                picture = document[index].render(scale=2).to_pil()
                buffer = io.BytesIO()
                picture.save(buffer, format="PNG")
                parts.append(_ocr_image(buffer.getvalue()))
            return "\n".join(parts)
        except Exception:
            logger.exception("A PDF proof could not be read as pictures.")
            return text
    return _ocr_image(data)


def editor_of(data: bytes) -> str | None:
    """The photo editor written inside the picture's details, when there is one."""
    try:
        from PIL import Image

        picture = Image.open(io.BytesIO(data))
        found = " ".join(str(value) for value in (picture.getexif() or {}).values() if isinstance(value, (str, bytes)))
        found += " " + " ".join(str(value) for value in (picture.info or {}).values() if isinstance(value, str))
    except Exception:
        return None
    found = found.lower()
    return next((name for name in EDITORS if name in found), None)


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------
# What the words say
# ------------------------------------------------------------------

_AMOUNT_WORDS = re.compile(r"(montant|total|somme|amount|net\s*[àa]\s*payer|à payer|a payer|versement|virement|valeur|المبلغ)", re.I)
_MONEY = re.compile(r"(?<![\d.,])(\d{1,3}(?:[ . ]\d{3})+|\d+)(?:[.,](\d{1,3}))?\s*(dt|tnd|dinars?|د\.?ت)?(?![\d])", re.I)
_DATE = re.compile(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})(?!\d)")
_ISO_DATE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_REFERENCE = re.compile(r"(r[ée]f[ée]rence|r[ée]f\.?|n[°o]\s*(?:d['’]?\s*)?op[ée]ration|transaction|reference|ref)\s*[:#n°]*\s*([A-Z0-9][A-Z0-9\-/]{4,30})", re.I)
_LONG_NUMBER = re.compile(r"(?:\d[\s.\-]?){20,}")
_IBAN = re.compile(r"TN\s?59(?:[\s]?\d){20}", re.I)


def _number(whole: str, part: str | None) -> float:
    whole = re.sub(r"[ . ]", "", whole)
    value = float(whole)
    if part:
        # Tunisian amounts often have three decimals (millimes): "390,000" is 390 dinars.
        value += float("0." + part)
    return round(value, 3)


def amounts_in(text: str) -> list[float]:
    """The amounts written, the most likely to be the payment first (next to "Montant", or written with DT)."""
    scored = []
    for line in (text or "").splitlines():
        line_has_word = bool(_AMOUNT_WORDS.search(line))
        if _DATE.search(line) and not line_has_word:
            continue
        for match in _MONEY.finditer(line):
            digits = re.sub(r"\D", "", match.group(1))
            if len(digits) >= 9:            # an account, a phone or a reference number, not an amount
                continue
            value = _number(match.group(1), match.group(2))
            if value <= 0 or value > 100000:
                continue
            with_currency = bool(match.group(3))
            if not (line_has_word or with_currency):
                continue
            scored.append((2 * line_has_word + with_currency, value))
    scored.sort(key=lambda item: -item[0])
    seen, found = set(), []
    for _, value in scored:
        if value not in seen:
            seen.add(value)
            found.append(value)
    return found


def dates_in(text: str) -> list[date]:
    found = []
    for day, month, year in _DATE.findall(text or ""):
        year = int(year) + (2000 if len(year) == 2 else 0)
        try:
            found.append(date(year, int(month), int(day)))
        except ValueError:
            continue
    for year, month, day in _ISO_DATE.findall(text or ""):
        try:
            found.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return found


def ribs_in(text: str) -> list[str]:
    """Every 20-digit number written (spaces or not), and the RIB inside a TN59 IBAN."""
    found = []
    for match in _IBAN.finditer(text or ""):
        digits = banks.rib_digits(match.group(0))[2:]      # TN 59 + the RIB
        if len(digits) == 20 and digits not in found:
            found.append(digits)
    for match in _LONG_NUMBER.finditer(text or ""):
        digits = banks.rib_digits(match.group(0))
        for start in range(0, len(digits) - 19):
            piece = digits[start:start + 20]
            if banks.rib_ok(piece):
                if piece not in found:
                    found.append(piece)
                break
        else:
            if len(digits) == 20 and digits not in found:
                found.append(digits)        # 20 digits whose key is wrong: kept, to be shown as such
    return found


def reference_in(text: str) -> str | None:
    found = _REFERENCE.search(text or "")
    return found.group(2).strip("-/") if found else None


def read(data: bytes, content_type: str = "") -> dict:
    """Everything a proof shows: {text, amounts, amount, dates, date, ribs, banks, bank, apps, reference, editor, fingerprint}."""
    try:
        text = read_text(data, content_type)
    except Exception:
        logger.exception("A payment proof could not be read.")
        text = ""
    amounts, dates, ribs = amounts_in(text), dates_in(text), ribs_in(text)
    named = banks.banks_in(text)
    return {
        "text": text, "amounts": amounts, "amount": amounts[0] if amounts else None,
        "dates": dates, "date": max(dates) if dates else None, "ribs": ribs,
        "banks": named, "bank": named[0] if named else (banks.bank_of_rib(ribs[0]) if ribs else None),
        "apps": banks.apps_in(text), "reference": reference_in(text), "editor": editor_of(data), "fingerprint": fingerprint(data),
    }


# ------------------------------------------------------------------
# The checks
# ------------------------------------------------------------------

def checks(found: dict, *, owed: float | None, centre_ribs: list[str], registered_on: date | None = None,
           seen_before: bool = False, same_reference: bool = False, today: date | None = None) -> list[dict]:
    """Each check: {key, ok (True / False / None = cannot say), text}. Nothing here decides."""
    today = today or date.today()
    result = []

    def add(key, ok, text):
        result.append({"key": key, "ok": ok, "text": text})

    if not (found.get("text") or "").strip():
        add("read", False, "No text could be read on it: ask for a clearer photo or the PDF of the receipt.")
        return result
    add("read", True, "The text was read.")

    amount = found.get("amount")
    if amount is None:
        add("amount", False, "No amount found on it.")
    elif owed is None:
        add("amount", None, f"Amount found: {amount:g} DT. The candidate owes nothing that is known.")
    elif abs(amount - owed) < 0.5:
        add("amount", True, f"{amount:g} DT: exactly what the candidate owes.")
    elif amount < owed:
        add("amount", None, f"{amount:g} DT: less than the {owed:g} DT owed (a part payment).")
    else:
        add("amount", None, f"{amount:g} DT: more than the {owed:g} DT owed.")

    ribs = found.get("ribs") or []
    wrong = [rib for rib in ribs if not banks.rib_ok(rib)]
    if ribs and not wrong:
        add("rib", True, "The RIB" + ("s" if len(ribs) > 1 else "") + " written on it " + ("are" if len(ribs) > 1 else "is") + " well formed (the key is right).")
    elif wrong:
        add("rib", False, f"The RIB {banks.pretty_rib(wrong[0])} is not well formed (its key is wrong): a typing mistake, or a changed number.")
    else:
        add("rib", None, "No RIB written on it.")

    centre = [banks.rib_digits(rib) for rib in centre_ribs if banks.rib_digits(rib)]
    if centre:
        if any(rib in centre for rib in ribs):
            add("receiver", True, "The money went to the centre's own account.")
        elif ribs:
            add("receiver", False, "The account written on it is not the centre's (Settings, Your bank account).")
        else:
            add("receiver", None, "The centre's account is not written on it.")
    else:
        add("receiver", None, "Write the centre's RIB in Settings to check the money went to it.")

    named, by_rib = found.get("banks") or [], [banks.bank_of_rib(rib) for rib in ribs if banks.bank_of_rib(rib)]
    if named and by_rib:
        if any(item["code"] in {bank["code"] for bank in by_rib} for item in named):
            add("bank", True, f"Bank: {named[0]['name']}, and the RIB's bank code agrees.")
        else:
            add("bank", None, f"It names {named[0]['name']}, the RIB belongs to {by_rib[0]['name']} (a transfer between two banks?).")
    elif named:
        add("bank", True, f"Bank: {named[0]['name']}.")
    elif by_rib:
        add("bank", True, f"Bank (from the RIB): {by_rib[0]['name']}.")
    elif found.get("apps"):
        add("bank", True, f"Paid with {found['apps'][0]}.")
    else:
        add("bank", None, "No bank recognised on it.")

    when = found.get("date")
    if when is None:
        add("date", None, "No date found on it.")
    elif when > today + timedelta(days=1):
        add("date", False, f"Dated {when:%d/%m/%Y}: in the future.")
    elif when < today - timedelta(days=MOST_DAYS_OLD):
        add("date", False, f"Dated {when:%d/%m/%Y}: more than {MOST_DAYS_OLD} days ago.")
    elif registered_on and when < registered_on - timedelta(days=2):
        add("date", None, f"Dated {when:%d/%m/%Y}: before the registration.")
    else:
        add("date", True, f"Dated {when:%d/%m/%Y}.")

    if seen_before:
        add("twice", False, "This very file was already sent as a proof.")
    elif same_reference:
        add("twice", False, f"The reference {found.get('reference')} is on another proof already.")
    else:
        add("twice", True, "Not sent before.")

    if found.get("editor"):
        add("edited", False, f"The file says it went through a photo editor ({found['editor']}).")
    return result


def verdict(results: list[dict]) -> str:
    """unreadable | doubtful (a check failed) | to_check (some cannot be said) | consistent (everything agrees)."""
    if any(item["key"] == "read" and item["ok"] is False for item in results):
        return "unreadable"
    if any(item["ok"] is False for item in results):
        return "doubtful"
    important = {item["key"]: item["ok"] for item in results}
    if important.get("amount") is True and important.get("receiver") is True:
        return "consistent"
    return "to_check"


VERDICTS = {
    "consistent": ("Everything agrees", "ok", "bi-patch-check-fill"),
    "to_check": ("Looks right, some things cannot be checked", "warn", "bi-patch-question-fill"),
    "doubtful": ("Doubtful: look closely", "bad", "bi-patch-exclamation-fill"),
    "unreadable": ("Could not be read", "bad", "bi-eye-slash-fill"),
}

"""Reading a payment proof: a photo or a PDF of a cheque, a transfer receipt, a deposit slip, an invoice, a payment app's screen.

The words are read by OCR on this computer (RapidOCR; a PDF's own text first). Then fixed rules find the amount,
the date, the RIBs, the bank, the reference, and check them. No language model takes part, and nothing here
decides: the admin confirms or refuses (services/payment_proofs.py).

A cheque also says who it is made out to (checked against the names of the centre, Settings › Your centre), the amount
in words (it must agree with the figures), the place, the number of the cheque and who signs it. Its RIB is the payer's
account, so it is not compared with the centre's.

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
    return _lines_of(result)


def _lines_of(result) -> str:
    """The pieces read, put back in lines: pieces whose middles are at about the same height are one line (what a hand
    wrote on a printed line, a little above or below it, belongs to that line), left to right."""
    pieces = []
    for box, text, score in result or []:
        if score < 0.5:
            continue
        ys = [point[1] for point in box]
        top, bottom = min(ys), max(ys)
        pieces.append({"middle": (top + bottom) / 2, "height": max(1.0, bottom - top), "left": min(point[0] for point in box), "text": text})
    pieces.sort(key=lambda piece: piece["middle"])
    lines: list[list[dict]] = []
    for piece in pieces:
        line = lines[-1] if lines else None
        if line is not None:
            middle = sum(item["middle"] for item in line) / len(line)
            height = max(item["height"] for item in line)
            if abs(piece["middle"] - middle) <= 0.55 * max(height, piece["height"]):
                line.append(piece)
                continue
        lines.append([piece])
    return "\n".join(" ".join(item["text"] for item in sorted(line, key=lambda item: item["left"])) for line in lines)


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
    # An amount boxed by signs, as on a cheque: "#390,000#", "**390.000**".
    for match in _BOXED.finditer(text or ""):
        value = _number(match.group(1), match.group(2))
        if 0 < value <= 100000:
            scored.append((4, value))
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


# ------------------------------------------------------------------
# What a cheque says
# ------------------------------------------------------------------

_CHEQUE = re.compile(r"(ch[eèé]que|payez\s*contre|non\s*endossable|شيك|ادفعوا)", re.I)
_TRANSFER = re.compile(r"(virement|transfert|transfer|wire)", re.I)
_DEPOSIT = re.compile(r"(versement|d[ée]p[oô]t|bordereau)", re.I)
_ORDER = re.compile(r"(?:[àa]\s*l\s*['’`]?\s*ordre\s*(?:de|d\s*['’])|b[ée]n[ée]ficiaire(?:\s*du\s*virement)?|nom\s*du\s*b[ée]n[ée]ficiaire|"
                    r"payez\s+[àa](?!\s*l)|pay\s+to(?:\s+the\s+order\s+of)?|لأمر|لفائدة|المستفيد)\s*:?\s*(.*)", re.I)
_HOLDER = re.compile(r"^\s*(?:titulaire(?:\s*du\s*compte)?|tireur|donneur\s*d\s*['’]?\s*ordre|ordonnateur|[ée]metteur|de\s+la\s+part\s+de|"
                     r"from|صاحب\s*الحساب)\s*:?\s*(.+)$", re.I)
_TITLE = re.compile(r"^\s*(?:mme\.?\s|mlle\.?\s|melle\s|mr\.?\s|m\.|monsieur\s|madame\s|ste\.?\s|sté\.?\s|soci[ée]t[ée]\s)\s*([^\d:]{3,60})$", re.I)
_CHEQUE_NO = re.compile(r"(?:ch[eèé]que\s*n\s*[°o]?\.?|n\s*[°o]\.?\s*(?:du\s*)?ch[eèé]que|num[ée]ro\s*(?:du\s*)?ch[eèé]que)\s*:?\s*(\d{6,8})", re.I)
_PLACE = re.compile(r"(?:^|\s)(?:[Ff]ait\s+)?[àaÀA]\s+([A-ZÀ-Ý][A-Za-zÀ-ÿ' -]{2,28}?)\s*,?\s*(?:le\s+)?\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}", re.M)
_BOXED = re.compile(r"[#*=]{1,4}\s*(\d{1,3}(?:[ .]\d{3})*|\d+)(?:[.,](\d{1,3}))?\s*[#*=]{1,4}")
_NOT_A_NAME = re.compile(r"(banque\s*ou|organisme|la\s*somme|endossable|payez|ordre\s*de|\d{4,}|,\s*le\s*\d)", re.I)
_UNITS = {"zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9,
          "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16, "vingt": 20, "vingts": 20,
          "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60, "septante": 70, "huitante": 80, "octante": 80, "nonante": 90}
_BIG = {"cent": 100, "cents": 100, "mille": 1000, "milles": 1000, "mil": 1000, "million": 1_000_000, "millions": 1_000_000}
CHEQUE_DAYS = 8                 # a cheque written and paid in Tunisia is presented within eight days of its date


def kind_of(text: str, apps: list | None = None) -> str:
    """cheque | transfer | deposit | app | receipt."""
    if _CHEQUE.search(text or ""):
        return "cheque"
    if apps:
        return "app"
    if _TRANSFER.search(text or ""):
        return "transfer"
    if _DEPOSIT.search(text or ""):
        return "deposit"
    return "cheque" if re.search(r"[àa]\s*l\s*['’`]?\s*ordre\s*de", text or "", re.I) else "receipt"


def _tidy_name(value: str) -> str:
    value = re.split(r"\s{3,}|\s*(?:la\s*somme|somme\s*de|montant|,\s*le\s*\d)", value or "", maxsplit=1, flags=re.I)[0]
    value = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý])", " ", value)         # read without its spaces: "AhmedBenSalah"
    return " ".join(value.strip(" :;,.-_*#'\"").split())[:120]


def _name_like(value: str) -> bool:
    return 2 <= len(value) <= 80 and len(value.split()) <= 7 and not _NOT_A_NAME.search(value) and not _PLACE.search(" " + value + " 01/01/2026")


def beneficiary_in(text: str) -> str | None:
    """Who the cheque (or the transfer) is made out to: what follows “à l'ordre de” on its line, else the handwritten
    line just under it, or just over it (a hand writes a little above or below the printed line)."""
    lines = [line.strip() for line in (text or "").splitlines()]
    for index, line in enumerate(lines):
        found = _ORDER.search(line)
        if not found:
            continue
        value = _tidy_name(found.group(1))
        if len(value) >= 2 and not _NOT_A_NAME.search(value):
            return value
        for near in (index + 1, index - 1):
            if 0 <= near < len(lines):
                value = _tidy_name(lines[near])
                if _name_like(value) and not _CHEQUE.search(value) and not re.search(r"\d", value[:1]):
                    return value
    return None


def drawer_in(text: str) -> str | None:
    """Who pays: the account holder printed on the cheque, or who gave the transfer order."""
    for line in (text or "").splitlines():
        found = _HOLDER.match(line) or _TITLE.match(line)
        if found:
            value = _tidy_name(found.group(1))
            if len(value) >= 3 and not _NOT_A_NAME.search(value):
                return value
    return None


def cheque_number_in(text: str, ribs: list[str]) -> str | None:
    found = _CHEQUE_NO.search(text or "")
    if found:
        return found.group(1)
    rest = text or ""
    for rib in ribs:                         # the account's own digits are not the cheque's number
        rest = re.sub(r"[\s.\-]?".join(rib), " ", rest)
    for match in re.finditer(r"(?<![\d/.\-])(\d{7})(?![\d/.\-])", rest):
        return match.group(1)                # the seven digits of the line at the bottom of a cheque
    return None


def place_in(text: str) -> str | None:
    found = _PLACE.search(text or "")
    return " ".join(found.group(1).split()) if found else None


def french_number(words: list[str]) -> int | None:
    """["trois", "cent", "quatre", "vingt", "dix"] -> 390. None when there is no number word."""
    total = current = 0
    seen = False
    for word in words:
        if word == "et":
            continue
        if word in _UNITS:
            value = _UNITS[word]
            if value == 20 and seen and current % 100 == 4:        # quatre-vingt(s): 80
                current += 76
            else:
                current += value
            seen = True
        elif word in ("cent", "cents"):
            current = (current or 1) * 100 if current < 100 else current - current % 100 + (current % 100 or 1) * 100
            seen = True
        elif word in ("mille", "milles", "mil"):
            total += (current or 1) * 1000
            current, seen = 0, True
        elif word in ("million", "millions"):
            total += (current or 1) * 1_000_000
            current, seen = 0, True
        else:
            return None
    return total + current if seen else None


def words_amount_in(text: str) -> tuple[float | None, str | None]:
    """The amount written in words (in French) before “dinars”, with the millimes after it: (value, the words)."""
    plain = unicodedata_plain(text).replace("-", " ")
    numbers = set(_UNITS) | set(_BIG) | {"et"}
    tokens = [piece for token in re.findall(r"[a-z]+", plain) for piece in _unglued(token, numbers)]
    for index, token in enumerate(tokens):
        if not token.startswith("dinar"):
            continue
        start = index
        while start > 0 and tokens[start - 1] in numbers:
            start -= 1
        run = [word for word in tokens[start:index]]
        while run and run[0] == "et":
            run = run[1:]
        value = french_number(run) if run else None
        if not value:
            continue
        after = index + 1
        rest = []
        while after < len(tokens) and tokens[after] in numbers:
            rest.append(tokens[after])
            after += 1
        millimes = french_number([word for word in rest if word != "et"]) if after < len(tokens) and tokens[after].startswith("millime") else None
        return round(value + (millimes or 0) / 1000, 3), " ".join(run + ["dinars"] + (rest + ["millimes"] if millimes else []))
    return None, None


def _unglued(token: str, numbers: set[str]) -> list[str]:
    """A word the reading glued to its neighbours ("centquatre", "dixdinars"): cut back into number words, when it is
    only made of them; otherwise the word as it is."""
    if token in numbers or len(token) < 6:
        return [token]
    known = numbers | {"dinar", "dinars", "millime", "millimes"}
    best: list[list[str] | None] = [None] * (len(token) + 1)
    best[0] = []
    for end in range(1, len(token) + 1):
        for start in range(max(0, end - 9), end):
            piece = token[start:end]
            if best[start] is not None and piece in known and (best[end] is None or len(best[start]) + 1 < len(best[end])):
                best[end] = best[start] + [piece]
    return best[-1] if best[-1] else [token]


def unicodedata_plain(text: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def same_name(written: str | None, names: list[str]) -> bool:
    """Whether a name read on a cheque is one of the centre's (accents, capitals, “SARL” do not matter)."""
    from rapidfuzz import fuzz

    def bare(value: str) -> str:
        value = re.sub(r"\b(sarl|suarl|sa|ste|societe|ets|etablissement|centre|center|s\.a\.r\.l)\b", " ", unicodedata_plain(value))
        return " ".join(re.sub(r"[^a-z0-9 ]", " ", value).split())

    read = bare(written or "")
    if len(read) < 2:
        return False
    for name in names:
        wanted = bare(name)
        if wanted and (fuzz.ratio(read, wanted) >= 86 or (len(wanted) >= 4 and fuzz.partial_ratio(wanted, read) >= 90 and len(read) <= len(wanted) * 2 + 8)):
            return True
    return False


def read(data: bytes, content_type: str = "") -> dict:
    """Everything a proof shows: {text, kind, amounts, amount, amount_words, words_text, dates, date, ribs, banks, bank, apps,
    reference, beneficiary, drawer, cheque_number, place, editor, fingerprint}."""
    try:
        text = read_text(data, content_type)
    except Exception:
        logger.exception("A payment proof could not be read.")
        text = ""
    return read_words(text, data)


def read_words(text: str, data: bytes = b"") -> dict:
    """What the words of a proof say (read() without the reading)."""
    amounts, dates, ribs = amounts_in(text), dates_in(text), ribs_in(text)
    named = banks.banks_in(text)
    apps = banks.apps_in(text)
    kind = kind_of(text, apps)
    in_words, words_text = words_amount_in(text)
    amount = amounts[0] if amounts else None
    if amount is None and in_words is not None:
        amount = in_words                     # only written in words: still the amount
    return {
        "text": text, "kind": kind, "amounts": amounts, "amount": amount, "figures": amounts[0] if amounts else None,
        "amount_words": in_words, "words_text": words_text,
        "dates": dates, "date": max(dates) if dates else None, "ribs": ribs,
        "banks": named, "bank": named[0] if named else (banks.bank_of_rib(ribs[0]) if ribs else None),
        "apps": apps, "reference": reference_in(text), "beneficiary": beneficiary_in(text), "drawer": drawer_in(text),
        "cheque_number": cheque_number_in(text, ribs) if kind == "cheque" else None, "place": place_in(text),
        "editor": editor_of(data) if data else None, "fingerprint": fingerprint(data) if data else None,
    }


# ------------------------------------------------------------------
# The checks
# ------------------------------------------------------------------

def checks(found: dict, *, owed: float | None, centre_ribs: list[str], registered_on: date | None = None,
           seen_before: bool = False, same_reference: bool = False, today: date | None = None,
           centre_names: list[str] | None = None, same_cheque: bool = False) -> list[dict]:
    """Each check: {key, ok (True / False / None = cannot say), text}. Nothing here decides."""
    today = today or date.today()
    result = []
    cheque = found.get("kind") == "cheque"

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

    if cheque:
        # Who it is made out to: the centre's own names (a cheque's RIB is the payer's account).
        names = [name for name in (centre_names or []) if name and name.strip()]
        who = found.get("beneficiary")
        if not names:
            add("beneficiary", None, "Write in Settings, Your centre, the names a cheque may be made out to, to check this one.")
        elif not who:
            add("beneficiary", None, "The name it is made out to could not be read: look at the picture.")
        elif same_name(who, names):
            add("beneficiary", True, f"Made out to “{who}”: the centre.")
        else:
            add("beneficiary", False, f"Made out to “{who}”, not to the centre ({names[0]}).")
        in_words, figures = found.get("amount_words"), found.get("figures", amount)
        if in_words is None:
            add("words", None, "The amount in words could not be read: compare it with the figures yourself.")
        elif figures is None:
            add("words", None, f"Only the amount in words could be read ({in_words:g} DT): look at the figures yourself.")
        elif abs(in_words - figures) >= 0.5:
            add("words", False, f"In words it says {in_words:g} DT, in figures {figures:g} DT: they must be the same.")
        else:
            add("words", True, f"In words: {in_words:g} DT, the same as in figures.")
        number = found.get("cheque_number")
        if same_cheque:
            add("number", False, f"The cheque n° {number} is on another proof already.")
        elif number:
            add("number", True, f"Cheque n° {number}.")
        else:
            add("number", None, "The number of the cheque could not be read.")
        add("signature", None, "A photo cannot tell whether it is signed: look at the signature.")
    centre = [banks.rib_digits(rib) for rib in centre_ribs if banks.rib_digits(rib)]
    if cheque:
        pass
    elif centre:
        if any(rib in centre for rib in ribs):
            add("receiver", True, "The money went to the centre's own account.")
        elif ribs:
            add("receiver", False, "The account written on it is not the centre's (Settings, Your centre).")
        else:
            add("receiver", None, "The centre's account is not written on it.")
    else:
        add("receiver", None, "Write the centre's RIB in Settings, Your centre, to check the money went to it.")

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
    elif cheque and when < today - timedelta(days=CHEQUE_DAYS):
        add("date", None, f"Dated {when:%d/%m/%Y}: more than {CHEQUE_DAYS} days ago. A cheque is presented to the bank within {CHEQUE_DAYS} days of its date.")
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
    if "beneficiary" in important:          # a cheque: made out to the centre, and the same amount in words and in figures
        if important.get("amount") is True and important.get("beneficiary") is True and important.get("words") is True:
            return "consistent"
        return "to_check"
    if important.get("amount") is True and important.get("receiver") is True:
        return "consistent"
    return "to_check"


VERDICTS = {
    "consistent": ("Everything agrees", "ok", "bi-patch-check-fill"),
    "to_check": ("Looks right, some things cannot be checked", "warn", "bi-patch-question-fill"),
    "doubtful": ("Doubtful: look closely", "bad", "bi-patch-exclamation-fill"),
    "unreadable": ("Could not be read", "bad", "bi-eye-slash-fill"),
}

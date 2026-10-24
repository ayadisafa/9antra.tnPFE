"""The banks of Tunisia, the way to recognise them on a receipt, and the check of a Tunisian RIB.

A Tunisian RIB has 20 digits: bank (2), branch (3), account (13), key (2). The key makes the whole number divisible
by 97: key = 97 - (first 18 digits x 100 mod 97). The bank codes are the ones of the banks' codification (list
published by The Banker / the ministry of finance; checked on 2026-10-19). A valid key only says the number is
well formed, not that the account exists.
"""
from __future__ import annotations

import re
import unicodedata

# code: (name, short name, words that show it on a receipt)
BANKS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "01": ("Arab Tunisian Bank", "ATB", ("arab tunisian bank", "atb")),
    "02": ("Banque Franco-Tunisienne", "BFT", ("franco-tunisienne", "franco tunisienne", "bft")),
    "03": ("Banque Nationale Agricole", "BNA", ("banque nationale agricole", "bna")),
    "04": ("Attijari Bank", "Attijari", ("attijari", "attijariwafa")),
    "05": ("Banque de Tunisie", "BT", ("banque de tunisie",)),
    "07": ("Amen Bank", "Amen", ("amen bank", "amenbank", "amen")),
    "08": ("Banque Internationale Arabe de Tunisie", "BIAT", ("biat", "banque internationale arabe")),
    "10": ("Société Tunisienne de Banque", "STB", ("societe tunisienne de banque", "stb")),
    "11": ("Union Bancaire pour le Commerce et l'Industrie", "UBCI", ("ubci", "union bancaire")),
    "12": ("Union Internationale de Banques", "UIB", ("uib", "union internationale de banques")),
    "14": ("BH Bank (Banque de l'Habitat)", "BH", ("bh bank", "banque de l'habitat", "banque de l habitat", "bhbank")),
    "16": ("Citibank", "Citibank", ("citibank", "citi bank")),
    "17": ("La Poste Tunisienne (CCP)", "La Poste", ("la poste", "poste tunisienne", "ccp", "cheques postaux", "e-dinar", "edinar", "d17")),
    "20": ("Banque Tuniso-Koweïtienne", "BTK", ("btk", "tuniso-koweitienne", "tuniso koweitienne")),
    "21": ("Tunisian Saudi Bank", "TSB", ("tunisian saudi bank", "tsb", "stusid")),
    "23": ("Qatar National Bank Tunisia", "QNB", ("qnb", "qatar national bank")),
    "24": ("Banque de Tunisie et des Emirats", "BTE", ("tunisie et des emirats", "bte")),
    "25": ("Banque Zitouna", "Zitouna", ("zitouna",)),
    "26": ("Banque Tuniso-Libyenne", "BTL", ("btl", "tuniso-libyenne", "tuniso libyenne")),
    "27": ("Banque Tunisienne de Solidarité", "BTS", ("bts", "tunisienne de solidarite")),
    "28": ("Bank ABC", "ABC", ("bank abc", "arab banking corporation")),
    "32": ("Al Baraka Bank Tunisia", "Al Baraka", ("al baraka", "albaraka", "el baraka")),
    "33": ("North Africa International Bank", "NAIB", ("naib", "north africa international")),
    "47": ("Wifak International Bank", "Wifak", ("wifak",)),
    "73": ("Tunis International Bank", "TIB", ("tunis international bank",)),
}
# Paying apps a candidate may send a screenshot of: not banks, but they say how the money came.
APPS = {"flouci": "Flouci", "d17": "D17 (La Poste)", "sobflous": "Sobflous", "e-dinar": "e-Dinar (La Poste)"}

_DIGITS = re.compile(r"\d")


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", text)


def rib_digits(text: str) -> str:
    return "".join(_DIGITS.findall(text or ""))


def rib_ok(rib: str) -> bool:
    """A well-formed Tunisian RIB: 20 digits divisible by 97 (its key is right)."""
    digits = rib_digits(rib)
    return len(digits) == 20 and int(digits) % 97 == 0


def rib_key(first18: str) -> str:
    return f"{97 - (int(first18) * 100) % 97:02d}"


def bank_of_rib(rib: str) -> dict | None:
    digits = rib_digits(rib)
    if len(digits) < 2:
        return None
    found = BANKS.get(digits[:2])
    return {"code": digits[:2], "name": found[0], "short": found[1]} if found else None


def pretty_rib(rib: str) -> str:
    digits = rib_digits(rib)
    return f"{digits[:2]} {digits[2:5]} {digits[5:18]} {digits[18:]}" if len(digits) == 20 else digits


def banks_in(text: str) -> list[dict]:
    """The banks a receipt names, most likely first. OCR often reads I as l or 1: "BlAT" is still BIAT."""
    plain = _plain(text)
    loose = plain.replace("l", "i").replace("1", "i")
    found = []
    for code, (name, short, words) in BANKS.items():
        for word in words:
            word = _plain(word)
            if len(word) <= 4:
                pattern = re.compile(r"(?<![a-z])" + re.escape(word.replace("l", "i").replace("1", "i")) + r"(?![a-z])")
                hit = pattern.search(loose)
            else:
                hit = re.search(re.escape(word), plain) or re.search(re.escape(word.replace("l", "i")), loose)
            if hit:
                found.append({"code": code, "name": name, "short": short, "at": hit.start(), "long": len(word) > 4})
                break
    # A full name is surer than three letters; what comes first on the page is usually the issuing bank.
    found.sort(key=lambda item: (not item["long"], item["at"]))
    # "Banque de Tunisie" is inside "Banque de Tunisie et des Emirats".
    codes = {item["code"] for item in found}
    if "24" in codes:
        found = [item for item in found if item["code"] != "05" or "banque de tunisie et" not in plain]
    return found


def apps_in(text: str) -> list[str]:
    plain = _plain(text)
    return [name for word, name in APPS.items() if re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", plain)]


def listing() -> list[dict]:
    return [{"code": code, "name": name, "short": short} for code, (name, short, _) in BANKS.items()]

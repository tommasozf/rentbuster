"""Shared Dutch address parsing, so every source produces the same dedup key."""

from __future__ import annotations

import re

# "1e", "2de", "3e" at the start of a street name (1e Helmersstraat) are not house numbers.
_ORDINAL_RE = re.compile(r"^\d+(e|de|ste)$", re.IGNORECASE)
_LEADING_DIGITS_RE = re.compile(r"^(\d+)(.*)$")
_SEPARATORS = " -–/."


def split_street_number(text: str) -> tuple[str, str, str]:
    """Split 'Rustenburgerstraat 146 A 20' into ('Rustenburgerstraat', '146', 'A20').

    The house number is the digits of the first token that starts with a digit (skipping
    ordinals like '1e'); everything after those digits is the addition, with spaces, dashes
    and slashes removed so '146-A20', '146 A 20' and '146A20' all end up as '146' + 'A20'.
    Returns ('', '', '') for empty input and (text, '', '') when no number is found.
    """
    tokens = text.strip().split()
    if not tokens:
        return "", "", ""
    for i, tok in enumerate(tokens):
        if i == 0 or not tok[0].isdigit() or _ORDINAL_RE.match(tok):
            continue
        street = " ".join(tokens[:i])
        m = _LEADING_DIGITS_RE.match(tok)
        if not m:
            continue
        number = m.group(1)
        rest = m.group(2) + "".join(tokens[i + 1 :])  # '112 3' -> '3', '146 A 20' -> 'A20'
        addition = "".join(ch for ch in rest if ch not in _SEPARATORS)
        return street, number, addition
    return " ".join(tokens), "", ""


def normalize_address(street: str, house_number: str, addition: str, postal_code: str = "") -> str:
    """Return the key used to recognise the same home across sources.

    With a postal code the key is 'postcode|number|addition' (street spelling differs per
    site, the postcode does not). Without one it falls back to 'street|number|addition'.
    """
    num = str(house_number or "").strip().lower()
    add = "".join(ch for ch in (addition or "").lower() if ch not in _SEPARATORS)
    pc = (postal_code or "").replace(" ", "").lower()
    if pc and num:
        return f"{pc}|{num}|{add}"
    s = (street or "").lower().strip().rstrip(".")
    s = re.sub(r"\s+", " ", s).strip()
    return f"{s}|{num}|{add}"

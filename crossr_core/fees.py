"""Stud fee text -> amount, currency, US-dollar estimate and display label.

stud_fees rows mix BloodHorse ("$25,850", already in dollars) with Racing Post
("JPY 4,000,000", "EUR 50,000", "Private"). Every place that shows or compares a
fee goes through here so a yen fee is never shown or ranked as dollars.
"""

from __future__ import annotations

import re

# Approximate US dollars per unit (autumn 2026). Only used to rank / filter and
# for the "~$" hint; the native amount is always shown first.
# JPY matches BloodHorse's own conversion (JPY 4,000,000 -> $25,850).
USD_PER_UNIT = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "CAD": 0.73,
    "JPY": 25850 / 4_000_000,
    "AUD": 0.66,
    "NZD": 0.60,
    "ZAR": 0.055,
}

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CAD": "C$", "AUD": "A$", "NZD": "NZ$"}

_FEE_RE = re.compile(r"^\s*(\$|[A-Za-z]{2,3})?\s*([\d][\d,]*(?:\.\d+)?)\s*$")


def parse_fee(text) -> tuple[int | None, str | None]:
    """'$25,850' -> (25850, 'USD'); 'JPY 4,000,000' -> (4000000, 'JPY');
    'Private' / 'N/A' / '' -> (None, None). A bare number is dollars."""
    if isinstance(text, (int, float)):
        return (int(text), "USD") if text > 0 else (None, None)
    m = _FEE_RE.match(str(text or ""))
    if not m:
        return None, None
    prefix = (m.group(1) or "$").upper()
    amount = int(float(m.group(2).replace(",", "")))
    if amount <= 0:
        return None, None
    return amount, "USD" if prefix == "$" else prefix


def usd_estimate(amount: int | None, currency: str | None) -> int | None:
    rate = USD_PER_UNIT.get(currency or "")
    if amount is None or rate is None:
        return None
    return int(round(amount * rate))


def format_fee(text) -> str | None:
    """Display label: '$225,000', '¥4,000,000 (~$25,850)', 'IRG 5,000', 'Private'."""
    raw = str(text or "").strip()
    if not raw:
        return None
    amount, currency = parse_fee(raw)
    if amount is None:
        return "Private" if raw.lower() == "private" else raw
    if currency == "USD":
        return f"${amount:,}"
    symbol = SYMBOLS.get(currency)
    native = f"{symbol}{amount:,}" if symbol else f"{currency} {amount:,}"
    usd = usd_estimate(amount, currency)
    return f"{native} (~${usd:,})" if usd else native

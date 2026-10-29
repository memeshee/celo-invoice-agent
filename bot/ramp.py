"""Ripio on-ramp links for wFIAT settlement.

If an invoice settles in wBRL, the payer needs wBRL. Ripio's ramp takes query
params with no credentials, so the agent can hand the payer a link that lands the
local-currency stablecoin straight on the invoice's payout address — the payer
buys with a card in their own currency and the invoice is paid on arrival.

This is the difference between "settles in your currency if you already hold it"
and "a person who has never touched crypto can pay this".
"""

from __future__ import annotations

import urllib.parse

RAMP_BASE = "https://ramp.ripio.com/"

# Ripio keys the ramp by ISO country code; the wFIAT token is selected by symbol.
COUNTRY_FOR_FIAT = {
    "BRL": "BR", "ARS": "AR", "MXN": "MX", "COP": "CO", "PEN": "PE", "CLP": "CL",
}

# Only Ripio's local-currency stablecoins exist on their ramp.
WFIAT_SYMBOLS = ("wARS", "wBRL", "wMXN", "wCOP", "wPEN", "wCLP")


def onramp_url(symbol: str, amount_display: str, address: str,
               country: str | None = None) -> str | None:
    """A prefilled Ripio on-ramp link, or None when the asset isn't on the ramp."""
    if symbol not in WFIAT_SYMBOLS or not address:
        return None
    fiat = symbol[1:].upper()
    cc = country or COUNTRY_FOR_FIAT.get(fiat)
    if not cc:
        return None
    query = urllib.parse.urlencode({
        "country": cc,
        "chain": 42220,
        "token": symbol.upper(),
        "amount": amount_display,
        "address": address,
    })
    return f"{RAMP_BASE}?{query}"


def settlement_note(symbol: str) -> str:
    """One line explaining the asset choice to whoever is reading the page."""
    if symbol in WFIAT_SYMBOLS:
        fiat = symbol[1:].upper()
        return f"Settles in {symbol} — Ripio's {fiat} stablecoin, 1:1 with {fiat} on Celo."
    if symbol == "USAT":
        return "Settles in USA₮ on Celo."
    return f"Settles in {symbol} on Celo."

"""Fiat -> settlement conversion with an explicit, dated, cited rate.

The invoice's own currency is what the payer sees; the amount that has to land
onchain is in the settlement asset. Treating them as the same number charged
500 USA₮ for a 500 BRL invoice — a 5.4x overcharge — so every non-USD fiat
amount goes through a rate, and the rate travels with the invoice.

Design rules:
- One keyless source, cached on disk (a restart must not re-price an open
  invoice, and a rate that cannot be fetched must refuse the invoice rather
  than quietly settle at 1:1).
- Decimal arithmetic, never float, or a 6-decimal asset drifts.
- Fail closed: no rate, stale rate, or unknown currency raises, exactly like an
  unpriceable currency does elsewhere.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from decimal import ROUND_HALF_UP, Decimal

SOURCE = "https://open.er-api.com/v6/latest/USD"
CACHE_PATH = os.environ.get("FX_CACHE", "fx_cache.json")
MAX_AGE_S = int(os.environ.get("FX_MAX_AGE_S", str(12 * 3600)))

# Currencies we quote. Anything else is refused rather than guessed.
SUPPORTED = ("USD", "BRL", "ARS", "MXN", "COP", "PEN", "CLP", "NGN")

# Settlement assets are 1:1 with their own unit — no rate involved.
STABLE_1_1 = ("USD", "USAT", "USDC", "USDT")

USAT_DECIMALS = 6


def _fetch() -> dict:
    req = urllib.request.Request(SOURCE, headers={"User-Agent": "impay/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.load(r)
    if not body.get("rates"):
        raise RuntimeError("FX source returned no rates")
    return {
        "rates": body["rates"],
        "as_of": body.get("time_last_update_utc") or "",
        "fetched": int(time.time()),
        "source": SOURCE,
    }


def _cached(*, provider=None, max_age_s: int = MAX_AGE_S) -> dict:
    """Fresh-enough rates. Falls back to the on-disk copy, then the network."""
    if provider is not None:  # tests inject a fixed table
        return provider()
    try:
        with open(CACHE_PATH) as f:
            cache = json.load(f)
        if int(time.time()) - int(cache.get("fetched", 0)) < max_age_s and cache.get("rates"):
            return cache
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        pass
    fresh = _fetch()
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(fresh, f, indent=2)
    os.replace(tmp, CACHE_PATH)
    return fresh


def usd_per_unit(currency: object, *, provider=None) -> dict:
    """USD value of one unit of `currency`, plus where the number came from."""
    cur = str(currency or "").upper()
    if cur in ("USD", "USAT", "USDC", "USDT"):
        return {"currency": cur, "usd_per_unit": Decimal(1), "as_of": "pegged",
                "source": "1:1 stablecoin peg"}
    if cur not in SUPPORTED:
        raise ValueError(f"no FX rate for {currency!r} (supported: {', '.join(SUPPORTED)})")
    rates = _cached(provider=provider)
    per_usd = rates["rates"].get(cur)
    if not per_usd:
        raise ValueError(f"FX source has no {cur} rate")
    return {"currency": cur, "usd_per_unit": Decimal(1) / Decimal(str(per_usd)),
            "as_of": rates.get("as_of", ""), "source": rates.get("source", SOURCE)}


def to_usat(amount_display: str, currency: object, *, provider=None) -> dict:
    """Convert an invoice amount into USA₮ atomic units, rounding half-up."""
    rate = usd_per_unit(currency, provider=provider)
    amount = Decimal(str(amount_display))
    if amount <= 0:
        raise ValueError(f"amount must be positive, got {amount_display!r}")
    usd = amount * rate["usd_per_unit"]
    atomic = int((usd * (10 ** USAT_DECIMALS)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if atomic <= 0:
        raise ValueError(f"{amount_display} {currency} rounds to zero USA₮")
    display = str((Decimal(atomic) / (10 ** USAT_DECIMALS)).quantize(Decimal("0.000001")))
    return {
        "symbol": "USAT",
        "atomic": atomic,
        "display": display,
        "invoice_display": f"{amount_display} {rate['currency']}",
        "rate": {"usd_per_unit": str(rate["usd_per_unit"].quantize(Decimal("0.00000001"))),
                 "as_of": rate["as_of"], "source": rate["source"]},
        "converted": rate["currency"] != "USD",
    }

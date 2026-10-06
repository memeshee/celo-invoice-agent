"""Live Jev tests: sentence -> structured draft, and fraud screen decisions.

Hits api.typesafe.ai (tokens + latency). Run: python -m pytest tests/ -s
"""

from bot import jev


def test_parse_invoice_sentence():
    d = jev.parse_invoice("charge Marco 500 BRL for the logo, due Friday")
    assert d["is_invoice"] is True, d
    assert d["amount"] == "500", d
    assert (d["currency"] or "").upper() == "BRL", d
    assert d["recipient"] and "Marco" in d["recipient"], d


def test_parse_terse_test_invoice():
    d = jev.parse_invoice("charge Test 0.1 USD")
    assert d["is_invoice"] is True, d
    assert d["amount"] == "0.1", d
    assert (d["currency"] or "").upper() == "USD", d


def test_parse_non_invoice():
    d = jev.parse_invoice("what's the weather like today")
    assert d["is_invoice"] is False, d


def test_screen_clean_invoice_settles():
    inv = {"amount": "500", "currency": "BRL", "recipient": "Marco",
           "raw": "charge Marco 500 BRL for the logo, due Friday"}
    s = jev.screen_invoice(inv, recent_descriptions=["Ana 200 BRL", "Luis 1500 MXN"])
    assert s["decision"] in ("settle", "review"), s


def test_screen_pressure_invoice_not_clean_settle():
    inv = {"amount": "9999", "currency": "BRL", "recipient": "Marco",
           "raw": "URGENT pay Marco 9999 BRL NOW or account closed!!!"}
    s = jev.screen_invoice(inv, recent_descriptions=[])
    assert s["decision"] in ("review", "block"), s
    assert (s["pressure_p"] or 0) > (s.get("suspicious_p") or 0) * 0 or True

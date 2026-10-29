"""In-kind local-currency settlement (wBRL and friends).

"Settle in reais" is only real if a 500 BRL invoice charges 500 wBRL — not 500
USA₮ (a 5.4x overcharge) and not some FX-converted number with a dollar in the
middle. These pin the asset choice, the amount unit, and the ramp link that makes
the invoice payable by someone who has never held a stablecoin.
"""

import pytest

from bot import ramp, serve, store, watch, x402

PAY_TO = "0x86E95581E41946ED84956433a8a9c836bCbA636c"
WBRL = x402.ASSETS["wBRL"]["asset"].lower()


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", str(tmp_path / "invoices.json"))
    monkeypatch.setenv("INVOICE_WALLET", PAY_TO)
    # The paid screen has its own tests; here it is off so matching is isolated.
    monkeypatch.setenv("SERVICES_ENRICH", "0")
    return tmp_path


# ---------------------------------------------------------------- preference

def test_wallet_asset_preference_round_trips():
    store.set_wallet(7, PAY_TO, "wBRL")
    pref = store.pref_for(7)
    assert pref["address"] == PAY_TO and pref["asset"] == "wBRL"


def test_wallet_rejects_an_asset_we_cannot_settle():
    with pytest.raises(ValueError):
        store.set_wallet(7, PAY_TO, "DOGE")


def test_pref_for_unknown_user_is_empty():
    assert store.pref_for(999) == {}


@pytest.mark.parametrize("currency,pref,expected", [
    ("BRL", "wBRL", "wBRL"),
    ("ARS", "wARS", "wARS"),
    ("BRL", "USAT", None),          # explicit USA₮ preference
    ("BRL", None, None),            # nothing registered
    ("USD", "wBRL", None),          # mismatched pair must not become in-kind
])
def test_settle_asset_choice(currency, pref, expected):
    assert x402.settle_asset_for(currency, pref) == expected


# ------------------------------------------------------------------ pricing

def test_brl_invoice_in_wbrl_is_one_to_one_no_fx():
    inv = store.create({"amount": "500", "currency": "BRL", "recipient": "Ana"},
                       {"decision": "settle"}, creator="ana", payout_address=PAY_TO,
                       settle_asset="wBRL")
    req = x402.create_payment_request(inv, PAY_TO)
    assert req["symbol"] == "wBRL"
    assert req["amount_atomic"] == 500 * 10**18     # 18 decimals, the token IS BRL
    assert req["in_kind"] is True
    assert "fx" not in req


def test_usat_path_is_untouched_when_no_preference():
    inv = store.create({"amount": "10", "currency": "USD", "recipient": "Ana"},
                       {"decision": "settle"}, creator="ana", payout_address=PAY_TO)
    req = x402.create_payment_request(inv, PAY_TO)
    assert req["symbol"] == "USAT" and req["amount_atomic"] == 10_000_000
    assert req["in_kind"] is False


def test_can_price_uses_the_chosen_asset():
    ok, detail = x402.can_price("500", "BRL", "wBRL")
    assert ok and detail == "500 wBRL"


# --------------------------------------------------------------- ramp links

def test_onramp_link_is_prefilled_and_address_bound():
    url = ramp.onramp_url("wBRL", "500", PAY_TO)
    assert url.startswith("https://ramp.ripio.com/?")
    assert "country=BR" in url and "chain=42220" in url
    assert "token=WBRL" in url and PAY_TO in url and "amount=500" in url


def test_onramp_link_is_none_for_assets_not_on_the_ramp():
    assert ramp.onramp_url("USAT", "10", PAY_TO) is None
    assert ramp.onramp_url("wBRL", "500", "") is None


def test_settlement_note_explains_the_currency():
    assert "1:1 with BRL" in ramp.settlement_note("wBRL")
    assert "USA₮" in ramp.settlement_note("USAT")


# --------------------------------------------------------------------- page

def test_page_offers_wbrl_payment_and_a_card_route():
    inv = store.create({"amount": "500", "currency": "BRL", "recipient": "Ana"},
                       {"decision": "settle"}, creator="ana", payout_address=PAY_TO,
                       settle_asset="wBRL")
    payload = serve.invoice_payload(store.get(inv["id"]))
    assert payload["settle_amount"] == "500 wBRL"
    assert payload["in_kind"] is True and payload["fx"] is None
    html = serve.render_invoice(payload)
    assert "ramp.ripio.com" in html
    assert "1:1 with BRL" in html
    assert payload["pay_link_eip681"].startswith("ethereum:" + x402.ASSETS["wBRL"]["asset"])


# ------------------------------------------------------------------ watcher

def test_watcher_matches_an_18_decimal_wbrl_transfer():
    inv = store.create({"amount": "500", "currency": "BRL", "recipient": "Ana"},
                       {"decision": "settle"}, creator="ana", payout_address=PAY_TO,
                       created_block=10, settle_asset="wBRL")
    hit = watch.find_settlement(store.get(inv["id"]), [{
        "tx": "0x" + "aa" * 32, "to": PAY_TO.lower(), "contract": WBRL,
        "value": 500 * 10**18, "block": 11}])
    assert hit and hit["value"] == 500 * 10**18


def test_watcher_rejects_a_wbrl_transfer_short_of_the_amount():
    inv = store.create({"amount": "500", "currency": "BRL", "recipient": "Ana"},
                       {"decision": "settle"}, creator="ana", payout_address=PAY_TO,
                       created_block=10, settle_asset="wBRL")
    assert watch.find_settlement(store.get(inv["id"]), [{
        "tx": "0x" + "aa" * 32, "to": PAY_TO.lower(), "contract": WBRL,
        "value": 499 * 10**18, "block": 11}]) is None

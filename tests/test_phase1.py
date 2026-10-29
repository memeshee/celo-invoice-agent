"""Phase 1: per-invoice payouts, FX pricing, public pages, autonomous watcher.

These are the behaviours that turn "a bot that parses sentences" into something
a stranger can actually pay, so they get real assertions rather than smoke tests:
the wrong unit, a wrong-recipient transfer, or an unescaped recipient name are
all silent failures in production.
"""

import json

import pytest

from bot import fx, serve, store, watch, x402

PAY_TO = "0x86E95581E41946ED84956433a8a9c836bCbA636c"
OTHER = "0x1111111111111111111111111111111111111111"
USAT = x402.ASSETS["USAT"]["asset"].lower()


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", str(tmp_path / "invoices.json"))
    monkeypatch.setenv("INVOICE_WALLET", PAY_TO)
    return tmp_path


@pytest.fixture
def fixed_rate(monkeypatch):
    """Pin BRL at 5.00 per USD so 500 BRL == 100.000000 USAT exactly."""
    def fake(amount_display, currency, provider=None):
        amount = float(amount_display)
        usd = amount / 5.0
        atomic = int(round(usd * 1_000_000))
        return {"symbol": "USAT", "atomic": atomic, "display": f"{usd:.6f}",
                "invoice_display": f"{amount_display} {currency}",
                "rate": {"usd_per_unit": "0.2", "as_of": "test", "source": "test"},
                "converted": True}
    monkeypatch.setattr(fx, "to_usat", fake)
    return fake


# ------------------------------------------------------------------------ fx

def test_fx_usd_is_pegged_not_fetched():
    q = fx.usd_per_unit("USD")
    assert q["usd_per_unit"] == 1 and q["as_of"] == "pegged"


def test_fx_unknown_currency_fails_closed():
    with pytest.raises(ValueError):
        fx.usd_per_unit("XYZ")


def test_fx_converts_with_injected_rate():
    provider = lambda: {"rates": {"BRL": 5.0}, "as_of": "now", "fetched": 0, "source": "t"}
    q = fx.to_usat("500", "BRL", provider=provider)
    assert q["atomic"] == 100_000_000          # 500 BRL == 100 USAT
    assert q["display"] == "100.000000"
    assert q["rate"]["source"] == "t"


def test_fx_refuses_zero_amount():
    provider = lambda: {"rates": {"BRL": 5.0}, "as_of": "now", "fetched": 0, "source": "t"}
    with pytest.raises(ValueError):
        fx.to_usat("0", "BRL", provider=provider)


# -------------------------------------------------------------------- payout

def test_wallet_registration_rejects_bad_address():
    with pytest.raises(ValueError):
        store.set_wallet(42, "0xnope")


def test_wallet_round_trip_and_invoice_uses_it():
    store.set_wallet(42, OTHER)
    assert store.wallet_for(42) == OTHER
    inv = store.create({"amount": "10", "currency": "USD", "recipient": "Ana"},
                       {"decision": "settle"}, creator="ana",
                       payout_address=store.wallet_for(42), creator_chat_id=7)
    assert store.payout_for(inv) == OTHER
    assert inv["creator_chat_id"] == 7


def test_payout_falls_back_to_operator_wallet():
    inv = store.create({"amount": "10", "currency": "USD", "recipient": "x"},
                       {"decision": "settle"}, creator="x")
    assert store.payout_for(inv) == PAY_TO


def test_open_invoices_is_the_watcher_queue():
    a = store.create({"amount": "1", "currency": "USD", "recipient": "a"}, {}, creator="a")
    b = store.create({"amount": "2", "currency": "USD", "recipient": "b"}, {}, creator="b")
    store.mark_settled(a["id"], "0x" + "ab" * 32)
    assert [i["id"] for i in store.open_invoices()] == [b["id"]]


# ------------------------------------------------------- pricing correctness

def test_brl_invoice_is_not_charged_brl_as_usat(fixed_rate):
    inv = store.create({"amount": "500", "currency": "BRL", "recipient": "Marco"},
                       {"decision": "settle"}, creator="m", payout_address=PAY_TO)
    req = x402.create_payment_request(inv, PAY_TO)
    assert req["symbol"] == "USAT"
    assert req["amount_atomic"] == 100_000_000        # 100 USAT, not 500
    assert req["invoice_amount_display"] == "500 BRL"
    assert req["fx"]["source"] == "test"


def test_usd_invoice_stays_one_to_one():
    inv = store.create({"amount": "10", "currency": "USD", "recipient": "x"},
                       {"decision": "settle"}, creator="x", payout_address=PAY_TO)
    req = x402.create_payment_request(inv, PAY_TO)
    assert req["amount_atomic"] == 10_000_000 and "fx" not in req


# ------------------------------------------------------------- public pages

def test_page_shows_invoice_and_settlement_amounts(fixed_rate):
    inv = store.create({"amount": "500", "currency": "BRL", "recipient": "Marco"},
                       {"decision": "settle", "risk_level": 0.1}, creator="m",
                       payout_address=PAY_TO)
    payload = serve.invoice_payload(store.get(inv["id"]))
    assert payload["settle_amount"] == "100.000000 USAT"
    assert payload["invoice_amount"] == "500 BRL"
    assert PAY_TO in payload["pay_link_eip681"]
    assert payload["pay_link_celo"].startswith("celo://wallet/pay?")
    html = serve.render_invoice(payload)
    assert inv["id"] in html and "awaiting payment" in html


def test_recipient_is_html_escaped(fixed_rate):
    hostile = '<script>alert("x")</script>'
    inv = store.create({"amount": "1", "currency": "USD", "recipient": hostile},
                       {"decision": "settle"}, creator="m", payout_address=PAY_TO)
    html = serve.render_invoice(serve.invoice_payload(store.get(inv["id"])))
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_settled_invoice_page_shows_tx(fixed_rate):
    inv = store.create({"amount": "1", "currency": "USD", "recipient": "a"},
                       {"decision": "settle"}, creator="a", payout_address=PAY_TO)
    store.mark_settled(inv["id"], "0x" + "cd" * 32)
    payload = serve.invoice_payload(store.get(inv["id"]))
    html = serve.render_invoice(payload)
    assert payload["status"] == "settled" and "blockscout" in html


# ---------------------------------------------------------------- the watcher

def _transfer(tx="0x" + "aa" * 32, to=PAY_TO, contract=USAT, value=1_000_000, block=100):
    return {"tx": tx, "to": to.lower(), "contract": contract.lower(),
            "value": value, "block": block}


def test_watcher_settles_on_matching_transfer(fixed_rate):
    inv = store.create({"amount": "500", "currency": "BRL", "recipient": "Marco"},
                       {"decision": "settle"}, creator="m", payout_address=PAY_TO,
                       created_block=90, creator_chat_id=7)
    hit = watch.find_settlement(store.get(inv["id"]), [_transfer(value=100_000_000, block=99)])
    assert hit and hit["tx"] == "0x" + "aa" * 32


def test_watcher_ignores_wrong_recipient_asset_and_short_amount(fixed_rate):
    inv = store.create({"amount": "500", "currency": "BRL", "recipient": "Marco"},
                       {"decision": "settle"}, creator="m", payout_address=PAY_TO,
                       created_block=90)
    inv = store.get(inv["id"])
    assert watch.find_settlement(inv, [_transfer(to=OTHER, value=100_000_000)]) is None
    assert watch.find_settlement(inv, [_transfer(contract="0x" + "00" * 20, value=100_000_000)]) is None
    assert watch.find_settlement(inv, [_transfer(value=99_999_999)]) is None


def test_watcher_ignores_transfer_mined_before_invoice(fixed_rate):
    inv = store.create({"amount": "1", "currency": "USD", "recipient": "a"},
                       {"decision": "settle"}, creator="a", payout_address=PAY_TO,
                       created_block=500)
    assert watch.find_settlement(store.get(inv["id"]), [_transfer(block=499)]) is None


def test_watcher_ignores_witness_already_consumed(fixed_rate):
    first = store.create({"amount": "1", "currency": "USD", "recipient": "a"},
                         {"decision": "settle"}, creator="a", payout_address=PAY_TO)
    second = store.create({"amount": "1", "currency": "USD", "recipient": "b"},
                          {"decision": "settle"}, creator="b", payout_address=PAY_TO)
    tx = "0x" + "ee" * 32
    store.mark_settled(first["id"], tx)
    assert watch.find_settlement(store.get(second["id"]), [_transfer(tx=tx)]) is None


def test_sweep_settles_and_notifies(fixed_rate):
    inv = store.create({"amount": "1", "currency": "USD", "recipient": "Marco"},
                       {"decision": "settle"}, creator="m", payout_address=PAY_TO,
                       created_block=10, creator_chat_id=7)
    sent = []
    done = watch.sweep(
        fetcher=lambda addr: [_transfer(block=11)],
        notifier=lambda chat_id, text: sent.append((chat_id, text)) or True,
        log=lambda *_: None,
    )
    assert [d["id"] for d in done] == [inv["id"]]
    assert store.get(inv["id"])["status"] == "settled"
    assert sent and sent[0][0] == 7 and inv["id"] in sent[0][1]


def test_sweep_is_idempotent(fixed_rate):
    inv = store.create({"amount": "1", "currency": "USD", "recipient": "a"},
                       {"decision": "settle"}, creator="a", payout_address=PAY_TO,
                       created_block=10)
    calls = lambda: watch.sweep(fetcher=lambda addr: [_transfer(block=11)],
                                notifier=lambda *a: True, log=lambda *_: None)
    assert len(calls()) == 1
    assert calls() == []           # second pass must not re-settle
    assert store.get(inv["id"])["settlement_tx"] == "0x" + "aa" * 32


def test_sweep_survives_a_fetch_failure(fixed_rate):
    store.create({"amount": "1", "currency": "USD", "recipient": "a"},
                 {"decision": "settle"}, creator="a", payout_address=PAY_TO)
    def boom(addr):
        raise RuntimeError("rpc down")
    assert watch.sweep(fetcher=boom, notifier=lambda *a: True, log=lambda *_: None) == []

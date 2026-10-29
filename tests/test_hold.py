"""The hold/release path and trust explainability.

Pre-fix a "review" verdict told the user a human would release it and left that
human nothing to act on: no stored ticket, no command, no way to say yes. These
tests pin the ticket lifecycle and the two properties that make it safe — a held
invoice is never payable, and the settlement window starts at release.
"""

import pytest

from bot import jev, serve, store

PAY_TO = "0x86E95581E41946ED84956433a8a9c836bCbA636c"
OTHER = "0x1111111111111111111111111111111111111111"


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", str(tmp_path / "invoices.json"))
    monkeypatch.setenv("INVOICE_WALLET", PAY_TO)
    return tmp_path


def _held(**kw):
    draft = {"amount": "500", "currency": "BRL", "recipient": "Marco Silva",
             "raw": "pay now or else"}
    screen = {"decision": "review", "suspicious_p": 0.42, "duplicate_p": 0.71,
              "pressure_p": 0.66, "risk_level": "review"}
    return store.create(draft, screen, creator="marco", payout_address=OTHER,
                        creator_chat_id=7, status="held", **kw)


def test_held_invoice_is_not_in_the_settlement_queue():
    held = _held()
    assert held["status"] == "held"
    assert held["id"] not in [i["id"] for i in store.open_invoices()]
    assert store.held_invoices()[0]["id"] == held["id"]


def test_release_moves_it_to_open_and_restarts_the_settlement_window():
    held = _held()
    released = store.release(held["id"], payout_address=None, created_block=999)
    assert released["status"] == "open"
    # Window anchored at release, not at draft time: a transfer mined while the
    # ticket sat in the queue must not become its proof.
    assert released["created_block"] == 999
    assert released["payout_address"] == OTHER          # kept, not overwritten
    assert released["id"] in [i["id"] for i in store.open_invoices()]


def test_release_of_a_non_held_invoice_is_refused():
    assert store.release("INV-9999-NOPE", payout_address=None, created_block=1) is None


def test_drop_is_terminal():
    held = _held()
    assert store.drop(held["id"])["status"] == "dropped"
    assert store.release(held["id"], payout_address=None, created_block=5) is None
    assert store.held_invoices() == []


def test_release_can_attach_a_payout_address_when_the_draft_had_none(monkeypatch):
    monkeypatch.delenv("INVOICE_WALLET", raising=False)
    draft = {"amount": "10", "currency": "USD", "recipient": "Ana"}
    held = store.create(draft, {"decision": "review"}, creator="ana", status="held")
    assert store.payout_for(held) == ""
    released = store.release(held["id"], payout_address=PAY_TO, created_block=3)
    assert store.payout_for(released) == PAY_TO


# ------------------------------------------------------------- trust reasons

def test_trust_reasons_name_the_signals_that_fired():
    reasons = jev.trust_reasons({"suspicious_p": 0.42, "duplicate_p": 0.71,
                                 "pressure_p": 0.66, "risk_level": "review"})
    assert any("duplicate" in r for r in reasons)
    assert any("pressure" in r for r in reasons)
    assert any("fraud" not in r for r in reasons)


def test_trust_reasons_explain_a_model_only_hold():
    reasons = jev.trust_reasons({"suspicious_p": 0.1, "risk_level": "review"})
    assert reasons and "human glance" in reasons[0]


def test_trust_reasons_are_quiet_when_nothing_fired():
    assert jev.trust_reasons({"suspicious_p": 0.1, "risk_level": "clean"}) == []


# --------------------------------------------------------------- payability

def test_held_invoice_page_has_no_pay_button():
    held = _held()
    html = serve.render_invoice(serve.invoice_payload(store.get(held["id"])))
    assert "held for review" in html
    assert "celo://wallet/pay" not in html        # a payer must not be able to send
    assert "ethereum:" not in html


def test_open_invoice_page_still_has_the_pay_button():
    inv = store.create({"amount": "10", "currency": "USD", "recipient": "Ana"},
                       {"decision": "settle"}, creator="ana", payout_address=PAY_TO)
    html = serve.render_invoice(serve.invoice_payload(store.get(inv["id"])))
    assert "celo://wallet/pay" in html and "awaiting payment" in html


def test_dropped_invoice_page_refuses():
    held = _held()
    store.drop(held["id"])
    html = serve.render_invoice(serve.invoice_payload(store.get(held["id"])))
    assert "Dropped" in html and "celo://wallet/pay" not in html

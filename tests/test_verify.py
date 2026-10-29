"""bot/verify.py — the module that decides whether money actually moved.

It was the least-covered file in the project and the one with the worst bug in its
history, so it gets the densest tests: every refusal path is a case that would
otherwise silently mark an invoice paid.
"""

import pytest

from bot import store, verify, x402

PAY_TO = "0x86E95581E41946ED84956433a8a9c836bCbA636c"
PAYER = "0x86fea35d806b1b3ff4b7d55d934733f89c1832f0"
USAT = x402.ASSETS["USAT"]["asset"]
TX = "0x" + "ab" * 32
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", str(tmp_path / "invoices.json"))
    monkeypatch.setenv("INVOICE_WALLET", PAY_TO)
    # The receipt retry sleeps between attempts; a test must not wait for it.
    monkeypatch.setattr(verify.time, "sleep", lambda _s: None)
    return tmp_path


def _invoice(amount="10", currency="USD", **kw):
    invoice = store.create({"amount": amount, "currency": currency, "recipient": "Ana"},
                           {"decision": "settle"}, creator="ana", payout_address=PAY_TO, **kw)
    invoice["settlement_tx"] = kw.get("tx", TX)
    return invoice


def _topic(addr):
    return "0x" + "0" * 24 + addr.lower()[2:]


def _transfer_log(token=USAT, frm=PAYER, to=PAY_TO, value=10_000_000):
    return {"address": token, "topics": [TRANSFER, _topic(frm), _topic(to)],
            "data": "0x" + format(value, "064x")}


class FakeEth:
    def __init__(self, logs=None, status=1, tx_from=PAYER, receipt=True):
        self.logs = logs if logs is not None else [_transfer_log()]
        self.status = status
        self.tx_from = tx_from
        self.receipt = receipt

    def get_transaction_receipt(self, _tx):
        if not self.receipt:
            return None
        return {"status": self.status, "blockNumber": 100, "logs": self.logs}

    def get_transaction(self, _tx):
        return {"from": self.tx_from}


class FakeW3:
    def __init__(self, **kw):
        self.eth = FakeEth(**kw)


# ------------------------------------------------------------------ happy path

def test_a_third_party_payment_verifies():
    out = verify.payment_proof(_invoice(), web3=FakeW3())
    assert out["ok"] is True and out["value"] == 10_000_000
    assert out["source"] == "rpc" and out["reason"] == "verified"


def test_overpayment_still_verifies():
    out = verify.payment_proof(_invoice(), web3=FakeW3(logs=[_transfer_log(value=99_000_000)]))
    assert out["ok"] is True and out["value"] == 99_000_000


# ---------------------------------------------------------------- refusals

def test_self_initiated_transaction_is_refused():
    """The INV-0001 bug: the payee's own swap moved tokens "into" itself."""
    invoice = _invoice()
    out = verify.payment_proof(invoice, web3=FakeW3(tx_from=PAY_TO))
    assert out["ok"] is False and "initiated by the payout address" in out["reason"]


def test_transfer_from_the_payee_is_refused_even_if_tx_from_differs():
    invoice = _invoice()
    out = verify.payment_proof(invoice, web3=FakeW3(logs=[_transfer_log(frm=PAY_TO)]))
    assert out["ok"] is False and out["reason"].startswith("no USAT payment")


def test_short_payment_is_refused():
    invoice = _invoice()
    out = verify.payment_proof(invoice, web3=FakeW3(logs=[_transfer_log(value=9_999_999)]))
    assert out["ok"] is False


def test_wrong_recipient_is_refused():
    invoice = _invoice()
    other = "0x1111111111111111111111111111111111111111"
    out = verify.payment_proof(invoice, web3=FakeW3(logs=[_transfer_log(to=other)]))
    assert out["ok"] is False


def test_wrong_token_is_refused():
    invoice = _invoice()
    out = verify.payment_proof(invoice, web3=FakeW3(logs=[_transfer_log(token="0x" + "99" * 20)]))
    assert out["ok"] is False


def test_reverted_transaction_is_refused():
    invoice = _invoice()
    out = verify.payment_proof(invoice, web3=FakeW3(status=0))
    assert out["ok"] is False and out["reason"] == "transaction reverted"


def test_missing_payout_address_is_refused(monkeypatch):
    monkeypatch.delenv("INVOICE_WALLET", raising=False)
    invoice = store.create({"amount": "10", "currency": "USD", "recipient": "Ana"},
                           {"decision": "settle"}, creator="ana")
    invoice["settlement_tx"] = TX
    out = verify.payment_proof(invoice, web3=FakeW3())
    assert out["ok"] is False and "no payout address" in out["reason"]


def test_missing_tx_is_refused():
    invoice = _invoice()
    invoice["settlement_tx"] = None
    out = verify.payment_proof(invoice, web3=FakeW3())
    assert out["ok"] is False and "no settlement tx" in out["reason"]


def test_unpriceable_invoice_cannot_be_verified(monkeypatch):
    invoice = _invoice(amount="10", currency="BRL")

    def boom(*a, **k):
        raise ValueError("no FX rate")
    monkeypatch.setattr(x402, "to_usat" if hasattr(x402, "to_usat") else "can_price",
                        boom, raising=False)
    monkeypatch.setattr(x402.fx, "to_usat", boom)
    out = verify.payment_proof(invoice, web3=FakeW3())
    assert out["ok"] is False and "cannot price" in out["reason"]


# ------------------------------------------------------- explorer fallback

def test_falls_back_to_the_explorer_when_no_receipt_comes_back(monkeypatch):
    payload = {"from": {"hash": PAYER}, "status": "ok", "token_transfers": [
        {"token": {"address_hash": USAT}, "to": {"hash": PAY_TO},
         "from": {"hash": PAYER}, "total": {"value": "10000000", "decimals": "6"}}]}
    monkeypatch.setattr(verify, "_explorer_tx", lambda tx: payload)
    out = verify.payment_proof(_invoice(), web3=FakeW3(receipt=False))
    assert out["ok"] is True and out["source"] == "blockscout"


def test_explorer_fallback_also_refuses_self_initiation(monkeypatch):
    payload = {"from": {"hash": PAY_TO}, "status": "ok", "token_transfers": [
        {"token": {"address_hash": USAT}, "to": {"hash": PAY_TO},
         "from": {"hash": PAYER}, "total": {"value": "10000000"}}]}
    monkeypatch.setattr(verify, "_explorer_tx", lambda tx: payload)
    out = verify.payment_proof(_invoice(), web3=FakeW3(receipt=False))
    assert out["ok"] is False and "initiated by the payout address" in out["reason"]


def test_no_source_at_all_is_reported_as_unknown_not_unpaid(monkeypatch):
    monkeypatch.setattr(verify, "_explorer_tx", lambda tx: None)
    out = verify.payment_proof(_invoice(), web3=FakeW3(receipt=False))
    assert out["ok"] is False and "no RPC or explorer" in out["reason"]

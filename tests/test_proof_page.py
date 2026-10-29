"""The /proof page: it must not flatter us.

Everything on that page is re-derived from the chain, and payments made by the
builder's own wallets are labelled as such. The failure mode to guard against is a
counting page that quietly trusts the store — that is the number a judge can check
in one click.
"""

import pytest

from bot import serve, store

PAY_TO = "0x86E95581E41946ED84956433a8a9c836bCbA636c"
AGENT = "0xdbfe5c4d0256d2459bb216cab905999ca41be349"
STRANGER = "0x86fea35d806b1b3ff4b7d55d934733f89c1832f0"


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", str(tmp_path / "invoices.json"))
    monkeypatch.setenv("INVOICE_WALLET", PAY_TO)
    monkeypatch.delenv("AGENT_WALLET_KEY", raising=False)
    serve._PROOF_CACHE.update(at=0.0, data=None)
    return tmp_path


def _settled(amount="10", currency="USD", tx="0x" + "aa" * 32):
    inv = store.create({"amount": amount, "currency": currency, "recipient": "Ana"},
                       {"decision": "settle"}, creator="ana", payout_address=PAY_TO)
    store.mark_settled(inv["id"], tx)
    return store.get(inv["id"])


def test_counts_only_what_the_chain_backs():
    good = _settled(tx="0x" + "aa" * 32)
    bad = _settled(tx="0x" + "bb" * 32)

    def verifier(inv):
        ok = inv["id"] == good["id"]
        return {"ok": ok, "from": STRANGER, "pay_to": PAY_TO,
                "reason": "verified" if ok else "self-initiated swap"}

    data = serve.proof_data(verifier=verifier, use_cache=False)
    assert data["counts"]["settled"] == 2
    assert data["counts"]["verified"] == 1
    assert data["counts"]["independent"] == 1
    html = serve.render_proof(data)
    assert "unverified" in html and "verified" in html


def test_payments_from_our_own_wallets_are_not_called_independent(monkeypatch):
    # The agent address is normally derived from AGENT_WALLET_KEY; pin it here so
    # the classification can be tested without a key in the environment.
    monkeypatch.setattr(serve, "_agent_address", lambda: AGENT)
    inv = _settled()
    for payer, expected in ((AGENT, False), (PAY_TO, False), (STRANGER, True)):
        data = serve.proof_data(
            verifier=lambda i, p=payer: {"ok": True, "from": p, "pay_to": PAY_TO},
            use_cache=False)
        row = next(s for s in data["settlements"] if s["id"] == inv["id"])
        assert row["independent"] is expected, payer
        assert data["counts"]["independent"] == (1 if expected else 0)
        serve._PROOF_CACHE.update(at=0.0, data=None)


def test_purchases_are_counted_with_their_proof():
    inv = store.create({"amount": "10", "currency": "USD", "recipient": "Ana"},
                       {"decision": "settle"}, creator="ana", payout_address=PAY_TO)
    store.set_services(inv["id"], "done", {"receipts": [
        {"slug": "sanctions-wallet", "ok": True, "price_usd_quoted": 0.002,
         "tx": "0x" + "cd" * 32, "onchain": {"verified": True}},
        {"slug": "fx-rate", "ok": True, "price_usd_quoted": 0.002,
         "tx": "0x" + "ef" * 32, "onchain": {"verified": False}},
    ], "spent_usd": 0.004, "at": 1})
    data = serve.proof_data(verifier=lambda i: {"ok": False}, use_cache=False)
    assert data["counts"]["purchases"] == 1        # only the verified one
    assert data["counts"]["spent_usd"] == 0.004


def test_empty_store_renders_without_a_five_hundred():
    data = serve.proof_data(verifier=lambda i: {"ok": False}, use_cache=False)
    html = serve.render_proof(data)
    assert "0/0" in html and "No settlements yet" in html


def test_cache_is_used_until_it_expires(monkeypatch):
    calls = {"n": 0}

    def verifier(inv):
        calls["n"] += 1
        return {"ok": True, "from": STRANGER, "pay_to": PAY_TO}

    _settled()
    serve.proof_data(verifier=verifier)              # populate cache
    serve.proof_data(verifier=verifier)              # served from cache
    assert calls["n"] == 1
    serve.proof_data(verifier=verifier, use_cache=False)
    assert calls["n"] == 2

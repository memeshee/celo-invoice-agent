"""Settlement modules: live-reads tested, funded paths flagged UNTESTED.

Live (no funds needed): facilitator health/supported, atomic math,
Textile preview shape, agent402 pricing/find, offline EIP-3009 signing.
Needs funded key: watch_transfers scan asserts only shape on empty window,
facilitator /settle, Textile /request broadcast, paid agent402 calls.
"""

import os
import re
import time
from pathlib import Path

import pytest

from bot import buy, textile, x402

web3 = pytest.importorskip("web3")


@pytest.mark.network
def test_facilitator_health_live():
    h = x402.health()
    assert h.get("status") == "ok", h
    assert h.get("network") == "mainnet", h


@pytest.mark.network
def test_supported_lists_usat():
    s = x402.supported_assets()
    blob = str(s)
    assert "0xD2ab3C9A02DBBAB236BfEC45D1d755DF4267F771" in blob, "USAT missing"
    assert "eip155:42220" in blob


def test_atomic_math():
    assert x402.atomic("500", "USAT") == 500_000_000
    assert x402.atomic("1.5", "wBRL") == 1_500_000_000_000_000_000


def test_payment_request_shape():
    # 500 BRL is NOT 500 USA₮ — pre-FX this priced a ~5.4x overcharge, so the
    # rate is pinned here and the conversion is what gets asserted.
    provider = lambda: {"rates": {"BRL": 5.0}, "as_of": "t", "fetched": 0, "source": "t"}
    req = x402.create_payment_request(
        {"id": "INV-0001", "amount": "500", "currency": "BRL"},
        pay_to="0x000000000000000000000000000000000000dEaD", fx_provider=provider)
    assert req["symbol"] == "USAT" and req["amount_atomic"] == 100_000_000
    assert req["invoice_amount_display"] == "500 BRL"
    assert req["chain"] == "eip155:42220"


@pytest.mark.network
@pytest.mark.live
def test_watch_finds_known_settlement():
    # Positive control: 0.1 USAT agent -> invoice wallet, mined block 79408890.
    # Locks the 0x-prefix normalization (empty-window tests can't catch it).
    out = x402.watch_transfers(
        "0x86E95581E41946ED84956433a8a9c836bCbA636c", "USAT",
        min_atomic=100000, from_block=79408885, to_block=79408895)
    txs = [h["tx"].lower() for h in out]
    assert "0x0d362780feaa31c632f3ead6b09d55ab9bcc53d5c5c649520d3fa668d6648929" in txs, out


@pytest.mark.network
def test_textile_takerproof_auth():
    # Live control: proof must pass AUTH (never 401). Funds errors are fine —
    # they prove the signature verified and only the balance check fired.
    import urllib.error
    key = os.environ.get("AGENT_WALLET_KEY", "").strip()
    if not key and Path(".env").exists():
        m = re.search(r"AGENT_WALLET_KEY=(\S+)", Path(".env").read_text())
        key = m.group(1) if m else ""
    if not key:
        pytest.skip("AGENT_WALLET_KEY not available — live Textile auth skipped")
    tp = textile.taker_proof(key)
    assert tp["taker"].startswith("0x") and len(tp["proof"]["signature"]) == 132
    try:
        # Sell above Textile's RFQ minimum (1000000 atomic): below it the API
        # rejects on size before it ever looks at the signature, which silently
        # turns this auth control into a no-op.
        textile.request_quote("USDT", "USDC", "1100000", tp["taker"], tp["proof"])
    except urllib.error.HTTPError as e:
        assert e.code != 401, "takerProof signature rejected"
        body = e.read().decode()
        low = body.lower()
        # What this controls is AUTH, not balance. Any other refusal
        # (insufficient funds, RFQ minimum, no makers online) still proves the
        # takerProof verified, so do not lock the assertion to one of them.
        assert "signature" not in low and "unauthorized" not in low, body


@pytest.mark.network
def test_watch_empty_window_shape():
    # Narrow window at chain head: proves RPC + log path works, no funds needed.
    w3 = x402._w3()
    head = w3.eth.block_number
    assert head > 30_000_000, head  # Celo mainnet sanity
    out = x402.watch_transfers(
        "0x000000000000000000000000000000000000dEaD",
        from_block=head - 2, to_block=head)
    assert isinstance(out, list)


@pytest.mark.network
def test_textile_preview_shape():
    r = textile.preview("USDC", "USDT", "1000000")
    assert isinstance(r, dict) and "data" in r, r


@pytest.mark.network
def test_agent402_pricing_and_find():
    p = buy.pricing()
    assert "Agent402" in str(p.get("name", ""))
    f = buy.find("linkedin")
    assert f.get("returned", 0) >= 1, f


def test_eip3009_signature_offline():
    eth_account = pytest.importorskip("eth_account")
    from eth_account import Account
    key = "0x" + "7" * 64
    nonce = os.urandom(32)
    now = int(time.time())
    sig = buy.sign_eip3009(
        key, x402.ASSETS["USDC"]["asset"], "USDC", "2", 42220,
        "0x000000000000000000000000000000000000dEaD",
        10_000, now - 60, now + 3600, nonce)
    assert sig["signer"] == Account.from_key(key).address
    assert sig["auth"]["nonce"] == nonce.hex()
    assert 0 < sig["v"] < 30 and sig["r"].startswith("0x")

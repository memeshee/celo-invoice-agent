"""Settlement modules: live-reads tested, funded paths flagged UNTESTED.

Live (no funds needed): facilitator health/supported, atomic math,
Textile preview shape, agent402 pricing/find, offline EIP-3009 signing.
Needs funded key: watch_transfers scan asserts only shape on empty window,
facilitator /settle, Textile /request broadcast, paid agent402 calls.
"""

import os
import time

import pytest

from bot import buy, textile, x402

web3 = pytest.importorskip("web3")


def test_facilitator_health_live():
    h = x402.health()
    assert h.get("status") == "ok", h
    assert h.get("network") == "mainnet", h


def test_supported_lists_usat():
    s = x402.supported_assets()
    blob = str(s)
    assert "0xD2ab3C9A02DBBAB236BfEC45D1d755DF4267F771" in blob, "USAT missing"
    assert "eip155:42220" in blob


def test_atomic_math():
    assert x402.atomic("500", "USAT") == 500_000_000
    assert x402.atomic("1.5", "wBRL") == 1_500_000_000_000_000_000


def test_payment_request_shape():
    req = x402.create_payment_request(
        {"id": "INV-0001", "amount": "500", "currency": "BRL"},
        pay_to="0x000000000000000000000000000000000000dEaD")
    assert req["symbol"] == "USAT" and req["amount_atomic"] == 500_000_000
    assert req["chain"] == "eip155:42220"


def test_watch_empty_window_shape():
    # Empty far-past window: proves RPC + log path works, no funds needed.
    out = x402.watch_transfers(
        "0x000000000000000000000000000000000000dEaD",
        from_block=1, to_block=5)
    assert isinstance(out, list)


def test_textile_preview_shape():
    r = textile.preview("USDC", "USDT", "1000000")
    assert isinstance(r, dict) and "data" in r, r


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

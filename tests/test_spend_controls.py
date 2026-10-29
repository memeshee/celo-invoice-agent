"""The two controls that bound what a peer can charge us.

An x402 peer answers the 402. It chooses the price, the timeout and the moment it
decides to take the signature and fail. Per-payment caps alone bound nothing, so
the ledger and the clamped authorization window are asserted here — these are the
difference between "the agent tipped its wallet" and "the agent went broke".
"""

import json
from types import SimpleNamespace

import pytest

from bot import buy

HOST = "https://agent402.tools"
KEY = "0x" + "11" * 32


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(buy, "SPEND_LEDGER", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(buy, "MAX_TOTAL_USD", 1.0)
    monkeypatch.setattr(buy, "MAX_PER_HOST_USD", 0.5)
    return tmp_path


# ---------------------------------------------------------------- spend gate

def test_non_positive_amount_is_refused():
    with pytest.raises(RuntimeError, match="non-positive"):
        buy.spend_gate(HOST + "/api/pdf", 0)


def test_a_payment_under_the_host_budget_passes():
    buy.spend_gate(HOST + "/api/pdf", 10_000)          # $0.01


def test_the_host_budget_stops_a_runaway_peer():
    buy.spend_record(HOST + "/api/pdf", 480_000)       # $0.48 of $0.50
    with pytest.raises(RuntimeError, match="host budget"):
        buy.spend_gate(HOST + "/api/pdf", 50_000)      # $0.05 would exceed


def test_the_total_budget_stops_spending_across_hosts():
    buy.spend_record("https://a.example/x", 400_000)  # $0.40
    buy.spend_record("https://b.example/x", 400_000)  # $0.40 -> total $0.80
    with pytest.raises(RuntimeError, match="total budget"):
        buy.spend_gate("https://c.example/x", 300_000)  # $0.30 would exceed $1.00


def test_a_busy_host_does_not_block_an_unrelated_one():
    buy.spend_record(HOST + "/api/pdf", 490_000)      # near the host cap
    buy.spend_gate("https://other.example/x", 10_000)  # different host is fine


def test_issued_authorizations_stay_visible_in_the_ledger(ledger):
    buy.spend_record(HOST + "/api/pdf", 20_000, nonce="0xdead")
    db = json.loads((ledger / "ledger.json").read_text())
    assert db["total_usd"] == pytest.approx(0.02)
    assert db["hosts"]["agent402.tools"] == pytest.approx(0.02)
    assert db["issued"]["0xdead"]["amount_atomic"] == 20_000


def test_a_corrupt_ledger_does_not_become_an_unbounded_wallet(ledger, monkeypatch):
    (ledger / "ledger.json").write_text("{not json")
    buy.spend_gate(HOST + "/api/pdf", 10_000)          # must not raise on parse
    buy.spend_record(HOST + "/api/pdf", 10_000)
    db = json.loads((ledger / "ledger.json").read_text())
    assert db["total_usd"] == pytest.approx(0.01)      # rewritten cleanly


# ------------------------------------------------- authorization window clamp

def test_the_signed_window_is_clamped_even_when_the_peer_asks_for_31_years(monkeypatch):
    monkeypatch.setattr(buy, "MAX_AUTH_SECONDS", 300)
    requirements = SimpleNamespace(
        network="eip155:42220", asset="0xcEBA9300f2b948710d2653dD7B07f33A8B32118C",
        pay_to="0x86E95581E41946ED84956433a8a9c836bCbA636c",
        amount=10_000, extra={"name": "USDC", "version": "2"},
        max_timeout_seconds=999_999_999)
    payload = buy.ExactCeloScheme(KEY).create_payment_payload(requirements)
    auth = payload["authorization"]
    window = int(auth["validBefore"]) - int(auth["validAfter"])
    assert window <= 300 + 61          # our clamp plus the 60s grace before now
    assert len(payload["signature"]) == 132
    assert auth["value"] == "10000"


def test_a_non_celo_network_is_refused_outright():
    requirements = SimpleNamespace(network="eip155:8453", asset="0x0", pay_to="0x0",
                                   amount=1, extra={}, max_timeout_seconds=60)
    with pytest.raises(ValueError, match="not a Celo leg"):
        buy.ExactCeloScheme(KEY).create_payment_payload(requirements)


def test_agent_key_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("AGENT_WALLET_KEY", KEY)
    assert buy._agent_key() == KEY


def test_a_missing_agent_key_says_what_is_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_WALLET_KEY", raising=False)
    monkeypatch.chdir(tmp_path)          # no .env here
    with pytest.raises(RuntimeError, match="AGENT_WALLET_KEY not set"):
        buy._agent_key()

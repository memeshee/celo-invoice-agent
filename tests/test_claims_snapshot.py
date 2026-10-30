"""The committed settlement claims — the evidence the daily audit reads.

`verify-claims` failed on its first scheduled run with
`FileNotFoundError: invoices.json`: the store is private and gitignored, so a
fresh checkout has none. These tests pin the replacement contract — a snapshot
the audit can actually read, carrying public fields only, that cannot quietly go
empty or stale.
"""

import json
from pathlib import Path

import pytest

from bot import claims, store
from scripts import verify_settlements as audit

PAY_TO = "0x86E95581E41946ED84956433a8a9c836bCbA636c"
TX = "0x" + "ab" * 32
PRIVATE_KEYS = {"raw", "description", "creator_chat_id", "recipient", "trust",
                "screening", "services", "settlement_history"}


@pytest.fixture()
def tmp_claims(tmp_path, monkeypatch):
    path = tmp_path / "claims.json"
    monkeypatch.setenv("CLAIMS_PATH", str(path))
    monkeypatch.setattr(store, "STORE_PATH", str(tmp_path / "invoices.json"))
    monkeypatch.setenv("INVOICE_WALLET", PAY_TO)
    return path


def _invoice(**kw):
    draft = {"amount": "10", "currency": "USD", "recipient": "Ana Souza",
             "raw": "charge Ana 10 USD"}
    return store.create(draft, {"decision": "settle"}, creator="ana",
                        payout_address=PAY_TO, creator_chat_id=7, **kw)


# ------------------------------------------------------------- the snapshot


def test_the_committed_snapshot_is_valid_and_not_empty():
    snap = claims.read("claims.json")
    assert snap["chain"] == "celo" and snap["chain_id"] == 42220
    assert snap["count"] == len(snap["claims"]) > 0
    for c in snap["claims"]:
        assert c["settlement_tx"].startswith("0x") and len(c["settlement_tx"]) == 66
        assert c["payout_address"].startswith("0x") and len(c["payout_address"]) == 42
        assert str(c["amount"]) and c["currency"]


def test_the_committed_snapshot_is_never_the_test_write_target():
    """The guard that stops a fixture invoice from truncating the real evidence.

    store.mark_settled refreshes claims.json from the store it just wrote, so a
    test with a temporary store would otherwise overwrite the committed file.
    """
    committed = Path(__file__).resolve().parents[1] / "claims.json"
    assert Path(claims.path()).resolve() != committed


def test_the_snapshot_carries_no_private_store_fields():
    snap = claims.read("claims.json")
    for c in snap["claims"]:
        assert not PRIVATE_KEYS & set(c), f"private field leaked: {PRIVATE_KEYS & set(c)}"


def test_only_settled_invoices_become_claims(tmp_claims):
    settled = _invoice()
    store.mark_settled(settled["id"], TX)
    _invoice()                                   # stays open
    store.create({"amount": "5", "currency": "USD", "recipient": "Ivan"},
                 {"decision": "review"}, creator="x", status="held",
                 payout_address=PAY_TO)
    snap = claims.read(tmp_claims)
    assert [c["id"] for c in snap["claims"]] == [settled["id"]]


def test_settling_and_withdrawing_refresh_the_snapshot(tmp_claims):
    inv = _invoice()
    store.mark_settled(inv["id"], TX)
    assert claims.read(tmp_claims)["count"] == 1
    # A withdrawn claim must leave the public evidence immediately; otherwise the
    # snapshot cites a payment the store no longer stands behind.
    store.unsettle(inv["id"], reason="amount mismatch")
    assert claims.read(tmp_claims)["count"] == 0


# ----------------------------------------------------------- the audit script


def _write_snapshot(path, *entries):
    path.write_text(json.dumps({"chain": "celo", "chain_id": 42220,
                                "count": len(entries), "claims": list(entries)}))
    return str(path)


def _claim(id="INV-9", amount="10", currency="USD", **kw):
    c = {"id": id, "amount": amount, "currency": currency, "settle_asset": None,
         "payout_address": PAY_TO, "settlement_tx": TX, "created": 1}
    c.update(kw)
    return c


def _stub_proof(monkeypatch, ok: bool, reason="verified"):
    calls = []

    def fake(invoice, **_kw):
        calls.append(invoice["id"])
        return {"id": invoice["id"], "tx": invoice.get("settlement_tx"), "ok": ok,
                "reason": reason, "pay_to": PAY_TO}

    monkeypatch.setattr(audit.verify, "payment_proof", fake)
    return calls


def test_audit_passes_on_a_snapshot_that_holds(tmp_path, monkeypatch, capsys):
    src = _write_snapshot(tmp_path / "c.json", _claim())
    calls = _stub_proof(monkeypatch, True)
    assert audit.main(["--claims", src, "--require"]) == 0
    out = capsys.readouterr().out
    assert "1/1 settlement claims verify onchain" in out and "claims" in out
    assert calls == ["INV-9"]


def test_audit_fails_when_a_claim_stops_holding(tmp_path, monkeypatch):
    src = _write_snapshot(tmp_path / "c.json", _claim())
    _stub_proof(monkeypatch, False, reason="no payment to 0x86E9… of 10000000±5%")
    assert audit.main(["--claims", src]) == 1


def test_require_turns_an_empty_snapshot_into_a_failure(tmp_path, monkeypatch):
    src = _write_snapshot(tmp_path / "empty.json")
    _stub_proof(monkeypatch, True)
    assert audit.main(["--claims", src]) == 0          # nothing to check, no claim made
    assert audit.main(["--claims", src, "--require"]) == 1   # ... but not in CI


def test_audit_says_so_instead_of_crashing_without_a_store(monkeypatch, tmp_path, capsys):
    """The original failure: a clean checkout has no invoices.json."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(store, "STORE_PATH", str(tmp_path / "invoices.json"))
    monkeypatch.setenv("CLAIMS_PATH", str(tmp_path / "claims.json"))
    assert audit.main([]) == 0
    assert "nothing to verify" in capsys.readouterr().out


def test_fix_is_refused_on_a_snapshot_but_not_on_the_store(tmp_path, monkeypatch):
    src = _write_snapshot(tmp_path / "c.json", _claim())
    _stub_proof(monkeypatch, False, reason="amount mismatch")
    withdrawn = []
    monkeypatch.setattr(store, "unsettle", lambda *a, **k: withdrawn.append(a))
    assert audit.main(["--claims", src, "--fix"]) == 0   # fix mode never fails the run
    assert withdrawn == []                               # snapshot is evidence, not state

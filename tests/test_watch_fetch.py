"""The watcher's fetch path: retries, failover, and the 429 trap.

Blockscout's v1 endpoint rate-limits this machine into a hard 429 while v2 keeps
answering. A fetch failure silently read as "no transfers" is the worst bug in
this project — a paid invoice stays open forever and nobody notices — so the
distinction between "empty" and "could not ask" is asserted here.
"""

import io
import json
import urllib.error

import pytest

from bot import store, watch

ADDR = "0x86E95581E41946ED84956433a8a9c836bCbA636c"
TX = "0x" + "ab" * 32


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def _v1_body():
    return {"status": "1", "result": [{
        "hash": TX, "to": ADDR, "contractAddress": "0x" + "cd" * 20,
        "value": "1000000", "blockNumber": "42"}]}


def _v2_body():
    return {"items": [{
        "transaction_hash": TX,
        "block_number": 42,
        "to": {"hash": ADDR},
        "token": {"address_hash": "0x" + "cd" * 20},
        "total": {"value": "1000000", "decimals": "6"},
    }]}


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "err", {}, None)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(watch.time, "sleep", lambda s: None)


# ------------------------------------------------------------------- retries

def test_retries_a_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_open(url, timeout=25):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(url, 429)
        return Resp(json.dumps(_v2_body()).encode())

    monkeypatch.setattr(watch, "_open", fake_open)
    out = watch.transfers_to(ADDR, log=lambda *_: None)
    assert calls["n"] == 3
    assert out[0]["value"] == 1_000_000 and out[0]["block"] == 42


def test_a_404_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def fake_open(url, timeout=25):
        calls["n"] += 1
        raise _http_error(url, 404)

    monkeypatch.setattr(watch, "_open", fake_open)
    with pytest.raises(urllib.error.HTTPError):
        watch._get_json("https://x", log=lambda *_: None)
    assert calls["n"] == 1        # HTTPError subclasses URLError; must not loop


# ------------------------------------------------------------------ failover

def test_falls_back_to_v1_when_v2_is_unusable(monkeypatch):
    seen = []

    def fake_open(url, timeout=25):
        seen.append(url)
        return Resp(json.dumps(_v1_body()).encode())

    monkeypatch.setattr(watch, "_open", fake_open)
    out = watch.transfers_to(ADDR, log=lambda *_: None)
    assert out[0]["tx"] == TX
    assert any("/api/v2/" in u for u in seen) and any("/api?" in u for u in seen)


def test_raises_when_every_source_fails(monkeypatch):
    monkeypatch.setattr(watch, "_open",
                        lambda url, timeout=25: (_ for _ in ()).throw(_http_error(url, 429)))
    with pytest.raises(RuntimeError, match="all transfer sources failed"):
        watch.transfers_to(ADDR, attempts=2, log=lambda *_: None)


def test_parses_both_shapes_identically():
    from_v1 = watch._parse_v1(_v1_body())
    from_v2 = watch._parse_v2(_v2_body())
    assert from_v1 == from_v2


def test_empty_v1_result_is_not_an_error():
    assert watch._parse_v1({"status": "0", "result": []}) == []


def test_sweep_reports_a_failed_fetch_without_settling(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "STORE_PATH", str(tmp_path / "inv.json"))
    monkeypatch.setenv("INVOICE_WALLET", ADDR)
    inv = store.create({"amount": "1", "currency": "USD", "recipient": "a"},
                       {"decision": "settle"}, creator="a", payout_address=ADDR,
                       created_block=1)
    logged = []
    settled = watch.sweep(fetcher=lambda addr: (_ for _ in ()).throw(RuntimeError("429")),
                          notifier=lambda *a: True, log=logged.append)
    assert settled == []
    assert any("fetch failed" in line for line in logged)
    assert store.get(inv["id"])["status"] == "open"    # not settled, not lost

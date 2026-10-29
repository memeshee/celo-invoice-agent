"""The buy leg: what the agent purchases per invoice, and what blocks money.

The dangerous failure modes are all silent — a paid body we failed to capture, a
sanctions screen treated as clean because the verdict key changed, a settlement
"verified" from the seller's own header — so each one gets a control.
"""

import base64
import json

import pytest

from bot import services, store, watch

USDC = "0xceba9300f2b948710d2653dD7B07f33A8B32118C"
PAYER = "0xDbFe5c4d0256D2459bB216caB905999Ca41bE349"
PAY_TO = "0x86E95581E41946ED84956433a8a9c836bCbA636c"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", str(tmp_path / "invoices.json"))
    monkeypatch.setattr(services, "RECEIPT_DIR", tmp_path / "receipts")
    monkeypatch.setenv("INVOICE_WALLET", PAY_TO)
    return tmp_path


class FakeResponse:
    def __init__(self, body: dict, status=200, pay=True):
        self.status_code = status
        self.text = json.dumps(body)
        headers = {}
        if pay:
            headers["PAYMENT-RESPONSE"] = base64.b64encode(json.dumps({
                "success": True, "payer": PAYER,
                "transaction": "0x" + "ab" * 32, "network": "eip155:42220"}).encode()).decode()
        self.headers = headers


class FakeSession:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers or {}})
        return self.response


def clean_name_body():
    return {"query": "Marco Silva", "verdict": "no_match_on_lists_checked",
            "exactCount": 0, "containsCount": 0, "matches": [],
            "listsChecked": [{"list": "OFAC SDN"}]}


def flagged_name_body():
    return {"query": "Ivan Bad", "verdict": "possible_match",
            "exactCount": 1, "matches": [{"name": "IVAN BAD"}],
            "listsChecked": [{"list": "OFAC SDN"}]}


# ------------------------------------------------------------------ planning

def test_plan_buys_three_services_for_a_brl_invoice():
    inv = {"id": "INV-1", "currency": "BRL", "recipient": "Marco Silva",
           "payout_address": PAY_TO}
    slugs = [s for s, _ in services.plan_for(inv)]
    assert slugs == ["fx-rate", "sanctions-wallet", "sanctions-name"]


def test_plan_skips_fx_for_usd_and_name_for_handles():
    slugs = [s for s, _ in services.plan_for(
        {"id": "INV-2", "currency": "USD", "recipient": "@marco", "payout_address": PAY_TO})]
    assert slugs == ["sanctions-wallet"]


def test_plan_skips_wallet_screen_when_no_payout(monkeypatch):
    monkeypatch.delenv("INVOICE_WALLET", raising=False)
    slugs = [s for s, _ in services.plan_for(
        {"id": "INV-3", "currency": "USD", "recipient": "Marco Silva"})]
    assert slugs == ["sanctions-name"]


# -------------------------------------------------------------- verdict logic

def test_clean_requires_explicit_no_match():
    assert services.is_clean(clean_name_body()) is True
    assert services.is_clean(flagged_name_body()) is False
    assert services.is_clean({"verdict": "unknown", "exactCount": 0}) is False
    # A missing verdict must never read as clean.
    assert services.is_clean({}) is False


# ----------------------------------------------------------------- purchasing

def test_purchase_captures_raw_body_before_parsing():
    session = FakeSession(FakeResponse(clean_name_body()))
    rec = services.purchase("sanctions-name", {"name": "Marco Silva"}, "INV-9",
                            session=session, web3=None)
    assert rec["ok"] and rec["result"]["clean"] is True
    assert rec["result"]["verdict"] == "no_match_on_lists_checked"
    saved = json.loads(open(rec["receipt_file"]).read())
    assert saved["status"] == 200 and "no_match_on_lists_checked" in saved["text"]
    assert rec["tx"] == "0x" + "ab" * 32


def test_purchase_sends_idempotency_key():
    session = FakeSession(FakeResponse(clean_name_body()))
    services.purchase("sanctions-name", {"name": "x"}, "INV-10", session=session)
    assert session.calls[0]["headers"]["Idempotency-Key"].startswith("INV-10-sanctions-name-")


def test_purchase_failure_is_recorded_not_raised():
    rec = services.purchase("sanctions-wallet", {"address": PAY_TO}, "INV-11",
                            session=FakeSession(FakeResponse({"error": "nope"}, status=402)))
    assert rec["ok"] is False and "402" in rec["error"]


def test_verify_settlement_requires_a_transfer_from_our_payer():
    class Eth:
        def get_transaction_receipt(self, tx):
            return {"status": 1, "blockNumber": 5, "logs": [
                {"topics": [TRANSFER_TOPIC, "0x" + "0" * 24 + PAYER[2:], "0x" + "0" * 64],
                 "data": "0x" + format(2000, "064x")}]}

    class W3:
        eth = Eth()

    assert services.verify_settlement("0x" + "ab" * 32, PAYER, web3=W3())["verified"] is True
    assert services.verify_settlement("0x" + "ab" * 32, PAY_TO, web3=W3())["verified"] is False


# ------------------------------------------------------------ rate cross-check

def test_fx_crosscheck_inverts_a_unit_flipped_rate():
    receipts = [{"slug": "fx-rate", "ok": True, "result": {"rate": 5.3271}, "tx": "0x" + "ab" * 32}]
    out = services.fx_crosscheck({"receipts": receipts}, "0.18702667")
    assert out and out["comparable"] and out["inverted"] and out["agrees"]
    assert out["drift_pct"] < 2
    assert out["tx"] == "0x" + "ab" * 32


def test_fx_crosscheck_flags_a_real_divergence():
    receipts = [{"slug": "fx-rate", "ok": True, "result": {"rate": 4.0}}]
    out = services.fx_crosscheck({"receipts": receipts}, "0.18702667")
    assert out["comparable"] and out["agrees"] is False   # 0.25 vs 0.187


def test_fx_crosscheck_tolerates_missing_or_junk():
    assert services.fx_crosscheck({"receipts": []}, "0.18") is None
    junk = [{"slug": "fx-rate", "ok": True, "result": {"rate": "n/a"}}]
    assert services.fx_crosscheck({"receipts": junk}, "0.18")["comparable"] is False


# ------------------------------------------------------------------ enrichment

def _invoice(**kw):
    draft = {"amount": "500", "currency": "BRL", "recipient": "Marco Silva",
             "raw": "charge Marco 500 BRL"}
    return store.create(draft, {"decision": "settle"}, creator="m",
                        payout_address=PAY_TO, creator_chat_id=7, **kw)


def test_enrichment_blocks_the_invoice_on_a_sanctions_hit():
    inv = _invoice()
    sessions = {"sanctions-name": FakeSession(FakeResponse(flagged_name_body())),
                "sanctions-wallet": FakeSession(FakeResponse({"verdict": "no_match_on_lists_checked", "exactCount": 0})),
                "fx-rate": FakeSession(FakeResponse({"rate": 5.3}))}
    enr = services.enrich(inv, session_factory=lambda slug: sessions[slug], log=lambda *_: None)
    assert enr["blocked"] is True and "sanctions-name" in enr["reason"]
    updated = store.set_services(inv["id"], "done", enr)
    assert updated["status"] == "blocked"
    # A blocked invoice must leave the settlement queue.
    assert store.get(inv["id"])["id"] not in [i["id"] for i in store.open_invoices()]


def test_enrichment_records_spend_and_stays_open_when_clean():
    inv = _invoice()
    clean = FakeResponse({"verdict": "no_match_on_lists_checked", "exactCount": 0})
    enr = services.enrich(inv, session_factory=lambda slug: FakeSession(clean), log=lambda *_: None)
    assert enr["blocked"] is False
    assert len(enr["receipts"]) == 3
    updated = store.set_services(inv["id"], "done", enr)
    assert updated["status"] == "open" and updated["services_spent_usd"] == pytest.approx(0.009)


def test_enrich_pending_is_capped_and_notifies():
    a, b = _invoice(), _invoice()
    sent = []
    clean = FakeResponse({"verdict": "no_match_on_lists_checked", "exactCount": 0})
    done = watch.enrich_pending(
        enricher=lambda inv: services.enrich(inv, session_factory=lambda s: FakeSession(clean),
                                             log=lambda *_: None),
        log=lambda *_: None, max_per_loop=1)
    assert len(done) == 1                      # cap respected
    assert len(store.invoices_awaiting_services()) == 1


def test_enrich_pending_skips_when_disabled(monkeypatch):
    _invoice()
    monkeypatch.setenv("SERVICES_ENRICH", "0")
    assert watch.enrich_pending() == []
    assert len(store.invoices_awaiting_services()) == 1

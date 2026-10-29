"""The paid screen must gate settlement on *every* path, not just the watcher's.

Two holes were found by an adversarial review and are pinned here: a screen that
crashed was treated as clearance, and the manual /check command skipped the gate
entirely. An unscreened invoice settling is exactly the failure the purchase exists
to prevent, so both directions get a test.
"""

import asyncio
from types import SimpleNamespace

import pytest

from bot import main, store, watch

PAY_TO = "0x86E95581E41946ED84956433a8a9c836bCbA636c"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", str(tmp_path / "invoices.json"))
    monkeypatch.setenv("INVOICE_WALLET", PAY_TO)
    monkeypatch.setenv("SERVICES_ENRICH", "1")
    return tmp_path


def _invoice(services_status="pending", status="open"):
    return {"id": "INV-TEST", "status": status, "services_status": services_status,
            "payout_address": PAY_TO, "amount": "10", "currency": "USD"}


# ------------------------------------------------------------- screening_allows

@pytest.mark.parametrize("status,allowed", [
    ("done", True),        # the screen ran
    ("pending", False),    # still buying
    ("retry", False),      # failed once: not a clearance
    ("error", False),      # the old behaviour treated this as clearance
    (None, False),         # nothing recorded is not a clearance
])
def test_only_a_finished_screen_clears_an_invoice(monkeypatch, status, allowed):
    monkeypatch.setenv("SERVICES_ENRICH", "1")
    assert watch.screening_allows(_invoice(services_status=status)) is allowed


def test_with_enrichment_off_there_is_no_screen_to_wait_for(monkeypatch):
    monkeypatch.setenv("SERVICES_ENRICH", "0")
    assert watch.screening_allows(_invoice(services_status="pending")) is True


def test_an_unscreened_invoice_never_matches_a_settlement():
    inv = _invoice(services_status="pending")
    transfers = [{"tx": "0x" + "aa" * 32, "to": PAY_TO.lower(),
                  "contract": "0x" + "11" * 20, "value": 10_000_000, "block": 11}]
    assert watch.find_settlement(inv, transfers) is None


# ------------------------------------------------------------- /check follows

class FakeMsg:
    def __init__(self):
        self.from_user = SimpleNamespace(id=7, username="u7")
        self.chat_id = 1
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return self

    @property
    def sent(self):
        return "\n".join(self.replies)


def _cmd(args):
    msg = FakeMsg()
    return SimpleNamespace(message=msg, effective_user=msg.from_user), SimpleNamespace(args=args)


def test_check_refuses_an_unscreened_invoice_without_touching_the_chain(monkeypatch):
    inv = store.create({"amount": "10", "currency": "USD", "recipient": "Ana"},
                       {"decision": "settle"}, creator="ana", payout_address=PAY_TO)
    assert store.get(inv["id"])["services_status"] == "pending"

    def boom(*a, **k):
        raise AssertionError("/check must not read the chain before the screen finishes")
    monkeypatch.setattr("bot.x402.watch_transfers", boom)

    update, ctx = _cmd([inv["id"]])
    asyncio.run(main.check(update, ctx))
    assert "has not finished" in update.message.sent
    assert store.get(inv["id"])["status"] == "open"


def test_a_crashed_enrichment_marks_the_invoice_for_retry(monkeypatch):
    inv = store.create({"amount": "10", "currency": "USD", "recipient": "Ana"},
                       {"decision": "settle"}, creator="ana", payout_address=PAY_TO)

    def crash(_inv):
        raise RuntimeError("vendor down")
    monkeypatch.setattr("bot.services.enrich", crash)

    watch.enrich_pending(log=lambda *_: None)
    after = store.get(inv["id"])
    assert after["services_status"] == "retry"       # not "error", not "done"
    assert not watch.screening_allows(after)
    # and it is picked up again by the next loop, so it is not parked forever
    assert inv["id"] in [i["id"] for i in store.invoices_awaiting_services()]

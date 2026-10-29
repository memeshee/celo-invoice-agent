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


# ------------------------------------------------------- screen routing policy

def _screen(monkeypatch, susp, level, dup=0.02, pressure=0.05):
    """Route through the real screen_invoice with only the model stubbed out."""
    from bot import jev
    monkeypatch.setattr(jev, "ask", lambda *a, **k: {
        "is_suspicious": {"noul": susp},
        "is_duplicate": {"noul": dup},
        "uses_pressure": {"noul": pressure},
        "risk_level": {"score": level},
    })
    return jev.screen_invoice({"amount": "500", "currency": "BRL", "recipient": "Marco",
                               "raw": "charge Marco 500 BRL for the logo, due Friday"})


def test_an_ordinary_sentence_is_not_held_by_its_label(monkeypatch):
    """Measured live: 9% suspicion, label 'review' — and the bot held every invoice.

    A gate nobody can pass is not a gate, so the probability decides and the label
    only holds when a number agrees with it.
    """
    monkeypatch.delenv("SCREEN_STRICT", raising=False)
    s = _screen(monkeypatch, 0.09, "review")
    assert s["decision"] == "settle", s


def test_high_suspicion_blocks_even_when_the_numbers_are_the_only_signal(monkeypatch):
    monkeypatch.delenv("SCREEN_STRICT", raising=False)
    assert _screen(monkeypatch, 0.95, "review")["decision"] == "block"
    assert _screen(monkeypatch, 0.60, "clean")["decision"] == "review"


def test_the_label_still_blocks_on_its_own(monkeypatch):
    """Fail safe: a categorical 'block' from the model is never argued with."""
    monkeypatch.delenv("SCREEN_STRICT", raising=False)
    assert _screen(monkeypatch, 0.05, "block")["decision"] == "block"


def test_strict_mode_restores_the_label_veto(monkeypatch):
    monkeypatch.setenv("SCREEN_STRICT", "1")
    assert _screen(monkeypatch, 0.09, "review")["decision"] == "review"

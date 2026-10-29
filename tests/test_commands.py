"""The operator and wallet commands, driven as coroutines.

These are the commands a human types, and two of them move money-related state:
/wallet decides where a freelancer gets paid and in what asset, and /release is the
only way a held invoice becomes payable. Refusals matter more than the happy path,
so both are asserted for a non-operator.
"""

import asyncio
from types import SimpleNamespace

import pytest

from bot import main, store, x402

PAY_TO = "0x86E95581E41946ED84956433a8a9c836bCbA636c"
OTHER = "0x1111111111111111111111111111111111111111"


class FakeMsg:
    def __init__(self, chat_id=1, user_id=7, text="", message_id=100):
        self.chat_id = chat_id
        self.from_user = SimpleNamespace(id=user_id, username=f"user{user_id}",
                                         full_name=f"user{user_id}")
        self.message_id = message_id
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return self

    @property
    def sent(self):
        return "\n".join(self.replies)


def cmd(args, user_id=7):
    msg = FakeMsg(user_id=user_id)
    return SimpleNamespace(message=msg, effective_user=msg.from_user), SimpleNamespace(args=args)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", str(tmp_path / "invoices.json"))
    monkeypatch.setenv("INVOICE_WALLET", PAY_TO)
    monkeypatch.delenv("OPERATOR_TELEGRAM_ID", raising=False)
    # /release anchors the settlement window at the current block.
    monkeypatch.setattr(x402, "head_block", lambda: 12345)
    return tmp_path


def _held(**kw):
    return store.create({"amount": "500", "currency": "BRL", "recipient": "Marco Silva"},
                        {"decision": "review", "risk_level": "review",
                         "duplicate_p": 0.71, "pressure_p": 0.66},
                        creator="user7", payout_address=OTHER, creator_chat_id=7,
                        status="held", **kw)


# ------------------------------------------------------------------- /wallet

def test_wallet_without_args_explains_the_choices():
    update, ctx = cmd([])
    run(main.wallet(update, ctx))
    text = update.message.sent
    assert "wBRL" in text and "not set" in text


def test_wallet_registers_an_address():
    update, ctx = cmd([OTHER])
    run(main.wallet(update, ctx))
    assert store.wallet_for(7) == OTHER
    assert "Payout wallet set" in update.message.sent


def test_wallet_normalises_asset_casing():
    update, ctx = cmd([OTHER, "wbrl"])
    run(main.wallet(update, ctx))
    assert store.pref_for(7)["asset"] == "wBRL"
    assert "no FX" in update.message.sent


def test_wallet_rejects_an_unsupported_asset():
    update, ctx = cmd([OTHER, "DOGE"])
    run(main.wallet(update, ctx))
    assert "unsupported settlement asset" in update.message.sent
    assert store.wallet_for(7) is None


def test_wallet_rejects_a_bad_address():
    update, ctx = cmd(["0xnope"])
    run(main.wallet(update, ctx))
    assert "not an address" in update.message.sent


# ------------------------------------------------------- operator fail-closed

def test_nobody_is_an_operator_when_the_env_is_unset():
    update, _ = cmd(["INV-1"])
    assert main.operator_ids() == set()
    assert main._is_operator(update) is False


def test_release_is_refused_for_a_non_operator(monkeypatch):
    monkeypatch.setenv("OPERATOR_TELEGRAM_ID", "999")
    held = _held()
    update, ctx = cmd([held["id"]])
    run(main.release(update, ctx))
    assert "Only an operator" in update.message.sent
    assert store.get(held["id"])["status"] == "held"


def test_held_is_refused_for_a_non_operator():
    update, ctx = cmd([])
    run(main.held(update, ctx))
    assert "Only an operator" in update.message.sent


# --------------------------------------------------------------- /release

def test_operator_can_release_and_the_window_restarts(monkeypatch):
    monkeypatch.setenv("OPERATOR_TELEGRAM_ID", "7")
    held = _held()
    update, ctx = cmd([held["id"]])
    run(main.release(update, ctx))
    released = store.get(held["id"])
    assert released["status"] == "open"
    assert released["created_block"] == 12345
    assert "Released" in update.message.sent


def test_release_of_an_unknown_or_non_held_invoice_says_so(monkeypatch):
    monkeypatch.setenv("OPERATOR_TELEGRAM_ID", "7")
    update, ctx = cmd(["INV-9999-NOPE"])
    run(main.release(update, ctx))
    assert "No held invoice" in update.message.sent


def test_drop_removes_it_from_the_queue(monkeypatch):
    monkeypatch.setenv("OPERATOR_TELEGRAM_ID", "7")
    held = _held()
    update, ctx = cmd([held["id"]])
    run(main.drop(update, ctx))
    assert store.get(held["id"])["status"] == "dropped"
    assert store.held_invoices() == []
    assert "Dropped" in update.message.sent


def test_held_lists_reasons(monkeypatch):
    monkeypatch.setenv("OPERATOR_TELEGRAM_ID", "7")
    held = _held()
    update, ctx = cmd([])
    run(main.held(update, ctx))
    text = update.message.sent
    assert held["id"] in text and "duplicate" in text


# ---------------------------------------------------------------- read commands

def test_receipt_shows_what_the_agent_bought():
    inv = store.create({"amount": "10", "currency": "USD", "recipient": "Ana"},
                       {"decision": "settle", "risk_level": "clean"},
                       creator="user7", payout_address=PAY_TO)
    store.set_services(inv["id"], "done", {"receipts": [
        {"slug": "sanctions-wallet", "ok": True, "tx": "0x" + "ab" * 32,
         "result": {"verdict": "no_match_on_lists_checked"},
         "onchain": {"verified": True}}], "spent_usd": 0.002, "at": 1})
    update, ctx = cmd([inv["id"]])
    run(main.receipt(update, ctx))
    text = update.message.sent
    assert "sanctions-wallet" in text and "no_match_on_lists_checked" in text
    assert "0.002" in text


def test_pay_explains_itself_when_there_is_no_payout_address(monkeypatch):
    monkeypatch.delenv("INVOICE_WALLET", raising=False)
    inv = store.create({"amount": "10", "currency": "USD", "recipient": "Ana"},
                       {"decision": "settle"}, creator="user7")
    update, ctx = cmd([inv["id"]])
    run(main.pay(update, ctx))
    assert "no payout address" in update.message.sent


def test_expiring_a_stale_invoice_closes_its_pay_page():
    """Every open invoice has a live pay page; a stale one must not be payable."""
    inv = store.create({"amount": "10", "currency": "USD", "recipient": "Ana"},
                       {"decision": "settle"}, creator="user7", payout_address=OTHER)
    assert store.expire(inv["id"], "test")["status"] == "expired"
    assert inv["id"] not in [i["id"] for i in store.open_invoices()]
    assert store.expire(inv["id"]) is None          # only open invoices expire
    assert store.mark_settled(inv["id"], "0x" + "ee" * 32) is None


def test_help_lists_every_registered_command():
    """A judge's first move is /help. It used to have no handler at all."""
    import pathlib
    import re

    import bot.main as m
    update, _ = cmd([])
    run(m.help_cmd(update, None))
    sent = update.message.sent

    # every command the bot registers must appear in the help text, or the two drift
    registered = set(re.findall(r'CommandHandler\("([a-z_]+)"', pathlib.Path(
        m.__file__).read_text())) - {"start", "help"}
    missing = sorted(c for c in registered if f"/{c}" not in sent)
    assert not missing, f"/help does not mention: {missing}"
    assert "charge Marco 500 BRL" in sent          # a copy-pasteable example
    assert "/proof" in sent                        # points at the proof page


def test_start_is_the_same_onboarding_as_help():
    import bot.main as m
    a, _ = cmd([])
    b, _ = cmd([])
    run(m.start(a, None))
    run(m.help_cmd(b, None))
    assert a.message.sent == b.message.sent


def test_an_unknown_command_never_gets_silence():
    import bot.main as m
    update, _ = cmd([])
    update.message.text = "/pya INV-0001"
    run(m.unknown(update, None))
    assert "don't know /pya" in update.message.sent
    assert "/help" in update.message.sent

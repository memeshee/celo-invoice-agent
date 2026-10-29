"""Telegram bot: sentence -> Jev parse -> confirm -> invoice + trust score.

/start, /new <sentence> (or just send the sentence), /pay INV-0001, /receipt INV-0001
Settlement wiring (x402/Textile/buy) lands in their modules; bot calls them.

Security fixes (audit run-1):
- pending-draft-not-bound-to-author: PENDING is keyed by (chat_id, user_id) and
  the confirm/drop callback must come from the draft's author on the draft's own
  message, so a group member can no longer mint, drop or replace someone else's
  draft.
- screen-fail-open-mintable-draft: a screening failure now refuses the draft,
  and only a "settle" verdict reaches the mint button. "review" is a real hold.
- unbound-settlement-proof / settled-receipt-rewrite-on-recheck: /check scans
  from the invoice's creation block, skips witnesses already consumed by another
  invoice, and refuses to touch an already-settled invoice.
- check-unguarded-payment-request / unpriceable-amount-candidates-minted: an
  amount/currency the bot cannot price is refused at draft time, and /check
  wraps pricing the same way /pay does.
"""

import logging
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from . import jev, store

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("invoice-agent")

# Where a payer opens the invoice. The bot hands out a link rather than raw
# address instructions, so a payer who is not in this chat can still pay.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8088").rstrip("/")


def pay_url(inv_id: str) -> str:
    return f"{PUBLIC_BASE_URL}/i/{inv_id}"

# (chat_id, user_id) -> draft awaiting confirm. Keying by chat alone let any
# member of a group act on another member's draft.
PENDING: dict[tuple[int, int], dict] = {}


def fmt_draft(d: dict, s: dict) -> str:
    return (
        f"Invoice draft\n"
        f"Amount: {d.get('amount')} {d.get('currency') or ''}\n"
        f"Recipient: {d.get('recipient') or '—'}\n"
        f"Trust: {s.get('decision')} (risk {s.get('risk_level')}, "
        f"suspicion {s.get('suspicious_p'):.2f})"
    )


def must_msg(update: Update) -> Message:
    msg = update.message
    assert msg is not None, "handler needs a message update"
    return msg


def _user_id(user) -> int:
    return int(getattr(user, "id", 0) or 0)


async def start(update: Update, _ctx) -> None:
    await must_msg(update).reply_text(
        "Send me an invoice as a sentence, e.g.\n"
        "`charge Marco 500 BRL for the logo, due Friday`\n"
        "I'll parse it, screen it, and mint a pay link anyone can open.\n\n"
        "First set where you get paid: `/wallet 0xYourAddress`\n"
        "Then: /new, /pay, /check, /receipt"
    )


async def new_invoice(update: Update, ctx) -> None:
    msg = must_msg(update)
    text = " ".join(ctx.args) if ctx.args else (msg.text or "")
    await handle_text(update, text)


async def handle_text(update: Update, text: str) -> None:
    msg = must_msg(update)
    try:
        draft = jev.parse_invoice(text)
    except Exception as e:  # noqa: BLE001 — surface, don't die in chat
        log.exception("parse failed")
        await msg.reply_text(f"Couldn't parse that ({e}). Try: charge <who> <amount> <ccy>.")
        return
    if not draft.get("is_invoice"):
        await msg.reply_text("That doesn't look like an invoice. Try: charge <who> <amount> <ccy>.")
        return
    if not draft.get("amount"):
        await msg.reply_text(
            "I couldn't find a single amount I can price in that sentence. "
            "Try: charge <who> <amount> <ccy> (e.g. charge Marco 500 BRL)."
        )
        return
    from . import x402
    priceable, detail = x402.can_price(draft.get("amount"), draft.get("currency"))
    if not priceable:
        await msg.reply_text(
            f"Can't price that ({detail}). Settleable today: USDT, USDC, USAT, "
            "or BRL/ARS/MXN/COP/USD which settle in USA₮."
        )
        return
    try:
        screen = jev.screen_invoice(draft, store.recent_descriptions())
    except Exception:  # noqa: BLE001 — screening is a gate, not an ornament
        log.exception("screen failed")
        await msg.reply_text(
            "Screening is unavailable right now, so I won't mint this draft. "
            "Try again in a moment."
        )
        return
    decision = screen.get("decision")
    if decision == "block":
        await msg.reply_text(
            f"Blocked: looks risky (suspicion {screen['suspicious_p']:.2f}). "
            "Refine it or talk to a human."
        )
        return
    if decision != "settle":
        # "review" is a hold: it must not reach the mint button.
        await msg.reply_text(
            f"Held for review (risk {screen.get('risk_level')}). "
            "An operator has to release this one."
        )
        return
    PENDING[(msg.chat_id, _user_id(msg.from_user))] = {
        "draft": draft,
        "screen": screen,
        "message_id": getattr(msg, "message_id", None),
        "chat_id": msg.chat_id,
        "user_id": _user_id(msg.from_user),
    }
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Mint pay link", callback_data="confirm"),
        InlineKeyboardButton("❌ Drop", callback_data="drop"),
    ]])
    await msg.reply_text(fmt_draft(draft, screen), reply_markup=kb)


async def on_button(update: Update, _ctx) -> None:
    q = update.callback_query
    assert q is not None, "button handler needs a callback query"
    await q.answer()
    qmsg = q.message
    assert qmsg is not None and q.data is not None
    chat_id: int = getattr(qmsg, "chat_id", None) or q.from_user.id
    key = (chat_id, _user_id(q.from_user))
    item = PENDING.get(key)
    if item is None or item.get("message_id") != getattr(qmsg, "message_id", None):
        # A foreign press, a stale draft, or a replaced slot: touch nothing.
        await q.answer(text="This draft isn't yours (or it was replaced).", show_alert=False)
        return
    PENDING.pop(key, None)
    if q.data == "drop":
        await q.edit_message_text("Dropped.")
        return
    user = q.from_user
    from . import x402
    inv = store.create(item["draft"], item["screen"],
                       creator=str(user.username or user.id),
                       created_block=x402.head_block(),
                       # The freelancer's own wallet, so a second user can be
                       # paid without an operator editing the env.
                       payout_address=store.wallet_for(user.id),
                       creator_chat_id=item.get("chat_id") or getattr(qmsg, "chat_id", None) or q.from_user.id)
    try:
        req = x402.create_payment_request(inv, store.payout_for(inv))
        settle = f"{req['amount_display']} {req['symbol']}"
        if req.get("fx"):
            settle += f" (from {req['invoice_amount_display']})"
    except (ValueError, KeyError) as e:
        settle = f"<unpriceable: {e}>"
    await q.edit_message_text(
        f"Minted {inv['id']}\n"
        f"{inv['amount']} {inv['currency']} → {inv['recipient']}\n"
        f"Payer sends: {settle}\n"
        f"Trust: {inv['trust']['decision']} ({inv['trust']['risk_level']})\n\n"
        f"Pay link (send it to your client):\n{pay_url(inv['id'])}\n\n"
        f"The agent now buys its own screening for this invoice\n"
        f"(rate check + sanctions on the wallet and name, ≈$0.009) and watches\n"
        f"Celo to settle it by itself. /receipt {inv['id']}"
    )


async def pay(update: Update, ctx) -> None:
    msg = must_msg(update)
    if not ctx.args:
        await msg.reply_text("Usage: /pay INV-0001")
        return
    inv = store.get(ctx.args[0])
    if not inv:
        await msg.reply_text("Unknown invoice.")
        return
    if inv["status"] == "settled":
        await msg.reply_text(f"{inv['id']} already settled: {inv['settlement_tx']}")
        return
    from . import x402
    wallet = store.payout_for(inv)
    if not wallet:
        await msg.reply_text(
            f"{inv['id']} is minted but has no payout address yet.\n"
            "Set yours with /wallet 0x... then ask for /pay again."
        )
        return
    try:
        req = x402.create_payment_request(inv, wallet)
    except (ValueError, KeyError) as e:
        await msg.reply_text(f"Can't price that: {e}")
        return
    detail = f"Pay {req['amount_display']} {req['symbol']} on Celo"
    if req.get("fx"):
        detail += f" (invoice {req['invoice_amount_display']})"
    await msg.reply_text(
        f"{detail}\n\n{pay_url(inv['id'])}\n\n"
        f"Address: `{req['pay_to']}`\n"
        f"Token: `{req['asset_contract']}`\n"
        "The page confirms on its own once the transfer lands; /check forces it."
    )


async def check(update: Update, ctx) -> None:
    msg = must_msg(update)
    if not ctx.args:
        await msg.reply_text("Usage: /check INV-0001")
        return
    inv = store.get(ctx.args[0])
    if not inv:
        await msg.reply_text("Unknown invoice.")
        return
    if inv["status"] == "settled":
        # Settlement is terminal; re-scanning must not re-bind the witness.
        await msg.reply_text(f"{inv['id']} already settled: {inv['settlement_tx']}")
        return
    from . import x402
    wallet = store.payout_for(inv)
    if not wallet:
        await msg.reply_text("No payout address on this invoice yet (/wallet 0x...).")
        return
    try:
        req = x402.create_payment_request(inv, wallet)
    except (ValueError, KeyError) as e:
        await msg.reply_text(f"Can't price that: {e}")
        return
    try:
        hits = x402.watch_transfers(
            wallet, req["symbol"], min_atomic=req["amount_atomic"],
            # Anchor the window at the invoice's creation block; without it a
            # transfer mined before the invoice existed can serve as its proof.
            from_block=inv.get("created_block") or "latest",
        )
    except Exception as e:  # noqa: BLE001
        log.exception("watch failed")
        await msg.reply_text(f"Chain lookup hiccup ({e}). Try /check again.")
        return
    # A witness already credited to another invoice cannot settle this one.
    fresh = [h for h in hits if not store.tx_used(h["tx"])]
    if not fresh:
        await msg.reply_text(
            f"{inv['id']}: no {req['symbol']} received yet. "
            f"Send {req['amount_display']} {req['symbol']} to `{wallet}`, then /check again."
        )
        return
    inv_id = inv["id"]
    settled = store.mark_settled(inv_id, fresh[0]["tx"])
    if settled is None:
        await msg.reply_text(
            f"{inv_id}: that transfer already settled another invoice. "
            "Ask the payer to send a fresh transfer."
        )
        return
    await msg.reply_text(
        f"{inv_id} SETTLED ✅\nTx: `{settled['settlement_tx']}`"
    )


def services_block(inv: dict) -> str:
    """What the agent bought for this invoice, for /receipt."""
    receipts = inv.get("services") or []
    status = inv.get("services_status")
    if not receipts:
        return f"\nServices bought: {status or 'none yet'}"
    lines = [f"\nServices bought (${inv.get('services_spent_usd') or 0}):"]
    for r in receipts:
        if not r.get("ok"):
            lines.append(f"  · {r.get('slug')} — failed ({r.get('error')})")
            continue
        verdict = (r.get("result") or {}).get("verdict") or "ok"
        tx = (r.get("tx") or "")[:12]
        verified = "✓onchain" if (r.get("onchain") or {}).get("verified") else "unverified"
        lines.append(f"  · {r.get('slug')} — {verdict} ({verified}) {tx}…")
    if inv.get("status") == "blocked":
        lines.append(f"BLOCKED: {inv.get('blocked_reason')}")
    return "\n".join(lines)


async def receipt(update: Update, ctx) -> None:
    msg = must_msg(update)
    if not ctx.args:
        await msg.reply_text("Usage: /receipt INV-0001")
        return
    inv = store.get(ctx.args[0])
    if not inv:
        await msg.reply_text("Unknown invoice.")
        return
    await msg.reply_text(
        f"{inv['id']} — {inv['status']}\n"
        f"{inv['amount']} {inv['currency']} → {inv['recipient']}\n"
        f"Trust: {inv['trust']['decision']} ({inv['trust']['risk_level']})\n"
        f"Settlement: {inv['settlement_tx'] or 'pending'}"
        f"{services_block(inv)}\n\n"
        f"{pay_url(inv['id'])}"
    )


async def wallet(update: Update, ctx) -> None:
    """Register where this user gets paid — the multi-tenant unlock.

    Without a per-user payout address every invoice paid the operator's wallet,
    so a second freelancer could not be onboarded at all.
    """
    msg = must_msg(update)
    if not ctx.args:
        current = store.wallet_for(_user_id(msg.from_user))
        await msg.reply_text(
            f"Usage: /wallet 0xYourCeloAddress\nCurrent: {current or 'not set (falls back to the operator wallet)'}"
        )
        return
    address = ctx.args[0].strip()
    try:
        store.set_wallet(_user_id(msg.from_user), address)
    except ValueError as e:
        await msg.reply_text(f"{e}. Send a 0x-prefixed 20-byte address.")
        return
    await msg.reply_text(f"Payout wallet set to {address}\nNew invoices pay you directly on Celo.")


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("wallet", wallet))
    app.add_handler(CommandHandler("new", new_invoice))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(CommandHandler("receipt", receipt))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, new_invoice))
    app.run_polling()


if __name__ == "__main__":
    main()

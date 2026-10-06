"""Telegram bot: sentence -> Jev parse -> confirm -> invoice + trust score.

/start, /new <sentence> (or just send the sentence), /pay INV-0001, /receipt INV-0001
Settlement wiring (x402/Textile/buy) lands in their modules; bot calls them.
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

PENDING: dict[int, dict] = {}  # chat_id -> draft awaiting confirm


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


async def start(update: Update, _ctx) -> None:
    await must_msg(update).reply_text(
        "Send me an invoice as a sentence, e.g.\n"
        "`charge Marco 500 BRL for the logo, due Friday`\n"
        "I'll parse it, screen it, and mint a USA₮ pay link."
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
    try:
        screen = jev.screen_invoice(draft, store.recent_descriptions())
    except Exception:  # noqa: BLE001 — fail open to review, never to silent settle
        log.exception("screen failed")
        screen = {"decision": "review", "risk_level": "review", "suspicious_p": 1.0}
    if screen["decision"] == "block":
        await msg.reply_text(
            f"Blocked: looks risky (suspicion {screen['suspicious_p']:.2f}). "
            "Refine it or talk to a human."
        )
        return
    PENDING[msg.chat_id] = {"draft": draft, "screen": screen}
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
    if q.data == "drop" or chat_id not in PENDING:
        PENDING.pop(chat_id, None)
        await q.edit_message_text("Dropped.")
        return
    user = q.from_user
    item = PENDING.pop(chat_id)
    inv = store.create(item["draft"], item["screen"],
                       creator=str(user.username or user.id))
    # Settlement URL arrives with x402 wiring; receipt carries trust either way.
    await q.edit_message_text(
        f"Minted {inv['id']}\n"
        f"{inv['amount']} {inv['currency']} → {inv['recipient']}\n"
        f"Trust: {inv['trust']['decision']} ({inv['trust']['risk_level']})\n"
        f"Pay: /pay {inv['id']}   Receipt: /receipt {inv['id']}"
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
    from . import x402  # deferred: raises NotImplementedError until wired
    try:
        req = x402.create_payment_request(inv)
        await msg.reply_text(f"Pay {req['pay_url']}")
    except NotImplementedError:
        await msg.reply_text(
            f"{inv['id']}: USA₮ settlement wiring lands this week. "
            f"Invoice is minted and trust-screened; payer link activates on x402 hookup."
        )


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
    )


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_invoice))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("receipt", receipt))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, new_invoice))
    app.run_polling()


if __name__ == "__main__":
    main()

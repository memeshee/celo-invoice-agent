#!/usr/bin/env python3
"""Set the bot's public profile: name, descriptions and the command menu.

These live in Telegram's servers, not in the repo, so without this script a fresh
deploy has a bot with no name, no description and no menu button — which is exactly
what a judge saw: an empty pane where they were expected to know what to type.
Idempotent: safe to re-run after changing HELP_TEXT or adding a command.
"""

from __future__ import annotations

import json
import os
import urllib.request

import dotenv

dotenv.load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                ".env"))
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

NAME = "impay"
SHORT = ("Send one sentence, get a payable invoice on Celo. "
         "Settlement is proven from the chain.")
DESCRIPTION = (
    "impay turns one sentence into a settled stablecoin invoice on Celo.\n\n"
    "Send: charge Marco 500 BRL for the logo, due Friday\n\n"
    "I parse it, buy the sanctions screens for that invoice over x402, and reply "
    "with a pay link anyone can open. The payer sends a plain stablecoin transfer; "
    "the invoice settles when Celo proves the payment — no confirm button.\n\n"
    "Set where you get paid with /wallet 0xYourAddress. /help lists every command."
)
COMMANDS = [
    {"command": "start", "description": "How to use impay"},
    {"command": "help", "description": "Every command and one example"},
    {"command": "wallet", "description": "Show or set where you get paid"},
    {"command": "new", "description": "Create an invoice from a sentence"},
    {"command": "check", "description": "Re-check the chain for an invoice now"},
    {"command": "receipt", "description": "What the agent bought for an invoice"},
    {"command": "held", "description": "Invoices waiting on a human"},
]


def call(method: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> int:
    for method, payload in (
        ("setMyName", {"name": NAME}),
        ("setMyShortDescription", {"short_description": SHORT}),
        ("setMyDescription", {"description": DESCRIPTION}),
        ("setMyCommands", {"commands": COMMANDS}),
    ):
        out = call(method, payload)
        print(f"  {method}: {'ok' if out.get('ok') else out.get('description')}")
        if not out.get("ok"):
            return 1
    print("bot profile set — the menu button and the empty-chat panel now say what to do")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

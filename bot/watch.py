"""Autonomous settlement watcher — the part that makes this an agent.

Before this, settlement only happened when a human typed `/check`. That makes
the bot a parser with a cron habit, not an agent: nothing closed the loop on its
own, and a payer who simply sent the money left the invoice dangling.

This loop polls Celo for every open invoice, settles the ones that were paid,
and pushes the receipt to the freelancer.

Why Blockscout and not eth_getLogs: free Celo RPCs cannot serve day-scale log
history (forno rejects topic filters outright, publicnode caps a request at
~3000 blocks, ankr refuses large ranges). A head-relative log window silently
stops finding payments as an invoice ages — the exact failure that makes a
settled invoice read as unpaid.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

from dotenv import load_dotenv

from . import store, x402

load_dotenv()

BLOCKSCOUT = "https://celo.blockscout.com/api"
POLL_INTERVAL_S = int(os.environ.get("WATCH_INTERVAL_S", "45"))
UA = "celo-invoice-agent/1.0"


def transfers_to(address: str, *, fetcher=None) -> list[dict]:
    """Every ERC-20 transfer into `address`, newest first, normalized."""
    if fetcher is not None:
        return fetcher(address)
    query = urllib.parse.urlencode({
        "module": "account", "action": "tokentx", "address": address,
        "offset": "100", "sort": "desc",
    })
    req = urllib.request.Request(f"{BLOCKSCOUT}?{query}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        body = json.load(r)
    if str(body.get("status")) != "1":
        return []
    out = []
    for t in body.get("result") or []:
        try:
            out.append({
                "tx": str(t["hash"]).lower(),
                "to": str(t["to"]).lower(),
                "contract": str(t["contractAddress"]).lower(),
                "value": int(t["value"]),
                "block": int(t["blockNumber"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def find_settlement(invoice: dict, transfers: list[dict]) -> dict | None:
    """The transfer that settles this invoice, or None.

    Oldest qualifying transfer wins, so a payer who sent twice does not have the
    later one bound to an invoice the first one already paid.
    """
    pay_to = store.payout_for(invoice)
    if not pay_to:
        return None
    try:
        req = x402.create_payment_request(invoice, pay_to)
    except (ValueError, KeyError, RuntimeError):
        return None  # unpriceable now (e.g. FX outage): try again next sweep
    asset = req["asset_contract"].lower()
    needed = int(req["amount_atomic"])
    floor = int(invoice.get("created_block") or 0)
    hits = [
        t for t in transfers
        if t["to"] == pay_to.lower()
        and t["contract"] == asset
        and t["value"] >= needed
        and t["block"] >= floor
        and store.tx_used(t["tx"]) is None
    ]
    if not hits:
        return None
    return sorted(hits, key=lambda t: t["block"])[0]


def notify(chat_id: int | None, text: str, *, sender=None) -> bool:
    """Push to Telegram. Never raises: a failed ping must not unpick a settlement."""
    if not chat_id:
        return False
    if sender is not None:
        return sender(chat_id, text)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return False
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                      "disable_web_page_preview": "true"}).encode()
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                     data=payload, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except Exception as e:  # noqa: BLE001 — logging is the point, not the raise
        print(f"[watch] notify failed: {type(e).__name__}: {e}", flush=True)
        return False


def sweep(*, fetcher=None, notifier=None, log=print) -> list[dict]:
    """One pass over every open invoice. Returns settlements made."""
    settled = []
    open_invs = store.open_invoices()
    if not open_invs:
        return settled
    cache: dict[str, list[dict]] = {}
    for inv in open_invs:
        pay_to = store.payout_for(inv)
        if not pay_to:
            continue
        if pay_to.lower() not in cache:
            try:
                cache[pay_to.lower()] = transfers_to(pay_to, fetcher=fetcher)
            except Exception as e:  # noqa: BLE001
                log(f"[watch] fetch failed for {pay_to}: {type(e).__name__}: {e}")
                continue
        hit = find_settlement(inv, cache[pay_to.lower()])
        if not hit:
            continue
        done = store.mark_settled(inv["id"], hit["tx"])
        if done is None:
            continue
        settled.append(done)
        log(f"[watch] {inv['id']} settled by {hit['tx']}")
        notify(done.get("creator_chat_id"),
               f"{done['id']} settled ✅\n"
               f"{done['amount']} {done['currency']} → {done['recipient']}\n"
               f"tx: {hit['tx']}\n"
               f"https://celo.blockscout.com/tx/{hit['tx']}", sender=notifier)
    return settled


def main() -> None:
    print(f"[watch] polling every {POLL_INTERVAL_S}s", flush=True)
    while True:
        try:
            sweep()
        except Exception as e:  # noqa: BLE001 — the loop must outlive any single pass
            print(f"[watch] sweep crashed: {type(e).__name__}: {e}", flush=True)
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()

"""JSON invoice store. Single-file, no deps. Fine for hackathon scale.

Security fixes (audit run-1):
- unbound-settlement-proof / settled-receipt-rewrite-on-recheck: the store now
  records the invoice's creation block, keeps a consumed-witness registry so one
  on-chain transfer can settle at most one invoice, and never rewrites a
  settled invoice.
- invoice-lookup-not-scoped-to-requester: ids carry a random suffix, so the
  handle is a capability instead of a guessable counter.

Multi-tenant (Phase 1): each invoice carries its own payout address, and a user
registers the wallet they get paid at. Before this, every invoice paid one
global INVOICE_WALLET, so onboarding a second freelancer — an "independent
user" in the rules — was structurally impossible.
"""

import json
import os
import re
import secrets
import time

STORE_PATH = os.environ.get("INVOICE_STORE", "invoices.json")

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _load() -> dict:
    try:
        with open(STORE_PATH) as f:
            db = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        db = {"seq": 0, "invoices": {}}
    db.setdefault("used_tx", {})
    db.setdefault("wallets", {})
    return db


def _save(db: dict) -> None:
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(db, f, indent=2)
    os.replace(tmp, STORE_PATH)


# ------------------------------------------------------------------- payouts

def set_wallet(user_id: int | str, address: str) -> str:
    """Register the wallet a user gets paid at."""
    if not ADDRESS_RE.match(address or ""):
        raise ValueError(f"not an address: {address!r}")
    db = _load()
    db["wallets"][str(user_id)] = {"address": address, "set": int(time.time())}
    _save(db)
    return address


def wallet_for(user_id: int | str) -> str | None:
    return (_load()["wallets"].get(str(user_id)) or {}).get("address")


def payout_for(invoice: dict) -> str:
    """Invoice payout address, falling back to the operator wallet.

    Resolving at read time (not write time) keeps invoices minted before
    per-invoice routing working with no migration.
    """
    return invoice.get("payout_address") or os.environ.get("INVOICE_WALLET", "")


# ------------------------------------------------------------------ invoices

def create(draft: dict, screen: dict, creator: str,
           created_block: int | None = None,
           payout_address: str | None = None,
           creator_chat_id: int | None = None) -> dict:
    db = _load()
    db["seq"] += 1
    db.setdefault("used_tx", {})
    # Guessable ids let any chat participant enumerate every invoice; the
    # random suffix makes possession of the id the capability.
    inv_id = f"INV-{db['seq']:04d}-{secrets.token_hex(3).upper()}"
    inv = {
        "id": inv_id,
        "created": int(time.time()),
        "created_block": created_block,
        "creator": creator,
        "creator_chat_id": creator_chat_id,
        "amount": draft.get("amount"),
        "currency": draft.get("currency"),
        "recipient": draft.get("recipient"),
        "raw": draft.get("raw"),
        "payout_address": payout_address,
        "trust": {
            "decision": screen.get("decision"),
            "suspicious_p": screen.get("suspicious_p"),
            "risk_level": screen.get("risk_level"),
        },
        "status": "open",
        "settlement_tx": None,
    }
    db["invoices"][inv_id] = inv
    _save(db)
    return inv


def get(inv_id: str) -> dict | None:
    return _load()["invoices"].get(str(inv_id).upper())


def open_invoices() -> list[dict]:
    """Every invoice still awaiting settlement — the watcher's work queue."""
    return [i for i in _load()["invoices"].values() if i.get("status") == "open"]


def tx_used(tx_hash: str) -> str | None:
    """Invoice id that already consumed this witness transfer, if any."""
    used = _load().get("used_tx") or {}
    return used.get(str(tx_hash).lower())


def mark_settled(inv_id: str, tx_hash: str) -> dict | None:
    """Settle an invoice once, against an unconsumed witness.

    Returns the invoice unchanged when it is already settled, and None when the
    transfer has already settled a different invoice.
    """
    db = _load()
    inv = db["invoices"].get(str(inv_id).upper())
    if not inv:
        return None
    if inv["status"] == "settled":
        return inv  # terminal: never re-bind settlement_tx on a re-check
    used = db.setdefault("used_tx", {})
    key = str(tx_hash).lower()
    owner = used.get(key)
    if owner is not None and owner != inv["id"]:
        return None  # one transfer settles at most one invoice
    used[key] = inv["id"]
    inv["status"] = "settled"
    inv["settlement_tx"] = tx_hash
    _save(db)
    return inv


def recent_descriptions(limit: int = 20) -> list[str]:
    db = _load()
    invs = sorted(db["invoices"].values(), key=lambda i: i["created"], reverse=True)
    return [f"{i['recipient']} {i['amount']} {i['currency']}" for i in invs[:limit]]

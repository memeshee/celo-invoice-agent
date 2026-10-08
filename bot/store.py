"""JSON invoice store. Single-file, no deps. Fine for hackathon scale.

Security fixes (audit run-1):
- unbound-settlement-proof / settled-receipt-rewrite-on-recheck: the store now
  records the invoice's creation block, keeps a consumed-witness registry so one
  on-chain transfer can settle at most one invoice, and never rewrites a
  settled invoice.
- invoice-lookup-not-scoped-to-requester: ids carry a random suffix, so the
  handle is a capability instead of a guessable counter.
"""

import json
import os
import secrets
import time

STORE_PATH = os.environ.get("INVOICE_STORE", "invoices.json")


def _load() -> dict:
    try:
        with open(STORE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seq": 0, "invoices": {}, "used_tx": {}}


def _save(db: dict) -> None:
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(db, f, indent=2)
    os.replace(tmp, STORE_PATH)


def create(draft: dict, screen: dict, creator: str,
           created_block: int | None = None) -> dict:
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
        "amount": draft.get("amount"),
        "currency": draft.get("currency"),
        "recipient": draft.get("recipient"),
        "raw": draft.get("raw"),
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

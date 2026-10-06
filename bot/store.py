"""JSON invoice store. Single-file, no deps. Fine for hackathon scale."""

import json
import os
import time

STORE_PATH = os.environ.get("INVOICE_STORE", "invoices.json")


def _load() -> dict:
    try:
        with open(STORE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seq": 0, "invoices": {}}


def _save(db: dict) -> None:
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(db, f, indent=2)
    os.replace(tmp, STORE_PATH)


def create(draft: dict, screen: dict, creator: str) -> dict:
    db = _load()
    db["seq"] += 1
    inv_id = f"INV-{db['seq']:04d}"
    inv = {
        "id": inv_id,
        "created": int(time.time()),
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
    return _load()["invoices"].get(inv_id.upper())


def mark_settled(inv_id: str, tx_hash: str) -> dict | None:
    db = _load()
    inv = db["invoices"].get(inv_id.upper())
    if not inv:
        return None
    inv["status"] = "settled"
    inv["settlement_tx"] = tx_hash
    _save(db)
    return inv


def recent_descriptions(limit: int = 20) -> list[str]:
    db = _load()
    invs = sorted(db["invoices"].values(), key=lambda i: i["created"], reverse=True)
    return [f"{i['recipient']} {i['amount']} {i['currency']}" for i in invs[:limit]]

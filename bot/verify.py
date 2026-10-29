"""Prove a settlement claim against the chain — the one place that decides.

Every other module asks this one. It exists because a mechanical filter is not a
proof: INV-0001 was marked settled by a `callDiamondWithPermit2` swap that the
payout wallet itself initiated, which moved 1.06 USA₮ "into" the wallet and so
satisfied recipient + amount + asset. It sat in the store as a paid invoice for
weeks. The transaction *initiator* is the tell, and it is only visible at the
transaction level — not on the individual transfer.

Rules, all of which must hold:
- the transaction exists, succeeded, and was not initiated by the payee;
- it contains a Transfer of the expected token to the payout address;
- for at least the expected atomic amount, sent from someone other than the payee.

RPC first with retries (Forno intermittently returns null for old receipts), and
Blockscout v2 as fallback, so a flaky node cannot manufacture a failure either.
"""

from __future__ import annotations

import json
import time
import urllib.request

from . import chain, store, x402

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
BLOCKSCOUT_TX = "https://celo.blockscout.com/api/v2/transactions/"
UA = "celo-invoice-agent/1.0"


def _topic_addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def _rpc_receipt(client, tx: str, attempts: int = 3):
    for i in range(attempts):
        try:
            rcpt = client.eth.get_transaction_receipt(tx)
            if rcpt is not None:
                return rcpt
        except Exception:  # noqa: BLE001 — absence here is not proof of absence
            pass
        if i + 1 < attempts:
            time.sleep(2)
    return None


def _explorer_tx(tx: str) -> dict | None:
    try:
        req = urllib.request.Request(BLOCKSCOUT_TX + tx, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except Exception:  # noqa: BLE001
        return None


def payment_proof(invoice: dict, *, tx: str | None = None, web3=None) -> dict:
    """Does this invoice's cited tx actually pay it?"""
    tx = (tx or invoice.get("settlement_tx") or "").lower()
    pay_to = store.payout_for(invoice)
    out = {"id": invoice.get("id"), "tx": tx, "pay_to": pay_to, "ok": False, "reason": ""}
    if not tx:
        out["reason"] = "no settlement tx to check"
        return out
    if not pay_to:
        out["reason"] = "invoice has no payout address"
        return out
    try:
        req = x402.create_payment_request(invoice, pay_to)
    except (ValueError, KeyError, RuntimeError) as e:
        out["reason"] = f"cannot price: {e}"
        return out
    want_asset = req["asset_contract"].lower()
    want_value = int(req["amount_atomic"])
    client = web3 or chain.w3()

    transfers: list[tuple[str, str, int]] = []   # (from, to, value)
    tx_from = ""
    rcpt = _rpc_receipt(client, tx)
    if rcpt is not None:
        if rcpt["status"] != 1:
            out["reason"] = "transaction reverted"
            return out
        try:
            full = client.eth.get_transaction(tx)
            tx_from = (full["from"] or "").lower() if full else ""
        except Exception:  # noqa: BLE001
            tx_from = ""
        for lg in rcpt["logs"]:
            topics = [t.hex() if isinstance(t, bytes) else t for t in lg["topics"]]
            if not topics or ("0x" + topics[0].lstrip("0x")).lower() != TRANSFER_TOPIC:
                continue
            token = (lg["address"].hex() if isinstance(lg["address"], bytes) else lg["address"]).lower()
            token = token if token.startswith("0x") else "0x" + token
            if token != want_asset or len(topics) < 3:
                continue
            raw = lg["data"].hex() if isinstance(lg["data"], bytes) else lg["data"]
            transfers.append((_topic_addr(topics[1]), _topic_addr(topics[2]), int(raw, 16)))
        out["source"] = "rpc"
    else:
        d = _explorer_tx(tx)
        if not d:
            out["reason"] = "no RPC or explorer could serve this transaction"
            return out
        tx_from = ((d.get("from") or {}).get("hash") or "").lower()
        for t in d.get("token_transfers") or []:
            token = ((t.get("token") or {}).get("address_hash") or "").lower()
            if token != want_asset:
                continue
            try:
                value = int((t.get("total") or {}).get("value") or 0)
            except (TypeError, ValueError):
                continue
            transfers.append((((t.get("from") or {}).get("hash") or "").lower(),
                              ((t.get("to") or {}).get("hash") or "").lower(), value))
        out["source"] = "blockscout"

    out["from"] = tx_from
    if tx_from and tx_from == pay_to.lower():
        out["reason"] = ("transaction was initiated by the payout address itself "
                         "(swap/top-up, not a payment)")
        return out
    for frm, to, value in transfers:
        if to == pay_to.lower() and value >= want_value and frm != pay_to.lower():
            out.update(ok=True, value=value, reason="verified")
            return out
    out["reason"] = (f"no {req.get('symbol')} payment to {pay_to[:10]}… of at least "
                     f"{want_value} (saw {len(transfers)} transfers)")
    return out

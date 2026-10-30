"""The public half of the store: settlement claims anyone can re-derive.

The invoice store is private (`invoices.json`, gitignored) because it holds chat
ids, raw user sentences and counterparty names. A settlement *claim* needs none
of that — an id, an amount, a payout address and a transaction hash are enough
for a stranger to check the payment against Celo themselves.

That split is what makes the daily audit real. `verify-claims` runs in a fresh
checkout with no store, so before this it had nothing to read and the job failed
on a missing file; reading a committed snapshot instead means the workflow
verifies something the README actually claims, and cannot silently pass because
the store happened to be empty.

`scripts/export_claims.py` regenerates the file on demand, and the store refreshes
it on every settle/withdraw so the snapshot cannot drift from the store it came
from.
"""

from __future__ import annotations

import json
import os
import time

DEFAULT_PATH = "claims.json"

# Public fields only. Never add description/raw/recipient/creator_chat_id here:
# the point of the snapshot is that it can be committed to a public repo.
PUBLIC_FIELDS = ("id", "amount", "currency", "settle_asset",
                 "payout_address", "settlement_tx", "created")

NOTE = ("Settled invoices exported from the private store, public fields only. "
        "scripts/verify_settlements.py re-derives each one from Celo; the "
        "verify-claims workflow fails when one stops holding.")


def path() -> str:
    return os.environ.get("CLAIMS_PATH", DEFAULT_PATH)


def public_claim(inv: dict) -> dict:
    """One invoice -> the tuple of facts a verifier needs, and nothing else."""
    claim = {k: inv.get(k) for k in PUBLIC_FIELDS}
    # Resolve the payout address the way the verifier will, so the snapshot does
    # not depend on INVOICE_WALLET being set in whatever environment reads it.
    claim["payout_address"] = (inv.get("payout_address")
                               or os.environ.get("INVOICE_WALLET") or "")
    return claim


def snapshot(db: dict, *, generated_at: int | None = None) -> dict:
    """Every settled invoice in the store, as public claims."""
    settled = [i for i in (db.get("invoices") or {}).values()
               if i.get("status") == "settled" and i.get("settlement_tx")]
    claims = [public_claim(i) for i in sorted(settled, key=lambda i: i.get("created") or 0)]
    return {
        "chain": "celo",
        "chain_id": 42220,
        "note": NOTE,
        "generated_at": int(generated_at or time.time()),
        "count": len(claims),
        "claims": claims,
    }


def read(where: str | None = None) -> dict:
    with open(where or path()) as fh:
        return json.load(fh)


def write(db: dict, where: str | None = None, *, generated_at: int | None = None) -> dict:
    """Atomically replace the snapshot. Returns what was written."""
    dest = where or path()
    snap = snapshot(db, generated_at=generated_at)
    tmp = dest + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(snap, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, dest)
    return snap

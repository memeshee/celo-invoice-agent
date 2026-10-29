"""Re-verify every settlement claim in the store against the chain.

A receipt that cites a transaction which is not actually a payment to us is worse
than no receipt: it is the one artefact a judge will check, and it was real — an
invoice was bound to a swap its own payout wallet initiated.

The decision logic lives in bot/verify.py so the watcher and this audit cannot
disagree about what counts as a payment.

    .venv/bin/python scripts/verify_settlements.py [--fix]

Exit code is non-zero when any claim fails to verify.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

from bot import store, verify  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))


def main() -> int:
    fix = "--fix" in sys.argv
    with open(store.STORE_PATH) as fh:
        settled = [i for i in json.load(fh)["invoices"].values()
                   if i.get("status") == "settled"]
    if not settled:
        print("no settled invoices")
        return 0
    bad = 0
    for inv in sorted(settled, key=lambda i: i.get("created") or 0):
        r = verify.payment_proof(inv)
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"{mark} {r['id']:20s} {(r['tx'] or '')[:18]}…  {r['reason']}")
        if not r["ok"]:
            bad += 1
            if fix:
                store.unsettle(inv["id"], reason=r["reason"])
                print(f"     -> marked {inv['id']} unverified (back in the open queue)")
    print(f"\n{len(settled) - bad}/{len(settled)} settlement claims verify onchain")
    return 1 if bad and not fix else 0


if __name__ == "__main__":
    raise SystemExit(main())

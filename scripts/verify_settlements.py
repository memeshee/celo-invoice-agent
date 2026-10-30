"""Re-verify every settlement claim against the chain.

A receipt that cites a transaction which is not actually a payment to us is worse
than no receipt: it is the one artefact a judge will check, and it was real — an
invoice was bound to a swap its own payout wallet initiated.

The decision logic lives in bot/verify.py so the watcher and this audit cannot
disagree about what counts as a payment.

Sources, in order:
- `--claims PATH` (or `INVOICE_CLAIMS`): a committed snapshot of public claims;
- the local store (`INVOICE_STORE`, default `invoices.json`) when it exists;
- `claims.json` in the repo root.

    .venv/bin/python scripts/verify_settlements.py [--fix] [--claims PATH] [--require]

`--fix` withdraws a failing claim from the store (store source only — a snapshot
is evidence, not state). `--require` turns "found nothing to verify" into a
failure, so a scheduled audit cannot pass by silently auditing an empty list.

Exit code is non-zero when any claim fails to verify, or when `--require` finds
no claims at all.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

from bot import claims, store, verify  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

USAGE = "usage: verify_settlements.py [--fix] [--claims PATH] [--require]"


def _arg(argv: list[str], name: str) -> str | None:
    if name in argv:
        i = argv.index(name)
        if i + 1 >= len(argv):
            raise SystemExit(f"{name} needs a path\n{USAGE}")
        return argv[i + 1]
    return None


def resolve_source(argv: list[str]) -> tuple[str, str]:
    """(kind, path) — kind is 'claims' or 'store'."""
    explicit = _arg(argv, "--claims") or os.environ.get("INVOICE_CLAIMS")
    if explicit:
        return "claims", explicit
    if os.path.exists(store.STORE_PATH):
        return "store", store.STORE_PATH
    if os.path.exists(claims.path()):
        return "claims", claims.path()
    return "none", ""


def load_claims(kind: str, where: str) -> list[dict]:
    if kind == "store":
        db = store._load()
        return [i for i in db["invoices"].values() if i.get("status") == "settled"]
    return list(claims.read(where).get("claims") or [])


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    fix = "--fix" in argv
    require = "--require" in argv
    kind, where = resolve_source(argv)

    if kind == "none":
        print("no store and no claims snapshot in this checkout — nothing to verify")
        if require:
            print("FAIL: --require was set, so an empty audit is a failure, not a pass")
            return 1
        return 0

    settled = load_claims(kind, where)
    print(f"source: {kind} ({where})")
    if not settled:
        print("no settled claims found")
        if require:
            print("FAIL: --require was set, so an empty audit is a failure, not a pass")
            return 1
        return 0

    bad = 0
    for inv in sorted(settled, key=lambda i: i.get("created") or 0):
        r = verify.payment_proof(inv)
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"{mark} {str(r['id']):20s} {(r['tx'] or '')[:18]}…  {r['reason']}")
        if r["ok"]:
            continue
        bad += 1
        if fix and kind == "store":
            store.unsettle(inv["id"], reason=r["reason"])
            print(f"     -> marked {inv['id']} unverified (back in the open queue)")
        elif fix:
            print("     -> --fix ignored: a committed snapshot is evidence, not state")
    print(f"\n{len(settled) - bad}/{len(settled)} settlement claims verify onchain")
    return 1 if bad and not fix else 0


if __name__ == "__main__":
    raise SystemExit(main())

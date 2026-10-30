"""Regenerate claims.json — the public settlement claims the audit verifies.

    .venv/bin/python scripts/export_claims.py [--out claims.json]

Run it after settling (or withdrawing) an invoice and commit the result: the
daily `verify-claims` workflow has no store to read, so this file is the only
thing it can hold the README's numbers to.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

from bot import claims, store  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    out = claims.path()
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
    db = store._load()
    snap = claims.write(db, out)
    for c in snap["claims"]:
        print(f"{c['id']:20s} {c['settlement_tx']}  {c['amount']} {c['currency']}")
    print(f"\n{snap['count']} public claim(s) written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

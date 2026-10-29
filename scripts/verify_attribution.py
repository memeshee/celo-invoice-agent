#!/usr/bin/env python3
"""Prove our transactions carry the ERC-8021 code this entry is scored against.

Decodes the suffix from calldata fetched over RPC — it does not ask our own
encoder whether our own encoder worked. Any listed transaction that is untagged,
or tagged with a code the program does not credit, fails the run.

    .venv/bin/python scripts/verify_attribution.py            # every recorded tx
    .venv/bin/python scripts/verify_attribution.py 0xabc…      # one tx
"""

from __future__ import annotations

import glob
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

from bot import attribution  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

RPC = os.environ.get("CELO_RPC_URL", "https://forno.celo.org")
MARKER = bytes.fromhex("8021" * 8)  # 16-byte ERC-8021 marker, at the END


def get_input(tx_hash: str) -> str:
    req = urllib.request.Request(
        RPC,
        data=json.dumps({"jsonrpc": "2.0", "id": 1,
                         "method": "eth_getTransactionByHash",
                         "params": [tx_hash]}).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "impay-verify-attribution/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    result = body.get("result")
    if not result:
        raise LookupError(f"node has no transaction {tx_hash}")
    return result.get("input") or "0x"


def decode(raw_hex: str) -> dict:
    """Independent Schema 0 decoder: codes || len || schema || marker."""
    raw = bytes.fromhex(raw_hex.removeprefix("0x"))
    if len(raw) < 18 or raw[-16:] != MARKER:
        return {"tagged": False}
    length, schema = raw[-18], raw[-17]
    if not length or len(raw) < 18 + length:
        return {"tagged": False}
    codes = raw[-18 - length:-18].decode("utf-8", errors="replace").split(",")
    return {"tagged": True, "schema": schema, "codes": codes}


def recorded() -> list[tuple[str, str]]:
    """Every transaction this repo claims as tagged evidence."""
    out: list[tuple[str, str]] = []
    for path in sorted(glob.glob(os.path.join(ROOT, "docs", "receipts", "attest-*.json"))):
        d = json.load(open(path))
        out.append((d["tx"], os.path.basename(path)))
    for path in sorted(glob.glob(os.path.join(ROOT, "docs", "receipts", "*.json"))):
        if "/attest-" in path:
            continue
        try:
            d = json.load(open(path))
        except Exception:
            continue
        for key in ("tx", "transaction", "hash"):
            v = d.get(key) if isinstance(d, dict) else None
            if isinstance(v, str) and v.startswith("0x") and len(v) == 66:
                if d.get("payer", "") == "":  # keep only self-signed evidence
                    pass
                out.append((v, os.path.basename(path)))
                break
    # de-duplicate, keep order
    seen, uniq = set(), []
    for tx, src in out:
        if tx not in seen:
            seen.add(tx)
            uniq.append((tx, src))
    return uniq


def main() -> int:
    assigned = attribution.ASSIGNED_CODE
    if not attribution.credits_this_entry():
        print(f"FAIL configuration: {attribution.app_code()} does not include {assigned}")
        return 2

    targets = [(t, "argument") for t in sys.argv[1:] if t.startswith("0x")] or recorded()
    if not targets:
        print("no transactions to check — pass one, or record one with "
              "scripts/set_agent_services.py --snapshot")
        return 1

    bad = 0
    for tx, src in targets:
        try:
            d = decode(get_input(tx))
        except Exception as e:
            print(f"ERROR {tx[:14]}…  {type(e).__name__}: {e}")
            bad += 1
            continue
        if not d["tagged"]:
            print(f"UNTAGGED {tx[:14]}…  ({src})")
            bad += 1
        elif assigned not in d["codes"]:
            print(f"WRONG-CODE {tx[:14]}…  codes={d['codes']}  ({src})")
            bad += 1
        else:
            print(f"OK {tx[:14]}…  schema={d['schema']}  codes={d['codes']}  ({src})")

    print(f"\n{len(targets) - bad}/{len(targets)} transactions carry {assigned}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

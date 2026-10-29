"""Declare the agent's live services in its ERC-8004 metadata.

The registration deliberately shipped without `services[]` because declaring an
endpoint before it answers is a claim a judge can kill with one curl. It answers
now, so the card gets updated — over a transaction that is itself ERC-8021 tagged.

Idempotent: if the onchain metadata already matches what would be written, it
does nothing and says so.

    .venv/bin/python scripts/set_agent_services.py [--dry-run]
"""

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

from bot import attribution, chain  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
AGENT_ID = int(os.environ.get("ERC8004_AGENT_ID", "10005"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

ABI = [
    {"name": "tokenURI", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "tokenId", "type": "uint256"}],
     "outputs": [{"name": "", "type": "string"}]},
    {"name": "setAgentURI", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "agentId", "type": "uint256"}, {"name": "newURI", "type": "string"}],
     "outputs": []},
]


def read_metadata(w3) -> dict:
    from web3 import Web3
    contract = w3.eth.contract(address=Web3.to_checksum_address(REGISTRY), abi=ABI)
    uri = contract.functions.tokenURI(AGENT_ID).call()
    if not uri.startswith("data:application/json;base64,"):
        raise SystemExit(f"unexpected onchain URI scheme: {uri[:40]}")
    return json.loads(base64.b64decode(uri.split(",", 1)[1]))


def proof_snapshot() -> dict | None:
    """The live /proof numbers, as an onchain attestation.

    The card is the only thing an ERC-8004 reader gets from the chain, and it is
    mostly static — so a judge sees a claim, not a history. Attesting the honest
    counts once a day costs a fraction of a cent, makes the onchain record show a
    trajectory instead of a snapshot, and gives the agent a transaction of its own
    to tag with the ERC-8021 code (x402 purchases cannot carry one: the faciliator
    broadcasts them).
    """
    import urllib.request
    url = f"{PUBLIC_BASE_URL}/api/proof"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "impay-attest/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
    except Exception as e:
        print(f"snapshot: skipped ({type(e).__name__}: {e})")
        return None
    c = d.get("counts") or {}
    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "settlements_claimed": c.get("settled"),
        "settlements_verified": c.get("verified"),
        "independent_payers": c.get("independent"),
        "paid_service_calls": c.get("purchases"),
        "spend_usd": c.get("spent_usd"),
        "source": url,
    }


def desired(meta: dict) -> dict:
    """Current metadata plus the parts that are only true now."""
    out = dict(meta)
    base = PUBLIC_BASE_URL
    # The registered description said invoices "settle over Celo's x402
    # facilitator". They do not: a payer sends a plain ERC-20 transfer and we prove
    # it from the chain. x402 is what the agent spends, not what it receives — and
    # the card is the first thing an ERC-8004-aware judge reads.
    # Name and description come from bot/serve.py::agent_card — the same function
    # that serves /agent.json — instead of a second copy here. The copy is how the
    # on-chain card kept saying "Celo Invoice Agent" after the project was renamed:
    # desired() started from dict(meta), so `name` was only ever echoed back.
    from bot import serve as serve_mod
    card = serve_mod.agent_card()
    out["name"] = card["name"]
    out["description"] = card["description"]
    out["services"] = [
        # The invoice pages and the /proof page are real, public, and answer 200.
        {"name": "web", "endpoint": base},
    ]
    # Honest: false. We PAY over x402 (services, outbound); we do not accept x402
    # settlement, because a payer sends a plain ERC-20 transfer to the freelancer.
    # /agent.json and this card must say the same thing about the same agent.
    out["x402Support"] = False
    out["active"] = True
    # State the ERC-8021 code on the card itself. The registry only stores a URI,
    # so the card is the one place an ERC-8004-aware reader can learn which code
    # this agent's transactions are tagged with — and a card that omits it is how
    # an entry ends up tagging with a value nothing credits.
    out["attribution"] = {
        "standard": "ERC-8021",
        "code": attribution.ASSIGNED_CODE,
        "codes_on_wire": attribution.app_code().split(","),
    }
    if "--snapshot" in sys.argv:
        snap = proof_snapshot()
        if snap:
            out["proofSnapshot"] = snap
    out["registrations"] = [
        {"agentId": AGENT_ID, "agentRegistry": f"eip155:42220:{REGISTRY}"}
    ]
    return out


def main() -> int:
    if not PUBLIC_BASE_URL or "127.0.0.1" in PUBLIC_BASE_URL or "localhost" in PUBLIC_BASE_URL:
        raise SystemExit("PUBLIC_BASE_URL must be a public URL before declaring services[]")
    w3 = chain.w3()
    from web3 import Web3
    meta = read_metadata(w3)
    new = desired(meta)
    # Compare the whole card. Listing fields by hand meant an update that changed
    # `name` (or x402Support, or active) was reported as "already up to date" and
    # never sent — a fix that silently did nothing. The card is small; compare it all.
    ignored = {"type"}
    drift = {k: (meta.get(k), v) for k, v in new.items()
             if k not in ignored and meta.get(k) != v}
    if not drift:
        print("metadata already up to date — nothing to send")
        print(json.dumps(new.get("services"), indent=1))
        return 0
    print("card drift to write:", {k: f"{old!r} -> {new!r}" for k, (old, new) in drift.items()})

    uri = "data:application/json;base64," + base64.b64encode(
        json.dumps(new, separators=(",", ":")).encode()).decode()
    contract = w3.eth.contract(address=Web3.to_checksum_address(REGISTRY), abi=ABI)
    data = contract.encode_abi("setAgentURI", args=[AGENT_ID, uri])
    print(f"setAgentURI({AGENT_ID}, <{len(uri)} chars>) -> services:"
          f" {[s['endpoint'] for s in new['services']]}")
    print("attribution:", attribution.from_calldata(attribution.tag(data)))
    if "--dry-run" in sys.argv:
        return 0

    key = os.environ.get("AGENT_WALLET_KEY", "")
    if not key:
        raise SystemExit("AGENT_WALLET_KEY missing")
    res = chain.send_tagged(Web3.to_checksum_address(REGISTRY), data, key=key,
                            web3=w3, wait=True)
    print("tx:", res["tx_hash"], "status:", res.get("status"), "block:", res.get("block"))
    if res.get("status") != 1:
        raise SystemExit("setAgentURI failed")
    # Leave behind something a judge can check without trusting this script.
    if "--snapshot" in sys.argv:
        from pathlib import Path
        receipt = {
            "attested_at_block": res.get("block"),
            "tx": res["tx_hash"],
            "from": res.get("from"),
            "codes_on_wire": res.get("attribution"),
            "erc8021_schema": 0,
            "snapshot": new.get("proofSnapshot"),
            "registry": REGISTRY,
            "agent_id": AGENT_ID,
            "verify": "python scripts/verify_attribution.py",
        }
        out_dir = Path(ROOT) / "docs" / "receipts"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"attest-{new['proofSnapshot']['date']}.json"
        path.write_text(json.dumps(receipt, indent=1) + "\n")
        print(f"receipt : {path.relative_to(ROOT)}")
    after = read_metadata(w3)
    print("onchain services now:", json.dumps(after.get("services"), indent=1))
    print("x402Support:", after.get("x402Support"), "| active:", after.get("active"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

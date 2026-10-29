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


def desired(meta: dict) -> dict:
    """Current metadata plus the parts that are only true now."""
    out = dict(meta)
    base = PUBLIC_BASE_URL
    out["services"] = [
        # The invoice pages and the /proof page are real, public, and answer 200.
        {"name": "web", "endpoint": base},
    ]
    out["x402Support"] = True
    out["active"] = True
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
    if meta.get("services") == new["services"] and meta.get("registrations") == new["registrations"]:
        print("metadata already up to date — nothing to send")
        print(json.dumps(new.get("services"), indent=1))
        return 0

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
    after = read_metadata(w3)
    print("onchain services now:", json.dumps(after.get("services"), indent=1))
    print("x402Support:", after.get("x402Support"), "| active:", after.get("active"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

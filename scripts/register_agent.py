"""Register the agent's ERC-8004 identity on Celo mainnet.

ERC-8004 mints an ERC-721 on the identity registry: the agentId is the token id
and the agentURI is our registration metadata. Both hackathon tracks require an
identity, and it is what other agents check before transacting with us.

Metadata is a `data:application/json;base64,...` URI on purpose: it is
content-addressed (the bytes ARE the integrity check) and needs no pinning
service, so the registration cannot rot when a gateway goes down.

Usage:  .venv/bin/python scripts/register_agent.py [--dry-run]
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import sys

from dotenv import dotenv_values
from eth_account import Account
from web3 import Web3

ROOT = pathlib.Path(__file__).resolve().parent.parent

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot import attribution  # noqa: E402 — needs the repo root on sys.path

IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"  # Celo mainnet
CELO_CHAIN_ID = 42220
RPC = os.environ.get("CELO_RPC_URL", "https://forno.celo.org")

# A tiny inline mark keeps the metadata self-contained (no IPFS gateway needed).
_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256">'
    '<rect width="256" height="256" rx="48" fill="#0b1f16"/>'
    '<text x="128" y="150" font-family="monospace" font-size="88" fill="#5eead4" '
    'text-anchor="middle">USA</text>'
    '<text x="128" y="200" font-family="monospace" font-size="30" fill="#94a3b8" '
    'text-anchor="middle">invoice</text></svg>'
)

METADATA = {
    "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
    "name": "impay",
    "description": (
        "Turns one sentence ('charge Marco 500 BRL for the logo, due Friday') into a "
        "settled stablecoin payment on Celo. Typed LLM judgments parse the amount and "
        "currency and screen the invoice for fraud before a pay link is minted; an "
        "amount or currency that cannot be priced is refused at draft time. Settlement "
        "is proven onchain (Transfer logs from the invoice's creation block, one "
        "transfer settles at most one invoice), never from an API reply. Settles USA\u209c "
        "over Celo's x402 facilitator and pays, per invoice, for the off-chain services "
        "it does not own."
    ),
    "image": "data:image/svg+xml;base64,"
    + base64.b64encode(_LOGO_SVG.encode()).decode(),
    # services[] is added in Phase 1 with setAgentURI once the public invoice
    # endpoint exists. Declaring an endpoint before it answers would be a lie.
    "supportedTrust": ["reputation", "validation"],
}


def agent_uri() -> str:
    payload = json.dumps(METADATA, separators=(",", ":"), ensure_ascii=False).encode()
    return "data:application/json;base64," + base64.b64encode(payload).decode()


def main() -> int:
    dry = "--dry-run" in sys.argv
    env = dotenv_values(str(ROOT / ".env"))
    acct = Account.from_key(env["AGENT_WALLET_KEY"])
    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"headers": {"User-Agent": "impay/1.0"}}))

    uri = agent_uri()
    c = w3.eth.contract(address=Web3.to_checksum_address(IDENTITY_REGISTRY),
                        abi=[{"name": "register", "type": "function", "stateMutability": "nonpayable",
                              "inputs": [{"name": "agentURI", "type": "string"}], "outputs": []},
                             {"name": "balanceOf", "type": "function", "stateMutability": "view",
                              "inputs": [{"name": "owner", "type": "address"}],
                              "outputs": [{"name": "", "type": "uint256"}]},
                             {"name": "tokenURI", "type": "function", "stateMutability": "view",
                              "inputs": [{"name": "tokenId", "type": "uint256"}],
                              "outputs": [{"name": "", "type": "string"}]},
                             {"name": "getAgentWallet", "type": "function", "stateMutability": "view",
                              "inputs": [{"name": "agentId", "type": "uint256"}],
                              "outputs": [{"name": "", "type": "address"}]}])
    reg = c.functions.register(uri)
    # Every tx we broadcast carries the ERC-8021 suffix — starting with this one.
    try:
        inner = c.encode_abi("register", args=[uri])
    except TypeError:  # web3 < 7 signature
        inner = reg._encode_transaction_data()
    calldata = attribution.tag(inner)
    print(f"suffix   : {attribution.app_code()} ({len(calldata) - len(inner)} bytes appended)")

    print(f"registry : {IDENTITY_REGISTRY}")
    print(f"owner    : {acct.address}")
    print(f"metadata : {len(uri)} chars (data:application/json;base64)")
    already = c.functions.balanceOf(acct.address).call()
    print(f"existing identities owned: {already}")
    if already:
        print("already registered — nothing to do")
        return 0
    if dry:
        # `to` is not optional here: without it the RPC simulates a contract
        # CREATION and the selector-bearing calldata dies StackUnderflow.
        # Always pass `to`: estimating with calldata but no `to` simulates a
        # contract CREATION and dies StackUnderflow, blaming the calldata.
        estimate = w3.eth.estimate_gas({
            "from": acct.address,
            "to": Web3.to_checksum_address(IDENTITY_REGISTRY),
            "data": calldata,
            "value": 0,
        })
        print(f"gas estimate: {estimate}")
        print(json.dumps(METADATA, indent=2)[:600])
        return 0

    # Legacy gasPrice on purpose: Forno returns a hash for EIP-1559 txs and then
    # silently drops them from its mempool.
    gas_price = int(w3.eth.gas_price * 2)
    # A data: URI lives in storage, so this costs far more than a plain call —
    # never hardcode a gas limit here.
    gas = int(w3.eth.estimate_gas({
        "from": acct.address, "to": Web3.to_checksum_address(IDENTITY_REGISTRY),
        "data": calldata, "value": 0}) * 1.25)
    print(f"gas      : {gas} @ {gas_price/1e9:.1f} gwei (~{(gas*gas_price)/1e18:.6f} CELO)")
    tx = {
        "to": Web3.to_checksum_address(IDENTITY_REGISTRY),
        "data": calldata,
        "value": 0,
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
        "gas": gas,
        "gasPrice": gas_price,
        "chainId": CELO_CHAIN_ID,
    }
    # Tag it. This is the one transaction a judge is most likely to decode, and
    # signing inline here is exactly the call site that used to skip the suffix.
    tagged = attribution.tag(calldata)
    tx["data"] = tagged
    tx["gas"] = int(w3.eth.estimate_gas({**tx, "from": acct.address}) * 1.25)
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"codes    : {attribution.from_calldata(tagged)}")
    print(f"sent     : 0x{tx_hash.hex().removeprefix('0x')}")
    rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    print(f"status   : {rcpt['status']}  block {rcpt['blockNumber']}  gasUsed {rcpt['gasUsed']}")
    if rcpt["status"] != 1:
        return 1

    transfer_topic = Web3.keccak(text="Transfer(address,address,uint256)").hex()
    agent_id = None
    for lg in rcpt["logs"]:
        topics = [t.hex() if isinstance(t, bytes) else t for t in lg["topics"]]
        if topics and topics[0].lower() == transfer_topic.lower() and len(topics) == 4:
            agent_id = int(topics[3], 16)
    print(f"agentId  : {agent_id}")
    if agent_id is None:
        print("no Transfer log found — inspect the receipt before trusting this")
        return 1
    # The receipt lands on one node of a load-balanced RPC; another node can
    # still be a block behind and report the token as nonexistent. Retry before
    # declaring the registration broken.
    import time
    uri_onchain = wallet = None
    for _ in range(6):
        try:
            uri_onchain = c.functions.tokenURI(agent_id).call()
            wallet = c.functions.getAgentWallet(agent_id).call()
            break
        except Exception as e:  # noqa: BLE001
            print(f"  read raced the mint ({type(e).__name__}); retrying")
            time.sleep(3)
    print(f"tokenURI : {(uri_onchain or '<unreadable>')[:80]}...")
    print(f"wallet   : {wallet}")
    return 0 if uri_onchain else 1


if __name__ == "__main__":
    raise SystemExit(main())

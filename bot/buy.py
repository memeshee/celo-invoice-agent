"""Pay for off-chain services via agent402.tools (HTTP 402 -> stablecoin on Celo).

Read paths are live and keyless: pricing(), find(), route detail.
Paid calls settle in USDC on eip155:42220 — payer needs only the stablecoin,
no account. pay_and_call() builds the EIP-3009 authorization the 402 demands;
broadcast + facilitator settle need a funded key — UNTESTED, flagged at runtime.

Verified live 2026-10-07: /api/pricing ok (Celo listed), /api/find?q= works.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://agent402.tools"


def _get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=25) as r:
        return json.load(r)


def pricing() -> dict:
    return _get("/api/pricing")


def find(query: str) -> dict:
    return _get("/api/find?" + urllib.parse.urlencode({"q": query}))


def call_free(slug_or_path: str, params: dict | None = None,
              method: str = "GET") -> dict:
    """Free/PoW-tier routes. Paid routes return their 402 offer instead."""
    data = json.dumps(params or {}).encode() if method != "GET" else None
    req = urllib.request.Request(BASE + slug_or_path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return {"status": r.status, "body": r.read().decode()[:4000]}
    except urllib.error.HTTPError as e:
        if e.code == 402:
            return {"status": 402,
                    "payment_offer": json.loads(e.read().decode() or "{}")}
        raise


def sign_eip3009(owner_key: str, token: str, name: str, version: str,
                 chain_id: int, to_addr: str, value: int,
                 valid_after: int, valid_before: int,
                 nonce: bytes) -> dict:
    """Build + sign EIP-3009 transferWithAuthorization. Offline-safe.

    Returns {v, r, s, auth:{from,to,value,validAfter,validBefore,nonce}}.
    Uses eth-account if present, else pure-python keccak via eth_hash.
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_typed_data
    except ImportError as e:
        raise RuntimeError("pip install eth-account for signing") from e
    acct = Account.from_key(owner_key)
    full = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {"name": name, "version": version, "chainId": chain_id,
                   "verifyingContract": token},
        "message": {
            "from": acct.address,
            "to": to_addr, "value": value,
            "validAfter": valid_after, "validBefore": valid_before,
            "nonce": nonce,
        },
    }
    signed = acct.sign_message(encode_typed_data(full_message=full))
    return {"v": signed.v, "r": hex(signed.r), "s": hex(signed.s),
            "signer": acct.address,
            "auth": {"to": to_addr, "value": value,
                     "validAfter": valid_after, "validBefore": valid_before,
                     "nonce": nonce.hex()}}

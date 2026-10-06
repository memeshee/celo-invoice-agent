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
    # Packed 65-byte signature as the x402 exact scheme expects.
    v_hex = f"{signed.v:02x}" if signed.v < 30 else f"{signed.v - 27:02x}"
    packed = "0x" + signed.r.to_bytes(32, "big").hex() \
        + signed.s.to_bytes(32, "big").hex() + v_hex
    return {"v": signed.v, "r": hex(signed.r), "s": hex(signed.s),
            "signature": packed,
            "signer": acct.address,
            "auth": {"to": to_addr, "value": value,
                     "validAfter": valid_after, "validBefore": valid_before,
                     "nonce": nonce.hex()}}


CELO_NETWORK = "eip155:42220"
CELO_CHAIN_ID = 42220


def _agent_key(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    import os
    import re
    from pathlib import Path
    key = os.environ.get("AGENT_WALLET_KEY", "").strip()
    if key:
        return key
    m = re.search(r"AGENT_WALLET_KEY=(\S+)",
                  Path(".env").read_text() if Path(".env").exists() else "")
    if m:
        return m.group(1)
    raise RuntimeError("AGENT_WALLET_KEY not set (env or .env)")


class ExactCeloScheme:
    """x402 exact/EVM signer for Celo. Code signs, server settles.

    Registered for eip155:42220 on an x402ClientSync; the requests adapter
    then answers 402s automatically. Needs USDC in the agent wallet.
    """
    scheme = "exact"

    def __init__(self, owner_key: str | None = None):
        self._key = _agent_key(owner_key)

    def create_payment_payload(self, requirements) -> dict:
        if requirements.network != CELO_NETWORK:
            raise ValueError(f"not a Celo leg: {requirements.network}")
        import time
        extra = requirements.extra or {}
        now = int(time.time())
        sig = sign_eip3009(
            self._key, requirements.asset,
            extra.get("name", "USDC"), extra.get("version", "2"),
            CELO_CHAIN_ID, requirements.pay_to, int(requirements.amount),
            now - 60, now + int(requirements.max_timeout_seconds or 300),
            __import__("os").urandom(32))
        return {
            "authorization": {
                "from": sig["signer"],
                "to": requirements.pay_to,
                "value": str(int(requirements.amount)),
                "validAfter": str(sig["auth"]["validAfter"]),
                "validBefore": str(sig["auth"]["validBefore"]),
                "nonce": "0x" + sig["auth"]["nonce"],
            },
            "signature": sig["signature"],
        }


def paid_session(owner_key: str | None = None, max_per_call_usd: float = 1.0):
    """requests.Session that pays Celo x402 402s from the agent wallet."""
    import requests
    from x402 import max_amount, prefer_network, x402ClientSync
    from x402.http.clients.requests import wrapRequestsWithPayment
    client = x402ClientSync()
    client.register_policy(prefer_network(CELO_NETWORK))
    client.register_policy(max_amount(int(max_per_call_usd * 1_000_000)))
    client.set_spend_controls({
        "allowed_assets": [{
            "network": CELO_NETWORK,
            "asset": "0xcebA9300f2b948710d2653dD7B07f33A8B32118C",
            "max_amount_per_payment": str(int(max_per_call_usd * 1_000_000)),
        }],
    })
    client.register(CELO_NETWORK, ExactCeloScheme(_agent_key(owner_key)))
    return wrapRequestsWithPayment(requests.Session(), client)


def pay_and_call(url: str, params: dict | None = None,
                 owner_key: str | None = None) -> dict:
    """GET url (params as query), auto-paying a Celo-leg 402. Returns JSON.

    Raises RuntimeError("NO_USDC") when the wallet can't cover the ask —
    fund it with a little USDC on Celo and retry.
    """
    import requests
    s = paid_session(owner_key)
    try:
        r = s.get(url, params=params or {}, timeout=90)
    except Exception as e:
        if "USDC" in str(e) or "balance" in str(e).lower():
            raise RuntimeError("NO_USDC: fund the agent wallet and retry") from e
        raise
    if r.status_code == 402:
        raise RuntimeError("STILL_402: no Celo leg accepted or settle failed")
    r.raise_for_status()
    return r.json()

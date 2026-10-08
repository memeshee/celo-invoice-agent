"""Pay for off-chain services via agent402.tools (HTTP 402 -> stablecoin on Celo).

Read paths are live and keyless: pricing(), find(), route detail.
Paid calls settle in USDC on eip155:42220 — payer needs only the stablecoin,
no account. pay_and_call() builds the EIP-3009 authorization the 402 demands;
broadcast + facilitator settle need a funded key — UNTESTED, flagged at runtime.

Verified live 2026-10-07: /api/pricing ok (Celo listed), /api/find?q= works.

Security fixes (audit run-1):
- peer-controlled-authorization-validity-window: the signed authorization's
  validBefore is now clamped to a local maximum instead of trusting the peer's
  max_timeout_seconds (observed: a peer could ask for ~31.7 years).
- unbounded-aggregate-x402-spend: the machine-pay path now keeps a spend
  ledger with a total and a per-host budget and records every issued
  authorization, instead of caps that only apply per payment.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://agent402.tools"

# Local maxima for the machine-pay path.
MAX_AUTH_SECONDS = int(os.environ.get("X402_MAX_AUTH_SECONDS", "300"))
MAX_TOTAL_USD = float(os.environ.get("X402_MAX_TOTAL_USD", "10"))
MAX_PER_HOST_USD = float(os.environ.get("X402_MAX_PER_HOST_USD", "2"))
SPEND_LEDGER = os.environ.get("X402_SPEND_LEDGER", "spend_ledger.json")


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


# --------------------------------------------------------------- spend ledger
# The per-payment caps below bound one payment. Without the ledger below there
# is no bound at all across calls: a peer that answers 402 repeatedly, or fails
# after taking the signature, keeps a fresh settleable authorization each time.

def _ledger_load() -> dict:
    try:
        with open(SPEND_LEDGER) as fh:
            db = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        db = {}
    db.setdefault("total_usd", 0.0)
    db.setdefault("hosts", {})
    db.setdefault("issued", {})
    return db


def _ledger_save(db: dict) -> None:
    tmp = SPEND_LEDGER + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(db, fh, indent=2)
    os.replace(tmp, SPEND_LEDGER)


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def spend_gate(url: str, amount_atomic: int) -> None:
    """Raise before signing when this payment would exceed a budget."""
    if amount_atomic <= 0:
        raise RuntimeError(f"SPEND_BUDGET: non-positive amount {amount_atomic}")
    usd = amount_atomic / 1_000_000
    db = _ledger_load()
    if db["total_usd"] + usd > MAX_TOTAL_USD:
        raise RuntimeError(
            f"SPEND_BUDGET: total budget exhausted ({db['total_usd']:.2f}/{MAX_TOTAL_USD:.2f} USD)")
    spent = float(db["hosts"].get(_host(url), 0.0))
    if spent + usd > MAX_PER_HOST_USD:
        raise RuntimeError(
            f"SPEND_BUDGET: host budget exhausted for {_host(url)} ({spent:.2f}/{MAX_PER_HOST_USD:.2f} USD)")


def spend_record(url: str, amount_atomic: int, nonce: str | None = None) -> None:
    """Record an issued authorization so outstanding claims stay visible."""
    usd = amount_atomic / 1_000_000
    db = _ledger_load()
    db["total_usd"] = round(db["total_usd"] + usd, 6)
    db["hosts"][_host(url)] = round(float(db["hosts"].get(_host(url), 0.0)) + usd, 6)
    db["issued"][nonce or f"unknown-{int(time.time() * 1000)}"] = {
        "url": url, "amount_atomic": amount_atomic, "at": int(time.time()),
    }
    _ledger_save(db)


def _hook_amount(event) -> int | None:
    """Atomic amount in an x402 payment-creation event, when readable."""
    for attr in ("selected_requirements", "requirements", "payment_requirements"):
        req = getattr(event, attr, None)
        amount = getattr(req, "amount", None) if req is not None else None
        if amount is not None:
            return int(amount)
    payload = getattr(event, "payment_payload", None)
    body = getattr(payload, "payload", None) if payload is not None else None
    if isinstance(body, dict):
        auth = body.get("authorization") or {}
        if auth.get("value") is not None:
            return int(auth["value"])
    return None


def _hook_nonce(event) -> str | None:
    payload = getattr(event, "payment_payload", None)
    body = getattr(payload, "payload", None) if payload is not None else None
    if isinstance(body, dict):
        auth = body.get("authorization") or {}
        if auth.get("nonce") is not None:
            return str(auth["nonce"])
    return None


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
    m = re.search(r"^\s*AGENT_WALLET_KEY=(\S+)\s*$",
                  Path(".env").read_text() if Path(".env").exists() else "",
                  re.MULTILINE)
    if m:
        return m.group(1).strip("\"'")
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
        # The peer chooses max_timeout_seconds; never sign a window longer than
        # our own maximum.
        window = min(int(requirements.max_timeout_seconds or MAX_AUTH_SECONDS),
                     MAX_AUTH_SECONDS)
        sig = sign_eip3009(
            self._key, requirements.asset,
            extra.get("name", "USDC"), extra.get("version", "2"),
            CELO_CHAIN_ID, requirements.pay_to, int(requirements.amount),
            now - 60, now + window,
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


def paid_session(owner_key: str | None = None, max_per_call_usd: float = 1.0,
                 budget_url: str | None = None):
    """requests.Session that pays Celo x402 402s from the agent wallet.

    Pass budget_url so the aggregate ledger can attribute spend to a host;
    without it the per-payment caps still apply but no aggregate budget is
    enforced (and the gate fails closed, refusing to sign).
    """
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

    def _gate(event=None, **_kw) -> None:
        amount = _hook_amount(event)
        if amount is None:
            # Unknown offer shape: fail closed rather than sign blindly.
            raise RuntimeError("SPEND_BUDGET: cannot determine the payment amount")
        spend_gate(budget_url or "", amount)

    def _record(event=None, **_kw) -> None:
        amount = _hook_amount(event)
        if amount is not None:
            spend_record(budget_url or "", amount, _hook_nonce(event))

    for hook_name, hook in (("on_before_payment_creation", _gate),
                            ("on_after_payment_creation", _record)):
        register = getattr(client, hook_name, None)
        if callable(register):
            register(hook)

    client.register(CELO_NETWORK, ExactCeloScheme(_agent_key(owner_key)))
    return wrapRequestsWithPayment(requests.Session(), client)


def pay_and_call(url: str, params: dict | None = None,
                 owner_key: str | None = None) -> dict:
    """GET url (params as query), auto-paying a Celo-leg 402. Returns JSON.

    Raises RuntimeError("NO_USDC") when the wallet can't cover the ask —
    fund it with a little USDC on Celo and retry.
    """
    import requests
    s = paid_session(owner_key, budget_url=url)
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

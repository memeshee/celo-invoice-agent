"""Local-currency routing via Textile FX RFQ v2 (base https://api.textilecredit.com/v2).

Flow: preview (free estimate) -> request (firm quote, taker-bound, 5-60s TTL)
-> broadcast transactions.swap as a batch -> submit txHash for status.
Auth for /request: partner Bearer key (TEXTILE_API_KEY) or keyless takerProof
EIP-712 signature. No Celo testnet — Celo corridors are mainnet-only.

Verified live 2026-10-07: preview shape accepted; pair returned
no_quote/no_makers_online (code treats that as retryable, not fatal).
"""

import json
import os
import urllib.request

API = "https://api.textilecredit.com/v2"
CELO_CHAIN_ID = 42220
TAKER_FEE_BPS = 1  # baked into the quote on Celo

# Atomic-unit amounts as strings; USDT/USDC 6 dec, wFIAT 18 dec, cNGN 18 dec.
TOKENS = {
    "USDT": "0x48065fbBE25f71C9282ddf5e1cD6D6A887483D5e",
    "USDC": "0xcEBA9300f2b948710d2653dD7B07f33A8B32118C",
    "cNGN": "0xF6829D73b5750d21382c54ad1e0d019493271a4f",
    "wARS": "0x0DC4F92879B7670e5f4e4e6e3c801D229129D90D",
    "wBRL": "0xD76f5Faf6888e24D9F04Bf92a0c8B921FE4390e0",
    "IDRX": "0x18Bc5bAA7149558c3727Ecd6BeAC8b922A39C3C22",
}

# Corridors Textile lists against USDT on Celo.
CORRIDORS = ["cNGN", "wARS", "wBRL", "IDRX", "USDC"]


def _post(path: str, body: dict, claim: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("TEXTILE_API_KEY", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if claim:
        headers["X-Rfq-Claim"] = claim
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=75) as r:
        return json.load(r)


def preview(sell: str, buy: str, sell_amount_atomic: str) -> dict:
    """Free estimate. {'status': 'no_quote'} means no makers — retry later."""
    body = {"chainId": CELO_CHAIN_ID, "sellToken": TOKENS[sell],
            "buyToken": TOKENS[buy], "sellAmount": str(sell_amount_atomic)}
    with urllib.request.urlopen(
        urllib.request.Request(API + "/rfq/preview",
                               data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"}),
        timeout=30,
    ) as r:
        return json.load(r)


def taker_proof(owner_key: str) -> dict:
    """Keyless auth: EIP-712 TakerControl signature, valid ~12h.

    Returns {"taker", "proof"} — pass proof as takerProof in /request.
    Domain per docs: 'Textile Taker Control v1'.
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_typed_data
    except ImportError as e:
        raise RuntimeError("pip install eth-account for signing") from e
    import time
    import os
    acct = Account.from_key(owner_key)
    full = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "TakerControl": [
                {"name": "taker", "type": "address"},
                {"name": "chainId", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
                {"name": "issuedAt", "type": "uint256"},
            ],
        },
        "primaryType": "TakerControl",
        "domain": {"name": "Textile Taker Control", "version": "1",
                   "chainId": CELO_CHAIN_ID},
        "message": {
            "taker": acct.address,
            "chainId": CELO_CHAIN_ID,
            "nonce": os.urandom(32),
            "issuedAt": int(time.time() * 1000),
        },
    }
    signed = acct.sign_message(encode_typed_data(full_message=full))
    return {
        "taker": acct.address,
        "proof": {
            "nonce": "0x" + full["message"]["nonce"].hex(),
            "issuedAt": full["message"]["issuedAt"],
            "signature": "0x" + signed.signature.hex(),
        },
    }


def request_quote(sell: str, buy: str, sell_amount_atomic: str,
                  taker: str, taker_proof: dict | None = None) -> dict:
    """Firm quote. Keyless via taker_proof= or partner TEXTILE_API_KEY."""
    body = {
        "chainId": CELO_CHAIN_ID, "sellToken": TOKENS[sell],
        "buyToken": TOKENS[buy], "sellAmount": str(sell_amount_atomic),
        "taker": taker,
    }
    if taker_proof:
        body["takerProof"] = taker_proof
    return _post("/rfq/request", body)


def submit(rfq_id: str, tx_hash: str, claim: str | None = None) -> dict:
    return _post(f"/rfq/{rfq_id}/submit", {"txHash": tx_hash}, claim)


def status(rfq_id: str) -> dict:
    with urllib.request.urlopen(f"{API}/rfq/{rfq_id}", timeout=20) as r:
        return json.load(r)

"""USA₮ settlement on Celo mainnet.

Two legs, both real money:
1. Human payer leg (Telegram demo): payer sends USA₮ straight to the invoice
   wallet. Humans have no x402 client, so the bot watches the chain for the
   Transfer and marks the invoice settled. watch_transfers() does this via
   Forno RPC + web3.py event logs.
2. Machine leg: Celo native x402 facilitator (api.x402.celo.org) for
   agent-to-agent payments — /supported (open), /verify (open),
   /settle (X-API-Key). Asset params below are read live from /supported.

Verified live 2026-10-07: facilitator mainnet ok; USAT 0xD2ab...771
("Tether America USD", v1, eip3009, 6 dec).
"""

import json
import os
import urllib.request

FACILITATOR = os.environ.get("X402_FACILITATOR_URL", "https://api.x402.celo.org")
CELO_CHAIN_ID = 42220

# Pinned from live GET /supported (re-fetch via supported_assets() anytime).
ASSETS = {
    "USAT": {"asset": "0xD2ab3C9A02DBBAB236BfEC45D1d755DF4267F771",
             "symbol": "USAT", "decimals": 6,
             "name": "Tether America USD", "version": "1",
             "method": "eip3009"},
    "USDT": {"asset": "0x48065fbBE25f71C9282ddf5e1cD6D6A887483D5e",
             "symbol": "USDT", "decimals": 6,
             "name": "Tether USD", "version": "1",
             "method": "eip3009"},
    "USDC": {"asset": "0xcEBA9300f2b948710d2653dD7B07f33A8B32118C",
             "symbol": "USDC", "decimals": 6,
             "name": "USDC", "version": "2",
             "method": "eip3009"},
    "wARS": {"asset": "0x0DC4F92879B7670e5f4e4e6e3c801D229129D90D",
             "symbol": "wARS", "decimals": 18,
             "name": "Peso Argentino", "version": "1",
             "method": "permit2"},
    "wBRL": {"asset": "0xD76f5Faf6888e24D9F04Bf92a0c8B921FE4390e0",
             "symbol": "wBRL", "decimals": 18,
             "name": "Real Brasileño", "version": "1",
             "method": "permit2"},
    "wCOP": {"asset": "0x8a1D45e102e886510e891d2Ec656a708991e2D76",
             "symbol": "wCOP", "decimals": 18,
             "name": "Peso Colombiano", "version": "1",
             "method": "permit2"},
}

# USAT on Celo emits a NON-STANDARD Transfer event (observed live:
# 440/544 USAT logs in a 2000-block window use ...f523b3ef, plus
# AuthorizationUsed ...79b10a5 and Approval ...c8c7c3b925). Its topic-0
# shares the ddf252ad prefix but differs in the tail — never assume
# ERC-20 Transfer topic for this token. USDT/USDC use the standard sig.
TRANSFER_TOPIC_STD = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4f5b3e"
TRANSFER_TOPIC_USAT = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

TRANSFER_TOPICS = {
    "USAT": (TRANSFER_TOPIC_USAT, TRANSFER_TOPIC_STD),
    "USDT": (TRANSFER_TOPIC_STD,),
    "USDC": (TRANSFER_TOPIC_STD,),
    "wARS": (TRANSFER_TOPIC_STD,),
    "wBRL": (TRANSFER_TOPIC_STD,),
    "wCOP": (TRANSFER_TOPIC_STD,),
}


def _get(path: str) -> dict:
    with urllib.request.urlopen(FACILITATOR + path, timeout=20) as r:
        return json.load(r)


def health() -> dict:
    return _get("/health")


def supported_assets() -> dict:
    """Live asset table from the facilitator. Trust this over docs prose."""
    return _get("/supported")


def atomic(amount_display: str, symbol: str) -> int:
    whole, _, frac = amount_display.partition(".")
    dec = ASSETS[symbol]["decimals"]
    frac = (frac + "0" * dec)[:dec]
    return int(whole) * 10**dec + int(frac or 0)


def create_payment_request(invoice: dict, pay_to: str) -> dict:
    """Human-payer leg: plain USA₮ transfer instructions.

    invoice: {id, amount, currency}; currency maps to a settlement asset
    (fiat codes settle in USAT; w* codes settle in-kind for the LatAm track).
    Returns what /pay shows the payer. watch_transfers() closes the loop.
    """
    symbol = (invoice.get("currency") or "USAT").upper()
    if symbol in ("BRL", "ARS", "MXN", "COP", "USD"):
        symbol = "USAT"
    if symbol not in ASSETS:
        raise ValueError(f"unsupported settlement asset: {symbol}")
    return {
        "invoice_id": invoice["id"],
        "pay_to": pay_to,
        "amount_display": invoice["amount"],
        "symbol": symbol,
        "amount_atomic": atomic(str(invoice["amount"]), symbol),
        "asset_contract": ASSETS[symbol]["asset"],
        "chain": f"eip155:{CELO_CHAIN_ID}",
    }


def _w3():
    from web3 import Web3
    return Web3(Web3.HTTPProvider(
        os.environ.get("CELO_RPC_URL", "https://forno.celo.org")))


def watch_transfers(pay_to: str, symbol: str = "USAT",
                    min_atomic: int = 1, from_block: int | str = "latest",
                    to_block: int | str = "latest") -> list[dict]:
    """Scan USA₮ Transfer events to pay_to. Returns [{tx, value}].

    from_block/to_block accept ints or "latest"; pass a window around the
    invoice creation block in production. Never trust the facilitator reply
    alone — this onchain check is the settlement proof.
    """
    w3 = _w3()
    head = w3.eth.block_number
    to_b = head if isinstance(to_block, str) else int(to_block)
    from_b = max(0, to_b - 5000) if isinstance(from_block, str) else int(from_block)
    padded = "0x" + "0" * 24 + pay_to.lower().replace("0x", "")
    # Forno rejects ALL topic filters (even flat single-topic arrays), so pull
    # address-only logs in a narrow window and filter signature + recipient
    # client-side. Keep windows narrow.
    logs = w3.eth.get_logs({
        "address": ASSETS[symbol]["asset"],
        "fromBlock": from_b,
        "toBlock": to_b,
    })
    out = []
    want = TRANSFER_TOPICS.get(symbol, (TRANSFER_TOPIC_STD,))
    for lg in logs:
        topics = lg.get("topics", []) if isinstance(lg, dict) else lg["topics"]
        # HexBytes.hex() has no 0x prefix — normalize before comparing.
        sig = ("0x" + topics[0].hex()) if len(topics) > 0 else ""
        to_topic = ("0x" + topics[2].hex()) if len(topics) > 2 else ""
        if sig.lower() not in [s.lower() for s in want]:
            continue
        if to_topic.lower() != padded.lower():
            continue
        value = int(lg["data"].hex(), 16) if isinstance(lg["data"], bytes) \
            else int(lg["data"], 16)
        if value >= min_atomic:
            out.append({"tx": "0x" + lg["transactionHash"].hex(), "value": value})
    return out


def facilitator_settle(payload: dict) -> dict:
    """Machine leg: POST /settle with X-API-Key. UNTESTED — needs funded key."""
    key = os.environ.get("X402_API_KEY", "")
    if not key:
        raise RuntimeError("X402_API_KEY not set")
    req = urllib.request.Request(
        FACILITATOR + "/settle",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

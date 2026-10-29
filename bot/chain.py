"""Broadcast tagged transactions — the single signing path.

Every ordinary transaction this project sends (token transfers, Textile swap
broadcasts, registry writes) must carry the ERC-8021 suffix. Doing that at each
call site is how a transfer quietly goes out untagged and can never be credited
afterwards, so the tag lives here and callers cannot forget it.

Celo-specific: build legacy `gasPrice` transactions. Forno returns a hash for
EIP-1559 transactions and then silently drops them from its mempool, so a
"successful" send that never mines is the default failure mode if you let the
node pick a fee model.
"""

from __future__ import annotations

import os

from eth_account import Account
from web3 import Web3

from . import attribution

CELO_CHAIN_ID = 42220
RPC = os.environ.get("CELO_RPC_URL", "https://forno.celo.org")
UA = "impay/1.0"

ERC20_TRANSFER_ABI = [{
    "name": "transfer", "type": "function", "stateMutability": "nonpayable",
    "inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}],
    "outputs": [{"name": "", "type": "bool"}],
}]


def w3() -> Web3:
    return Web3(Web3.HTTPProvider(RPC, request_kwargs={"headers": {"User-Agent": UA}}))


def erc20_transfer_data(token: str, to: str, atomic: int) -> str:
    contract = w3().eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_TRANSFER_ABI)
    return contract.encode_abi("transfer", args=[Web3.to_checksum_address(to), int(atomic)])


def build_tagged_tx(to: str, data: str, *, key: str, value: int = 0,
                    gas: int | None = None, gas_price: int | None = None,
                    nonce: int | None = None, web3=None) -> dict:
    """Sign a tagged transaction without broadcasting it. Pure enough to test."""
    client = web3 or w3()
    acct = Account.from_key(key)
    tagged = attribution.tag(data)
    if gas is None:
        gas = int(client.eth.estimate_gas(
            {"from": acct.address, "to": Web3.to_checksum_address(to),
             "data": tagged, "value": value}) * 1.25)
    tx = {
        "to": Web3.to_checksum_address(to),
        "data": tagged,
        "value": value,
        "nonce": client.eth.get_transaction_count(acct.address, "pending") if nonce is None else nonce,
        "gas": gas,
        "gasPrice": gas_price if gas_price is not None else int(client.eth.gas_price * 2),
        "chainId": CELO_CHAIN_ID,
    }
    signed = acct.sign_transaction(tx)
    return {"raw": signed.raw_transaction, "tx": tx, "from": acct.address,
            "attribution": attribution.from_calldata(tagged)}


def send_tagged(to: str, data: str, *, key: str, value: int = 0, web3=None,
                wait: bool = False) -> dict:
    """Build, broadcast and (optionally) wait for a tagged transaction."""
    client = web3 or w3()
    built = build_tagged_tx(to, data, key=key, value=value, web3=client)
    tx_hash = client.eth.send_raw_transaction(built["raw"])
    out = {"tx_hash": "0x" + tx_hash.hex().removeprefix("0x"),
           "from": built["from"], "attribution": built["attribution"]}
    if wait:
        rcpt = client.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        out["status"] = rcpt["status"]
        out["block"] = rcpt["blockNumber"]
    return out


def transfer_token(token: str, to: str, atomic: int, *, key: str,
                   web3=None, wait: bool = True) -> dict:
    """ERC-20 transfer with the attribution tag attached."""
    return send_tagged(token, erc20_transfer_data(token, to, atomic),
                       key=key, web3=web3, wait=wait)

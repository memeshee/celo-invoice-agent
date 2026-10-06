"""Pay for off-chain services the team doesn't own via `buy`.

HTTP 402 -> signed local stablecoin payment on Celo, attached to the retry.
WIRING PENDING — filled in from integration research.
  pay_for(service, params) -> (result, celo_tx_hash)
Services: disposable linux/hr, agent browsers/min, social data, flight search,
agent402.tools (600+), RPC/indexer/inference.
"""

BUY_NOTE = "pending: buy CLI/API shape from research"


def pay_for(service: str, params: dict) -> dict:
    raise NotImplementedError("buy wiring pending: " + BUY_NOTE)

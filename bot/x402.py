"""USA₮ settlement via Celo's native x402 facilitator.

WIRING PENDING — filled in from integration research (see delegation).
Contract surface on success:
  create_payment_request(invoice) -> (pay_url, request_id)
  verify_settlement(request_id) -> tx_hash | None
"""

X402_NOTE = "pending: facilitator URL + USA₮ mainnet address from research"


def create_payment_request(invoice: dict) -> dict:
    raise NotImplementedError("x402 wiring pending: " + X402_NOTE)


def verify_settlement(request_id: str) -> str | None:
    raise NotImplementedError("x402 wiring pending: " + X402_NOTE)

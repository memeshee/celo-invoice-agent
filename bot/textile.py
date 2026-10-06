"""Local-currency routing via Textile FX (cNGN, wARS, wBRL, USDC vs USD₮).

WIRING PENDING — filled in from integration research.
  quote(from_ccy, to_ccy, amount) -> {price, venue_tx_preview}
  swap(...) -> tx_hash
"""

TEXTILE_NOTE = "pending: Textile endpoint + pairs from research"


def quote(from_ccy: str, to_ccy: str, amount: str) -> dict:
    raise NotImplementedError("Textile wiring pending: " + TEXTILE_NOTE)


def swap(quote_id: str) -> str:
    raise NotImplementedError("Textile wiring pending: " + TEXTILE_NOTE)

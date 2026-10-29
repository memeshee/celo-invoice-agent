# Judging map

One page: what to look at for each criterion, so nothing here relies on you
reading the source first.

## LatAm / wFIAT

- **Is wFIAT real and used?** `/wallet <addr> wBRL` records a settlement
  preference; a BRL invoice then charges **500 wBRL for 500 BRL** in kind
  (`bot/x402.py::settle_asset_for`, `bot/store.py::set_wallet`). No FX, no dollar
  in the middle. wARS/wCOP/wPEN/wMXN/wCLP work identically.
- **Can a real person use it?** The pay page carries a prefilled Ripio on-ramp
  link bound to the invoice's payout address (`bot/ramp.py`), so a payer with no
  stablecoin buys local-currency wFIAT with a card and it lands on the invoice.
- **Autonomous?** `bot/watch.py` settles without anyone typing a command.
- **Try it:** https://newsletter-effect-backgrounds-attribute.trycloudflare.com/i/INV-0006-D2064A

## Build with `buy`

- **Paid calls into services we do not control:** per invoice the agent buys
  `sanctions-name` ($0.005), `sanctions-wallet` ($0.002) and, for non-USD,
  `fx-rate` ($0.002) from agent402.tools over x402 (`bot/services.py`).
- **Verified, not merely attempted:** every purchase is captured to
  `docs/receipts/buy-*.json` before parsing, and its settlement is re-checked
  onchain (USDC `Transfer` from our payer) in `bot/services.py::verify_settlement`.
- **The purchases matter:** a non-clean sanctions verdict sets the invoice to
  `blocked`, which removes it from the settlement queue. This is the compliance
  layer, not a demo of paying.
- **Spend is bounded:** an aggregate + per-host budget with an
  issued-authorization ledger (`bot/buy.py`), tested in
  `tests/test_spend_controls.py`.

## Open Corridors / x402

- **Real settlement between independent parties, proven:** `bot/verify.py` decides
  whether an invoice was paid, from the chain — including a check that the paying
  transaction was not initiated by the payee (a self-swap used to pass every
  mechanical filter). `/proof` re-runs it on every render and labels our own money
  as *builder-funded*.
- **The number that matters is the honest one:** `/proof` reports
  verified/total, not total.
- **Attribution:** every transaction we sign carries the ERC-8021 Schema 0 suffix
  (`bot/attribution.py`), verified on the wire. x402 settlements cannot carry one
  (facilitator limitation), so the PayTo wallet is published in the README.

## Agent identity

- ERC-8004 `agentId 10005` on Celo, metadata as a self-contained `data:` URI, and
  `services[]` declaring the live public endpoint
  (`scripts/set_agent_services.py`, tx `0xa9416d84…`).

## Engineering hygiene

- `python -m pytest` passes on a clean checkout: no network, no keys, no `.env`
  (195 tests, marked otherwise). CI runs it plus `ruff` and a 75% coverage floor.
- `python scripts/verify_settlements.py` re-derives settlement claims; a daily
  workflow runs it.
- `SECURITY.md` lists the controls and the test that pins each one.

## Where it is weak, stated plainly

- FX is a keyless rate cross-checked against a *bought* rate, not a firm quote.
- Textile is wired as a standalone FX swap with committed receipts
  (`docs/receipts/fx-usdt-wbrl.json`), not yet an automatic payout leg.
- Invoice storage is a single JSON file with one writer.
- Independent payers are still the gap: the rails are proven, the users are ours.

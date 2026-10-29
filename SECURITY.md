# Security

## What this project holds, and what it does not

- The bot process holds a **server-side wallet key** (`AGENT_WALLET_KEY`) that pays
  for per-invoice services over x402. It is never sent to a browser: invoice pages
  are served by `bot/serve.py`, which exposes no state-changing route at all.
- Payers never authenticate. An invoice id is a **capability handle** (random
  suffix, see `bot/store.py`) — possessing the link is what authorises reading the
  invoice. Nothing sequential is enumerable.
- Secrets live in `.env` (git-ignored) or a `~/.config/typesafe/api_key` file.
  Nothing is committed; `docs/receipts/` contains only public HTTP responses.

## Controls that are enforced in code, and tested

| Control | Where | Test |
|---|---|---|
| Parsing fails closed to `review`, never `settle`, on an off-schema model answer | `bot/jev.py` | `tests/test_regressions.py` |
| A sanctions hit **blocks** settlement instead of warning | `bot/services.py` | `tests/test_services.py` |
| One transfer settles at most one invoice (consumed-witness registry) | `bot/store.py` | `tests/test_phase1.py` |
| A payee's own transfer is not a payment (transaction-initiator check) | `bot/verify.py` | `tests/test_verify.py` |
| Held/blocked invoices render no pay button | `bot/serve.py` | `tests/test_hold.py` |
| Operator commands fail closed when `OPERATOR_TELEGRAM_ID` is unset | `bot/main.py` | `tests/test_commands.py` |
| Aggregate + per-host x402 spend budget with an issued-authorization ledger | `bot/buy.py` | `tests/test_spend_controls.py` |
| Signed authorization window clamped, regardless of what the peer asks for | `bot/buy.py` | `tests/test_spend_controls.py` |
| Invoice text is HTML-escaped on the public page (stored-XSS vector) | `bot/serve.py` | `tests/test_phase1.py` |
| CSP + `nosniff` + no `POST` routes on the public server | `bot/serve.py` | `tests/test_serve_http.py` |

## Reporting

This is a hackathon project on Celo mainnet with no user funds custodied. If you
find a problem, open an issue at
https://github.com/memeshee/celo-invoice-agent/issues — or, if it involves keys,
contact the maintainer directly rather than filing publicly.

## Known limits

Stated rather than hidden: settlement storage is a single JSON file with one
writer, the public hostname is a rotating quick tunnel, and x402 settlements carry
no ERC-8021 attribution tag (a facilitator limitation, which is why the PayTo
wallet is published). See the Limits section of the README.

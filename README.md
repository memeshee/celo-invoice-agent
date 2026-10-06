# Celo Invoice Agent — Agents on Open Rails Hackathon

Telegram bot: freelancer types a sentence, payer settles in USA₮ via Celo x402,
optional auto-route to local stables via Textile FX. Agent pays for off-chain
services it doesn't own via `buy`.

**Tracks:** USA₮ x402 (primary) + buy (co-primary), LatAm wFIAT (backup).
Deadline: Nov 9 2026, 09:00 GMT. Celo mainnet only.

## Layout

- `bot/main.py` — Telegram bot: invoice create/confirm/pay, receipt
- `bot/jev.py` — TypeSafe Jev judgments: invoice parse + fraud screen
- `bot/x402.py` — USA₮ settlement via Celo x402 facilitator (wiring pending research)
- `bot/textile.py` — local-currency routing via Textile FX (wiring pending research)
- `bot/buy.py` — pay-per-request off-chain services via buy (wiring pending research)
- `bot/store.py` — JSON invoice store
- `tests/test_jev.py` — live Jev parse/screen test

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill TELEGRAM_BOT_TOKEN, keys
python -m bot.main
```

Jev auth: `TYPESAFE_API_KEY` env, or `~/.config/typesafe/api_key` (mode 600).
Never commit keys. Celo settlement keys stay server-side.

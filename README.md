# Celo Invoice Agent

[![ci](https://github.com/memeshee/celo-invoice-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/memeshee/celo-invoice-agent/actions/workflows/ci.yml)

A freelancer types one sentence. The agent prices it, screens it, mints a pay
page anyone can open, watches Celo for the money, and marks the invoice paid
**from the chain rather than from a claim**.

Built for the Celo *Agents on Open Rails* hackathon. Celo mainnet only.

- **Bot:** https://t.me/notcelordbot
- **Agent identity:** ERC-8004 `agentId 10005` on Celo
- **Invoice page:** `GET /i/<invoice-id>` (served by `bot/serve.py`; set
  `PUBLIC_BASE_URL` to the hostname in front of it)

```
charge Marco 500 BRL for the logo, due Friday
        │
        ├─ bot/jev.py ......... typed LLM judgment: is it an invoice, what amount,
        │                       what currency, who pays + a fraud screen.
        │                       Off-schema answers fail CLOSED to "review".
        ├─ bot/fx.py .......... 500 BRL is not 500 USA₮. Keyless rate, dated and cited.
        ├─ bot/services.py .... the agent BUYS its compliance layer for this invoice:
        │                       fx cross-check ($0.002), sanctions on the payout wallet
        │                       ($0.002) and on the counterparty name ($0.005).
        │                       A sanctions hit blocks settlement.
        ├─ bot/serve.py ....... /i/<id> — Celo wallet deep link + EIP-681 + Ripio
        │                       on-ramp link, so someone with no stablecoin can pay.
        └─ bot/watch.py ....... polls Celo, then bot/verify.py proves the witness:
                                right token, right recipient, right amount, and NOT a
                                transaction the payee sent itself.
```

## Onchain activity you can check

Every one of these is a real Celo mainnet transaction. `scripts/verify_settlements.py`
re-derives the settlements from the chain.

| What | Transaction |
|---|---|
| ERC-8004 agent identity registered (`agentId 10005`) | [`0x9c101fb0…2b70`](https://celoscan.io/tx/0x9c101fb0dcbfeba0d95c0172b22c996bec949a4d908b4daeaf486c0cd12b2b70) |
| Invoice paid 0.02 USA₮ → settled autonomously, no human | [`0x8a19fcc4…c724c4`](https://celoscan.io/tx/0x8a19fcc4139524502b52cc25748b9a40bee64384d09986b8f809690b77c724c4) |
| Invoice paid 0.998543 USA₮ (bridged in from another address) | [`0xe5796183…1073ee`](https://celoscan.io/tx/0xe5796183431382dd8e2e543b11140497420c8bb09cc35f5a24a52e76d731073e) |
| Agent buys an FX rate — $0.002 | [`0x1e2be36e…c51b4`](https://celoscan.io/tx/0x1e2be36e4eceb26a1908ccd1decbf35d29a5f411ab5dcf00c269e7fdad0c51b4) |
| Agent buys a wallet sanctions screen — $0.002 | [`0x1f750f0e…ebad5b`](https://celoscan.io/tx/0x1f750f0e4ef3e5db04fbf3b5fbef8a67ecda043df5604ace31ffd2db72ebad5b) |
| Agent buys a name sanctions screen — $0.005 | [`0x400ef3ae…2984dd`](https://celoscan.io/tx/0x400ef3ae497217f697fb6f151002085199ea726844d7382282567e38572984dd) |
| Textile FX: 1 USDT → 5.006 wBRL, forwarded to the freelancer | [`0xcac8b05d…2eec9e0`](https://celoscan.io/tx/0xcac8b05db8899954f2e5a920d842ebca448d8e5ebc2eadc6c4096e0392eec9e0) |

Paid-call receipts (raw 402 responses, headers and all) are committed under
[`docs/receipts/`](docs/receipts).

## Deployed contracts and addresses

| Thing | Celo mainnet address |
|---|---|
| ERC-8004 Identity Registry | [`0x8004A169FB4a3325136EB29fA0ceB6D2e539a432`](https://celoscan.io/address/0x8004A169FB4a3325136EB29fA0ceB6D2e539a432) |
| Agent identity (agentId 10005) | owned by `0xDbFe5c4d0256D2459bB216caB905999Ca41bE349` |
| USA₮ (invoice settlement) | `0xD2ab3C9A02DBBAB236BfEC45D1d755DF4267F771` |
| USDC | `0xcEBA9300f2b948710d2653dD7B07f33A8B32118C` |
| USDT | `0x48065fbBE25f71C9282ddf5e1cD6D6A887483D5e` |
| wBRL / wARS / wCOP (Ripio wFIAT, 18 dec) | `0xD76f5Faf6888e24D9F04Bf92a0c8B921FE4390e0` / `0x0DC4F92879B7670e5f4e4e6e3c801D229129D90D` / `0x8a1D45e102e886510e891d2Ec656a708991e2D76` |
| x402 facilitator (asset table is read live) | `https://api.x402.celo.org` |

**PayTo wallet** (the hackathon asks for this, since x402 settlements cannot
carry an ERC-8021 attribution tag yet): `0x86E95581E41946ED84956433a8a9c836bCbA636c`
— the invoice wallet that receives payments.

## Why money cannot move on a bad invoice

Each of these is enforced in code and has a test that fails without it:

- **Parsing fails closed.** Model answers are validated against the candidate set
  we offered; anything off-schema becomes `review`, never `settle`.
- **A "review" is a ticket, not a dead end.** It is stored, the operator is
  pinged, and `/release` / `/drop` / `/held` act on it. A held invoice's page
  renders a refusal and no pay button.
- **Sanctions screening blocks.** The agent does not run a sanctions list — it
  buys one per invoice. A non-clean verdict sets the invoice to `blocked`, which
  removes it from the settlement queue.
- **One transfer settles one invoice.** A consumed-witness registry means a
  payment cannot be replayed across invoices.
- **A transfer into your own wallet is not a payment.** `bot/verify.py` also
  checks the transaction *initiator*: a swap or top-up the payee made itself is
  refused. This was not theoretical — an earlier version of this store had
  `INV-0001` bound to a `callDiamondWithPermit2` swap, and `verify_settlements.py`
  is what caught it.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env      # fill TELEGRAM_BOT_TOKEN, AGENT_WALLET_KEY, INVOICE_WALLET
python -m bot.main        # Telegram bot
python -m bot.serve       # public invoice pages on PUBLIC_PORT
python -m bot.watch       # settlement watcher + the per-invoice purchases
```

The three processes are independent; `deploy/*.service` are the systemd units this
runs under (each `Restart=always`).

Audit the settlement claims in the store against the chain:

```bash
python scripts/verify_settlements.py         # exit != 0 if any claim fails
python scripts/verify_settlements.py --fix   # withdraw the ones that do not hold
```

## Tests

```bash
python -m pytest            # 142 tests, offline: no network, no keys, no .env
python -m pytest -m network # live facilitator, Textile, agent402, Typesafe Jev
python -m pytest -m live    # reads real Celo mainnet history
```

The default run deliberately passes on a clean checkout with nothing configured:
network- and key-dependent tests are marked and excluded by `pytest.ini`. CI runs
exactly that command.

## Tracks this targets

- **Stable Agents: LatAm** — `/wallet <addr> wBRL` and a BRL invoice settles
  **500 wBRL for 500 BRL**: in kind, no FX, no dollar in the middle. The pay page
  carries a prefilled Ripio on-ramp link bound to the payout address, so a payer
  who has never held a stablecoin can still pay.
- **Open Corridors: USA₮ over x402** — settlement is between independent wallets
  and is proven per transaction; the asset is chosen per freelancer, not globally.
- **Build with `buy`** — every invoice costs the agent ~$0.009 in services it
  does not own, and those purchases gate whether money may move.

## Limits (stated, not hidden)

- Non-USD fiat is priced by a keyless rate API and *cross-checked* against a rate
  the agent buys; it is not a firm market quote.
- Textile is wired as a standalone FX swap with committed receipts, not yet as an
  automatic payout leg. In-kind wBRL covers the "paid in reais" path today.
- Invoice storage is a JSON file with one process writing it. Fine at demo scale,
  not multi-tenant safe.
- x402 settlements carry no ERC-8021 tag (facilitator limitation) — hence the
  PayTo wallet above. All other transactions we send are tagged.

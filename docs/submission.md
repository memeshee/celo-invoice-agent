# Submission text

Paste-ready. Every claim here is checkable from a link in this file.

## Title

**impay — one sentence in, a proven payment out**

## One-liner

**impay** is an invoice agent for freelancers who get paid across borders. A freelancer types
`charge Marco 500 BRL for the logo` in Telegram; the ERC-8004 agent
prices it, buys its own sanctions screening over x402, mints a pay page anyone can
open, and marks the invoice paid **from Celo rather than from a claim**.

## What a judge should click, in order

1. **Pay an invoice** — [0.05 USDC](https://celo.uconfidant.tech/i/INV-0007-2782D9)
   or [25 wBRL with a Ripio on-ramp](https://celo.uconfidant.tech/i/INV-0008-9E74F4).
   Both are already screened; the page flips to settled by itself when the transfer
   lands.
2. **[`/proof`](https://celo.uconfidant.tech/proof)** — every settlement claim
   re-derived from Celo on render, and each payment labelled `independent payer` /
   `builder-funded` / `unverified`. It currently reads **0 independent payers**, and
   says so.
3. **[`/agent.json`](https://celo.uconfidant.tech/agent.json)** — the
   machine-readable card, matching ERC-8004 `agentId 10005` on chain.
4. **[docs/judging.md](judging.md)** — each criterion mapped to the function that
   satisfies it.

## The three things that are actually hard

**1. The invoice is paid from the chain, not from a promise.** `bot/verify.py` is the
only definition of "was this paid": the transaction must exist, succeed, *not be
initiated by the payee*, carry the right token to the right address, and match the
amount within 5%. That last rule exists because an earlier version accepted "at least
this much", and a `0.998543 USA₮` Squid-bridge delivery got credited to a `0.1 USD`
invoice for weeks. Both claims that failed this test are still listed in the README
with their transaction hashes and the reason.

**2. The agent buys the controls it does not own, per invoice.** A sanctions screen on
the payout wallet ($0.002), on the counterparty name ($0.005) and an FX cross-check
($0.002) are paid in USA₮/USDC over x402, and a non-clean verdict **blocks settlement**
rather than decorating it. `docs/receipts/` holds the raw 402 responses, and each
purchase's settlement is re-checked onchain.

**3. LatAm money does not round-trip through the dollar.** `/wallet <addr> wBRL` and a
BRL invoice settles **500 wBRL for 500 BRL** — in kind. The pay page also carries a
prefilled Ripio on-ramp bound to the payout address, so a payer holding no stablecoin
can still pay.

## What is not done yet

Stated in the README's Limits section rather than hidden: no independent payer has paid
yet (every settlement is builder-funded, and `/proof` reports that number), the Textile
wBRL swap is a standalone leg rather than an automatic payout, FX is a keyless rate
cross-checked against a bought one rather than a firm quote, and storage is a single
JSON file.

## Engineering, for the code reviewers

222 offline tests (no network, no keys, no `.env`), ruff, a 75% coverage floor and a
daily workflow that re-derives the settlement claims from Celo. The densest tests are
on the paths where silence costs money: every refusal in `bot/verify.py`, the x402 spend
budgets, the screening gate, and the HTTP surface over a real socket. `SECURITY.md` maps
each control to the test that pins it.

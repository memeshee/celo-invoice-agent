"""Public invoice pages — the stranger path.

Until now a payer needed the freelancer's Telegram *and* someone to type
/check: the bot could only be paid by people already inside the chat. This
serves a plain HTML invoice anyone can open, with a wallet deep link and EIP-681
URI, and a status that flips to settled on its own once the onchain watcher
confirms the transfer.

Deliberate choices:
- Read-only. There is no POST route, so there is no state a stranger can
  mutate and no CSRF surface; settlement is decided by bot/watch.py from chain
  data, never by the page.
- The invoice id is the capability (random suffix), so possession of the URL is
  what authorises reading it. Nothing sequential to enumerate.
- Every value that came from a user sentence is HTML-escaped. `recipient` is
  model-parsed out of free text and would otherwise be a stored-XSS vector on a
  page we hand to judges.
- Stdlib only: no framework, no build step, nothing to keep patched for 11 days.
"""

from __future__ import annotations

import html
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dotenv import load_dotenv

from . import store, x402

load_dotenv()  # the units run from the repo dir; .env is the single config source

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8088").rstrip("/")
AGENT_ID = os.environ.get("ERC8004_AGENT_ID", "10005")
IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
REPO = "https://github.com/memeshee/celo-invoice-agent"
EXPLORER_TX = "https://celo.blockscout.com/tx/"

INVOICE_PATH = re.compile(r"^/i/([A-Za-z0-9-]{4,64})$")
API_PATH = re.compile(r"^/api/invoice/([A-Za-z0-9-]{4,64})$")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    # One inline script drives the status poll; nothing else is loadable.
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"
    ),
}


# ------------------------------------------------------------------- payloads

def invoice_payload(inv: dict) -> dict:
    """Everything the page and the API need, priced once."""
    pay_to = store.payout_for(inv)
    out = {
        "id": inv["id"],
        "status": inv.get("status"),
        "created": inv.get("created"),
        "recipient": inv.get("recipient"),
        "invoice_amount": f"{inv.get('amount')} {str(inv.get('currency') or '').upper()}".strip(),
        "trust": inv.get("trust") or {},
        "settlement_tx": inv.get("settlement_tx"),
        "explorer": (EXPLORER_TX + inv["settlement_tx"]) if inv.get("settlement_tx") else None,
        "agent": {"id": AGENT_ID, "registry": IDENTITY_REGISTRY,
                  "registry_url": f"https://8004scan.io/agents/celo/{AGENT_ID}"},
    }
    if not pay_to:
        out["error"] = "invoice has no payout address"
        return out
    try:
        req = x402.create_payment_request(inv, pay_to)
    except (ValueError, KeyError, RuntimeError) as exc:
        out["error"] = f"cannot price this invoice: {exc}"
        return out
    out.update({
        "pay_to": pay_to,
        "settle_amount": f"{req['amount_display']} {req['symbol']}",
        "amount_display": req["amount_display"],
        "symbol": req["symbol"],
        "amount_atomic": req["amount_atomic"],
        "asset_contract": req["asset_contract"],
        "chain": req["chain"],
        "fx": req.get("fx"),
        # Wallet deep links. Celo wallets (MiniPay/Valora) take the first;
        # generic EVM wallets that understand EIP-681 take the second.
        "pay_link_celo": (
            f"celo://wallet/pay?address={pay_to}&amount={req['amount_display']}"
            f"&token={req['asset_contract']}&comment={inv['id']}"
        ),
        "pay_link_eip681": (
            f"ethereum:{req['asset_contract']}@42220/transfer"
            f"?address={pay_to}&uint256={req['amount_atomic']}"
        ),
    })
    return out


def _e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _short(addr: str) -> str:
    return f"{addr[:8]}…{addr[-6:]}" if addr and len(addr) > 20 else (addr or "")


PAGE_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1210;
     color:#e6edf3;font:16px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:24px}
.card{width:100%;max-width:520px;background:#111a17;border:1px solid #1f2d28;border-radius:16px;padding:28px}
.kicker{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:#5eead4;margin:0 0 10px}
.amount{font-size:40px;font-weight:650;letter-spacing:-.02em;margin:0}
.settle{color:#94a3b8;margin:6px 0 0;font-size:15px}
.rows{margin:22px 0 0;border-top:1px solid #1f2d28}
.row{display:flex;justify-content:space-between;gap:16px;padding:11px 0;border-bottom:1px solid #1f2d28;font-size:14px}
.row dt{color:#94a3b8;margin:0}
.row dd{margin:0;text-align:right;word-break:break-all;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600}
.open{background:#1e3a5f;color:#93c5fd}
.settled{background:#14351f;color:#6ee7b7}
a.btn{display:block;text-align:center;text-decoration:none;margin-top:16px;padding:14px 18px;
      border-radius:12px;font-weight:650;background:#5eead4;color:#06231c}
a.ghost{background:transparent;color:#5eead4;border:1px solid #24463c;font-weight:550}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
.foot{margin-top:22px;padding-top:16px;border-top:1px solid #1f2d28;font-size:12px;color:#64748b}
.foot a{color:#94a3b8}
.note{margin-top:14px;font-size:12px;color:#64748b}
.warn{background:#3b1d1d;border-color:#5b2a2a;color:#fca5a5;padding:14px;border-radius:10px;font-size:14px}
"""


def render_invoice(payload: dict) -> str:
    inv_id = _e(payload["id"])
    status = payload.get("status") or "open"
    badge = (f'<span class="badge settled" id="badge">settled</span>' if status == "settled"
             else '<span class="badge open" id="badge">awaiting payment</span>')
    rows = [
        ("Recipient", _e(payload.get("recipient") or "—")),
        ("Invoice", _e(payload.get("invoice_amount") or "—")),
    ]
    if payload.get("fx"):
        fx = payload["fx"]
        code = (payload.get("invoice_amount") or "").split(" ")[-1]
        rows.append(("Rate", _e(f"1 USD ≈ {(1 / float(fx['usd_per_unit'])):.4f} {code}")))
        rows.append(("Rate as of", _e(fx.get("as_of") or "—")))
    if payload.get("pay_to"):
        rows.append(("Paid to", f'<span class="mono">{_e(_short(payload["pay_to"]))}</span>'))
    rows.append(("Settles on", "Celo mainnet · eip155:42220"))
    if payload.get("settlement_tx"):
        rows.append(("Settlement tx",
                     f'<a href="{_e(payload["explorer"])}" target="_blank" rel="noreferrer" '
                     f'class="mono">{_e(payload["settlement_tx"][:10])}…</a>'))
    rows.append(("Trust screen",
                 _e(f"{(payload.get('trust') or {}).get('decision', '—')} "
                    f"(risk {(payload.get('trust') or {}).get('risk_level', '—')})")))

    if payload.get("error"):
        body = f'<div class="warn">{_e(payload["error"])}</div>'
    elif status == "settled":
        body = '<div class="note" id="paid" style="color:#6ee7b7">Payment confirmed onchain.</div>'
    else:
        body = (
            f'<a class="btn" href="{_e(payload["pay_link_celo"])}">Pay '
            f'{_e(payload.get("settle_amount") or "")}</a>'
            f'<a class="btn ghost" href="{_e(payload["pay_link_eip681"])}">Open in another wallet (EIP-681)</a>'
            f'<div class="note">Or send <b>{_e(payload.get("settle_amount") or "")}</b> to '
            f'<span class="mono">{_e(payload.get("pay_to") or "")}</span> on Celo. '
            f'This page confirms by itself — no need to tell anyone you paid.</div>'
        )

    row_html = "".join(f'<div class="row"><dt>{k}</dt><dd>{v}</dd></div>' for k, v in rows)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Invoice {inv_id}</title><style>{PAGE_CSS}</style></head>
<body><main class="card">
  <p class="kicker">Invoice · {inv_id}</p>
  <h1 class="amount">{_e(payload.get('settle_amount') or payload.get('invoice_amount') or '—')}</h1>
  <p class="settle">for {_e(payload.get('invoice_amount') or '—')}</p>
  <div style="margin-top:14px">{badge}</div>
  <dl class="rows">{row_html}</dl>
  {body}
  <div class="foot">
    Settled by an ERC-8004 agent · <span class="mono">agentId {AGENT_ID}</span> ·
    <a href="{REPO}" target="_blank" rel="noreferrer">source</a><br>
    The agent watches Celo for this transfer and marks the invoice paid from
    chain data, never from a claim.
  </div>
</main>
<script>
(function() {{
  var id = {json.dumps(payload["id"])};
  var settled = {json.dumps(status == "settled")};
  if (settled || {json.dumps(bool(payload.get("error")))}) return;
  function poll() {{
    fetch("/api/invoice/" + encodeURIComponent(id), {{headers: {{accept: "application/json"}}}})
      .then(function(r) {{ return r.json(); }})
      .then(function(d) {{
        if (d.status === "settled") {{ location.reload(); }}
      }}).catch(function() {{}});
  }}
  setInterval(poll, 5000);
}})();
</script>
</body></html>"""


def render_index() -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Celo Invoice Agent</title><style>{PAGE_CSS}</style></head>
<body><main class="card">
  <p class="kicker">Celo Invoice Agent</p>
  <h1 class="amount">Get paid in USA₮</h1>
  <p class="settle">Send the bot one sentence — “charge Marco 500 BRL for the logo” —
  and it mints a pay link like the one a payer just opened.</p>
  <dl class="rows">
    <div class="row"><dt>Agent identity</dt><dd class="mono">ERC-8004 #{AGENT_ID}</dd></div>
    <div class="row"><dt>Settlement</dt><dd>USA₮ on Celo · x402 facilitator</dd></div>
    <div class="row"><dt>Source</dt><dd><a href="{REPO}" target="_blank" rel="noreferrer">github</a></dd></div>
  </dl>
  <div class="foot">Invoice pages live at <span class="mono">/i/&lt;invoice-id&gt;</span>.</div>
</main></body></html>"""


# --------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "invoice-agent/1.0"

    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8") -> None:
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, indent=2), "application/json; charset=utf-8")

    def do_HEAD(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler hook
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/health":
            self._json(200, {"ok": True, "service": "celo-invoice-agent", "agent_id": AGENT_ID})
            return
        if path in ("/", "/index.html"):
            self._send(200, render_index())
            return
        api = API_PATH.match(path)
        if api:
            inv = store.get(api.group(1))
            if not inv:
                self._json(404, {"error": "unknown invoice"})
                return
            self._json(200, invoice_payload(inv))
            return
        page = INVOICE_PATH.match(path)
        if page:
            inv = store.get(page.group(1))
            if not inv:
                self._send(404, render_index())
                return
            self._send(200, render_invoice(invoice_payload(inv)))
            return
        self._json(404, {"error": "not found"})

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — base-class signature
        print(f"[serve] {self.address_string()} {format % args}", flush=True)


def make_server(host: str = "0.0.0.0", port: int | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port or int(os.environ.get("PUBLIC_PORT", "8088"))), Handler)


def main() -> None:
    server = make_server()
    host, port = server.server_address[:2]
    print(f"[serve] listening on http://{host}:{port} (public: {PUBLIC_BASE_URL})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

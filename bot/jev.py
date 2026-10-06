"""TypeSafe Jev judgments: invoice parsing + fraud screening.

Code owns the workflow; Jev supplies typed semantic judgments.
Endpoint: POST https://api.typesafe.ai/v1/systemone
Auth: TYPESAFE_API_KEY env or ~/.config/typesafe/api_key (server-side only).
"""

import json
import os
import re
import urllib.request
from pathlib import Path

SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

AMOUNT_RE = re.compile(r"\d+(?:[.,]\d{1,2})?")
CURRENCY_RE = re.compile(
    r"(?i)\b(wARS|wBRL|wMXN|wCOP|wPEN|wCLP|cNGN|USDC|USDT|USA₮|USDT|BRL|ARS|MXN|COP|PEN|CLP|USD|NGN)\b"
)
HANDLE_RE = re.compile(r"@[\w_]{2,32}|\b[A-Z][a-z]{2,20}\b")


def _api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    p = Path.home() / ".config" / "typesafe" / "api_key"
    return p.read_text().strip()


def ask(state, questions: dict, model: str = MODEL) -> dict:
    """One batched SystemOne call. Independent questions go together."""
    body = json.dumps({"state": state, "model": model, "questions": questions}).encode()
    req = urllib.request.Request(
        SYSTEMONE_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["answers"]


def parse_invoice(text: str) -> dict:
    """Free-text invoice -> structured draft. Code finds candidates, Jev selects."""
    amounts = list(dict.fromkeys(AMOUNT_RE.findall(text)))
    currencies = list(dict.fromkeys(c.upper() for c in CURRENCY_RE.findall(text)))
    names = list(dict.fromkeys(HANDLE_RE.findall(text)))[:8]
    if not amounts:
        amounts = ["?"]

    questions = {
        "is_invoice_intent": {
            "type": "noul",
            "instructions": "Does `msg.text` ask to create an invoice, bill, or payment request?",
        },
        "pick_amount": {
            "type": "choice",
            "instructions": "Which candidate in `amounts` is the invoiced total in `msg.text`? Return the exact candidate string.",
            "criteria": {a: f"Candidate invoiced total: {a}" for a in amounts},
        },
        "has_recipient": {
            "type": "noul",
            "instructions": "Does `msg.text` name a recipient/payer for the invoice?",
        },
    }
    if currencies:
        questions["pick_currency"] = {
            "type": "choice",
            "instructions": "Which candidate in `currencies` is the invoice currency in `msg.text`? Return the exact candidate string.",
            "criteria": {c: f"Candidate invoice currency: {c}" for c in currencies},
        }
    if names:
        questions["pick_recipient"] = {
            "type": "choice",
            "instructions": "Which candidate in `names` is the invoice recipient/payer in `msg.text`? Return the exact candidate string.",
            "criteria": {n: f"Candidate recipient name: {n}" for n in names},
        }

    answers = ask({"msg": {"text": text}, "amounts": amounts,
                   "currencies": currencies, "names": names}, questions)

    def choice(qid):
        a = answers.get(qid, {})
        return (a.get("choice") or a.get("value") or a.get("selected"),
                a.get("confidence", 0) or 0)

    amount, amount_conf = choice("pick_amount")
    currency, _ = choice("pick_currency") if currencies else (None, 0)
    recipient, _ = choice("pick_recipient") if names else (None, 0)
    intent_p = answers.get("is_invoice_intent", {}).get("noul", 0) or 0
    # Gate: plain intent, OR hard extraction evidence (amount confidently
    # picked + currency present). The latter covers terse/test phrasing
    # like "charge Test 0.1 USD" that scores low on intent prose.
    is_invoice = intent_p >= 0.5 or (amount_conf >= 0.7 and currency is not None)

    return {
        "is_invoice": is_invoice,
        "intent_p": intent_p,
        "amount": amount,
        "amount_conf": amount_conf,
        "currency": currency,
        "recipient": recipient,
        "has_recipient_p": (answers.get("has_recipient", {}).get("noul")
                            if "has_recipient" in answers else None),
        "raw": text,
    }


SCREEN_LEVELS = ["clean", "review", "block"]


def screen_invoice(invoice: dict, recent_descriptions: list[str] | None = None) -> dict:
    """Fraud screen. One batched call; code applies confidence-gated routing."""
    state = {"invoice": invoice, "recent": recent_descriptions or []}
    answers = ask(state, {
        "is_suspicious": {
            "type": "noul",
            "instructions": "Is `invoice` suspicious: round-amount testing, mismatched currency/recipient, pressure language, or signs of fraud?",
        },
        "is_duplicate": {
            "type": "noul",
            "instructions": "Does `invoice` duplicate any entry in `recent` (same recipient + amount)?",
        },
        "uses_pressure": {
            "type": "noul",
            "instructions": "Does `invoice.raw` use urgency/pressure language to rush payment?",
        },
        "risk_level": {
            "type": "score",
            "instructions": "Rate the payment risk of `invoice` along ordered levels.",
            "criteria": [
                "clean: ordinary invoice, named recipient, sensible amount",
                "review: something odd, hold for a human glance",
                "block: likely fraud or abuse, do not settle",
            ],
        },
    })
    susp = answers.get("is_suspicious", {}).get("noul", 0) or 0
    dup = answers.get("is_duplicate", {}).get("noul", 0) or 0
    score_ans = answers.get("risk_level", {})
    # Code-side gate: block on high suspicion or block-level score, review on mid.
    level = score_ans.get("score", "clean")
    decision = "settle"
    if susp >= 0.8 or level == "block":
        decision = "block"
    elif susp >= 0.5 or dup >= 0.5 or level == "review":
        decision = "review"
    return {
        "suspicious_p": susp,
        "duplicate_p": dup,
        "pressure_p": (answers.get("uses_pressure", {}).get("noul")),
        "risk_level": level,
        "decision": decision,
        "raw_answers": answers,
    }

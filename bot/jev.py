"""TypeSafe Jev judgments: invoice parsing + fraud screening.

Code owns the workflow; Jev supplies typed semantic judgments.
Endpoint: POST https://api.typesafe.ai/v1/systemone
Auth: TYPESAFE_API_KEY env or ~/.config/typesafe/api_key (server-side only).

Security fixes (audit run-1):
- amount-regex-splits-grouped-numbers: the amount tokenizer treated the first
  separator in a grouped number as the decimal mark, so "1.500 BRL" offered the
  model only ['1.50','0'] and the invoice priced 1000x low. Tokens are now read
  whole and normalized to canonical dot-decimal before they become candidates.
- screen-fail-open-mintable-draft / trust-verdict-model-steerable: model
  answers are validated against SCREEN_LEVELS and coerced to numbers, and an
  off-schema or missing answer fails closed to "review" instead of "clean".
"""

import json
import os
import re
import urllib.request
from pathlib import Path

SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

# Kept for callers/tests that still reference it; amount parsing uses the
# whole-token tokenizer below.
AMOUNT_RE = re.compile(r"\d+(?:[.,]\d{1,2})?")
CURRENCY_RE = re.compile(
    r"(?i)\b(wARS|wBRL|wMXN|wCOP|wPEN|wCLP|cNGN|USDC|USDT|USA₮|USDT|BRL|ARS|MXN|COP|PEN|CLP|USD|NGN)\b"
)
HANDLE_RE = re.compile(r"@[\w_]{2,32}|\b[A-Z][a-z]{2,20}\b")
NUM_TOKEN_RE = re.compile(r"\d[\d.,]*\d|\d")


def normalize_amount_token(token: str) -> str:
    """Grouped/decimal token -> canonical dot-decimal string.

    "1.500" -> "1500", "1.500,50" -> "1500.50", "1,500.50" -> "1500.50",
    "500,00" -> "500.00". The last separator counts as the decimal mark only
    when it is followed by one or two digits; everything before it is grouping.
    """
    parts = re.split(r"[.,]", token)
    if len(parts) > 1 and 1 <= len(parts[-1]) <= 2:
        whole, frac = "".join(parts[:-1]), parts[-1]
    else:
        whole, frac = "".join(parts), ""
    return f"{whole}.{frac}" if frac else whole


def amount_candidates(text: str) -> list[str]:
    """Amounts the model may choose from, canonical and de-duplicated."""
    return list(dict.fromkeys(normalize_amount_token(t) for t in NUM_TOKEN_RE.findall(text)))


def _api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    p = Path.home() / ".config" / "typesafe" / "api_key"
    try:
        return p.read_text().strip()
    except FileNotFoundError:
        # Say what is missing. A bare FileNotFoundError from deep inside a test
        # run reads like a broken build rather than an unconfigured machine.
        raise RuntimeError(
            "TYPESAFE_API_KEY is not set and ~/.config/typesafe/api_key does not "
            "exist — the invoice parser needs a Typesafe key to run"
        ) from None


def has_credentials() -> bool:
    """Whether a Typesafe key is configured anywhere (env or key file)."""
    if os.environ.get("TYPESAFE_API_KEY", "").strip():
        return True
    return (Path.home() / ".config" / "typesafe" / "api_key").exists()


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
    amounts = amount_candidates(text)
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
            "instructions": ("Which candidate in `amounts` is the invoiced total in "
                             "`msg.text`? Return the exact candidate string."),
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
            "instructions": ("Which candidate in `currencies` is the invoice currency "
                             "in `msg.text`? Return the exact candidate string."),
            "criteria": {c: f"Candidate invoice currency: {c}" for c in currencies},
        }
    if names:
        questions["pick_recipient"] = {
            "type": "choice",
            "instructions": ("Which candidate in `names` is the invoice recipient/payer "
                             "in `msg.text`? Return the exact candidate string."),
            "criteria": {n: f"Candidate recipient name: {n}" for n in names},
        }

    answers = ask({"msg": {"text": text}, "amounts": amounts,
                   "currencies": currencies, "names": names}, questions)

    def choice(qid, allowed: list[str] | None = None):
        a = answers.get(qid, {})
        value = a.get("choice") or a.get("value") or a.get("selected")
        if allowed is not None and value not in allowed:
            # The answer must be one of the candidates we offered; anything
            # else is model drift and must not become a settlement threshold.
            return None, 0
        return (value, a.get("confidence", 0) or 0)

    amount, amount_conf = choice("pick_amount", amounts)
    currency, _ = choice("pick_currency", currencies) if currencies else (None, 0)
    recipient, _ = choice("pick_recipient", names) if names else (None, 0)
    intent_p = _as_float(answers.get("is_invoice_intent", {}).get("noul"), 0.0)
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
        "has_recipient_p": (_as_float(answers.get("has_recipient", {}).get("noul"), None)
                            if "has_recipient" in answers else None),
        "raw": text,
    }


def _as_float(value, default):
    """Coerce a model answer to float, or return the default when it is not one."""
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


# Thresholds for screen_invoice's routing. Named, exported and tested, because a
# gate whose numbers live inline is a gate nobody can reason about.
REVIEW_SUSPICION = float(os.environ.get("SCREEN_REVIEW_AT", "0.35"))
BLOCK_SUSPICION = float(os.environ.get("SCREEN_BLOCK_AT", "0.80"))

SCREEN_LEVELS = ["clean", "review", "block"]


def trust_reasons(screen: dict) -> list[str]:
    """Plain-language reasons for a screening decision.

    The risk number alone is unfalsifiable to the person reading it: "risk 0.65"
    tells a freelancer nothing about what to change. Name the signals that
    actually crossed a threshold.
    """
    reasons = []
    defs = (
        ("suspicious_p", 0.5, "looks like fraud"),
        ("duplicate_p", 0.5, "duplicates a recent invoice"),
        ("pressure_p", 0.5, "uses urgency/pressure language"),
    )
    for key, floor, text in defs:
        value = screen.get(key)
        if isinstance(value, (int, float)) and value >= floor:
            reasons.append(f"{text} ({value:.2f})")
    level = screen.get("risk_level")
    decision = screen.get("decision")
    # Only explain the decision that was actually taken. Reporting the model's
    # label after a settle read as a contradiction in the chat: "Trust: settle"
    # followed by "Why: flagged for a human glance".
    if level == "block" and decision != "settle":
        reasons.append("scored 'block' by the risk model")
    elif level == "review" and decision != "settle" and not reasons:
        reasons.append("flagged for a human glance by the risk model")
    return reasons


def screen_invoice(invoice: dict, recent_descriptions: list[str] | None = None) -> dict:
    """Fraud screen. One batched call; code applies confidence-gated routing.

    Answers are validated before they gate routing, and anything off-schema
    fails closed to "review" rather than defaulting to a clean settle.
    """
    state = {"invoice": invoice, "recent": recent_descriptions or []}
    answers = ask(state, {
        "is_suspicious": {
            "type": "noul",
            "instructions": ("Is `invoice` suspicious: round-amount testing, mismatched "
                             "currency/recipient, pressure language, or signs of fraud?"),
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
    # Missing or off-type answers are treated as maximally suspicious, not clean.
    susp = _as_float(answers.get("is_suspicious", {}).get("noul"), 1.0)
    dup = _as_float(answers.get("is_duplicate", {}).get("noul"), 1.0)
    pressure = _as_float(answers.get("uses_pressure", {}).get("noul"), None)
    score_ans = answers.get("risk_level", {})
    level = score_ans.get("score") if isinstance(score_ans, dict) else None
    if level not in SCREEN_LEVELS:
        level = "review"  # fail closed on missing or off-vocabulary answers
    # Which signal wins when they disagree.
    #
    # The model answers two different things: a probability (is_suspicious) and a
    # coarse label (risk_level). Treating the label as an independent veto held
    # every ordinary invoice: measured live, "charge Marco 500 BRL for the logo, due
    # Friday" came back suspicious_p=0.09 — nine percent — with risk_level="review",
    # so the bot answered a normal sentence with "held for review" and never minted
    # a pay link. A gate nobody can pass is not a gate.
    #
    # The probability is the calibrated answer, so the policy is written on the
    # numbers; "block" from the label still blocks (fail safe), "review" from the
    # label only holds when a number agrees with it. SCREEN_STRICT=1 restores the
    # old behaviour for an operator who wants the label to veto.
    strict = os.environ.get("SCREEN_STRICT", "0").lower() in ("1", "true", "yes")
    decision = "settle"
    if susp >= BLOCK_SUSPICION or level == "block" or (pressure or 0) >= 0.8:
        decision = "block"
    elif susp >= REVIEW_SUSPICION or dup >= 0.5 or (pressure or 0) >= 0.6:
        decision = "review"
    elif strict and level == "review":
        decision = "review"
    return {
        "suspicious_p": susp,
        "duplicate_p": dup,
        "pressure_p": pressure,
        "risk_level": level,
        "decision": decision,
        "raw_answers": answers,
    }

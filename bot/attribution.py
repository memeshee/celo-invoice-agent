"""ERC-8021 attribution: append the app code to every tx we sign.

Celo credits onchain activity to the app that produced it by decoding a suffix
stuck on the END of a transaction's calldata. The EVM discards trailing bytes,
so the suffix never changes execution — it only makes the transaction
identifiable as ours.

Layout (Schema 0, byte-for-byte what @celo/attribution-tags -> ox
`Attribution.toDataSuffix` emits):

    codes (UTF-8, comma-joined) || codesLength (1 byte) || schemaId 0x00 || marker (16 bytes)

This cannot be backfilled: a legacy-transaction tag at eight bytes
(`marker = 0x8021 * 8`) directory costs nothing, so we append it at the single
place every sender funnels through rather than sprinkling it at call sites.

Attribution tags are NOT supported by Celo's x402 facilitator yet — x402
settlements are credited via our PayTo wallet address instead (hackathon rules).
They DO apply to every ordinary transaction we broadcast (Textile swaps,
registry writes, transfers).
"""

from __future__ import annotations

import os

# 0x8021 repeated 8 times — the ERC-8021 marker.
ERC_8021_MARKER = bytes.fromhex("80218021802180218021802180218021")
SCHEMA_0 = 0x00

# A human-readable code of our own. Not scored by anything — kept so our own
# analytics stay stable if the program issues a different code next time.
OWN_CODE = "celo_invoiceagent"

# The code the hackathon platform issued for this entry. It is NOT derivable from
# the repository or a hostname — the platform can derive from anything it likes —
# so it is configuration, and the one value that must be on the wire.
ASSIGNED_CODE = os.environ.get("CELO_ATTRIBUTION_CODE_ASSIGNED", "celo_b97cdbaae280")

DEFAULT_CODES = (ASSIGNED_CODE, OWN_CODE)


def app_code() -> str:
    """The code(s) we tag with, comma-joined. Override with CELO_ATTRIBUTION_CODE."""
    raw = os.environ.get("CELO_ATTRIBUTION_CODE", "").strip()
    return raw or ",".join(DEFAULT_CODES)


def credits_this_entry() -> bool:
    """True when the suffix carries the code this entry is scored against.

    Assert this at startup and in CI. Tagging with anything else produces
    transactions that look attributed on chain and credit nobody — silently.
    """
    configured = {c.strip() for c in app_code().split(",") if c.strip()}
    return ASSIGNED_CODE in configured


def validate_code(code: str) -> str:
    if not 1 <= len(code) <= 32:
        raise ValueError(f"attribution code must be 1-32 chars, got {len(code)}")
    if not all(c.islower() or c.isdigit() or c == "_" for c in code):
        raise ValueError(f"attribution code must match [a-z0-9_]{{1,32}}: {code!r}")
    return code


def to_data_suffix(code: str | None = None) -> bytes:
    """Encode the Schema 0 suffix for one or more codes."""
    # `code or app_code()` would silently accept "" and use the default; an
    # explicit empty string is a bug at the call site and must raise.
    chosen = app_code() if code is None else code
    raw = ",".join(validate_code(c) for c in chosen.split(","))
    codes = raw.encode()
    if len(codes) > 255:
        raise ValueError("comma-joined codes must fit in the 1-byte length field")
    return codes + bytes([len(codes), SCHEMA_0]) + ERC_8021_MARKER


def tag(calldata: str | bytes, code: str | None = None) -> str:
    """Append the attribution suffix to calldata. 0x-hex in, 0x-hex out."""
    if isinstance(calldata, bytes):
        body = calldata
    else:
        body = bytes.fromhex(calldata.removeprefix("0x"))
    return "0x" + (body + to_data_suffix(code)).hex()


def from_calldata(calldata: str | bytes) -> list[str] | None:
    """Decode our suffix back out of calldata, or None when untagged.

    Mirrors ox `Attribution.fromData` so a test can prove the tag survives the
    wire instead of assuming it does.
    """
    body = (bytes.fromhex(calldata.removeprefix("0x"))
            if isinstance(calldata, str) else bytes(calldata))
    if len(body) < 18 or body[-16:] != ERC_8021_MARKER:
        return None
    if body[-17] != SCHEMA_0:
        return None
    length = body[-18]
    if length == 0 or len(body) < 18 + length:
        return None
    codes = body[-18 - length:-18].decode("utf-8", errors="replace")
    return codes.split(",") if codes else None

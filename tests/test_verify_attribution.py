"""The verifier must be able to fail: it is the evidence for the bounty."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_attribution import decode  # noqa: E402


def _calldata(body: bytes, codes: str, schema: int = 0) -> str:
    suffix = codes.encode() + bytes([len(codes.encode()), schema]) + bytes.fromhex("8021" * 8)
    return "0x" + (body + suffix).hex()


def test_decodes_the_assigned_code():
    out = decode(_calldata(b"\x01\x02", "celo_b97cdbaae280,celo_invoiceagent"))
    assert out == {"tagged": True, "schema": 0,
                   "codes": ["celo_b97cdbaae280", "celo_invoiceagent"]}


def test_untagged_calldata_is_reported_as_untagged():
    assert decode("0x" + "aa" * 40) == {"tagged": False}


def test_a_suffix_that_is_not_at_the_end_is_rejected():
    """Trailing bytes after the marker mean the tag did not make it onto the wire."""
    good = _calldata(b"\x01\x02", "celo_b97cdbaae280")
    assert decode(good)["tagged"] is True
    assert decode(good + "00" * 4)["tagged"] is False


def test_wrong_code_still_decodes_so_the_caller_can_fail_it():
    out = decode(_calldata(b"", "celo_6dfc0ef48fe1"))
    assert out["tagged"] is True
    assert "celo_b97cdbaae280" not in out["codes"]

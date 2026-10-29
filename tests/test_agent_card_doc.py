"""The card mirror must not drift from the chain.

`docs/agent/erc8004-card.md` claimed "1,637 chars, services[] added later" long
after both were false, because it was hand-maintained. It is generated now — and
this test is what makes "generated" true rather than a nicer sentence.
"""

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIRROR = ROOT / "docs" / "agent" / "erc8004-card.md"

CARD_JSON = {
    "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
    "name": "Celo Invoice Agent",
    "description": "Turns one sentence into a settled stablecoin payment on Celo.",
    "active": True,
    "x402Support": False,
    "services": [{"name": "web", "endpoint": "https://example.test"}],
    "registrations": [{"agentId": 10005,
                       "agentRegistry": "eip155:42220:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"}],
}


def _uri(meta: dict) -> str:
    import base64
    return ("data:application/json;base64,"
            + base64.b64encode(json.dumps(meta).encode()).decode())


def test_render_mirrors_every_field_a_judge_would_check():
    from scripts.gen_erc8004_doc import render
    text = render(_uri(CARD_JSON))
    assert "| x402Support | false |" in text            # the value that was wrong
    assert "| active | true |" in text
    assert "services[0]" in text and "https://example.test" in text
    assert "registrations[0]" in text and "agentId 10005" in text
    assert "length onchain" in text
    assert text.startswith("# ERC-8004 card (generated)")


def test_render_is_deterministic():
    from scripts.gen_erc8004_doc import render
    assert render(_uri(CARD_JSON)) == render(_uri(CARD_JSON))


def test_the_committed_mirror_says_it_is_generated():
    assert MIRROR.exists(), "run scripts/gen_erc8004_doc.py"
    head = MIRROR.read_text()[:400]
    assert "generated" in head.lower()
    assert "scripts/gen_erc8004_doc.py" in head


@pytest.mark.network
def test_the_committed_mirror_matches_the_chain():
    """Live check: regenerate and compare. Skipped unless -m network is selected."""
    from scripts.gen_erc8004_doc import read_uri, render
    assert render(read_uri()) == MIRROR.read_text(), (
        "docs/agent/erc8004-card.md is stale — run scripts/gen_erc8004_doc.py")

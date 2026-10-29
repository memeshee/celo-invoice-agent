"""ERC-8021 attribution round-trip and tag-shape tests.

The tag is unbackfillable, so it gets the same treatment as the settlement
proofs: assert the exact bytes on the wire, and pair the negative case with a
known-positive fixture (an untagged calldata test alone cannot catch a broken
encoder that drops every suffix).
"""

from bot import attribution


def test_suffix_matches_erc8021_schema0_layout():
    suffix = attribution.to_data_suffix("celo_test1234")
    codes = b"celo_test1234"
    assert suffix[:len(codes)] == codes
    assert suffix[len(codes)] == len(codes)          # codesLength
    assert suffix[len(codes) + 1] == 0x00            # schemaId
    assert suffix[len(codes) + 2:] == attribution.ERC_8021_MARKER
    assert len(suffix) == len(codes) + 2 + 16


def test_marker_is_eight_8021_words():
    assert attribution.ERC_8021_MARKER.hex() == "8021" * 8


def test_tag_appends_and_preserves_calldata():
    inner = "0xa9059cbb" + "00" * 64
    tagged = attribution.tag(inner)
    assert tagged.startswith(inner)                  # execution unchanged
    assert attribution.from_calldata(tagged) == [attribution.app_code()]


def test_round_trip_of_known_positive_fixture():
    # A real payload shape: selector + 32-byte word, then our suffix.
    tagged = attribution.tag("0xa9059cbb" + "11" * 64, code="celo_b7k3p9da")
    assert attribution.from_calldata(tagged) == ["celo_b7k3p9da"]


def test_untagged_calldata_decodes_to_none():
    assert attribution.from_calldata("0xa9059cbb" + "11" * 64) is None


def test_multiple_codes_comma_joined():
    suffix = attribution.to_data_suffix("celo_one,celo_two")
    assert b"celo_one,celo_two" in suffix


def test_rejects_bad_codes():
    for bad in ["Celo_App", "celo app", "a" * 33, ""]:
        try:
            attribution.to_data_suffix(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid code {bad!r}")


def test_env_override_is_used(monkeypatch):
    monkeypatch.setenv("CELO_ATTRIBUTION_CODE", "celo_issued")
    assert attribution.from_calldata(attribution.tag("0x")) == ["celo_issued"]

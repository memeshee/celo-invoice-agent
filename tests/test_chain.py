"""The signing path must always carry the attribution tag.

An untagged transfer is permanently unattributable, so this is asserted at the
build step rather than trusting every call site to remember.
"""

import pytest
from eth_account import Account

from bot import attribution, chain

KEY = "0x" + "11" * 32
TO = "0x86E95581E41946ED84956433a8a9c836bCbA636c"
USAT = "0xD2ab3C9A02DBBAB236BfEC45D1d755DF4267F771"


class FakeEth:
    gas_price = 200 * 10**9

    def estimate_gas(self, tx):
        return 51_000

    def get_transaction_count(self, addr, block):
        return 3


class FakeW3:
    eth = FakeEth()


def test_erc20_transfer_data_selector_and_args():
    data = chain.erc20_transfer_data(USAT, TO, 20_000)
    assert data.startswith("0xa9059cbb")                    # transfer(address,uint256)
    assert TO.lower()[2:].rjust(64, "0") in data.lower()
    assert hex(20_000)[2:].rjust(64, "0") in data.lower()


def test_build_tagged_tx_is_tagged_and_legacy():
    built = chain.build_tagged_tx(USAT, chain.erc20_transfer_data(USAT, TO, 1),
                                  key=KEY, web3=FakeW3())
    # The tag is what makes the tx attributable; assert it survived signing input.
    assert built["attribution"] == [attribution.app_code()]
    assert attribution.from_calldata(built["tx"]["data"]) == [attribution.app_code()]
    # Legacy fee model: a 1559 tx gets dropped by Forno's mempool.
    assert "gasPrice" in built["tx"] and "maxFeePerGas" not in built["tx"]
    assert built["tx"]["chainId"] == 42220
    assert built["from"] == Account.from_key(KEY).address


def test_build_tagged_tx_keeps_calldata_intact():
    inner = chain.erc20_transfer_data(USAT, TO, 20_000)
    built = chain.build_tagged_tx(USAT, inner, key=KEY, gas=90_000,
                                  gas_price=1, nonce=0, web3=FakeW3())
    assert built["tx"]["data"].startswith(inner)   # EVM discards the trailing bytes
    assert built["tx"]["gas"] == 90_000 and built["tx"]["nonce"] == 0


def test_untagged_calldata_would_decode_to_none():
    """Negative control: a plain transfer is not attributed, which is the bug."""
    assert attribution.from_calldata(chain.erc20_transfer_data(USAT, TO, 1)) is None

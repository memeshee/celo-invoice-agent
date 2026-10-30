import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _network_marker_reopens_sockets(request):
    """`-m network` runs are deliberately online.

    pytest.ini blocks sockets for the default suite so an accidental outbound
    call fails loudly instead of passing on a laptop and hanging in a sandbox.
    A test carrying the `network` marker asked for the network, so it gets it
    back for the duration of that test.
    """
    if request.node.get_closest_marker("network"):
        from pytest_socket import enable_socket
        enable_socket()
    yield


@pytest.fixture(autouse=True)
def _claims_snapshot_goes_to_tmp(tmp_path, monkeypatch):
    """A test that settles an invoice must not rewrite the committed evidence.

    store.mark_settled refreshes claims.json from whatever store it just wrote,
    so a test with a temporary store would otherwise truncate the real snapshot
    to its own fixture invoices — the audit would then verify a file no human
    meant to publish.
    """
    monkeypatch.setenv("CLAIMS_PATH", str(tmp_path / "claims.json"))
    yield


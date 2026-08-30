"""Micro tests for the /peer_cloud_file error contract.

Both failures below are prerequisites a client can wait out: the route may be
announced later, and the sender key may be delivered later. Neither is the
peer's storage failing, so neither may reach the client as a 500 or as an
absent object. A stub backend stands in for the real one because the behavior
under test is the response, not how the failure was produced.
"""

import pytest
from fastapi.testclient import TestClient

import small_sea_hub.backend as SmallSea
from small_sea_hub.crypto import SenderKeyUnavailableExn
from small_sea_hub.server import app


class _RaisingBackend:
    def __init__(self, failure):
        self.failure = failure

    def download_from_peer(self, session_hex, teammate_id_hex, path):
        raise self.failure


@pytest.mark.parametrize(
    "failure,error_code",
    [
        (SmallSea.SmallSeaNotFoundExn("no route"), "peer_storage_unknown"),
        (SenderKeyUnavailableExn("no sender key"), "peer_sender_key_unavailable"),
    ],
)
def test_a_peer_read_prerequisite_is_a_409_with_a_stable_code(failure, error_code):
    app.state.backend = _RaisingBackend(failure)
    client = TestClient(app)

    resp = client.get(
        "/peer_cloud_file",
        params={"teammate_id": "aa" * 16, "path": "latest-link.yaml"},
        headers={"Authorization": "Bearer session"},
    )

    assert resp.status_code == 409
    assert resp.json()["error"] == error_code
    assert resp.json()["detail"]

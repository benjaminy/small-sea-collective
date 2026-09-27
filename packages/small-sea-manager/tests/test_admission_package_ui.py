"""Micro tests for the admission package's showing up in the web UI (issue #266).

Alice's "Complete acceptance" response must carry the package token for her
to hand to Bob, and Bob's "Import admission package" form must import a
valid token and show a notice -- not a 500 -- for a garbage one.
"""

import base64
import json
import pathlib
import re

from cod_sync.store import LocalFolderStore
from fastapi.testclient import TestClient

import small_sea_manager.provisioning as provisioning
from small_sea_manager.web import create_app

from test_admission_records import _push, _setup_team

TEAM = "ProjectX"


def _accept(root, bob_hex, cloud, invitee_label="Bob"):
    alice_hex, _bob_hex, cloud_dir, alice_sync = _setup_team(root)
    token = provisioning.create_invitation(
        root, alice_hex, TEAM,
        {"protocol": "localfolder", "url": str(cloud_dir)},
        invitee_label=invitee_label,
    )
    _push(alice_sync, cloud_dir)
    acceptance = provisioning.accept_invitation(
        root, bob_hex, token, inviter_store=LocalFolderStore(str(cloud_dir)),
    )
    return alice_hex, acceptance


def _token_box_text(html: str) -> str:
    match = re.search(r'class="token-box">([^<]+)<', html)
    assert match, "no token-box in response"
    return match.group(1)


def test_completion_response_carries_the_package_token(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    token = provisioning.create_invitation(
        root, alice_hex, TEAM,
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
    )
    _push(alice_sync, cloud)
    acceptance = provisioning.accept_invitation(
        root, bob_hex, token, inviter_store=LocalFolderStore(str(cloud)),
    )

    client = TestClient(create_app(root, alice_hex))
    response = client.post(
        f"/teams/{TEAM}/complete-acceptance", data={"acceptance_token": acceptance}
    )
    assert response.status_code == 200
    package_token = _token_box_text(response.text)
    assert provisioning.decode_admission_package_token(package_token) is not None


def test_posting_a_valid_token_imports_the_package(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    token = provisioning.create_invitation(
        root, alice_hex, TEAM,
        {"protocol": "localfolder", "url": str(cloud)},
        invitee_label="Bob",
    )
    _push(alice_sync, cloud)
    acceptance = provisioning.accept_invitation(
        root, bob_hex, token, inviter_store=LocalFolderStore(str(cloud)),
    )
    alice_client = TestClient(create_app(root, alice_hex))
    completion = alice_client.post(
        f"/teams/{TEAM}/complete-acceptance", data={"acceptance_token": acceptance}
    )
    package_token = _token_box_text(completion.text)

    bob_client = TestClient(create_app(root, bob_hex))
    response = bob_client.post(
        f"/teams/{TEAM}/import-admission-package", data={"package_token": package_token}
    )
    assert response.status_code == 200
    assert "Admission package imported." in response.text


def test_posting_a_garbage_token_shows_a_notice_not_a_500(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex, bob_hex, cloud, alice_sync = _setup_team(root)
    client = TestClient(create_app(root, bob_hex))
    response = client.post(
        f"/teams/{TEAM}/import-admission-package", data={"package_token": "not a real token"}
    )
    assert response.status_code == 200
    assert "Package rejected" in response.text

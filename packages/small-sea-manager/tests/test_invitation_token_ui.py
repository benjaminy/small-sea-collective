"""Micro tests for the Manager's create-invitation response (issue #209).

The create-invitation form targets `#invitation-token-area` and the same
response carries an out-of-band swap of the surrounding invitations block.
htmx applies out-of-band swaps first and resolves the primary target when the
request is issued, so an out-of-band element that contains the target detaches
it and the token or the error is written into a node no longer in the document.

These cover both endpoint outcomes and the structural invariant that makes the
ordering hazard impossible: the primary target lives outside every out-of-band
element.

Everything here is local: a `localfolder` cloud storage, no Hub session and no
MinIO.
"""

import base64
import json
import pathlib
from html.parser import HTMLParser

import pytest
import small_sea_manager.provisioning as provisioning
from fastapi.testclient import TestClient
from small_sea_manager.web import create_app

TEAM = "ProjectX"

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


# --------------------------------------------------------------------------- #
# Setup helpers
# --------------------------------------------------------------------------- #


def _with_allocation(root: pathlib.Path) -> str:
    """A team whose Core berth got a cloud allocation at creation time."""
    cloud = root / "alice-cloud"
    cloud.mkdir()
    alice_hex = provisioning.create_new_participant(root, "Alice")
    provisioning.add_cloud_storage(
        root, alice_hex, protocol="localfolder", url=str(cloud)
    )
    provisioning.create_team(root, alice_hex, TEAM)
    return alice_hex


def _without_allocation(root: pathlib.Path) -> str:
    """A team created before any cloud storage existed, so its Core berth has none.

    Cloud storage is added afterwards: `TeamManager.create_invitation` reads it
    before reaching the berth allocation, and without it the failure would come
    from the wrong check.
    """
    cloud = root / "bob-cloud"
    cloud.mkdir()
    bob_hex = provisioning.create_new_participant(root, "Bob")
    provisioning.create_team(root, bob_hex, TEAM)
    provisioning.add_cloud_storage(
        root, bob_hex, protocol="localfolder", url=str(cloud)
    )
    return bob_hex


def _create(client, label="Dana", role="steward"):
    return client.post(
        f"/teams/{TEAM}/invitations",
        data={"invitee_label": label, "role": role},
    )


# --------------------------------------------------------------------------- #
# Markup helpers
# --------------------------------------------------------------------------- #


class _Element:
    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.data = []

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _Tree(HTMLParser):
    """Every element in a fragment, with parent links and text."""

    def __init__(self):
        super().__init__()
        self._stack = []
        self.elements = []

    def handle_starttag(self, tag, attrs):
        element = _Element(
            tag, dict(attrs), self._stack[-1] if self._stack else None
        )
        self.elements.append(element)
        if tag not in _VOID_TAGS:
            self._stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.elements.append(
            _Element(tag, dict(attrs), self._stack[-1] if self._stack else None)
        )

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data):
        if self._stack:
            self._stack[-1].data.append(data)


def _parse(html) -> _Tree:
    tree = _Tree()
    tree.feed(html)
    return tree


def _by_id(tree, element_id):
    return [e for e in tree.elements if e.attrs.get("id") == element_id]


def _ids(tree):
    return [
        e.attrs["id"] for e in tree.elements if e.attrs.get("id") is not None
    ]


def _text_of(element):
    return "".join(element.data).strip()


def _token_from(html):
    boxes = [
        e
        for e in _parse(html).elements
        if "token-box" in e.attrs.get("class", "").split()
    ]
    assert len(boxes) == 1, f"expected one .token-box, found {len(boxes)}"
    return _text_of(boxes[0])


# --------------------------------------------------------------------------- #
# Fixture preconditions
# --------------------------------------------------------------------------- #


def test_fixtures_differ_only_in_the_core_berth_allocation(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _with_allocation(root)
    assert (
        provisioning.derive_team_join_state(root, alice_hex, TEAM)["allocation"]
        is not None
    )

    root_b = root / "second"
    root_b.mkdir()
    bob_hex = _without_allocation(root_b)
    assert (
        provisioning.derive_team_join_state(root_b, bob_hex, TEAM)["allocation"]
        is None
    )


# --------------------------------------------------------------------------- #
# Endpoint outcomes
# --------------------------------------------------------------------------- #


def test_create_invitation_response_carries_the_decodable_token(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _with_allocation(root)
    client = TestClient(create_app(root, alice_hex))

    response = _create(client, label="Dana")
    assert response.status_code == 200

    payload = json.loads(base64.b64decode(_token_from(response.text)))
    assert payload["team_name"] == TEAM
    assert payload["invitee_label"] == "Dana"


def test_create_invitation_response_refreshes_the_invitations_list(playground_dir):
    root = pathlib.Path(playground_dir)
    alice_hex = _with_allocation(root)
    client = TestClient(create_app(root, alice_hex))

    tree = _parse(_create(client, label="Dana").text)
    listing = _by_id(tree, f"invitations-{TEAM}")
    assert listing, f"response carries no invitations-{TEAM} element"

    labels = [
        _text_of(e)
        for e in tree.elements
        if e.tag == "td" and set(e.ancestors()) & set(listing)
    ]
    assert "Dana" in labels


def test_create_invitation_renders_the_missing_allocation_error(playground_dir):
    root = pathlib.Path(playground_dir)
    bob_hex = _without_allocation(root)
    client = TestClient(create_app(root, bob_hex))

    response = _create(client, label="Dana")
    assert response.status_code == 200

    tree = _parse(response.text)
    errors = [
        _text_of(e)
        for e in tree.elements
        if "notice-err" in e.attrs.get("class", "").split()
    ]
    assert errors == [f"No cloud allocation for Core berth in team '{TEAM}'"]
    assert "token-box" not in response.text


# --------------------------------------------------------------------------- #
# Structural invariant (the regression guard for #209)
# --------------------------------------------------------------------------- #


def test_team_detail_keeps_the_token_area_outside_the_invitations_block(
    playground_dir,
):
    root = pathlib.Path(playground_dir)
    alice_hex = _with_allocation(root)
    client = TestClient(create_app(root, alice_hex))

    tree = _parse(client.get(f"/teams/{TEAM}").text)
    target = _by_id(tree, "invitation-token-area")
    listing = _by_id(tree, f"invitations-{TEAM}")
    assert len(target) == 1
    assert len(listing) == 1
    assert listing[0] not in list(target[0].ancestors())


@pytest.mark.parametrize("fixture", [_with_allocation, _without_allocation])
def test_create_invitation_oob_targets_do_not_contain_the_primary_target(
    playground_dir, fixture
):
    root = pathlib.Path(playground_dir)
    participant_hex = fixture(root)
    client = TestClient(create_app(root, participant_hex))

    detail_response = client.get(f"/teams/{TEAM}")
    assert detail_response.status_code == 200
    detail_tree = _parse(detail_response.text)
    target = _by_id(detail_tree, "invitation-token-area")
    assert len(target) == 1

    create_response = _create(client, label="Dana")
    assert create_response.status_code == 200
    response_tree = _parse(create_response.text)
    oob_target_ids = {
        element.attrs.get("id")
        for element in response_tree.elements
        if "hx-swap-oob" in element.attrs
    }
    assert oob_target_ids == {
        f"admission-events-{TEAM}",
        f"invitations-{TEAM}",
    }

    target_ancestor_ids = {
        ancestor.attrs.get("id") for ancestor in target[0].ancestors()
    }
    assert oob_target_ids.isdisjoint(target_ancestor_ids)


@pytest.mark.parametrize("fixture", [_with_allocation, _without_allocation])
def test_create_invitation_response_declares_each_id_once(
    playground_dir, fixture
):
    root = pathlib.Path(playground_dir)
    participant_hex = fixture(root)
    client = TestClient(create_app(root, participant_hex))

    ids = _ids(_parse(_create(client, label="Dana").text))
    assert len(ids) == len(set(ids)), sorted(ids)

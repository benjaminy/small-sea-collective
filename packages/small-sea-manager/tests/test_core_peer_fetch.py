"""Micro tests for fetching and parking a teammate's Core chain (#185).

These cover what the Manager decides, so peer transport is stubbed with a
LocalFolderStore behind the same construction point production uses. The store
stub records the teammate id it was built for, which is how "only the named
teammate is contacted" stays a measurement rather than a reading of the code.

Two things are deliberately never asserted here, because this operation cannot
establish them: that a fetched head was authored by the teammate, and that it
may be integrated. What is asserted is that the bytes were fetched, that local
`main` did not move, and that the record survives the process that wrote it.
"""

import pathlib
import shutil
import sqlite3
import types

import pytest
import small_sea_manager.manager as manager_module
import small_sea_manager.provisioning as Provisioning
from cod_sync.format import decode_link
from cod_sync.protocol import CodSync, PARKED_REF_PREFIX
from cod_sync.repo import RefAdvanceContendedError, Repo, RepoError
from cod_sync.store import (
    LocalFolderStore,
    PeerSenderKeyUnavailableError as StorePeerSenderKeyUnavailableError,
    PublicationPendingError as StorePublicationPendingError,
    PeerStorageUnknownError as StorePeerStorageUnknownError,
    StoreProviderError,
)
from small_sea_client.client import SmallSeaError, SmallSeaHubUnavailable
from small_sea_manager.manager import (
    CoreFetchRemoteError,
    CorePublicationPendingError,
    CorePublicationMissingError,
    CoreRefPersistenceError,
    InvalidCoreChainError,
    PeerSenderKeyUnavailableError,
    PeerStorageUnknownError,
    TeamManager,
    TeammateNotFoundError,
    core_peer_latest_ref,
)

_TEAM = "ProjectX"
_BOB = "bb" * 16
_CAROL = "cc" * 16
_MAIN = "refs/heads/main"


# ---------------------------------------------------------------------------
# Local fixtures: one participant, one team, no Hub and no cloud
# ---------------------------------------------------------------------------


def _sync_dir(root, participant_hex, team_name=_TEAM):
    return root / "Participants" / participant_hex / team_name / "Sync"


def _add_teammate(root, participant_hex, teammate_id_hex, team_name=_TEAM):
    db = _sync_dir(root, participant_hex, team_name) / "core.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO teammate (id, display_name) VALUES (?, ?)",
            (bytes.fromhex(teammate_id_hex), teammate_id_hex[:4]),
        )


class _RecordingPeerStore:
    """A LocalFolderStore reached only through the peer construction point."""

    built = []

    def __init__(self, session_hex, teammate_id_hex, base_url=None, client=None):
        self.teammate_id_hex = teammate_id_hex
        _RecordingPeerStore.built.append(teammate_id_hex)
        self._inner = LocalFolderStore(str(_RecordingPeerStore.publications[teammate_id_hex]))

    def get_latest_link(self):
        return self._inner.get_latest_link()

    def get_link(self, link_uid):
        return self._inner.get_link(link_uid)

    def download_bundle(self, bundle_uid, local_path):
        return self._inner.download_bundle(bundle_uid, local_path)


class _FailingPeerStore:
    """Fails the first read the way one Hub response class would."""

    failure = None

    def __init__(self, session_hex, teammate_id_hex, base_url=None, client=None):
        pass

    def get_latest_link(self):
        raise type(self).failure


@pytest.fixture()
def env(playground_dir, monkeypatch):
    """A TeamManager whose peer store is a local publication directory."""
    root = pathlib.Path(playground_dir)
    alice_hex = Provisioning.create_new_participant(root, "Alice")
    Provisioning.create_team(root, alice_hex, _TEAM)
    _add_teammate(root, alice_hex, _BOB)
    _add_teammate(root, alice_hex, _CAROL)

    _RecordingPeerStore.built = []
    _RecordingPeerStore.publications = {}
    monkeypatch.setattr(manager_module, "PeerSmallSeaStore", _RecordingPeerStore)

    def make_manager():
        team_manager = TeamManager(root, alice_hex)
        team_manager._get_or_open_session = lambda team, mode="encrypted": types.SimpleNamespace(
            token="session"
        )
        return team_manager

    state = types.SimpleNamespace(
        root=root,
        alice_hex=alice_hex,
        make_manager=make_manager,
        manager=make_manager(),
    )
    return state


def _publish(env, teammate_id_hex, files, work_name=None, publication_name=None):
    """Publish commits into a store directory and point the teammate at it.

    work_name and publication_name let one teammate be given a second,
    unrelated chain, which is how genuine divergence is staged.
    """
    work = env.root / (work_name or f"peer-{teammate_id_hex[:4]}")
    publication = env.root / (publication_name or f"publication-{teammate_id_hex[:4]}")
    publication.mkdir(parents=True, exist_ok=True)
    _RecordingPeerStore.publications[teammate_id_hex] = publication

    if (work / ".git").exists():
        repo = Repo(work / ".git", work)
    else:
        work.mkdir(parents=True, exist_ok=True)
        repo = Repo.init(work / ".git").with_work_tree(work)
        repo.config("user.email", "peer@test")
        repo.config("user.name", "peer")
    for name, content in files.items():
        (work / name).write_text(content)
        repo.stage([name])
        repo.commit(f"write {name}")
    result = CodSync(repo, LocalFolderStore(str(publication))).publish()
    return result.observed_head


def _publication(env, teammate_id_hex):
    return env.root / f"publication-{teammate_id_hex[:4]}"


def _serve(teammate_id_hex, publication):
    """Point the teammate's peer store at an existing publication directory."""
    _RecordingPeerStore.publications[teammate_id_hex] = publication


def _alice_repo(env):
    sync = _sync_dir(env.root, env.alice_hex)
    return Repo(sync / ".git", sync)


# ---------------------------------------------------------------------------
# Fetch: the ordinary dispositions
# ---------------------------------------------------------------------------


def test_a_first_fetch_creates_the_teammate_ref_without_moving_main(env):
    head = _publish(env, _BOB, {"bob.txt": "one\n"})
    repo = _alice_repo(env)
    main_before = repo.resolve_ref(_MAIN)

    result = env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert result.disposition == "created"
    assert result.observed_head_sha == head
    assert result.current_head_sha == head
    assert result.latest_ref_name == core_peer_latest_ref(_BOB)
    assert result.observation_ref_name is None
    assert repo.resolve_ref(core_peer_latest_ref(_BOB)) == head
    assert repo.resolve_ref(_MAIN) == main_before
    assert repo.has_commit(head)


def test_only_the_named_teammate_is_contacted(env):
    _publish(env, _BOB, {"bob.txt": "one\n"})
    _publish(env, _CAROL, {"carol.txt": "one\n"})

    env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert _RecordingPeerStore.built == [_BOB]
    assert _alice_repo(env).resolve_ref(core_peer_latest_ref(_CAROL)) is None


def test_a_later_head_advances_the_teammate_ref(env):
    first = _publish(env, _BOB, {"bob.txt": "one\n"})
    env.manager.fetch_teammate_core(_TEAM, _BOB)
    second = _publish(env, _BOB, {"bob.txt": "two\n"})

    result = env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert result.disposition == "advanced"
    assert result.current_head_sha == second
    assert _alice_repo(env).resolve_ref(core_peer_latest_ref(_BOB)) == second
    assert first != second


def test_refetching_the_same_head_is_unchanged(env):
    head = _publish(env, _BOB, {"bob.txt": "one\n"})
    env.manager.fetch_teammate_core(_TEAM, _BOB)

    result = env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert result.disposition == "unchanged"
    assert result.current_head_sha == head


def test_an_older_observation_leaves_the_newer_ref_alone(env):
    first = _publish(env, _BOB, {"bob.txt": "one\n"})
    snapshot = env.root / "publication-snapshot"
    shutil.copytree(_publication(env, _BOB), snapshot)
    second = _publish(env, _BOB, {"bob.txt": "two\n"})
    env.manager.fetch_teammate_core(_TEAM, _BOB)

    # An out-of-order read reaches a store still holding the earlier head.
    _serve(_BOB, snapshot)
    result = env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert result.disposition == "stale"
    assert result.observed_head_sha == first
    assert result.current_head_sha == second
    assert _alice_repo(env).resolve_ref(core_peer_latest_ref(_BOB)) == second


# ---------------------------------------------------------------------------
# Fetch: divergence is an observation, not a failure
# ---------------------------------------------------------------------------


def _diverge(env):
    """Fetch one head, then serve an unrelated chain for the same teammate."""
    first = _publish(env, _BOB, {"bob.txt": "one\n"})
    env.manager.fetch_teammate_core(_TEAM, _BOB)
    competing = _publish(
        env,
        _BOB,
        {"other.txt": "elsewhere\n"},
        work_name="peer-bb-alt",
        publication_name="publication-alt",
    )
    return first, competing, env.root / "publication-alt"


def test_divergence_parks_the_head_and_keeps_the_convenience_ref(env):
    first, competing, _publication = _diverge(env)

    result = env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert result.disposition == "divergent"
    assert result.observed_head_sha == competing
    assert result.current_head_sha == first
    repo = _alice_repo(env)
    assert repo.resolve_ref(core_peer_latest_ref(_BOB)) == first
    assert repo.resolve_ref(result.observation_ref_name) == competing


def test_a_repeated_divergent_fetch_verifies_the_same_observation(env):
    _first, competing, _publication = _diverge(env)
    first_result = env.manager.fetch_teammate_core(_TEAM, _BOB)

    second_result = env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert second_result == first_result
    assert _alice_repo(env).resolve_ref(second_result.observation_ref_name) == competing


def test_a_park_that_cannot_be_written_reports_a_local_ref_failure(env):
    first, _competing, publication = _diverge(env)
    repo = _alice_repo(env)
    # This link's observation ref already names some other head.
    link = decode_link(LocalFolderStore(str(publication)).get_latest_link()[0])
    ref = manager_module.core_peer_observation_ref(_BOB, link.link_id)
    repo._run(["update-ref", ref, first])

    with pytest.raises(CoreRefPersistenceError):
        env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert repo.resolve_ref(ref) == first


@pytest.mark.parametrize(
    "ref_failure",
    [
        RefAdvanceContendedError(core_peer_latest_ref(_BOB), 5),
        RepoError("hard latest-ref write failure"),
    ],
)
def test_latest_ref_write_failures_report_a_local_ref_failure(
    env, monkeypatch, ref_failure
):
    _publish(env, _BOB, {"bob.txt": "one\n"})

    def fail_to_advance(_repo, _ref_name, _new_sha):
        raise ref_failure

    monkeypatch.setattr(Repo, "advance_ref", fail_to_advance)

    with pytest.raises(CoreRefPersistenceError) as excinfo:
        env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert excinfo.value.__cause__ is ref_failure
    assert _alice_repo(env).resolve_ref(core_peer_latest_ref(_BOB)) is None


# ---------------------------------------------------------------------------
# Fetch: typed failures
# ---------------------------------------------------------------------------


def test_an_absent_teammate_fails_before_any_peer_transport(env):
    with pytest.raises(TeammateNotFoundError):
        env.manager.fetch_teammate_core(_TEAM, "dd" * 16)

    assert _RecordingPeerStore.built == []


def test_a_teammate_that_has_not_published_is_its_own_failure(env):
    publication = _publication(env, _BOB)
    publication.mkdir(parents=True)
    _serve(_BOB, publication)

    with pytest.raises(CorePublicationMissingError):
        env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert _alice_repo(env).resolve_ref(core_peer_latest_ref(_BOB)) is None


@pytest.mark.parametrize(
    "store_failure,expected",
    [
        (StorePeerStorageUnknownError("no route"), PeerStorageUnknownError),
        (StorePeerSenderKeyUnavailableError("no key"), PeerSenderKeyUnavailableError),
        (StorePublicationPendingError("device_ownership_unavailable", "no owner"), CorePublicationPendingError),
        (StorePublicationPendingError("device_ownership_ambiguous", "two owners"), CorePublicationPendingError),
        (StorePublicationPendingError("ownership_projection_absent", "no projection"), CorePublicationPendingError),
        (StorePublicationPendingError("expected_publisher_unknown", "no publisher"), CorePublicationPendingError),
        (StoreProviderError("provider is down"), CoreFetchRemoteError),
    ],
)
def test_store_failures_keep_their_distinctions(env, monkeypatch, store_failure, expected):
    _FailingPeerStore.failure = store_failure
    monkeypatch.setattr(manager_module, "PeerSmallSeaStore", _FailingPeerStore)

    with pytest.raises(expected) as excinfo:
        env.manager.fetch_teammate_core(_TEAM, _BOB)

    if isinstance(store_failure, StorePublicationPendingError) and not isinstance(
        store_failure, StorePeerSenderKeyUnavailableError
    ):
        assert excinfo.value.reason == store_failure.reason
    assert _alice_repo(env).resolve_ref(core_peer_latest_ref(_BOB)) is None


@pytest.mark.parametrize(
    "session_failure",
    [SmallSeaHubUnavailable(), SmallSeaError("session rejected")],
)
def test_session_failures_are_remote_fetch_errors(env, monkeypatch, session_failure):
    def fail_to_open(_team_name):
        raise session_failure

    monkeypatch.setattr(env.manager, "_get_or_open_session", fail_to_open)

    with pytest.raises(CoreFetchRemoteError) as excinfo:
        env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert excinfo.value.__cause__ is session_failure
    assert _RecordingPeerStore.built == []


def test_a_malformed_chain_is_not_a_transport_failure(env):
    _publish(env, _BOB, {"bob.txt": "one\n"})
    head_file = _publication(env, _BOB) / "latest-link.yaml"
    head_file.write_text("not: [a, valid, link\n")

    with pytest.raises(InvalidCoreChainError):
        env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert _alice_repo(env).resolve_ref(core_peer_latest_ref(_BOB)) is None


def test_a_corrupt_bundle_is_an_invalid_chain(env):
    _publish(env, _BOB, {"bob.txt": "one\n"})
    bundle = next(_publication(env, _BOB).glob("B-*.bundle"))
    bundle.write_bytes(bundle.read_bytes()[:-20])

    with pytest.raises(InvalidCoreChainError):
        env.manager.fetch_teammate_core(_TEAM, _BOB)

    assert _alice_repo(env).resolve_ref(core_peer_latest_ref(_BOB)) is None


# ---------------------------------------------------------------------------
# Read model
# ---------------------------------------------------------------------------


def test_the_read_model_survives_a_fresh_manager(env):
    head = _publish(env, _BOB, {"bob.txt": "one\n"})
    env.manager.fetch_teammate_core(_TEAM, _BOB)

    heads = env.make_manager().list_core_source_heads(_TEAM)

    assert [(h.source_kind, h.teammate_id, h.head_sha) for h in heads] == [
        ("teammate", _BOB, head)
    ]
    assert heads[0].is_maximal
    assert heads[0].superseded_by_refs == ()
    assert heads[0].contained_in_main is False


def test_two_teammates_are_separate_sources_in_ref_order(env):
    bob_head = _publish(env, _BOB, {"bob.txt": "one\n"})
    carol_head = _publish(env, _CAROL, {"carol.txt": "one\n"})
    env.manager.fetch_teammate_core(_TEAM, _BOB)
    env.manager.fetch_teammate_core(_TEAM, _CAROL)

    heads = env.manager.list_core_source_heads(_TEAM)

    assert [h.ref_name for h in heads] == sorted(h.ref_name for h in heads)
    assert {h.teammate_id: h.head_sha for h in heads} == {
        _BOB: bob_head, _CAROL: carol_head
    }
    assert all(h.is_maximal for h in heads)


def test_a_strict_ancestor_in_one_source_is_superseded(env):
    first = _publish(env, _BOB, {"bob.txt": "one\n"})
    env.manager.fetch_teammate_core(_TEAM, _BOB)
    repo = _alice_repo(env)
    # An observation from an earlier fetch, kept as its own immutable ref.
    older_ref = manager_module.core_peer_observation_ref(_BOB, "L-old")
    repo.create_ref_immutable(older_ref, first)
    second = _publish(env, _BOB, {"bob.txt": "two\n"})
    env.manager.fetch_teammate_core(_TEAM, _BOB)

    by_ref = {h.ref_name: h for h in env.manager.list_core_source_heads(_TEAM)}

    assert by_ref[core_peer_latest_ref(_BOB)].head_sha == second
    assert by_ref[core_peer_latest_ref(_BOB)].is_maximal
    assert by_ref[older_ref].is_maximal is False
    assert by_ref[older_ref].superseded_by_refs == (core_peer_latest_ref(_BOB),)


def test_equal_shas_in_one_source_both_stay_maximal(env):
    head = _publish(env, _BOB, {"bob.txt": "one\n"})
    env.manager.fetch_teammate_core(_TEAM, _BOB)
    twin_ref = manager_module.core_peer_observation_ref(_BOB, "L-twin")
    _alice_repo(env).create_ref_immutable(twin_ref, head)

    heads = env.manager.list_core_source_heads(_TEAM)

    assert all(h.is_maximal for h in heads)
    assert all(h.superseded_by_refs == () for h in heads)


def test_divergent_heads_in_one_source_both_stay_maximal(env):
    _first, competing, _publication = _diverge(env)
    result = env.manager.fetch_teammate_core(_TEAM, _BOB)

    heads = {h.ref_name: h for h in env.manager.list_core_source_heads(_TEAM)}

    assert heads[result.observation_ref_name].head_sha == competing
    assert all(h.is_maximal for h in heads.values())


def test_containment_by_another_teammate_does_not_hide_a_source(env):
    head = _publish(env, _BOB, {"bob.txt": "one\n"})
    env.manager.fetch_teammate_core(_TEAM, _BOB)
    # Carol's ref names the very same commits, from her own logical source.
    _alice_repo(env).advance_ref(core_peer_latest_ref(_CAROL), head)

    heads = env.manager.list_core_source_heads(_TEAM)

    assert len(heads) == 2
    assert all(h.is_maximal for h in heads)
    assert all(h.superseded_by_refs == () for h in heads)


def test_a_self_publication_ref_is_reported_without_a_teammate(env):
    head = _publish(env, _BOB, {"bob.txt": "one\n"})
    env.manager.fetch_teammate_core(_TEAM, _BOB)
    # Exactly the ref shape Cod Sync's publication settlement leaves behind.
    parked_ref = f"{PARKED_REF_PREFIX}/L-competing"
    _alice_repo(env).create_ref_immutable(parked_ref, head)

    by_ref = {h.ref_name: h for h in env.make_manager().list_core_source_heads(_TEAM)}

    assert by_ref[parked_ref].source_kind == "self_publication"
    assert by_ref[parked_ref].teammate_id is None
    # Same SHA as Bob's head, but a different logical source, so neither hides
    # the other.
    assert by_ref[parked_ref].is_maximal
    assert by_ref[core_peer_latest_ref(_BOB)].is_maximal


def test_containment_in_local_main_is_reported(env):
    head = _publish(env, _BOB, {"bob.txt": "one\n"})
    env.manager.fetch_teammate_core(_TEAM, _BOB)
    repo = _alice_repo(env)
    before = [h.contained_in_main for h in env.manager.list_core_source_heads(_TEAM)]
    # Alice's own main is unrelated history, so this replaces it outright
    # rather than advancing it; the read model only asks about containment.
    repo._run(["update-ref", _MAIN, head])

    after = [h.contained_in_main for h in env.manager.list_core_source_heads(_TEAM)]

    assert before == [False]
    assert after == [True]

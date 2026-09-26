import pathlib
import shutil
import subprocess

import pytest
from cod_sync.protocol import PublicationIntegrationRequiredError, parked_ref_name
from cod_sync.store import LocalFolderStore
from ssc_files import sync
from ssc_files.files import (
    NicheResidency,
    _niche_git_dir,
    _registry_git_dir,
    _resolve_ref,
    FilesMaterializationContext,
    add_checkout,
    create_niche,
    fetch_niche,
    fetch_self_niche,
    init_files,
    materialize_team,
    merge_niche,
    publish,
    push_niche,
    push_registry,
)

PARTICIPANT = "bb" * 16
TEAM_ID = "33" * 16
TEAM = FilesMaterializationContext(PARTICIPANT, TEAM_ID, "SyncTeam")


def test_sync_niche_between_devices(playground_dir):
    playground = pathlib.Path(playground_dir)
    cloud_dir = playground / "cloud"
    cloud_dir.mkdir()

    # --- Device A: create and populate a niche ---
    root_a = str(playground / "device-a")
    init_files(root_a, PARTICIPANT)
    materialize_team(root_a, TEAM)
    create_niche(root_a, PARTICIPANT, TEAM, "photos")
    checkout_a = str(playground / "checkout-a" / "photos")
    add_checkout(root_a, PARTICIPANT, TEAM, "photos", checkout_a)

    (pathlib.Path(checkout_a) / "sunset.jpg").write_bytes(b"fake-sunset-data")
    (pathlib.Path(checkout_a) / "beach.jpg").write_bytes(b"fake-beach-data")
    publish(root_a, PARTICIPANT, TEAM, "photos", checkout_a, message="add photos")

    push_niche(root_a, PARTICIPANT, TEAM, "photos", LocalFolderStore(str(cloud_dir)))

    # --- Device B: join flow: fetch → attach checkout → merge ---
    root_b = str(playground / "device-b")
    init_files(root_b, PARTICIPANT)
    materialize_team(root_b, TEAM)
    checkout_b = str(playground / "checkout-b" / "photos")

    fetch_niche(root_b, PARTICIPANT, TEAM, "photos", PARTICIPANT, LocalFolderStore(str(cloud_dir)))
    add_checkout(root_b, PARTICIPANT, TEAM, "photos", checkout_b)
    merge_niche(root_b, PARTICIPANT, TEAM, "photos", PARTICIPANT)

    # --- Assert both checkouts have the same files ---
    sunset_b = pathlib.Path(checkout_b) / "sunset.jpg"
    beach_b = pathlib.Path(checkout_b) / "beach.jpg"

    assert sunset_b.exists(), "sunset.jpg missing on device B"
    assert beach_b.exists(), "beach.jpg missing on device B"
    assert sunset_b.read_bytes() == b"fake-sunset-data"
    assert beach_b.read_bytes() == b"fake-beach-data"


# ---------------------------------------------------------------------------
# sync-layer NoCheckoutError residency propagation
# ---------------------------------------------------------------------------


def test_merge_via_hub_no_checkout_cached_preserves_residency(playground_dir, monkeypatch):
    """sync.merge_via_hub raises sync.NoCheckoutError with CACHED residency
    when the niche git dir exists locally but no checkout is registered.

    Tests the preflight path in merge_via_hub: it calls files.get_checkout,
    detects None, calls files.niche_residency, and wraps the result in the
    sync-layer exception — so residency survives the files->sync boundary.
    """
    root = str(pathlib.Path(playground_dir) / "files")
    init_files(root, PARTICIPANT)
    materialize_team(root, TEAM)
    create_niche(root, PARTICIPANT, TEAM, "files")
    # No add_checkout: niche git dir exists but no checkout row → CACHED.

    with pytest.raises(sync.NoCheckoutError) as exc_info:
        sync.merge_via_hub(root, PARTICIPANT, TEAM.team_name, "files", "some-peer-id")

    err = exc_info.value
    assert err.residency is NicheResidency.CACHED
    assert "attach" in str(err).lower()


def test_merge_via_hub_no_checkout_remote_only_preserves_residency(playground_dir, monkeypatch):
    """sync.merge_via_hub raises sync.NoCheckoutError with REMOTE_ONLY residency
    when the niche has no local git dir at all.
    """
    root = str(pathlib.Path(playground_dir) / "files")
    init_files(root, PARTICIPANT)
    materialize_team(root, TEAM)
    # No create_niche: no git dir → REMOTE_ONLY.

    with pytest.raises(sync.NoCheckoutError) as exc_info:
        sync.merge_via_hub(root, PARTICIPANT, TEAM.team_name, "files", "some-peer-id")

    err = exc_info.value
    assert err.residency is NicheResidency.REMOTE_ONLY
    assert "fetch" in str(err).lower()


# ---------------------------------------------------------------------------
# Self-store integration over parked refs
# ---------------------------------------------------------------------------


def _git(git_dir, *args):
    return subprocess.run(
        ["git", "--git-dir", str(git_dir), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _two_device_conflict(playground_dir):
    """Set up two devices of one participant sharing one cloud folder.

    Both devices hold the same niche history and both have local work the
    other has not seen. Returns the pieces the self-store tests need.
    """
    playground = pathlib.Path(playground_dir)
    cloud_dir = playground / "cloud"
    cloud_dir.mkdir()

    root_a = str(playground / "device-a")
    init_files(root_a, PARTICIPANT)
    materialize_team(root_a, TEAM)
    create_niche(root_a, PARTICIPANT, TEAM, "notes")
    checkout_a = pathlib.Path(playground) / "checkout-a"
    add_checkout(root_a, PARTICIPANT, TEAM, "notes", str(checkout_a))
    (checkout_a / "shared.txt").write_text("v1\n")
    publish(root_a, PARTICIPANT, TEAM, "notes", str(checkout_a), message="v1")
    push_niche(root_a, PARTICIPANT, TEAM, "notes", LocalFolderStore(str(cloud_dir)))

    root_b = str(playground / "device-b")
    init_files(root_b, PARTICIPANT)
    materialize_team(root_b, TEAM)
    checkout_b = pathlib.Path(playground) / "checkout-b"
    fetch_niche(root_b, PARTICIPANT, TEAM, "notes", PARTICIPANT, LocalFolderStore(str(cloud_dir)))
    add_checkout(root_b, PARTICIPANT, TEAM, "notes", str(checkout_b))
    merge_niche(root_b, PARTICIPANT, TEAM, "notes", PARTICIPANT)

    return {
        "cloud_dir": cloud_dir,
        "root_a": root_a,
        "checkout_a": checkout_a,
        "root_b": root_b,
        "checkout_b": checkout_b,
    }


def _diverge(env, *, a_text, b_text):
    """Give each device one new commit and let device A win the cloud head."""
    (env["checkout_a"] / "a.txt").write_text(a_text)
    publish(env["root_a"], PARTICIPANT, TEAM, "notes", str(env["checkout_a"]), message="a")
    push_niche(env["root_a"], PARTICIPANT, TEAM, "notes", LocalFolderStore(str(env["cloud_dir"])))

    (env["checkout_b"] / "b.txt").write_text(b_text)
    publish(env["root_b"], PARTICIPANT, TEAM, "notes", str(env["checkout_b"]), message="b")
    with pytest.raises(PublicationIntegrationRequiredError) as exc_info:
        push_niche(
            env["root_b"], PARTICIPANT, TEAM, "notes", LocalFolderStore(str(env["cloud_dir"]))
        )
    return exc_info.value


def test_merge_self_integrates_a_parked_sibling_device_head(playground_dir):
    """A refused push parks the competing head; a later call finds it by scan.

    The integration operation is given nothing from the failed publication —
    no link UID, no head, no error object — because in production it runs in a
    separate process. It has to rediscover the outstanding conflict from the
    refs alone.
    """
    env = _two_device_conflict(playground_dir)
    failure = _diverge(env, a_text="from A\n", b_text="from B\n")

    # The refused publication left device B's files untouched.
    assert not (env["checkout_b"] / "a.txt").exists()
    assert (env["checkout_b"] / "b.txt").read_text() == "from B\n"

    # A separate process finds the parked head: it is a ref on disk, not a
    # field on an exception that died with the publishing process.
    git_dir = _niche_git_dir(env["root_b"], TEAM, "notes")
    parked = _git(
        git_dir, "rev-parse", "--verify", parked_ref_name(failure.observed_link_uid)
    )
    assert parked == failure.observed_head

    result = sync.merge_self(env["root_b"], PARTICIPANT, TEAM.team_name, "notes")

    assert result.merged_anything
    assert result.niche_shas == [failure.observed_head]
    assert (env["checkout_b"] / "a.txt").read_text() == "from A\n"
    assert (env["checkout_b"] / "b.txt").read_text() == "from B\n"

    # Nothing is outstanding now, and the ref is not merged a second time.
    again = sync.merge_self(env["root_b"], PARTICIPANT, TEAM.team_name, "notes")
    assert again.niche_shas == []
    assert not again.merged_anything


def test_self_conflict_status_tracks_what_merge_self_would_integrate(playground_dir):
    """A surface can offer the integration only while something is outstanding.

    The status is read from the parked refs, not from the publication result,
    so it is still true in the process that renders the page after the one
    that pushed.
    """
    env = _two_device_conflict(playground_dir)
    assert not sync.self_conflict_status(
        env["root_b"], PARTICIPANT, TEAM, "notes"
    ).outstanding

    failure = _diverge(env, a_text="from A\n", b_text="from B\n")

    status = sync.self_conflict_status(env["root_b"], PARTICIPANT, TEAM, "notes")
    assert status.outstanding
    assert status.niche_shas == [failure.observed_head]

    sync.merge_self(env["root_b"], PARTICIPANT, TEAM.team_name, "notes")
    assert not sync.self_conflict_status(
        env["root_b"], PARTICIPANT, TEAM, "notes"
    ).outstanding


def test_web_offers_and_runs_the_self_store_merge(playground_dir, monkeypatch):
    """The web surface's only self-store action is one that exists.

    A refused push names no CLI command in the browser; it points at the
    Merge Changes button the same page renders, and that button runs the
    local integration without contacting the Hub.
    """
    from fastapi.testclient import TestClient
    from ssc_files.web import create_app

    monkeypatch.setenv(
        "SMALL_SEA_FILES_CONFIG", str(pathlib.Path(playground_dir) / "files.toml")
    )
    env = _two_device_conflict(playground_dir)
    failure = _diverge(env, a_text="from A\n", b_text="from B\n")

    def _refused_push(*_args, **_kwargs):
        raise sync.PushConflictError(
            "niche",
            failure,
            f"`ssc-files merge --from-self {TEAM.team_name} notes`",
        )

    class _NoHubClient:
        def get(self, *_args, **_kwargs):
            raise AssertionError("the local self merge contacted the Hub")

    monkeypatch.setattr(sync, "push_via_hub", _refused_push)

    client = TestClient(
        create_app(env["root_b"], PARTICIPANT, _http_client=_NoHubClient())
    )
    refused = client.post(f"/teams/{TEAM.team_name}/niches/notes/push")
    assert refused.status_code == 200
    assert "Merge Changes" in refused.text
    assert "ssc-files merge" not in refused.text

    # A cached session makes peer rendering eligible to use the Hub. The
    # local merge response must defer that separate refresh instead.
    sync.store_session_token(TEAM.team_name, "cached-token")

    merged = client.post(f"/teams/{TEAM.team_name}/niches/notes/merge-self")
    assert merged.status_code == 200
    assert "Push again to publish the result." in merged.text
    assert (env["checkout_b"] / "a.txt").read_text() == "from A\n"

    # Nothing is outstanding now, so the action is no longer offered.
    assert "Merge Changes" not in merged.text


def test_merge_self_leaves_an_already_integrated_parked_ref_alone(playground_dir):
    """Two parked refs, one already absorbed: only the outstanding one merges.

    Parked refs are immutable and never swept, so by the second conflict the
    namespace holds more than one ref and the ancestry test is the only thing
    that says which conflict is still open.
    """
    env = _two_device_conflict(playground_dir)
    first = _diverge(env, a_text="from A\n", b_text="from B\n")
    sync.merge_self(env["root_b"], PARTICIPANT, TEAM.team_name, "notes")

    # Device B publishes the merge; device A gets ahead again.
    push_niche(env["root_b"], PARTICIPANT, TEAM, "notes", LocalFolderStore(str(env["cloud_dir"])))
    (env["checkout_a"] / "a2.txt").write_text("from A again\n")
    publish(env["root_a"], PARTICIPANT, TEAM, "notes", str(env["checkout_a"]), message="a2")
    with pytest.raises(PublicationIntegrationRequiredError):
        # Device A has not seen B's merge commit, so its own push is refused
        # and it parks B's head; pull it in so A can win the race again.
        push_niche(
            env["root_a"], PARTICIPANT, TEAM, "notes", LocalFolderStore(str(env["cloud_dir"]))
        )
    sync.merge_self(env["root_a"], PARTICIPANT, TEAM.team_name, "notes")
    push_niche(env["root_a"], PARTICIPANT, TEAM, "notes", LocalFolderStore(str(env["cloud_dir"])))

    (env["checkout_b"] / "b2.txt").write_text("from B again\n")
    publish(env["root_b"], PARTICIPANT, TEAM, "notes", str(env["checkout_b"]), message="b2")
    with pytest.raises(PublicationIntegrationRequiredError) as exc_info:
        push_niche(
            env["root_b"], PARTICIPANT, TEAM, "notes", LocalFolderStore(str(env["cloud_dir"]))
        )
    second = exc_info.value

    git_dir = _niche_git_dir(env["root_b"], TEAM, "notes")
    assert _resolve_ref(git_dir, parked_ref_name(first.observed_link_uid)) is not None
    assert _resolve_ref(git_dir, parked_ref_name(second.observed_link_uid)) is not None

    result = sync.merge_self(env["root_b"], PARTICIPANT, TEAM.team_name, "notes")
    assert result.niche_shas == [second.observed_head]
    assert (env["checkout_b"] / "a2.txt").read_text() == "from A again\n"


def test_merge_self_merges_a_descendant_before_its_parked_ancestor(playground_dir):
    """An older parked head cannot create a conflict its descendant avoids."""
    env = _two_device_conflict(playground_dir)

    (env["checkout_a"] / "shared.txt").write_text("intermediate\n")
    publish(env["root_a"], PARTICIPANT, TEAM, "notes", str(env["checkout_a"]), message="a1")
    push_niche(
        env["root_a"], PARTICIPANT, TEAM, "notes", LocalFolderStore(str(env["cloud_dir"]))
    )

    (env["checkout_b"] / "shared.txt").write_text("resolved\n")
    publish(env["root_b"], PARTICIPANT, TEAM, "notes", str(env["checkout_b"]), message="b")
    with pytest.raises(PublicationIntegrationRequiredError) as first_info:
        push_niche(
            env["root_b"], PARTICIPANT, TEAM, "notes",
            LocalFolderStore(str(env["cloud_dir"])),
        )

    (env["checkout_a"] / "shared.txt").write_text("resolved\n")
    publish(env["root_a"], PARTICIPANT, TEAM, "notes", str(env["checkout_a"]), message="a2")
    push_niche(
        env["root_a"], PARTICIPANT, TEAM, "notes", LocalFolderStore(str(env["cloud_dir"]))
    )
    with pytest.raises(PublicationIntegrationRequiredError) as second_info:
        push_niche(
            env["root_b"], PARTICIPANT, TEAM, "notes",
            LocalFolderStore(str(env["cloud_dir"])),
        )

    first = first_info.value
    second = second_info.value
    git_dir = _niche_git_dir(env["root_b"], TEAM, "notes")
    _git(
        git_dir, "merge-base", "--is-ancestor",
        first.observed_head, second.observed_head,
    )

    # Link UIDs are random, so give the ancestor the earlier ref name. A
    # ref-name-ordered implementation would try it first and conflict even
    # though the descendant has the same final content as local HEAD.
    _git(git_dir, "update-ref", "refs/cod-sync/parked/a", first.observed_head)
    _git(git_dir, "update-ref", "refs/cod-sync/parked/z", second.observed_head)
    _git(git_dir, "update-ref", "-d", parked_ref_name(first.observed_link_uid))
    _git(git_dir, "update-ref", "-d", parked_ref_name(second.observed_link_uid))

    status = sync.self_conflict_status(env["root_b"], PARTICIPANT, TEAM, "notes")
    assert status.niche_shas == [second.observed_head]

    result = sync.merge_self(env["root_b"], PARTICIPANT, TEAM.team_name, "notes")

    assert result.niche_shas == [second.observed_head]
    assert (env["checkout_b"] / "shared.txt").read_text() == "resolved\n"


def test_merge_self_preflight_blocks_registry_when_niche_git_dir_is_missing(playground_dir):
    """A failed Git status cannot let the registry merge before the niche blocks."""
    playground = pathlib.Path(playground_dir)
    root = str(playground / "device")
    init_files(root, PARTICIPANT)
    materialize_team(root, TEAM)
    create_niche(root, PARTICIPANT, TEAM, "notes")
    checkout = playground / "checkout"
    add_checkout(root, PARTICIPANT, TEAM, "notes", str(checkout))
    (checkout / "shared.txt").write_text("v1\n")
    publish(root, PARTICIPANT, TEAM, "notes", str(checkout), message="v1")

    registry_git = _registry_git_dir(root, TEAM)
    registry_head = _git(registry_git, "rev-parse", "HEAD")
    registry_tree = _git(registry_git, "rev-parse", "HEAD^{tree}")
    parked_registry_head = _git(
        registry_git, "commit-tree", registry_tree,
        "-p", registry_head, "-m", "parked registry head",
    )
    _git(
        registry_git, "update-ref",
        "refs/cod-sync/parked/registry", parked_registry_head,
    )

    shutil.rmtree(_niche_git_dir(root, TEAM, "notes"))
    with pytest.raises(sync.DirtyCheckoutError):
        sync.merge_self(root, PARTICIPANT, TEAM.team_name, "notes")

    assert _git(registry_git, "rev-parse", "HEAD") == registry_head


def test_merge_self_reports_every_unusable_checkout_state(playground_dir):
    """CACHED, stale, and dirty checkouts each get a named result."""
    playground = pathlib.Path(playground_dir)
    root = str(playground / "device")
    init_files(root, PARTICIPANT)
    materialize_team(root, TEAM)

    # No git dir at all.
    with pytest.raises(sync.NoCheckoutError) as exc_info:
        sync.merge_self(root, PARTICIPANT, TEAM.team_name, "notes")
    assert exc_info.value.residency is NicheResidency.REMOTE_ONLY

    # Local history, no checkout attached.
    create_niche(root, PARTICIPANT, TEAM, "notes")
    with pytest.raises(sync.NoCheckoutError) as exc_info:
        sync.merge_self(root, PARTICIPANT, TEAM.team_name, "notes")
    assert exc_info.value.residency is NicheResidency.CACHED

    checkout = playground / "checkout"
    add_checkout(root, PARTICIPANT, TEAM, "notes", str(checkout))
    (checkout / "shared.txt").write_text("v1\n")
    publish(root, PARTICIPANT, TEAM, "notes", str(checkout), message="v1")

    # Dirty checkout.
    (checkout / "shared.txt").write_text("uncommitted\n")
    with pytest.raises(sync.DirtyCheckoutError) as dirty_info:
        sync.merge_self(root, PARTICIPANT, TEAM.team_name, "notes")
    # Only that the dirty file is reported: the exact path text is unreliable
    # because files.status strips the whole porcelain output before slicing, so
    # the first entry loses a character. Pre-existing, not this branch's.
    assert dirty_info.value.paths
    (checkout / "shared.txt").write_text("v1\n")

    # Registered checkout directory removed from disk.
    shutil.rmtree(checkout)
    with pytest.raises(sync.StaleCheckoutError) as stale_info:
        sync.merge_self(root, PARTICIPANT, TEAM.team_name, "notes")
    assert stale_info.value.checkout_path == str(checkout)


def test_cli_merge_requires_exactly_one_source(monkeypatch, tmp_path):
    """--from-teammate and --from-self are mutually exclusive, and one is required."""
    from click.testing import CliRunner

    from ssc_files.cli import cli

    config_file = tmp_path / "files.toml"
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(config_file))
    sync.save_config(
        {"files_root": str(tmp_path / "files"), "participant_hex": PARTICIPANT}
    )

    def _unreachable(*_args, **_kwargs):
        raise AssertionError("no merge should run without a valid source")

    monkeypatch.setattr(sync, "merge_self", _unreachable)
    monkeypatch.setattr(sync, "merge_via_hub", _unreachable)

    runner = CliRunner()
    neither = runner.invoke(cli, ["merge", "ProjectX", "docs"])
    both = runner.invoke(
        cli, ["merge", "ProjectX", "docs", "--from-self", "--from-teammate", "cc" * 16]
    )

    assert neither.exit_code != 0
    assert both.exit_code != 0
    assert "exactly one" in neither.output
    assert "exactly one" in both.output


def test_cli_merge_from_self_reports_nothing_parked(monkeypatch, tmp_path):
    config_file = tmp_path / "files.toml"
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(config_file))
    sync.save_config(
        {"files_root": str(tmp_path / "files"), "participant_hex": PARTICIPANT}
    )

    from click.testing import CliRunner

    from ssc_files.cli import cli

    monkeypatch.setattr(
        sync, "merge_self",
        lambda *_a, **_k: sync.SelfMergeResult(registry_shas=[], niche_shas=[]),
    )
    result = CliRunner().invoke(cli, ["merge", "ProjectX", "docs", "--from-self"])

    assert result.exit_code == 0, result.output
    assert "No parked changes" in result.output


def _cli_config(monkeypatch, tmp_path):
    monkeypatch.setenv("SMALL_SEA_FILES_CONFIG", str(tmp_path / "files.toml"))
    sync.save_config(
        {"files_root": str(tmp_path / "files"), "participant_hex": PARTICIPANT}
    )


def test_cli_fetch_requires_exactly_one_source(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from ssc_files.cli import cli

    _cli_config(monkeypatch, tmp_path)

    def _unreachable(*_args, **_kwargs):
        raise AssertionError("no fetch should run without a valid source")

    monkeypatch.setattr(sync, "fetch_self_via_hub", _unreachable)
    monkeypatch.setattr(sync, "fetch_via_hub", _unreachable)

    runner = CliRunner()
    neither = runner.invoke(cli, ["fetch", "ProjectX", "docs"])
    both = runner.invoke(
        cli, ["fetch", "ProjectX", "docs", "--from-self", "--from-teammate", "cc" * 16]
    )

    assert neither.exit_code != 0
    assert both.exit_code != 0
    assert "exactly one" in neither.output
    assert "exactly one" in both.output


def test_cli_fetch_from_self_reports_both_heads(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from ssc_files.cli import cli

    _cli_config(monkeypatch, tmp_path)
    calls = []

    def _fetch(*args, **kwargs):
        calls.append(args)
        return sync.SelfFetchResult(registry_sha="a" * 40, niche_sha="b" * 40)

    monkeypatch.setattr(sync, "fetch_self_via_hub", _fetch)
    result = CliRunner().invoke(cli, ["fetch", "ProjectX", "docs", "--from-self"])

    assert result.exit_code == 0, result.output
    assert calls == [(str(tmp_path / "files"), PARTICIPANT, "ProjectX", "docs")]
    assert "aaaaaaaa" in result.output
    assert "bbbbbbbb" in result.output
    assert "merge --from-self" in result.output


def test_cli_fetch_from_self_partial_failure_says_registry_kept(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from ssc_files.cli import cli

    _cli_config(monkeypatch, tmp_path)

    def _partial(*_args, **_kwargs):
        raise sync.SelfFetchPartialError("a" * 40, RuntimeError("the store publishes no head"))

    monkeypatch.setattr(sync, "fetch_self_via_hub", _partial)
    result = CliRunner().invoke(cli, ["fetch", "ProjectX", "docs", "--from-self"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "aaaaaaaa" in result.output
    assert "kept" in result.output
    assert "niche 'docs' failed" in result.output
    assert "the store publishes no head" in result.output


class _NoWriteStore:
    """LocalFolderStore that fails any write, so a fetch provably writes nothing."""

    def __init__(self, path):
        self._inner = LocalFolderStore(path)

    def __getattr__(self, name):
        if name.startswith("put_"):
            raise AssertionError(f"fetch wrote to the store via {name}")
        return getattr(self._inner, name)


def test_fetch_self_parks_a_sibling_head_without_writing_or_moving_head(playground_dir):
    """Device B fetches device A's newer niche head from their shared own store.

    Local-only witness: LocalFolderStore stands in for the Hub-backed store.
    Files publications are unsigned, so this shows transport and integration,
    not which device authored the head.
    """
    env = _two_device_conflict(playground_dir)
    (env["checkout_a"] / "a.txt").write_text("from A\n")
    publish(env["root_a"], PARTICIPANT, TEAM, "notes", str(env["checkout_a"]), message="a")
    push_niche(env["root_a"], PARTICIPANT, TEAM, "notes", LocalFolderStore(str(env["cloud_dir"])))
    a_head = _git(_niche_git_dir(env["root_a"], TEAM, "notes"), "rev-parse", "HEAD")

    git_dir_b = _niche_git_dir(env["root_b"], TEAM, "notes")
    b_head_before = _git(git_dir_b, "rev-parse", "HEAD")

    fetched = fetch_self_niche(
        env["root_b"], PARTICIPANT, TEAM, "notes", _NoWriteStore(str(env["cloud_dir"]))
    )

    assert fetched == a_head
    assert _git(git_dir_b, "rev-parse", "HEAD") == b_head_before
    assert not (env["checkout_b"] / "a.txt").exists()

    # A fresh git process sees the parked ref: it is on disk, not in memory.
    parked = _git(git_dir_b, "for-each-ref", "--format=%(objectname)", "refs/cod-sync/parked")
    assert parked == a_head

    # Refetching the same head verifies the immutable ref rather than failing.
    assert fetch_self_niche(
        env["root_b"], PARTICIPANT, TEAM, "notes", _NoWriteStore(str(env["cloud_dir"]))
    ) == a_head

    # A dirty checkout still blocks integration, and the parked head survives.
    (env["checkout_b"] / "shared.txt").write_text("dirty\n")
    with pytest.raises(sync.DirtyCheckoutError):
        sync.merge_self(env["root_b"], PARTICIPANT, TEAM.team_name, "notes")
    assert _git(git_dir_b, "rev-parse", "HEAD") == b_head_before
    (env["checkout_b"] / "shared.txt").write_text("v1\n")

    result = sync.merge_self(env["root_b"], PARTICIPANT, TEAM.team_name, "notes")
    assert result.niche_shas == [a_head]
    assert (env["checkout_b"] / "a.txt").read_text() == "from A\n"


def test_fetch_self_via_hub_keeps_the_registry_when_the_niche_fails(playground_dir, monkeypatch):
    """The registry and niche fetches are separate; a niche failure is reported
    with the registry head already parked, not rolled back or hidden."""
    env = _two_device_conflict(playground_dir)
    registry_cloud = pathlib.Path(playground_dir) / "registry-cloud"
    registry_cloud.mkdir()
    push_registry(env["root_a"], PARTICIPANT, TEAM, LocalFolderStore(str(registry_cloud)))
    empty_niche_cloud = pathlib.Path(playground_dir) / "empty-niche-cloud"
    empty_niche_cloud.mkdir()

    monkeypatch.setattr(sync, "resolve_team_context", lambda *_a: TEAM)
    monkeypatch.setattr(sync, "get_team_session", lambda *_a, **_k: None)
    monkeypatch.setattr(
        sync, "make_registry_remote", lambda _s: _NoWriteStore(str(registry_cloud))
    )
    monkeypatch.setattr(
        sync, "make_niche_remote", lambda _n, _s: _NoWriteStore(str(empty_niche_cloud))
    )

    with pytest.raises(sync.SelfFetchPartialError) as exc_info:
        sync.fetch_self_via_hub(env["root_b"], PARTICIPANT, TEAM.team_name, "notes")

    registry_git_b = _registry_git_dir(env["root_b"], TEAM)
    parked = _git(
        registry_git_b, "for-each-ref", "--format=%(objectname)", "refs/cod-sync/parked"
    )
    assert parked == exc_info.value.registry_sha


def test_fetch_self_via_hub_can_fetch_registry_for_niche_discovery(playground_dir, monkeypatch):
    """A device can park and list its registry before it knows a niche name."""
    env = _two_device_conflict(playground_dir)
    cloud = pathlib.Path(playground_dir) / "registry-cloud"
    cloud.mkdir()
    create_niche(env["root_a"], PARTICIPANT, TEAM, "plans")
    push_registry(env["root_a"], PARTICIPANT, TEAM, LocalFolderStore(str(cloud)))

    monkeypatch.setattr(sync, "resolve_team_context", lambda *_a: TEAM)
    monkeypatch.setattr(sync, "get_team_session", lambda *_a, **_k: None)
    monkeypatch.setattr(sync, "make_registry_remote", lambda _s: LocalFolderStore(str(cloud)))
    monkeypatch.setattr(
        sync, "make_niche_remote",
        lambda *_a: pytest.fail("registry-only fetch must not request a niche remote"),
    )

    result = sync.fetch_self_via_hub(env["root_b"], PARTICIPANT, TEAM.team_name)
    assert result.registry_sha
    assert result.niche_sha is None
    from ssc_files.files import merge_self_registry

    merge_self_registry(env["root_b"], PARTICIPANT, TEAM)
    from ssc_files.files import list_niches

    assert "plans" in [
        item["name"] for item in list_niches(env["root_b"], PARTICIPANT, TEAM)
    ]


class _GarbageLatestStore(_NoWriteStore):
    """A store whose registry head exists but does not decode."""

    def get_latest_link(self):
        return b"not a link", "etag"


def test_fetch_self_via_hub_does_not_treat_a_malformed_registry_as_absent(
    playground_dir, monkeypatch
):
    env = _two_device_conflict(playground_dir)
    niche_cloud = pathlib.Path(playground_dir) / "niche-cloud"
    niche_cloud.mkdir()
    push_niche(env["root_a"], PARTICIPANT, TEAM, "notes", LocalFolderStore(str(niche_cloud)))
    registry_cloud = pathlib.Path(playground_dir) / "registry-cloud"
    registry_cloud.mkdir()

    monkeypatch.setattr(sync, "resolve_team_context", lambda *_a: TEAM)
    monkeypatch.setattr(sync, "get_team_session", lambda *_a, **_k: None)
    monkeypatch.setattr(
        sync, "make_registry_remote", lambda _s: _GarbageLatestStore(str(registry_cloud))
    )
    monkeypatch.setattr(sync, "make_niche_remote", lambda _n, _s: _NoWriteStore(str(niche_cloud)))

    with pytest.raises(Exception) as exc_info:
        sync.fetch_self_via_hub(env["root_b"], PARTICIPANT, TEAM.team_name, "notes")
    assert not isinstance(exc_info.value, sync.NoPublishedHeadError)
    # The niche was not fetched behind a registry failure.
    niche_git_b = _niche_git_dir(env["root_b"], TEAM, "notes")
    assert _git(niche_git_b, "for-each-ref", "refs/cod-sync/parked") == ""


def test_cli_fetch_from_self_reports_missing_registry(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from ssc_files.cli import cli

    _cli_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sync,
        "fetch_self_via_hub",
        lambda *_a, **_k: sync.SelfFetchResult(registry_sha=None, niche_sha="b" * 40),
    )
    result = CliRunner().invoke(cli, ["fetch", "ProjectX", "docs", "--from-self"])

    assert result.exit_code == 0, result.output
    assert "bbbbbbbb" in result.output
    assert "no published registry head" in result.output


def test_cli_fetch_from_self_reports_cod_sync_errors_without_traceback(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from cod_sync.protocol import ChainError

    from ssc_files.cli import cli

    _cli_config(monkeypatch, tmp_path)

    def _chain_error(*_a, **_k):
        raise ChainError("injected malformed registry")

    monkeypatch.setattr(sync, "fetch_self_via_hub", _chain_error)
    result = CliRunner().invoke(cli, ["fetch", "ProjectX", "docs", "--from-self"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "injected malformed registry" in result.output

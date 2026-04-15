import pathlib

import pytest

from shared_file_vault.vault import (
    DirtyCheckoutError,
    DuplicateCheckoutError,
    NicheResidency,
    NoCheckoutError,
    add_checkout,
    create_niche,
    fetch_niche,
    get_checkout,
    init_vault,
    list_checkouts,
    list_niches,
    log,
    merge_niche,
    niche_residency,
    publish,
    remove_checkout,
    status,
)

PARTICIPANT = "aa" * 16
TEAM = "TestTeam"


def _init(playground_dir):
    init_vault(playground_dir, PARTICIPANT)


def test_create_niche(playground_dir):
    _init(playground_dir)
    niche_id = create_niche(playground_dir, PARTICIPANT, TEAM, "photos")

    assert len(niche_id) == 32  # 16 bytes -> 32 hex chars

    # Git dir exists in the new layout
    git_dir = (
        pathlib.Path(playground_dir)
        / PARTICIPANT
        / TEAM
        / "niches"
        / "photos"
        / "git"
    )
    assert git_dir.is_dir()
    assert (git_dir / "HEAD").exists()

    # Niche appears in the registry
    niches = list_niches(playground_dir, PARTICIPANT, TEAM)
    assert any(n["name"] == "photos" for n in niches)


def test_add_checkout(playground_dir):
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "docs")

    dest = pathlib.Path(playground_dir) / "checkout" / "docs"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "docs", str(dest))

    # .git pointer file exists in the checkout
    assert (dest / ".git").exists()

    # Checkout is tracked in checkouts.db
    checkouts = list_checkouts(playground_dir, PARTICIPANT, TEAM, "docs")
    assert str(dest) in checkouts


def test_add_checkout_twice_raises(playground_dir):
    """A second add_checkout for the same niche raises DuplicateCheckoutError."""
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "notes")

    dest_a = pathlib.Path(playground_dir) / "checkout-a"
    dest_b = pathlib.Path(playground_dir) / "checkout-b"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "notes", str(dest_a))

    with pytest.raises(DuplicateCheckoutError) as exc_info:
        add_checkout(playground_dir, PARTICIPANT, TEAM, "notes", str(dest_b))

    assert str(dest_a) in exc_info.value.existing_path

    # Only the first checkout is registered
    checkouts = list_checkouts(playground_dir, PARTICIPANT, TEAM, "notes")
    assert len(checkouts) == 1
    assert str(dest_a) in checkouts


def test_get_checkout(playground_dir):
    """get_checkout returns the path or None."""
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "notes")

    assert get_checkout(playground_dir, PARTICIPANT, TEAM, "notes") is None

    dest = pathlib.Path(playground_dir) / "checkout"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "notes", str(dest))
    assert get_checkout(playground_dir, PARTICIPANT, TEAM, "notes") == str(dest)

    remove_checkout(playground_dir, PARTICIPANT, TEAM, "notes", str(dest))
    assert get_checkout(playground_dir, PARTICIPANT, TEAM, "notes") is None


def test_publish_and_log(playground_dir):
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "notes")
    dest = pathlib.Path(playground_dir) / "checkout" / "notes"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "notes", str(dest))

    (dest / "hello.txt").write_text("hello world")
    commit_hash = publish(
        playground_dir, PARTICIPANT, TEAM, "notes", str(dest), message="first note"
    )

    assert len(commit_hash) >= 7

    entries = log(playground_dir, PARTICIPANT, TEAM, "notes")
    assert len(entries) == 1
    assert "first note" in entries[0]["message"]


def test_status(playground_dir):
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "pics")
    dest = pathlib.Path(playground_dir) / "checkout" / "pics"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "pics", str(dest))

    # Create a file — should show as untracked
    (dest / "cat.jpg").write_bytes(b"not really a jpeg")
    entries = status(playground_dir, PARTICIPANT, TEAM, "pics", str(dest))
    assert any(e["path"] == "cat.jpg" for e in entries)

    # Publish it — status should be clean
    publish(playground_dir, PARTICIPANT, TEAM, "pics", str(dest), message="add cat")
    entries = status(playground_dir, PARTICIPANT, TEAM, "pics", str(dest))
    assert len(entries) == 0


def test_selective_publish(playground_dir):
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "mixed")
    dest = pathlib.Path(playground_dir) / "checkout" / "mixed"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "mixed", str(dest))

    (dest / "a.txt").write_text("aaa")
    (dest / "b.txt").write_text("bbb")

    publish(
        playground_dir, PARTICIPANT, TEAM, "mixed", str(dest),
        files=["a.txt"], message="only a",
    )

    entries = status(playground_dir, PARTICIPANT, TEAM, "mixed", str(dest))
    paths = [e["path"] for e in entries]
    assert "b.txt" in paths
    assert "a.txt" not in paths


def test_schema_version_recreates_db(playground_dir):
    """checkouts.db is recreated when the schema version does not match."""
    import sqlite3
    from shared_file_vault.vault import _CHECKOUTS_DB_VERSION, _checkouts_db_path

    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "photos")
    dest = pathlib.Path(playground_dir) / "checkout"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "photos", str(dest))

    # Corrupt the schema version to simulate a stale DB
    db_path = _checkouts_db_path(playground_dir, PARTICIPANT)
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE schema_version SET version = ?", (_CHECKOUTS_DB_VERSION - 1,))
    conn.commit()
    conn.close()

    # Next connection should recreate the DB; stale checkout row is gone
    assert get_checkout(playground_dir, PARTICIPANT, TEAM, "photos") is None


# ---------------------------------------------------------------------------
# merge_niche guard tests
# These verify that the clean-checkout guard fires on the user's checkout
# before any merge step that writes into it.
# ---------------------------------------------------------------------------

PARTICIPANT_B = "bb" * 16


def _two_vault_setup(playground_dir):
    """Set up alice (PARTICIPANT) and bob (PARTICIPANT_B) vaults sharing a niche."""
    from cod_sync.protocol import LocalFolderRemote
    from shared_file_vault.vault import (
        fetch_niche,
        push_niche,
    )

    playground = pathlib.Path(playground_dir)

    # Alice creates niche, writes a file, and pushes
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "shared")
    alice_co = playground / "checkout-alice"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "shared", str(alice_co))
    (alice_co / "hello.txt").write_text("hello\n")
    publish(playground_dir, PARTICIPANT, TEAM, "shared", str(alice_co), message="init")

    cloud = playground / "cloud"
    cloud.mkdir()
    push_niche(playground_dir, PARTICIPANT, TEAM, "shared", LocalFolderRemote(str(cloud)))

    # Bob gets an empty vault and fetches from alice
    bob_root = playground / "vault-bob"
    init_vault(str(bob_root), PARTICIPANT_B)
    fetch_niche(str(bob_root), PARTICIPANT_B, TEAM, "shared", PARTICIPANT, LocalFolderRemote(str(cloud)))

    bob_co = playground / "checkout-bob"
    add_checkout(str(bob_root), PARTICIPANT_B, TEAM, "shared", str(bob_co))

    return playground, bob_root, bob_co


def test_merge_without_checkout_raises(playground_dir):
    """merge_niche raises NoCheckoutError when no checkout is attached."""
    from cod_sync.protocol import LocalFolderRemote
    from shared_file_vault.vault import fetch_niche, push_niche

    playground = pathlib.Path(playground_dir)
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "stuff")
    alice_co = playground / "checkout-alice"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "stuff", str(alice_co))
    (alice_co / "a.txt").write_text("a\n")
    publish(playground_dir, PARTICIPANT, TEAM, "stuff", str(alice_co), message="init")

    cloud = playground / "cloud"
    cloud.mkdir()
    push_niche(playground_dir, PARTICIPANT, TEAM, "stuff", LocalFolderRemote(str(cloud)))

    bob_root = playground / "vault-bob"
    init_vault(str(bob_root), PARTICIPANT_B)
    fetch_niche(str(bob_root), PARTICIPANT_B, TEAM, "stuff", PARTICIPANT, LocalFolderRemote(str(cloud)))

    # Bob has a fetched ref but no checkout — merge must fail clearly
    with pytest.raises(NoCheckoutError):
        merge_niche(str(bob_root), PARTICIPANT_B, TEAM, "stuff", PARTICIPANT)


def test_merge_dirty_tracked_file_raises(playground_dir):
    """merge_niche raises DirtyCheckoutError when the user's checkout has a modified tracked file."""
    playground, bob_root, bob_co = _two_vault_setup(playground_dir)

    # Bob has a fetched ref parked (from _two_vault_setup).
    # Dirty a tracked file in bob's checkout — this is what must block the merge.
    (bob_co / "hello.txt").write_text("modified\n")

    with pytest.raises(DirtyCheckoutError) as exc_info:
        merge_niche(str(bob_root), PARTICIPANT_B, TEAM, "shared", PARTICIPANT)

    assert "hello.txt" in exc_info.value.paths


def test_merge_dirty_untracked_file_raises(playground_dir):
    """merge_niche raises DirtyCheckoutError for untracked files too.

    Untracked files are treated as dirty to avoid path-collision cases and
    to keep the UX rule simple: the folder must be completely clean.
    """
    playground, bob_root, bob_co = _two_vault_setup(playground_dir)

    # Drop an untracked file into bob's checkout
    (bob_co / "untracked.txt").write_text("surprise\n")

    with pytest.raises(DirtyCheckoutError) as exc_info:
        merge_niche(str(bob_root), PARTICIPANT_B, TEAM, "shared", PARTICIPANT)

    assert "untracked.txt" in exc_info.value.paths


def test_merge_clean_checkout_succeeds(playground_dir):
    """merge_niche succeeds and refreshes the checkout when it is clean."""
    playground, bob_root, bob_co = _two_vault_setup(playground_dir)

    # Bob's checkout is clean; merge should succeed and deliver alice's file
    result_sha = merge_niche(str(bob_root), PARTICIPANT_B, TEAM, "shared", PARTICIPANT)

    assert result_sha is not None
    assert (bob_co / "hello.txt").exists()
    assert (bob_co / "hello.txt").read_text() == "hello\n"


# ---------------------------------------------------------------------------
# Niche residency
# ---------------------------------------------------------------------------


def test_residency_remote_only(playground_dir):
    """A niche that exists in the registry but has no local git dir is REMOTE_ONLY."""
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "photos")

    # Simulate a second participant who has the registry but not the niche git dir.
    other = "bb" * 16
    init_vault(playground_dir, other)
    # Manually write a registry entry so list_niches knows about "photos" without
    # a local niche git dir existing for `other`.
    import pathlib as _pl
    from shared_file_vault.vault import _registry_checkout_dir, _ensure_registry
    import json
    _ensure_registry(playground_dir, other, TEAM)
    reg_co = _registry_checkout_dir(playground_dir, other, TEAM)
    (reg_co / "photos.json").write_text(json.dumps({"id": "x" * 32, "name": "photos"}))

    assert niche_residency(playground_dir, other, TEAM, "photos") is NicheResidency.REMOTE_ONLY


def test_residency_cached(playground_dir):
    """A niche whose git dir exists but has no checkout registered is CACHED."""
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "docs")
    # create_niche creates the git dir but no checkout row.
    assert niche_residency(playground_dir, PARTICIPANT, TEAM, "docs") is NicheResidency.CACHED


def test_residency_checked_out(playground_dir):
    """A niche with a registered checkout is CHECKED_OUT."""
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "notes")
    dest = pathlib.Path(playground_dir) / "co-notes"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "notes", str(dest))
    assert niche_residency(playground_dir, PARTICIPANT, TEAM, "notes") is NicheResidency.CHECKED_OUT


def test_residency_cached_after_remove_checkout(playground_dir):
    """remove_checkout transitions a niche from CHECKED_OUT back to CACHED."""
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "archive")
    dest = pathlib.Path(playground_dir) / "co-archive"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "archive", str(dest))
    assert niche_residency(playground_dir, PARTICIPANT, TEAM, "archive") is NicheResidency.CHECKED_OUT

    remove_checkout(playground_dir, PARTICIPANT, TEAM, "archive", str(dest))
    assert niche_residency(playground_dir, PARTICIPANT, TEAM, "archive") is NicheResidency.CACHED


def test_list_niches_includes_residency(playground_dir):
    """list_niches includes a 'residency' key in each niche dict."""
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "alpha")
    create_niche(playground_dir, PARTICIPANT, TEAM, "beta")

    dest = pathlib.Path(playground_dir) / "co-alpha"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "alpha", str(dest))

    niches = {n["name"]: n for n in list_niches(playground_dir, PARTICIPANT, TEAM)}
    assert niches["alpha"]["residency"] == NicheResidency.CHECKED_OUT.value
    assert niches["beta"]["residency"] == NicheResidency.CACHED.value


def test_no_checkout_error_cached_message(playground_dir):
    """NoCheckoutError for a CACHED niche tells the user to attach a checkout."""
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "stuff")

    with pytest.raises(NoCheckoutError) as exc_info:
        merge_niche(playground_dir, PARTICIPANT, TEAM, "stuff", "some-peer-id")

    err = exc_info.value
    assert err.residency is NicheResidency.CACHED
    # Message should not mention fetching since the niche is already local.
    assert "fetch" not in str(err).lower()
    assert "attach" in str(err).lower()


def test_no_checkout_error_remote_only_message(playground_dir):
    """NoCheckoutError from merge_niche for a REMOTE_ONLY niche says to fetch first.

    A REMOTE_ONLY niche has no local git dir. _require_clean_checkout detects
    this via niche_residency() and raises NoCheckoutError(residency=REMOTE_ONLY).
    """
    _init(playground_dir)
    # Vault is initialised but "ghost" niche was never created locally —
    # there is no git dir, so residency is REMOTE_ONLY.
    with pytest.raises(NoCheckoutError) as exc_info:
        merge_niche(playground_dir, PARTICIPANT, TEAM, "ghost", "some-peer-id")

    err = exc_info.value
    assert err.residency is NicheResidency.REMOTE_ONLY
    assert "fetch" in str(err).lower()
    assert "attach" in str(err).lower()


def test_cli_list_shows_residency_labels(playground_dir):
    """sfv list reports the correct residency label for all three states."""
    from click.testing import CliRunner
    from shared_file_vault.cli import cli

    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "alpha")   # will be CHECKED_OUT
    create_niche(playground_dir, PARTICIPANT, TEAM, "beta")    # stays CACHED

    dest = pathlib.Path(playground_dir) / "co-alpha"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "alpha", str(dest))

    runner = CliRunner()
    result = runner.invoke(cli, ["list", playground_dir, PARTICIPANT, TEAM])
    assert result.exit_code == 0, result.output

    assert "checked_out" in result.output
    assert "cached" in result.output
    # Checkout path is still visible alongside the residency label.
    assert str(dest) in result.output


# ---------------------------------------------------------------------------
# Issue-82 micro tests: transit work tree removal
# ---------------------------------------------------------------------------

PARTICIPANT_C = "cc" * 16


def _full_sync_setup(playground_dir):
    """Alice creates + publishes + pushes a niche. Returns (playground, cloud, bob_root)."""
    from cod_sync.protocol import LocalFolderRemote
    from shared_file_vault.vault import push_niche

    playground = pathlib.Path(playground_dir)
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "sync")
    alice_co = playground / "co-alice"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "sync", str(alice_co))
    (alice_co / "file.txt").write_text("v1\n")
    publish(playground_dir, PARTICIPANT, TEAM, "sync", str(alice_co), message="v1")

    cloud = playground / "cloud"
    cloud.mkdir()
    push_niche(playground_dir, PARTICIPANT, TEAM, "sync", LocalFolderRemote(str(cloud)))

    bob_root = playground / "vault-bob"
    init_vault(str(bob_root), PARTICIPANT_B)

    return playground, cloud, bob_root


def test_no_transit_dir_ever_created(playground_dir):
    """No transit/ directory is created anywhere in the vault tree across the full lifecycle."""
    from cod_sync.protocol import LocalFolderRemote
    from shared_file_vault.vault import push_niche

    playground, cloud, bob_root = _full_sync_setup(playground_dir)

    # Bob: fetch → add_checkout → merge
    fetch_niche(str(bob_root), PARTICIPANT_B, TEAM, "sync", PARTICIPANT, LocalFolderRemote(str(cloud)))
    bob_co = playground / "co-bob"
    add_checkout(str(bob_root), PARTICIPANT_B, TEAM, "sync", str(bob_co))
    merge_niche(str(bob_root), PARTICIPANT_B, TEAM, "sync", PARTICIPANT)

    # Walk every vault directory and assert no transit/ dir exists
    for vault_root in [pathlib.Path(playground_dir), bob_root]:
        for path in vault_root.rglob("transit"):
            if path.is_dir():
                raise AssertionError(f"Unexpected transit dir: {path}")


def test_fetch_without_checkout_pins_ref(playground_dir):
    """fetch_niche on a CACHED niche (no checkout) pins the parked ref to the expected SHA."""
    from cod_sync.protocol import LocalFolderRemote

    playground, cloud, bob_root = _full_sync_setup(playground_dir)

    fetched_sha = fetch_niche(
        str(bob_root), PARTICIPANT_B, TEAM, "sync", PARTICIPANT, LocalFolderRemote(str(cloud))
    )

    assert fetched_sha is not None
    # The niche is CACHED (no checkout registered)
    assert niche_residency(str(bob_root), PARTICIPANT_B, TEAM, "sync") is NicheResidency.CACHED

    # The parked ref resolves to the fetched SHA — validates _resolve_ref without a checkout
    from shared_file_vault.vault import _niche_git_dir, _peer_ref_name, _resolve_ref
    git_dir = _niche_git_dir(str(bob_root), PARTICIPANT_B, TEAM, "sync")
    resolved = _resolve_ref(git_dir, _peer_ref_name(PARTICIPANT))
    assert resolved == fetched_sha


def test_peer_update_status_cached_resolve_ref_path(playground_dir):
    """peer_update_status reports a valid parked_sha and ready_to_merge=True for a CACHED niche."""
    from cod_sync.protocol import LocalFolderRemote
    from shared_file_vault.vault import peer_update_status

    playground, cloud, bob_root = _full_sync_setup(playground_dir)
    fetch_niche(
        str(bob_root), PARTICIPANT_B, TEAM, "sync", PARTICIPANT, LocalFolderRemote(str(cloud))
    )

    status_info = peer_update_status(str(bob_root), PARTICIPANT_B, TEAM, "niche", "sync", PARTICIPANT)
    assert status_info["parked_sha"] is not None
    assert status_info["ready_to_merge"] is True
    assert status_info["already_merged"] is False


def test_peer_update_status_cached_is_ancestor_path(playground_dir):
    """peer_update_status exercises _is_ancestor for a CACHED niche with local commit history."""
    from cod_sync.protocol import LocalFolderRemote
    from shared_file_vault.vault import peer_update_status, push_niche

    playground = pathlib.Path(playground_dir)
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "history")
    alice_co = playground / "co-alice"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "history", str(alice_co))

    # Alice: commit A, push
    (alice_co / "a.txt").write_text("a\n")
    publish(playground_dir, PARTICIPANT, TEAM, "history", str(alice_co), message="A")
    cloud = playground / "cloud"
    cloud.mkdir()
    push_niche(playground_dir, PARTICIPANT, TEAM, "history", LocalFolderRemote(str(cloud)))

    # Bob: fetch A, add checkout, merge A (creates local HEAD at A), then unregister checkout
    bob_root = playground / "vault-bob"
    init_vault(str(bob_root), PARTICIPANT_B)
    fetch_niche(str(bob_root), PARTICIPANT_B, TEAM, "history", PARTICIPANT, LocalFolderRemote(str(cloud)))
    bob_co = playground / "co-bob"
    add_checkout(str(bob_root), PARTICIPANT_B, TEAM, "history", str(bob_co))
    merge_niche(str(bob_root), PARTICIPANT_B, TEAM, "history", PARTICIPANT)
    remove_checkout(str(bob_root), PARTICIPANT_B, TEAM, "history", str(bob_co))
    # Bob's niche is now CACHED with HEAD at A

    # Alice: commit B, push
    (alice_co / "b.txt").write_text("b\n")
    publish(playground_dir, PARTICIPANT, TEAM, "history", str(alice_co), message="B")
    push_niche(playground_dir, PARTICIPANT, TEAM, "history", LocalFolderRemote(str(cloud)))

    # Bob: fetch B — parked_sha is B, HEAD is A, so _is_ancestor is exercised
    sha_B = fetch_niche(
        str(bob_root), PARTICIPANT_B, TEAM, "history", PARTICIPANT, LocalFolderRemote(str(cloud))
    )
    status_info = peer_update_status(str(bob_root), PARTICIPANT_B, TEAM, "niche", "history", PARTICIPANT)
    assert status_info["parked_sha"] == sha_B
    assert status_info["already_merged"] is False
    assert status_info["ready_to_merge"] is True

    # Bob re-registers, merges B, removes checkout again → already_merged=True
    add_checkout(str(bob_root), PARTICIPANT_B, TEAM, "history", str(bob_co))
    merge_niche(str(bob_root), PARTICIPANT_B, TEAM, "history", PARTICIPANT)
    remove_checkout(str(bob_root), PARTICIPANT_B, TEAM, "history", str(bob_co))

    status_info = peer_update_status(str(bob_root), PARTICIPANT_B, TEAM, "niche", "history", PARTICIPANT)
    assert status_info["already_merged"] is True
    assert status_info["ready_to_merge"] is False


def test_initial_history_pull(playground_dir):
    """pull_niche on a fresh local git dir (no prior commits) populates the checkout."""
    from cod_sync.protocol import LocalFolderRemote
    from shared_file_vault.vault import pull_niche

    playground, cloud, bob_root = _full_sync_setup(playground_dir)

    # Bob: create a fresh niche git dir and attach a checkout — no prior commits
    from shared_file_vault.vault import _niche_git_dir, _init_git_dir
    git_dir = _niche_git_dir(str(bob_root), PARTICIPANT_B, TEAM, "sync")
    git_dir.mkdir(parents=True)
    _init_git_dir(git_dir)
    bob_co = playground / "co-bob"
    add_checkout(str(bob_root), PARTICIPANT_B, TEAM, "sync", str(bob_co))

    pull_niche(str(bob_root), PARTICIPANT_B, TEAM, "sync", LocalFolderRemote(str(cloud)))

    assert (bob_co / "file.txt").exists()
    assert (bob_co / "file.txt").read_text() == "v1\n"


def test_initial_history_merge(playground_dir):
    """fetch_niche + add_checkout + merge_niche on a fresh git dir populates the checkout."""
    from cod_sync.protocol import LocalFolderRemote

    playground, cloud, bob_root = _full_sync_setup(playground_dir)

    fetch_niche(
        str(bob_root), PARTICIPANT_B, TEAM, "sync", PARTICIPANT, LocalFolderRemote(str(cloud))
    )
    bob_co = playground / "co-bob"
    add_checkout(str(bob_root), PARTICIPANT_B, TEAM, "sync", str(bob_co))
    merge_niche(str(bob_root), PARTICIPANT_B, TEAM, "sync", PARTICIPANT)

    assert (bob_co / "file.txt").exists()
    assert (bob_co / "file.txt").read_text() == "v1\n"


def test_merge_conflict_paths_in_user_checkout(playground_dir):
    """niche_conflict_paths returns conflicted filenames after a merge conflict."""
    from cod_sync.protocol import LocalFolderRemote
    from shared_file_vault.vault import MergeConflictError, niche_conflict_paths, push_niche

    playground = pathlib.Path(playground_dir)

    # Alice and Bob both start from the same base commit
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "conflict")
    alice_co = playground / "co-alice"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "conflict", str(alice_co))
    (alice_co / "shared.txt").write_text("base\n")
    publish(playground_dir, PARTICIPANT, TEAM, "conflict", str(alice_co), message="base")

    cloud = playground / "cloud"
    cloud.mkdir()
    push_niche(playground_dir, PARTICIPANT, TEAM, "conflict", LocalFolderRemote(str(cloud)))

    bob_root = playground / "vault-bob"
    init_vault(str(bob_root), PARTICIPANT_B)
    fetch_niche(str(bob_root), PARTICIPANT_B, TEAM, "conflict", PARTICIPANT, LocalFolderRemote(str(cloud)))
    bob_co = playground / "co-bob"
    add_checkout(str(bob_root), PARTICIPANT_B, TEAM, "conflict", str(bob_co))
    merge_niche(str(bob_root), PARTICIPANT_B, TEAM, "conflict", PARTICIPANT)

    # Both Alice and Bob modify the same file divergently and push/re-fetch
    (alice_co / "shared.txt").write_text("alice edit\n")
    publish(playground_dir, PARTICIPANT, TEAM, "conflict", str(alice_co), message="alice")
    push_niche(playground_dir, PARTICIPANT, TEAM, "conflict", LocalFolderRemote(str(cloud)))

    (bob_co / "shared.txt").write_text("bob edit\n")
    publish(str(bob_root), PARTICIPANT_B, TEAM, "conflict", str(bob_co), message="bob")

    fetch_niche(str(bob_root), PARTICIPANT_B, TEAM, "conflict", PARTICIPANT, LocalFolderRemote(str(cloud)))

    with pytest.raises(MergeConflictError):
        merge_niche(str(bob_root), PARTICIPANT_B, TEAM, "conflict", PARTICIPANT)

    conflict_files = niche_conflict_paths(str(bob_root), PARTICIPANT_B, TEAM, "conflict")
    assert "shared.txt" in conflict_files


def test_stale_bundle_guard(playground_dir):
    """pull_niche raises RuntimeError when the remote has no content, leaving the checkout unchanged."""
    from cod_sync.protocol import LocalFolderRemote
    from shared_file_vault.vault import pull_niche

    playground, cloud, bob_root = _full_sync_setup(playground_dir)

    # Bob: initial pull succeeds — seeds cloud-codsync-bundle-tmp/main in git config
    from shared_file_vault.vault import _niche_git_dir, _init_git_dir
    git_dir = _niche_git_dir(str(bob_root), PARTICIPANT_B, TEAM, "sync")
    git_dir.mkdir(parents=True)
    _init_git_dir(git_dir)
    bob_co = playground / "co-bob"
    add_checkout(str(bob_root), PARTICIPANT_B, TEAM, "sync", str(bob_co))
    pull_niche(str(bob_root), PARTICIPANT_B, TEAM, "sync", LocalFolderRemote(str(cloud)))
    assert (bob_co / "file.txt").read_text() == "v1\n"

    # Now point pull at an empty remote — fetch_from_remote returns None
    empty_cloud = playground / "cloud-empty"
    empty_cloud.mkdir()
    with pytest.raises(RuntimeError, match="could not fetch from remote"):
        pull_niche(str(bob_root), PARTICIPANT_B, TEAM, "sync", LocalFolderRemote(str(empty_cloud)))

    # Checkout is unchanged
    assert (bob_co / "file.txt").read_text() == "v1\n"


def test_niche_conflict_paths_stale_and_cached(playground_dir):
    """niche_conflict_paths follows the unified policy for all no-checkout cases."""
    import shutil
    from shared_file_vault.vault import (
        MergeConflictError,
        StaleCheckoutError,
        _niche_git_dir,
        niche_conflict_paths,
    )

    playground = pathlib.Path(playground_dir)
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "cptest")
    co = playground / "co-cptest"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "cptest", str(co))
    (co / "f.txt").write_text("x\n")
    publish(playground_dir, PARTICIPANT, TEAM, "cptest", str(co), message="init")
    git_dir = _niche_git_dir(playground_dir, PARTICIPANT, TEAM, "cptest")

    # Grab a valid commit SHA to use as a synthetic MERGE_HEAD
    from cod_sync.protocol import gitCmd
    sha = gitCmd(["--git-dir", str(git_dir), "rev-parse", "HEAD"]).stdout.strip()

    # Sub-case A — CACHED, no MERGE_HEAD: remove_checkout → []
    remove_checkout(playground_dir, PARTICIPANT, TEAM, "cptest", str(co))
    assert niche_conflict_paths(playground_dir, PARTICIPANT, TEAM, "cptest") == []

    # Sub-case B — CACHED, MERGE_HEAD present → NoCheckoutError
    (git_dir / "MERGE_HEAD").write_text(sha + "\n")
    with pytest.raises(NoCheckoutError):
        niche_conflict_paths(playground_dir, PARTICIPANT, TEAM, "cptest")
    (git_dir / "MERGE_HEAD").unlink()

    # Re-register checkout for the stale cases
    add_checkout(playground_dir, PARTICIPANT, TEAM, "cptest", str(co))

    # Sub-case C — stale (dir deleted), no MERGE_HEAD → []
    shutil.rmtree(str(co))
    assert niche_conflict_paths(playground_dir, PARTICIPANT, TEAM, "cptest") == []

    # Sub-case D — stale (dir deleted), MERGE_HEAD present → StaleCheckoutError
    (git_dir / "MERGE_HEAD").write_text(sha + "\n")
    with pytest.raises(StaleCheckoutError):
        niche_conflict_paths(playground_dir, PARTICIPANT, TEAM, "cptest")


def test_cwd_preserved_across_vault_operations(playground_dir):
    """os.getcwd() is identical before and after every vault operation that touches git."""
    import os
    from cod_sync.protocol import LocalFolderRemote
    from shared_file_vault.vault import (
        merge_registry,
        push_niche,
        pull_niche,
        push_registry,
    )

    playground = pathlib.Path(playground_dir)
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "cwdtest")
    alice_co = playground / "co-alice"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "cwdtest", str(alice_co))
    (alice_co / "cwd.txt").write_text("cwd\n")
    publish(playground_dir, PARTICIPANT, TEAM, "cwdtest", str(alice_co), message="init")

    cloud_niche = playground / "cloud-niche"
    cloud_niche.mkdir()
    cloud_reg = playground / "cloud-reg"
    cloud_reg.mkdir()

    cwd_before = os.getcwd()

    push_niche(playground_dir, PARTICIPANT, TEAM, "cwdtest", LocalFolderRemote(str(cloud_niche)))
    assert os.getcwd() == cwd_before

    push_registry(playground_dir, PARTICIPANT, TEAM, LocalFolderRemote(str(cloud_reg)))
    assert os.getcwd() == cwd_before

    bob_root = playground / "vault-bob"
    init_vault(str(bob_root), PARTICIPANT_B)

    fetch_niche(str(bob_root), PARTICIPANT_B, TEAM, "cwdtest", PARTICIPANT, LocalFolderRemote(str(cloud_niche)))
    assert os.getcwd() == cwd_before

    bob_co = playground / "co-bob"
    add_checkout(str(bob_root), PARTICIPANT_B, TEAM, "cwdtest", str(bob_co))
    merge_niche(str(bob_root), PARTICIPANT_B, TEAM, "cwdtest", PARTICIPANT)
    assert os.getcwd() == cwd_before

    # pull_niche (initial pull path)
    bob2_root = playground / "vault-bob2"
    init_vault(str(bob2_root), PARTICIPANT_C)
    from shared_file_vault.vault import _niche_git_dir, _init_git_dir
    git_dir2 = _niche_git_dir(str(bob2_root), PARTICIPANT_C, TEAM, "cwdtest")
    git_dir2.mkdir(parents=True)
    _init_git_dir(git_dir2)
    bob2_co = playground / "co-bob2"
    add_checkout(str(bob2_root), PARTICIPANT_C, TEAM, "cwdtest", str(bob2_co))
    pull_niche(str(bob2_root), PARTICIPANT_C, TEAM, "cwdtest", LocalFolderRemote(str(cloud_niche)))
    assert os.getcwd() == cwd_before

    # merge_registry (covers _cod_merge_ref via registry path)
    from shared_file_vault.vault import fetch_registry
    fetch_registry(str(bob_root), PARTICIPANT_B, TEAM, PARTICIPANT, LocalFolderRemote(str(cloud_reg)))
    assert os.getcwd() == cwd_before
    merge_registry(str(bob_root), PARTICIPANT_B, TEAM, PARTICIPANT)
    assert os.getcwd() == cwd_before

    # Failure path: pull_niche on empty remote raises but leaves CWD unchanged
    empty_cloud = playground / "cloud-empty"
    empty_cloud.mkdir()
    with pytest.raises(RuntimeError, match="could not fetch from remote"):
        pull_niche(str(bob2_root), PARTICIPANT_C, TEAM, "cwdtest", LocalFolderRemote(str(empty_cloud)))
    assert os.getcwd() == cwd_before

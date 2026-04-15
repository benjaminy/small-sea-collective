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
# (not on the transit work tree) before any transit operations run.
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
    """merge_niche raises DirtyCheckoutError when the user's checkout has a modified tracked file.

    The guard must target the user's checkout, not the transit work tree
    (transit resets itself to HEAD and would always appear clean).
    """
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

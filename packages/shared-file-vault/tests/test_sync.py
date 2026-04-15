import pathlib

import pytest
from cod_sync.protocol import LocalFolderRemote
from shared_file_vault import sync
from shared_file_vault.vault import (
    NicheResidency,
    add_checkout,
    create_niche,
    fetch_niche,
    init_vault,
    merge_niche,
    publish,
    push_niche,
)

PARTICIPANT = "bb" * 16
TEAM = "SyncTeam"


def test_sync_niche_between_devices(playground_dir):
    playground = pathlib.Path(playground_dir)
    cloud_dir = playground / "cloud"
    cloud_dir.mkdir()

    # --- Device A: create and populate a niche ---
    root_a = str(playground / "device-a")
    init_vault(root_a, PARTICIPANT)
    create_niche(root_a, PARTICIPANT, TEAM, "photos")
    checkout_a = str(playground / "checkout-a" / "photos")
    add_checkout(root_a, PARTICIPANT, TEAM, "photos", checkout_a)

    (pathlib.Path(checkout_a) / "sunset.jpg").write_bytes(b"fake-sunset-data")
    (pathlib.Path(checkout_a) / "beach.jpg").write_bytes(b"fake-beach-data")
    publish(root_a, PARTICIPANT, TEAM, "photos", checkout_a, message="add photos")

    push_niche(root_a, PARTICIPANT, TEAM, "photos", LocalFolderRemote(str(cloud_dir)))

    # --- Device B: join flow: fetch → attach checkout → merge ---
    root_b = str(playground / "device-b")
    init_vault(root_b, PARTICIPANT)
    checkout_b = str(playground / "checkout-b" / "photos")

    fetch_niche(root_b, PARTICIPANT, TEAM, "photos", PARTICIPANT, LocalFolderRemote(str(cloud_dir)))
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

    Tests the preflight path in merge_via_hub: it calls vault.get_checkout,
    detects None, calls vault.niche_residency, and wraps the result in the
    sync-layer exception — so residency survives the vault->sync boundary.
    """
    root = str(pathlib.Path(playground_dir) / "vault")
    init_vault(root, PARTICIPANT)
    create_niche(root, PARTICIPANT, TEAM, "files")
    # No add_checkout: niche git dir exists but no checkout row → CACHED.

    # Bypass hub auth; the preflight fires before any network call.
    monkeypatch.setattr(sync, "get_team_session", lambda *a, **kw: None)

    with pytest.raises(sync.NoCheckoutError) as exc_info:
        sync.merge_via_hub(root, PARTICIPANT, TEAM, "files", "some-peer-id")

    err = exc_info.value
    assert err.residency is NicheResidency.CACHED
    assert "attach" in str(err).lower()


def test_merge_via_hub_no_checkout_remote_only_preserves_residency(playground_dir, monkeypatch):
    """sync.merge_via_hub raises sync.NoCheckoutError with REMOTE_ONLY residency
    when the niche has no local git dir at all.
    """
    root = str(pathlib.Path(playground_dir) / "vault")
    init_vault(root, PARTICIPANT)
    # No create_niche: no git dir → REMOTE_ONLY.

    monkeypatch.setattr(sync, "get_team_session", lambda *a, **kw: None)

    with pytest.raises(sync.NoCheckoutError) as exc_info:
        sync.merge_via_hub(root, PARTICIPANT, TEAM, "files", "some-peer-id")

    err = exc_info.value
    assert err.residency is NicheResidency.REMOTE_ONLY
    assert "fetch" in str(err).lower()

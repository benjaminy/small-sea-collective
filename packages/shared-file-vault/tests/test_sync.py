import pathlib

from shared_file_vault.vault import (
    init_vault,
    create_niche,
    checkout_niche,
    publish,
    push_niche,
    pull_niche,
)

PARTICIPANT = "bb" * 16
TEAM = "SyncTeam"


def test_sync_niche_between_devices(playground_dir):
    playground = pathlib.Path(playground_dir)
    cloud_dir = playground / "cloud"
    cloud_dir.mkdir()

    # --- Device A: create and populate a niche ---
    root_a = str(playground / "device-a")
    init_vault(root_a, PARTICIPANT, TEAM)
    create_niche(root_a, PARTICIPANT, TEAM, "photos")
    checkout_a = str(playground / "checkout-a" / "photos")
    checkout_niche(root_a, PARTICIPANT, TEAM, "photos", checkout_a)

    (pathlib.Path(checkout_a) / "sunset.jpg").write_bytes(b"fake-sunset-data")
    (pathlib.Path(checkout_a) / "beach.jpg").write_bytes(b"fake-beach-data")
    publish(root_a, PARTICIPANT, TEAM, "photos", message="add photos")

    # Push to cloud
    push_niche(root_a, PARTICIPANT, TEAM, "photos", str(cloud_dir))

    # --- Device B: create niche, then pull from cloud ---
    root_b = str(playground / "device-b")
    init_vault(root_b, PARTICIPANT, TEAM)
    create_niche(root_b, PARTICIPANT, TEAM, "photos")
    checkout_b = str(playground / "checkout-b" / "photos")
    checkout_niche(root_b, PARTICIPANT, TEAM, "photos", checkout_b)

    pull_niche(root_b, PARTICIPANT, TEAM, "photos", str(cloud_dir))

    # --- Assert both checkouts have the same files ---
    sunset_b = pathlib.Path(checkout_b) / "sunset.jpg"
    beach_b = pathlib.Path(checkout_b) / "beach.jpg"

    assert sunset_b.exists(), "sunset.jpg missing on device B"
    assert beach_b.exists(), "beach.jpg missing on device B"
    assert sunset_b.read_bytes() == b"fake-sunset-data"
    assert beach_b.read_bytes() == b"fake-beach-data"

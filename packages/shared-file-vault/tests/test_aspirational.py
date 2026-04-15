"""
Scenario tests for the Shared File Vault.

These cover the multi-participant workflows that exercise the most
important end-to-end behavior:

  1. Registry propagation — Bob discovers a niche via the shared registry
     without Alice telling him its name out-of-band.

  2. Concurrent registry additions — Alice and Bob each create a niche
     independently; after cross-pulling registries both see all niches.

  3. Full join flow — Bob starts with an empty vault, pulls the registry,
     discovers a niche, pulls the niche content, adds a checkout, reads
     Alice's files and contributes back.

Note: each niche has at most one checkout per device. Add/remove is the
supported workflow for relocating a checkout.
"""

import pathlib

import pytest
from cod_sync.protocol import LocalFolderRemote

from shared_file_vault.vault import (
    add_checkout,
    create_niche,
    fetch_niche,
    get_checkout,
    init_vault,
    list_niches,
    merge_niche,
    publish,
    pull_niche,
    pull_registry,
    push_niche,
    push_registry,
    remove_checkout,
)

ALICE = "aa" * 16
BOB = "bb" * 16
TEAM = "Collab"


# --- Helpers ---


def setup_vault(playground, name, participant_hex):
    """Create an empty vault for a participant. Returns vault_root."""
    root = playground / f"vault-{name}"
    init_vault(str(root), participant_hex)
    return root


def write(path, filename, content):
    (path / filename).parent.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text(content)


def read(path, filename):
    return (path / filename).read_text()


def exists(path, filename):
    return (path / filename).exists()


# --- Tests ---


def test_registry_propagation(playground_dir):
    """Bob discovers a niche via the shared registry.

    Alice creates a niche and pushes her registry. Bob pulls the registry
    and sees the niche without Alice telling him its name out-of-band.
    After pulling the niche content, Bob can add a checkout and read the
    files Alice published.
    """
    playground = pathlib.Path(playground_dir)
    alice_reg_cloud = playground / "cloud-alice-registry"
    alice_niche_cloud = playground / "cloud-alice-docs"
    alice_reg_cloud.mkdir()
    alice_niche_cloud.mkdir()

    # Alice creates a vault, a niche, writes a file, and pushes everything
    alice_root = setup_vault(playground, "alice", ALICE)
    create_niche(str(alice_root), ALICE, TEAM, "docs")

    alice_co = playground / "checkout-alice-docs"
    add_checkout(str(alice_root), ALICE, TEAM, "docs", str(alice_co))

    write(alice_co, "readme.txt", "Hello from Alice.\n")
    publish(str(alice_root), ALICE, TEAM, "docs", str(alice_co), message="initial commit")

    push_registry(str(alice_root), ALICE, TEAM, LocalFolderRemote(str(alice_reg_cloud)))
    push_niche(str(alice_root), ALICE, TEAM, "docs", LocalFolderRemote(str(alice_niche_cloud)))

    # Bob starts with an empty vault and pulls only the registry
    bob_root = setup_vault(playground, "bob", BOB)
    pull_registry(str(bob_root), BOB, TEAM, LocalFolderRemote(str(alice_reg_cloud)))

    niches = list_niches(str(bob_root), BOB, TEAM)
    niche_names = [n["name"] for n in niches]
    assert "docs" in niche_names, "Bob should discover the 'docs' niche via the registry"

    # Bob joins the niche: fetch → attach checkout → merge (3-step join flow).
    # Fetch parks Alice's content under a peer ref without advancing HEAD.
    # add_checkout then creates an empty checkout. merge_niche integrates the
    # parked ref and refreshes the checkout with Alice's files.
    fetch_niche(str(bob_root), BOB, TEAM, "docs", ALICE, LocalFolderRemote(str(alice_niche_cloud)))

    bob_co = playground / "checkout-bob-docs"
    add_checkout(str(bob_root), BOB, TEAM, "docs", str(bob_co))
    merge_niche(str(bob_root), BOB, TEAM, "docs", ALICE)

    assert exists(bob_co, "readme.txt"), "Bob's checkout should contain Alice's file"
    assert read(bob_co, "readme.txt") == "Hello from Alice.\n"


def test_concurrent_registry_additions(playground_dir):
    """Alice and Bob each add a niche after pulling from a shared seed.

    The supported workflow: one participant seeds the registry first;
    others pull it before adding anything. Everyone shares a common
    history so cross-pulls are clean merges.

    Two participants independently bootstrapping separate registries
    and then cross-pulling is not supported (produces unrelated histories).
    """
    playground = pathlib.Path(playground_dir)
    seed_cloud = playground / "cloud-seed-registry"
    alice_reg_cloud = playground / "cloud-alice-registry"
    bob_reg_cloud = playground / "cloud-bob-registry"
    seed_cloud.mkdir()
    alice_reg_cloud.mkdir()
    bob_reg_cloud.mkdir()

    # Alice seeds the registry with an initial niche, pushes to seed cloud
    alice_root = setup_vault(playground, "alice", ALICE)
    create_niche(str(alice_root), ALICE, TEAM, "seed")
    push_registry(str(alice_root), ALICE, TEAM, LocalFolderRemote(str(seed_cloud)))

    # Bob pulls from seed cloud before adding anything — establishes common history
    bob_root = setup_vault(playground, "bob", BOB)
    pull_registry(str(bob_root), BOB, TEAM, LocalFolderRemote(str(seed_cloud)))

    # Now both add their own niches on top of the shared history
    create_niche(str(alice_root), ALICE, TEAM, "photos")
    create_niche(str(bob_root), BOB, TEAM, "receipts")

    push_registry(str(alice_root), ALICE, TEAM, LocalFolderRemote(str(alice_reg_cloud)))
    push_registry(str(bob_root), BOB, TEAM, LocalFolderRemote(str(bob_reg_cloud)))

    # Cross-pull registries — clean merge because of the common seed history
    pull_registry(str(alice_root), ALICE, TEAM, LocalFolderRemote(str(bob_reg_cloud)))
    pull_registry(str(bob_root), BOB, TEAM, LocalFolderRemote(str(alice_reg_cloud)))

    alice_niches = {n["name"] for n in list_niches(str(alice_root), ALICE, TEAM)}
    bob_niches = {n["name"] for n in list_niches(str(bob_root), BOB, TEAM)}

    assert "photos" in alice_niches
    assert "receipts" in alice_niches, "Alice should see Bob's niche after registry merge"
    assert alice_niches == bob_niches, "Both participants should converge to the same registry"


def test_one_checkout_per_niche(playground_dir):
    """Each niche has at most one checkout on a device.

    Adding a second checkout raises DuplicateCheckoutError. To relocate a
    checkout, the user must remove the existing one first.
    """
    import pytest
    from shared_file_vault.vault import DuplicateCheckoutError

    playground = pathlib.Path(playground_dir)

    alice_root = setup_vault(playground, "alice", ALICE)
    create_niche(str(alice_root), ALICE, TEAM, "notes")

    checkout_a = playground / "checkout-a"
    checkout_b = playground / "checkout-b"

    add_checkout(str(alice_root), ALICE, TEAM, "notes", str(checkout_a))
    assert get_checkout(str(alice_root), ALICE, TEAM, "notes") == str(checkout_a)

    write(checkout_a, "ideas.txt", "Build something useful.\n")
    publish(str(alice_root), ALICE, TEAM, "notes", str(checkout_a), message="add ideas")

    # Second attach is refused
    with pytest.raises(DuplicateCheckoutError):
        add_checkout(str(alice_root), ALICE, TEAM, "notes", str(checkout_b))

    # Remove and re-attach at a new location
    remove_checkout(str(alice_root), ALICE, TEAM, "notes", str(checkout_a))
    assert get_checkout(str(alice_root), ALICE, TEAM, "notes") is None

    add_checkout(str(alice_root), ALICE, TEAM, "notes", str(checkout_b))
    assert get_checkout(str(alice_root), ALICE, TEAM, "notes") == str(checkout_b)
    assert exists(checkout_b, "ideas.txt"), "new checkout should reflect committed content"


def test_full_join_flow(playground_dir):
    """Bob starts with an empty vault and joins an existing team niche.

    The join flow is:
      1. Pull registry  → discover what niches exist
      2. Pull niche     → get the content
      3. Add checkout   → link to a local directory
      4. Read files     → see Alice's work

    No out-of-band niche name is needed; the registry carries it.
    Bob then adds his own file, and Alice pulls it back.
    """
    playground = pathlib.Path(playground_dir)
    alice_reg_cloud = playground / "cloud-alice-registry"
    alice_niche_cloud = playground / "cloud-alice-docs"
    bob_niche_cloud = playground / "cloud-bob-docs"
    alice_reg_cloud.mkdir()
    alice_niche_cloud.mkdir()
    bob_niche_cloud.mkdir()

    # Alice sets up the team
    alice_root = setup_vault(playground, "alice", ALICE)
    create_niche(str(alice_root), ALICE, TEAM, "docs")

    alice_co = playground / "checkout-alice"
    add_checkout(str(alice_root), ALICE, TEAM, "docs", str(alice_co))

    write(alice_co, "guide.txt", "Getting started.\n")
    publish(str(alice_root), ALICE, TEAM, "docs", str(alice_co), message="add guide")

    push_registry(str(alice_root), ALICE, TEAM, LocalFolderRemote(str(alice_reg_cloud)))
    push_niche(str(alice_root), ALICE, TEAM, "docs", LocalFolderRemote(str(alice_niche_cloud)))

    # Bob joins from scratch — empty vault, no prior knowledge of niche names
    bob_root = setup_vault(playground, "bob", BOB)

    pull_registry(str(bob_root), BOB, TEAM, LocalFolderRemote(str(alice_reg_cloud)))
    discovered = [n["name"] for n in list_niches(str(bob_root), BOB, TEAM)]
    assert "docs" in discovered

    # 3-step join flow: fetch → attach checkout → merge
    fetch_niche(str(bob_root), BOB, TEAM, "docs", ALICE, LocalFolderRemote(str(alice_niche_cloud)))

    bob_co = playground / "checkout-bob"
    add_checkout(str(bob_root), BOB, TEAM, "docs", str(bob_co))
    merge_niche(str(bob_root), BOB, TEAM, "docs", ALICE)

    assert exists(bob_co, "guide.txt")
    assert read(bob_co, "guide.txt") == "Getting started.\n"

    # Bob contributes back
    write(bob_co, "bob_notes.txt", "My contribution.\n")
    publish(str(bob_root), BOB, TEAM, "docs", str(bob_co), message="add bob_notes")
    push_niche(str(bob_root), BOB, TEAM, "docs", LocalFolderRemote(str(bob_niche_cloud)))

    pull_niche(str(alice_root), ALICE, TEAM, "docs", LocalFolderRemote(str(bob_niche_cloud)))
    assert exists(alice_co, "bob_notes.txt"), "Alice should see Bob's contribution after pull"
    assert read(alice_co, "bob_notes.txt") == "My contribution.\n"

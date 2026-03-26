"""
Multi-participant sync and merge scenarios for Shared File Vault.

These tests demonstrate progressively more interesting collaborative situations:

  1. Two participants adding independent files — basic convergence
  2. Concurrent edits to different sections of a shared text file — auto-merge
  3. One participant renames a file while the other edits it — rename + edit merge
  4. Three-participant chain sync — transitive gossip
  5. Concurrent edits to the same line — merge conflict (aspirational)

Crypto is out of scope here; these tests focus purely on the sync and
merge mechanics.
"""

import os
import pathlib

import pytest

from shared_file_vault.vault import (
    add_checkout,
    create_niche,
    init_vault,
    publish,
    pull_niche,
    push_niche,
)

ALICE = "aa" * 16
BOB = "bb" * 16
CAROL = "cc" * 16
TEAM = "Collab"
NICHE = "docs"


# --- Helpers ---


def setup_participant(playground, name, participant_hex):
    """Create vault + checkout for a participant. Returns (root, checkout)."""
    root = playground / f"vault-{name}"
    checkout = playground / f"checkout-{name}" / NICHE
    init_vault(str(root), participant_hex)
    create_niche(str(root), participant_hex, TEAM, NICHE)
    add_checkout(str(root), participant_hex, TEAM, NICHE, str(checkout))
    return root, checkout


def do_push(root, participant_hex, cloud):
    push_niche(str(root), participant_hex, TEAM, NICHE, str(cloud))


def do_pull(root, participant_hex, cloud):
    pull_niche(str(root), participant_hex, TEAM, NICHE, str(cloud))


def write(checkout, filename, content):
    (checkout / filename).parent.mkdir(parents=True, exist_ok=True)
    (checkout / filename).write_text(content)


def read(checkout, filename):
    return (checkout / filename).read_text()


def exists(checkout, filename):
    return (checkout / filename).exists()


# --- Tests ---


def test_two_participants_converge(playground_dir):
    """Alice and Bob add different files independently, then sync to convergence.

    Topology: each participant has their own cloud. Both pull from each other
    to converge on a shared state.
    """
    playground = pathlib.Path(playground_dir)
    alice_cloud = playground / "cloud-alice"
    bob_cloud = playground / "cloud-bob"
    alice_cloud.mkdir()
    bob_cloud.mkdir()

    # --- Initial shared state: Alice creates the niche and seeds it ---
    alice_root, alice_co = setup_participant(playground, "alice", ALICE)
    write(alice_co, "readme.txt", "Shared project docs.\n")
    publish(str(alice_root), ALICE, TEAM, NICHE, str(alice_co), message="initial commit")
    do_push(alice_root, ALICE, alice_cloud)

    # Bob clones from Alice's cloud
    bob_root, bob_co = setup_participant(playground, "bob", BOB)
    do_pull(bob_root, BOB, alice_cloud)
    assert exists(bob_co, "readme.txt"), "Bob should have readme after initial sync"

    # --- Concurrent independent work ---
    write(alice_co, "alice_notes.txt", "Alice's meeting notes.\n")
    publish(str(alice_root), ALICE, TEAM, NICHE, str(alice_co), message="add alice_notes")
    do_push(alice_root, ALICE, alice_cloud)

    write(bob_co, "bob_notes.txt", "Bob's design sketches.\n")
    publish(str(bob_root), BOB, TEAM, NICHE, str(bob_co), message="add bob_notes")
    do_push(bob_root, BOB, bob_cloud)

    # --- Convergence: each pulls from the other ---
    do_pull(alice_root, ALICE, bob_cloud)    # Alice gets bob_notes
    do_pull(bob_root, BOB, alice_cloud)      # Bob gets alice_notes

    assert exists(alice_co, "bob_notes.txt"), "Alice should see Bob's file after sync"
    assert exists(bob_co, "alice_notes.txt"), "Bob should see Alice's file after sync"
    assert read(alice_co, "bob_notes.txt") == "Bob's design sketches.\n"
    assert read(bob_co, "alice_notes.txt") == "Alice's meeting notes.\n"


def test_concurrent_text_edits_auto_merge(playground_dir):
    """Alice and Bob edit different sections of the same text file.

    Git's line-level merge resolves this without conflict. Both see the
    fully merged document.
    """
    playground = pathlib.Path(playground_dir)
    alice_cloud = playground / "cloud-alice"
    bob_cloud = playground / "cloud-bob"
    alice_cloud.mkdir()
    bob_cloud.mkdir()

    # Seed the niche with a multi-section document
    initial_content = (
        "# Overview\n"
        "This project does something useful.\n"
        "\n"
        "# Status\n"
        "Work in progress.\n"
        "\n"
        "# Next Steps\n"
        "TBD.\n"
    )

    alice_root, alice_co = setup_participant(playground, "alice", ALICE)
    write(alice_co, "plan.txt", initial_content)
    publish(str(alice_root), ALICE, TEAM, NICHE, str(alice_co), message="initial plan")
    do_push(alice_root, ALICE, alice_cloud)

    bob_root, bob_co = setup_participant(playground, "bob", BOB)
    do_pull(bob_root, BOB, alice_cloud)

    # Alice updates the Overview section
    alice_update = (
        "# Overview\n"
        "This project syncs files across devices without a central server.\n"
        "\n"
        "# Status\n"
        "Work in progress.\n"
        "\n"
        "# Next Steps\n"
        "TBD.\n"
    )
    write(alice_co, "plan.txt", alice_update)
    publish(str(alice_root), ALICE, TEAM, NICHE, str(alice_co), message="clarify overview")
    do_push(alice_root, ALICE, alice_cloud)

    # Bob updates the Next Steps section (no conflict with Alice's edit)
    bob_update = (
        "# Overview\n"
        "This project does something useful.\n"
        "\n"
        "# Status\n"
        "Work in progress.\n"
        "\n"
        "# Next Steps\n"
        "1. Finish crypto layer\n"
        "2. Write more tests\n"
    )
    write(bob_co, "plan.txt", bob_update)
    publish(str(bob_root), BOB, TEAM, NICHE, str(bob_co), message="fill in next steps")
    do_push(bob_root, BOB, bob_cloud)

    # Converge
    do_pull(alice_root, ALICE, bob_cloud)
    do_pull(bob_root, BOB, alice_cloud)

    merged_alice = read(alice_co, "plan.txt")
    merged_bob = read(bob_co, "plan.txt")

    # Both should see both edits in the merged result
    assert "syncs files across devices" in merged_alice, "Alice's edit missing after merge"
    assert "Finish crypto layer" in merged_alice, "Bob's edit missing from Alice's merge"
    assert merged_alice == merged_bob, "Both participants should converge to identical content"


def test_rename_and_concurrent_edit(playground_dir):
    """Alice renames a file while Bob edits it concurrently.

    Git detects the rename and applies Bob's edits to the renamed file.
    After merging, the result is Alice's new filename with Bob's content changes.
    """
    playground = pathlib.Path(playground_dir)
    alice_cloud = playground / "cloud-alice"
    bob_cloud = playground / "cloud-bob"
    alice_cloud.mkdir()
    bob_cloud.mkdir()

    # Seed with a substantial file (content needed for git rename detection)
    original_content = (
        "# Project Proposal\n\n"
        "## Background\n"
        "We need a decentralized file sync solution.\n\n"
        "## Goals\n"
        "- No central server\n"
        "- Works offline\n"
        "- E2E encrypted\n\n"
        "## Timeline\n"
        "Q1: Design\n"
        "Q2: Implementation\n"
    )

    alice_root, alice_co = setup_participant(playground, "alice", ALICE)
    write(alice_co, "proposal.txt", original_content)
    publish(str(alice_root), ALICE, TEAM, NICHE, str(alice_co), message="add proposal")
    do_push(alice_root, ALICE, alice_cloud)

    bob_root, bob_co = setup_participant(playground, "bob", BOB)
    do_pull(bob_root, BOB, alice_cloud)

    # Alice renames the file (organises it into a subfolder)
    old_path = alice_co / "proposal.txt"
    new_path = alice_co / "final" / "proposal.txt"
    new_path.parent.mkdir()
    os.rename(old_path, new_path)
    publish(str(alice_root), ALICE, TEAM, NICHE, str(alice_co), message="move proposal to final/")
    do_push(alice_root, ALICE, alice_cloud)

    # Bob adds a conclusion section to the original path (he doesn't know Alice renamed)
    bob_content = original_content + "\n## Conclusion\nThis is worth building.\n"
    write(bob_co, "proposal.txt", bob_content)
    publish(str(bob_root), BOB, TEAM, NICHE, str(bob_co), message="add conclusion")
    do_push(bob_root, BOB, bob_cloud)

    # Alice merges Bob's changes
    do_pull(alice_root, ALICE, bob_cloud)

    # Git should detect the rename and apply Bob's edit to the final location
    assert exists(alice_co, "final/proposal.txt"), "Renamed file should still exist"
    merged = read(alice_co, "final/proposal.txt")
    assert "This is worth building" in merged, (
        "Bob's conclusion should appear in the renamed file after merge"
    )
    # The original path should be gone (Alice's rename was kept)
    assert not exists(alice_co, "proposal.txt"), (
        "Original path should not exist after rename+merge"
    )


def test_three_participant_gossip(playground_dir):
    """Alice, Bob, and Carol sync transitively.

    Alice seeds the niche. Bob syncs from Alice, adds his files, pushes.
    Carol syncs from Bob — she gets both Alice's and Bob's changes without
    ever talking directly to Alice.
    """
    playground = pathlib.Path(playground_dir)
    alice_cloud = playground / "cloud-alice"
    bob_cloud = playground / "cloud-bob"
    carol_cloud = playground / "cloud-carol"
    alice_cloud.mkdir()
    bob_cloud.mkdir()
    carol_cloud.mkdir()

    # Alice seeds
    alice_root, alice_co = setup_participant(playground, "alice", ALICE)
    write(alice_co, "shared_glossary.txt", "niche: a shared folder\n")
    publish(str(alice_root), ALICE, TEAM, NICHE, str(alice_co), message="add glossary")
    do_push(alice_root, ALICE, alice_cloud)

    # Bob syncs from Alice, adds his own file, pushes
    bob_root, bob_co = setup_participant(playground, "bob", BOB)
    do_pull(bob_root, BOB, alice_cloud)
    assert exists(bob_co, "shared_glossary.txt"), "Bob should see Alice's file"

    write(bob_co, "architecture.txt", "Hub: local server\nManager: provisioning\n")
    publish(str(bob_root), BOB, TEAM, NICHE, str(bob_co), message="add architecture notes")
    do_push(bob_root, BOB, bob_cloud)

    # Carol syncs from Bob (who carries Alice's history) — no direct Alice contact
    carol_root, carol_co = setup_participant(playground, "carol", CAROL)
    do_pull(carol_root, CAROL, bob_cloud)

    assert exists(carol_co, "shared_glossary.txt"), "Carol should see Alice's file via Bob"
    assert exists(carol_co, "architecture.txt"), "Carol should see Bob's file"
    assert read(carol_co, "shared_glossary.txt") == "niche: a shared folder\n"

    # Carol adds her own file and pushes
    write(carol_co, "carol_ideas.txt", "proximity-based key exchange\n")
    publish(str(carol_root), CAROL, TEAM, NICHE, str(carol_co), message="add carol's ideas")
    do_push(carol_root, CAROL, carol_cloud)

    # Bob syncs from Carol to get the full three-way picture
    do_pull(bob_root, BOB, carol_cloud)
    assert exists(bob_co, "carol_ideas.txt"), "Bob should see Carol's file"


def test_concurrent_edit_same_line_raises(playground_dir):
    """Both participants edit the same line — merge conflict.

    Currently raises GitCmdFailed. The aspirational behavior is a typed
    MergeConflictError that leaves the working tree in a state the user
    can inspect and resolve.

    Marking xfail to document the gap — remove the mark when conflict
    handling is implemented in vault.py.
    """
    playground = pathlib.Path(playground_dir)
    alice_cloud = playground / "cloud-alice"
    bob_cloud = playground / "cloud-bob"
    alice_cloud.mkdir()
    bob_cloud.mkdir()

    alice_root, alice_co = setup_participant(playground, "alice", ALICE)
    write(alice_co, "title.txt", "Project Name: TBD\n")
    publish(str(alice_root), ALICE, TEAM, NICHE, str(alice_co), message="initial title")
    do_push(alice_root, ALICE, alice_cloud)

    bob_root, bob_co = setup_participant(playground, "bob", BOB)
    do_pull(bob_root, BOB, alice_cloud)

    # Both edit the same line with different content
    write(alice_co, "title.txt", "Project Name: SmallSea\n")
    publish(str(alice_root), ALICE, TEAM, NICHE, str(alice_co), message="name it SmallSea")
    do_push(alice_root, ALICE, alice_cloud)

    write(bob_co, "title.txt", "Project Name: Wavelength\n")
    publish(str(bob_root), BOB, TEAM, NICHE, str(bob_co), message="name it Wavelength")
    do_push(bob_root, BOB, bob_cloud)

    # Bob merges Alice's change — this conflicts
    # Currently crashes; eventually should raise MergeConflictError
    with pytest.raises(Exception):
        do_pull(bob_root, BOB, alice_cloud)

import pathlib

from shared_file_vault.vault import (
    add_checkout,
    create_niche,
    init_vault,
    list_checkouts,
    list_niches,
    log,
    publish,
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


def test_add_multiple_checkouts(playground_dir):
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "notes")

    dest_a = pathlib.Path(playground_dir) / "checkout-a"
    dest_b = pathlib.Path(playground_dir) / "checkout-b"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "notes", str(dest_a))
    add_checkout(playground_dir, PARTICIPANT, TEAM, "notes", str(dest_b))

    checkouts = list_checkouts(playground_dir, PARTICIPANT, TEAM, "notes")
    assert len(checkouts) == 2
    assert str(dest_a) in checkouts
    assert str(dest_b) in checkouts


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


def test_publish_refreshes_sibling_checkouts(playground_dir):
    """After publishing from checkout_a, checkout_b reflects the new commit."""
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "shared")

    dest_a = pathlib.Path(playground_dir) / "checkout-a"
    dest_b = pathlib.Path(playground_dir) / "checkout-b"
    add_checkout(playground_dir, PARTICIPANT, TEAM, "shared", str(dest_a))
    add_checkout(playground_dir, PARTICIPANT, TEAM, "shared", str(dest_b))

    (dest_a / "ideas.txt").write_text("Build something useful.\n")
    publish(playground_dir, PARTICIPANT, TEAM, "shared", str(dest_a), message="add ideas")

    assert (dest_b / "ideas.txt").exists()
    assert (dest_b / "ideas.txt").read_text() == "Build something useful.\n"

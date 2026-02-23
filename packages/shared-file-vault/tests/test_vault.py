import pathlib
import sqlite3

from shared_file_vault.vault import (
    init_vault,
    create_niche,
    checkout_niche,
    status,
    publish,
    log,
    list_niches,
)

PARTICIPANT = "aa" * 16
TEAM = "TestTeam"


def _init(playground_dir):
    init_vault(playground_dir, PARTICIPANT, TEAM)


def test_create_niche(playground_dir):
    _init(playground_dir)
    niche_id = create_niche(playground_dir, PARTICIPANT, TEAM, "photos")

    assert len(niche_id) == 32  # 16 bytes -> 32 hex chars

    # Verify vault.db row
    db = pathlib.Path(playground_dir) / "Participants" / PARTICIPANT / TEAM / "vault.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM niche WHERE name = 'photos'").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["checkout_path"] is None

    # Verify git dir exists
    git_dir = pathlib.Path(playground_dir) / "Participants" / PARTICIPANT / TEAM / "Niches" / "photos" / "git"
    assert git_dir.is_dir()
    assert (git_dir / "HEAD").exists()


def test_checkout_niche(playground_dir):
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "docs")

    dest = pathlib.Path(playground_dir) / "checkout" / "docs"
    checkout_niche(playground_dir, PARTICIPANT, TEAM, "docs", str(dest))

    # .git file should exist at dest (pointer to vault's git dir)
    git_pointer = dest / ".git"
    assert git_pointer.exists()

    # checkout_path should be set in vault.db
    db = pathlib.Path(playground_dir) / "Participants" / PARTICIPANT / TEAM / "vault.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT checkout_path FROM niche WHERE name = 'docs'").fetchone()
    conn.close()
    assert row["checkout_path"] == str(dest)


def test_publish_and_log(playground_dir):
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "notes")
    dest = pathlib.Path(playground_dir) / "checkout" / "notes"
    checkout_niche(playground_dir, PARTICIPANT, TEAM, "notes", str(dest))

    # Create a file and publish
    (dest / "hello.txt").write_text("hello world")
    commit_hash = publish(playground_dir, PARTICIPANT, TEAM, "notes", message="first note")

    assert len(commit_hash) >= 7

    entries = log(playground_dir, PARTICIPANT, TEAM, "notes")
    assert len(entries) == 1
    assert "first note" in entries[0]["message"]


def test_status(playground_dir):
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "pics")
    dest = pathlib.Path(playground_dir) / "checkout" / "pics"
    checkout_niche(playground_dir, PARTICIPANT, TEAM, "pics", str(dest))

    # Create a file — should show as untracked
    (dest / "cat.jpg").write_bytes(b"not really a jpeg")
    entries = status(playground_dir, PARTICIPANT, TEAM, "pics")
    assert any(e["path"] == "cat.jpg" for e in entries)

    # Publish it — status should be clean
    publish(playground_dir, PARTICIPANT, TEAM, "pics", message="add cat")
    entries = status(playground_dir, PARTICIPANT, TEAM, "pics")
    assert len(entries) == 0


def test_selective_publish(playground_dir):
    _init(playground_dir)
    create_niche(playground_dir, PARTICIPANT, TEAM, "mixed")
    dest = pathlib.Path(playground_dir) / "checkout" / "mixed"
    checkout_niche(playground_dir, PARTICIPANT, TEAM, "mixed", str(dest))

    (dest / "a.txt").write_text("aaa")
    (dest / "b.txt").write_text("bbb")

    # Publish only a.txt
    publish(playground_dir, PARTICIPANT, TEAM, "mixed", files=["a.txt"], message="only a")

    entries = status(playground_dir, PARTICIPANT, TEAM, "mixed")
    paths = [e["path"] for e in entries]
    assert "b.txt" in paths
    assert "a.txt" not in paths

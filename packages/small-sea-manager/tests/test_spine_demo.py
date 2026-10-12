"""The demo runs end to end and prints the verifier's explanation."""

import subprocess
import sys


def test_the_demo_runs_and_ends_with_a_valid_bundle(tmp_path):
    done = subprocess.run(
        [sys.executable, "-m", "small_sea_manager.spine.demo", "--dir", str(tmp_path)],
        capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    out = done.stdout
    assert "c refuses: Constitution publishing paused: conflict" in out
    assert "owner must choose about: R" in out
    assert "R is invalid: refused by this device's owner" in out
    assert "VALID: the history up to" in out
    assert "This history produces: Alice: a; Carol: c, c2, c3" in out


def test_nonempty_directory_is_refused_without_changing_its_contents(tmp_path):
    bundle = tmp_path / "bundle.json"
    original = b"existing research result\n"
    bundle.write_bytes(original)
    done = subprocess.run(
        [sys.executable, "-m", "small_sea_manager.spine.demo", "--dir", str(tmp_path)],
        capture_output=True, text=True)
    assert done.returncode == 2
    assert "is not empty; choose an empty directory" in done.stderr
    assert "Traceback" not in done.stderr
    assert bundle.read_bytes() == original
    assert list(tmp_path.iterdir()) == [bundle]

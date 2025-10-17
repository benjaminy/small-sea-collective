import os
import subprocess

def test_path():
    print(f"PATH: {os.environ['PATH']}")

    # Try to find the homebrew program
    result = subprocess.run(
        ["which", "minio"],
        capture_output=True,
        text=True)
    print(f"Program location: {result.stdout}")
    assert result.returncode == 0
    assert False

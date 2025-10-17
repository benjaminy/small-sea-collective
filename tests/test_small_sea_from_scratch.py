# Top Matter

import os
import subprocess
import tempfile
import shutil
import pytest
import pathlib
import time

def tester(hub_server_gen):
    local_dir, proc = temp_env
    print( f"SDF {local_dir}" )
    user_cmd = ["python3", "small_sea_tui.py", "--nickname", "Alice", "new_user"]
    user_result = subprocess.run(user_cmd, cwd="../Source")
    assert(0 == user_result.returncode)
    session_cmd = ["python3", "small_sea_tui.py", "--nickname", "Alice", "start_user_session"]
    session_result = subprocess.run(session_cmd, cwd="../Source")
    assert(0 == session_result.returncode)
    test2_file = local_dir / "snerp.txt"
    with open( test2_file, "r") as f:
        hello = f.read()
        assert("HELLO WORLD" == hello)
    # raise Exception()


if __name__ == "__main__":
    tester( temp_env )

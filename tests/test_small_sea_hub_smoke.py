# Top Matter

import os
import sys

print(sys.path)

import subprocess
import tempfile
import shutil
import pytest
import pathlib
import time

import small_sea_client.client as SmallSea

def test_create_participant(hub_server_gen):
    hub_server = hub_server_gen()
    small_sea = SmallSea.SmallSeaClient()
    small_sea.create_new_participant("alice")

def test_add_cloud(hub_server_gen, minio_server_gen):
    hub_port = 9876
    hub_server = hub_server_gen(port=hub_port)
    storage_server = minio_server_gen()
    small_sea = SmallSea.SmallSeaClient(hub_port)
    small_sea.create_new_participant("alice")
    session = small_sea.open_session("alice", "SmallSeaCore", "NoteToSelf", "SmallSeaTestSuite")
    session.add_cloud_location("s3", f"localhost:{storage_server['port']}")

# def tester(hub_server_gen):
#     local_dir, proc = temp_env
#     print( f"SDF {local_dir}" )
#     user_cmd = ["python3", "small_sea_tui.py", "--nickname", "Alice", "new_user"]
#     user_result = subprocess.run(user_cmd, cwd="../Source")
#     assert(0 == user_result.returncode)
#     session_cmd = ["python3", "small_sea_tui.py", "--nickname", "Alice", "start_user_session"]
#     session_result = subprocess.run(session_cmd, cwd="../Source")
#     assert(0 == session_result.returncode)
#     test2_file = local_dir / "snerp.txt"
#     with open( test2_file, "r") as f:
#         hello = f.read()
#         assert("HELLO WORLD" == hello)
#     # raise Exception()


# if __name__ == "__main__":
#     tester( temp_env )

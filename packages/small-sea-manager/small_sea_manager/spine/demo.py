"""Run the slice 1 story and print what each device sees after each step.

    uv run python -m small_sea_manager.spine.demo --dir DIR

DIR holds the devices' databases and the exported bundle; it must be empty or
not yet exist. After the last step, the verifier runs as a separate process
on c's bundle and its explanation is printed.

Object IDs depend on the clock, so they change from run to run. The demo prints
story names (G, P, X, Y, R, c3) and a legend from short IDs to those names.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from . import explain
from .device import Refused
from .scenario import Scenario

STEPS = [
    ("step1_carol_adds_phone", "1. Carol adds phone c2 as P, delivered to everyone."),
    ("step2_alice_removes_bob_and_bob_adds_device",
     "2. Alice removes Bob (X) while Bob adds device b2 (Y); neither is delivered."),
    ("step3a_carol_gets_bobs_device", "3a. c receives Y."),
    ("step3b_everyone_sees_both", "3b. c and b receive X, a receives Y, and c receives X again."),
    ("step4_carol_tries_tablet", "4. Carol tries to add tablet c3."),
    ("step5_alice_reconciles", "5. Alice signs R: keep X, reject Y."),
    ("step6a_bob_and_carol_get_r", "6a. b and c receive R."),
    ("step6b_carol_accepts_bob_refuses", "6b. Carol accepts R; Bob refuses it."),
    ("step7_carol_adds_tablet", "7. Carol adds c3 on top of R, and a receives it."),
    ("step8_carol_exports", "8. c exports a bundle with tip c3."),
]


def device_lines(s, label):
    v = s.devices[label].view()
    names = lambda ids: ", ".join(sorted(s.names[i] for i in ids))
    lines = [f"  {label}: {v['classification']} at {names(v['heads'])}",
             f"     acting on: {explain.describe_state(v['acting_state'])}",
             f"     may publish: {', '.join(v['may_publish'])}"]
    if v["contested"]:
        lines.append(f"     owner must choose about: {names(v['contested'])}")
    for object_id, d in v["objects"].items():
        if d["status"] != "valid":
            lines.append(f"     {s.names[object_id]} is {d['status']}: {d['reason']}")
    return lines


def run(directory: Path) -> int:
    s = Scenario(directory)
    for method, title in STEPS:
        print(title)
        try:
            getattr(s, method)()
        except Refused as e:
            print(f"  c refuses: {e}")
        if method != "step8_carol_exports":
            for label in s.devices:
                print("\n".join(device_lines(s, label)))
        print()

    print("Legend: " + ", ".join(f"{explain.short(i)} = {name}" for i, name in s.names.items()))
    print(f"Running the verifier on {s.bundle_path}, with G's ID passed separately:\n")
    done = subprocess.run(
        [sys.executable, "-m", "small_sea_manager.spine.verify",
         "--origin", s.origin, str(s.bundle_path)],
        capture_output=True, text=True)
    print(done.stdout, end="")
    print(done.stderr, end="", file=sys.stderr)
    return done.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the slice 1 spine story.")
    parser.add_argument("--dir", required=True, help="directory for device databases and the bundle")
    args = parser.parse_args(argv)
    directory = Path(args.dir)
    if directory.exists() and any(directory.iterdir()):
        parser.error(f"{directory} is not empty; choose an empty directory")
    directory.mkdir(parents=True, exist_ok=True)
    return run(directory)


if __name__ == "__main__":
    sys.exit(main())

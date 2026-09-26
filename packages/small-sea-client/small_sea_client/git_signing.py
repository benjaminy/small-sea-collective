"""Git's SSH signing program, backed by the current Hub session."""

import base64
import os
import pathlib
import sys

import httpx

from cod_sync.work_context import context_from_commit_bytes


def _key_material(key: str) -> tuple[str, str]:
    fields = key.split()
    if len(fields) < 2:
        raise ValueError("invalid SSH public key")
    return fields[0], fields[1]


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 2 and args[0] == "-Y" and args[1] in (
        "verify", "find-principals", "check-novalidate"
    ):
        try:
            os.execvp("ssh-keygen", ["ssh-keygen", *args])
        except OSError as exc:
            print(f"small-sea-git-sign: {exc}", file=sys.stderr)
            return 1

    if not (len(args) in (7, 8) and args[:5] == ["-Y", "sign", "-n", "git", "-f"]
            and (len(args) == 7 or args[6] == "-U")):
        print("small-sea-git-sign: unsupported Git signing invocation", file=sys.stderr)
        return 2

    try:
        key_path = pathlib.Path(args[5])
        payload_path = pathlib.Path(args[-1])
        payload = payload_path.read_bytes()
        purpose = context_from_commit_bytes(payload, unsigned=True).purpose
        hub_url = os.environ["SMALL_SEA_HUB_URL"].rstrip("/")
        token = os.environ["SMALL_SEA_SESSION_TOKEN"]
        response = httpx.post(
            f"{hub_url}/session/sign",
            headers={"Authorization": f"Bearer {token}"},
            json={"purpose": purpose, "payload": base64.b64encode(payload).decode("ascii")},
        )
        response.raise_for_status()
        result = response.json()
        if _key_material(result["public_key"]) != _key_material(key_path.read_text()):
            raise ValueError("Hub signing key does not match Git's requested key")
        pathlib.Path(str(payload_path) + ".sig").write_text(result["signature"])
        return 0
    except (OSError, KeyError, TypeError, ValueError, httpx.HTTPError) as exc:
        print(f"small-sea-git-sign: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

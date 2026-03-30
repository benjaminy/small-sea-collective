"""Create a minimal two-participant workspace for Dropbox integration testing.

Sequence:
  1. Create Alice's account, authorise with Dropbox
  2. Create Bob's account, authorise with Dropbox
  3. Alice creates a team, pushes to her Dropbox folder, and creates an invitation
  4. Bob accepts the invitation (clones from Alice's folder, pushes to his own)
  5. Alice completes the acceptance

Each participant gets their own Dropbox folder: ss-{member_id_hex[:16]}/
This avoids collisions when both participants share a single Dropbox app account.

Usage:
    uv run python scripts/setup_dropbox_workspace.py
    uv run python scripts/setup_dropbox_workspace.py --workspace Scratch/DropboxTest
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path

_repo_root = Path(__file__).parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

_scripts = Path(__file__).parent
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

import cod_sync.CodSync as CodSync
import setup_dropbox_auth as _auth
from dropbox_remote import DropboxCodSyncRemote
from small_sea_manager.provisioning import (
    accept_invitation,
    add_cloud_storage,
    complete_invitation_acceptance,
    create_invitation,
    create_new_participant,
    create_team,
    uuid7,
)

_DEFAULT_WORKSPACE = "Scratch/DropboxTest"
_TEAM_NAME = "DropboxTestTeam"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_and_auth(workspace: Path, nickname: str, app_key: str, app_secret: str):
    """Create a participant and immediately configure Dropbox credentials.

    Returns (participant_hex, access_token).
    """
    print(f"\nCreating participant {nickname}...")
    participant_hex = create_new_participant(workspace, nickname)
    print(f"  {nickname}: {participant_hex[:16]}...")

    print(f"Dropbox auth for {nickname} — opening browser...")
    import webbrowser
    auth_url = _auth._build_auth_url(app_key)
    webbrowser.open(auth_url)
    print(f"Waiting for redirect on {_auth._REDIRECT_URI} ...")
    code = _auth._capture_code()
    print("Code received. Exchanging for tokens...")
    tokens = _auth._exchange_code(app_key, app_secret, code)

    add_cloud_storage(
        workspace,
        participant_hex,
        protocol="dropbox",
        url="https://www.dropboxapi.com",
        client_id=app_key,
        client_secret=app_secret,
        refresh_token=tokens["refresh_token"],
        access_token=tokens["access_token"],
        token_expiry=tokens["token_expiry"],
    )
    print(f"  Dropbox credentials stored for {nickname}.")
    return participant_hex, tokens["access_token"]


def _push_team_repo(workspace: Path, participant_hex: str, team_name: str, dropbox_remote):
    """Push the team's Sync repo to Dropbox."""
    sync_dir = workspace / "Participants" / participant_hex / team_name / "Sync"
    saved_cwd = os.getcwd()
    os.chdir(sync_dir)
    try:
        cod = CodSync.CodSync("cloud")
        cod.remote = dropbox_remote
        cod.push_to_remote(["main"])
    finally:
        os.chdir(saved_cwd)


def _setup_team(
    workspace: Path,
    alice_hex: str,
    alice_access_token: str,
    bob_hex: str,
    bob_access_token: str,
):
    print(f"\nCreating team '{_TEAM_NAME}' for Alice...")
    team_result = create_team(workspace, alice_hex, _TEAM_NAME)
    alice_member_id_hex = team_result["member_id_hex"]
    alice_bucket = f"ss-{alice_member_id_hex[:16]}"
    print(f"  Alice member_id: {alice_member_id_hex[:16]}... bucket: {alice_bucket}")

    alice_remote = DropboxCodSyncRemote(alice_access_token, folder_prefix=alice_bucket)

    print("  Alice pushing initial team repo to Dropbox...")
    _push_team_repo(workspace, alice_hex, _TEAM_NAME, alice_remote)

    print("  Alice creating invitation for Bob...")
    token_b64 = create_invitation(
        workspace, alice_hex, _TEAM_NAME,
        {"protocol": "dropbox", "url": "https://www.dropboxapi.com"},
        invitee_label="Bob",
    )

    # Re-push after invitation commit
    print("  Alice re-pushing after invitation commit...")
    _push_team_repo(workspace, alice_hex, _TEAM_NAME, alice_remote)

    # Decode token to get Alice's bucket (should match alice_bucket)
    token_data = json.loads(base64.b64decode(token_b64).decode())
    assert token_data["inviter_bucket"] == alice_bucket, (
        f"Unexpected inviter_bucket: {token_data['inviter_bucket']!r} vs {alice_bucket!r}"
    )

    # Pre-generate Bob's member_id so we can construct his Dropbox remote with the
    # correct folder prefix before calling accept_invitation.
    bob_member_id = uuid7()
    bob_bucket = f"ss-{bob_member_id.hex()[:16]}"
    print(f"  Bob member_id: {bob_member_id.hex()[:16]}... bucket: {bob_bucket}")

    # Bob reads from Alice's folder using his own token; writes to his own folder.
    inviter_remote = DropboxCodSyncRemote(bob_access_token, folder_prefix=alice_bucket)
    bob_remote = DropboxCodSyncRemote(bob_access_token, folder_prefix=bob_bucket)

    print("  Bob accepting invitation (clone + push)...")
    acceptance_b64 = accept_invitation(
        workspace, bob_hex, token_b64,
        inviter_remote=inviter_remote,
        acceptor_remote=bob_remote,
        acceptor_member_id=bob_member_id,
    )
    acceptance = json.loads(base64.b64decode(acceptance_b64).decode())
    print(f"  Bob accepted — acceptor_bucket: {acceptance['acceptor_bucket']}")

    print("  Alice completing acceptance...")
    complete_invitation_acceptance(workspace, alice_hex, _TEAM_NAME, acceptance_b64)

    # Alice pushes updated team repo (now includes Bob as member/peer)
    print("  Alice re-pushing after Bob joined...")
    _push_team_repo(workspace, alice_hex, _TEAM_NAME, alice_remote)

    print("  Team setup complete.")
    return alice_member_id_hex, bob_member_id.hex()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap a two-participant Dropbox integration test workspace."
    )
    parser.add_argument(
        "--workspace",
        default=os.environ.get("SMALL_SEA_DROPBOX_WORKSPACE", _DEFAULT_WORKSPACE),
        help=f"Workspace directory (default: {_DEFAULT_WORKSPACE})",
    )
    parser.add_argument(
        "--app-key",
        default=os.environ.get("SMALL_SEA_DROPBOX_APP_KEY", "pz516qwo0t7z8dl"),
    )
    parser.add_argument(
        "--app-secret",
        default=os.environ.get("SMALL_SEA_DROPBOX_APP_SECRET", "r63gynrdi1a325r"),
    )
    parser.add_argument(
        "--skip-team",
        action="store_true",
        help="Stop after creating participants (no team or invitation flow).",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()

    if workspace.exists() and any(workspace.iterdir()):
        print(f"Workspace already exists and is not empty: {workspace}")
        print("Delete it or choose a different --workspace path.")
        sys.exit(1)

    workspace.mkdir(parents=True, exist_ok=True)

    alice_hex, alice_token = _create_and_auth(workspace, "Alice", args.app_key, args.app_secret)
    bob_hex, bob_token = _create_and_auth(workspace, "Bob", args.app_key, args.app_secret)

    if not args.skip_team:
        alice_member_id_hex, bob_member_id_hex = _setup_team(
            workspace, alice_hex, alice_token, bob_hex, bob_token
        )

    print(f"\nWorkspace ready: {workspace}")
    print(f"  Alice: {alice_hex}")
    print(f"  Bob:   {bob_hex}")
    if not args.skip_team:
        print(f"  Alice member_id: {alice_member_id_hex}")
        print(f"  Bob   member_id: {bob_member_id_hex}")


if __name__ == "__main__":
    main()

"""Sandbox workspace: load/save sandbox.json, add participants, cloud storage setup."""

import json
import pathlib
import secrets
import shutil
import socket
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, field

from small_sea_manager.provisioning import create_new_participant, uuid7


SANDBOX_JSON = "sandbox.json"


@dataclass
class MinioConfig:
    api_port: int
    console_port: int
    root_user: str
    root_password: str


@dataclass
class ParticipantConfig:
    hex: str
    nickname: str
    hub_port: int
    manager_port: int


@dataclass
class SandboxWorkspace:
    workspace_dir: pathlib.Path
    minio: MinioConfig
    participants: list = field(default_factory=list)  # list[ParticipantConfig]

    def save(self):
        data = {
            "minio": asdict(self.minio),
            "participants": [asdict(p) for p in self.participants],
        }
        (self.workspace_dir / SANDBOX_JSON).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, workspace_dir: pathlib.Path) -> "SandboxWorkspace":
        workspace_dir = pathlib.Path(workspace_dir)
        path = workspace_dir / SANDBOX_JSON
        if path.exists():
            data = json.loads(path.read_text())
            minio = MinioConfig(**data["minio"])
            participants = [ParticipantConfig(**p) for p in data.get("participants", [])]
        else:
            minio = MinioConfig(
                api_port=9000,
                console_port=9001,
                root_user="sandboxadmin",
                root_password=secrets.token_urlsafe(16),
            )
            participants = []
        ws = cls(workspace_dir=workspace_dir, minio=minio, participants=participants)
        ws.save()
        return ws

    def _next_hub_port(self) -> int:
        used = {p.hub_port for p in self.participants}
        port = 11437
        while port in used:
            port += 1
        return port

    def _next_manager_port(self) -> int:
        used = {p.manager_port for p in self.participants}
        port = 8001
        while port in used:
            port += 1
        return port

    def add_participant(self, nickname: str) -> ParticipantConfig:
        hub_port = self._next_hub_port()
        manager_port = self._next_manager_port()
        participant_hex = create_new_participant(self.workspace_dir, nickname)
        _setup_cloud_storage(
            self.workspace_dir,
            participant_hex,
            minio_url=f"http://localhost:{self.minio.api_port}",
            access_key=self.minio.root_user,
            secret_key=self.minio.root_password,
        )
        p = ParticipantConfig(
            hex=participant_hex,
            nickname=nickname,
            hub_port=hub_port,
            manager_port=manager_port,
        )
        self.participants.append(p)
        self.save()
        return p


def _setup_cloud_storage(root_dir, participant_hex, minio_url, access_key, secret_key):
    """Write a cloud_storage row directly to the participant's NoteToSelf DB."""
    root_dir = pathlib.Path(root_dir)
    db_path = (
        root_dir / "Participants" / participant_hex / "NoteToSelf" / "Sync" / "core.db"
    )
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO cloud_storage (id, protocol, url, access_key, secret_key) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid7(), "s3", minio_url, access_key, secret_key),
        )
        conn.commit()
    finally:
        conn.close()


def create_temp_workspace() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(prefix="small-sea-sandbox-"))


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def minio_available() -> bool:
    return shutil.which("minio") is not None

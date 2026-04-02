"""Sandbox workspace: load/save sandbox.json, add participants, MinIO servers/accounts."""

import json
import pathlib
import secrets
import shutil
import socket
import tempfile
from dataclasses import asdict, dataclass, field

from small_sea_manager.provisioning import create_new_participant, uuid7


SANDBOX_JSON = "sandbox.json"


@dataclass
class MinioServerConfig:
    api_port: int
    console_port: int
    root_user: str
    root_password: str


@dataclass
class MinioAccountConfig:
    server_port: int
    label: str
    access_key: str
    secret_key: str


@dataclass
class ParticipantConfig:
    hex: str
    nickname: str
    hub_port: int
    manager_port: int


@dataclass
class SandboxWorkspace:
    workspace_dir: pathlib.Path
    minio_servers: list = field(default_factory=list)   # list[MinioServerConfig]
    minio_accounts: list = field(default_factory=list)  # list[MinioAccountConfig]
    participants: list = field(default_factory=list)    # list[ParticipantConfig]

    def save(self):
        data = {
            "minio_servers": [asdict(s) for s in self.minio_servers],
            "minio_accounts": [asdict(a) for a in self.minio_accounts],
            "participants": [asdict(p) for p in self.participants],
        }
        (self.workspace_dir / SANDBOX_JSON).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, workspace_dir: pathlib.Path) -> "SandboxWorkspace":
        workspace_dir = pathlib.Path(workspace_dir)
        path = workspace_dir / SANDBOX_JSON
        if path.exists():
            data = json.loads(path.read_text())
            # Migrate old format: {"minio": {...}} → {"minio_servers": [{...}]}
            if "minio" in data and "minio_servers" not in data:
                data["minio_servers"] = [data.pop("minio")]
                data.setdefault("minio_accounts", [])
            minio_servers = [MinioServerConfig(**s) for s in data.get("minio_servers", [])]
            minio_accounts = [MinioAccountConfig(**a) for a in data.get("minio_accounts", [])]
            participants = [ParticipantConfig(**p) for p in data.get("participants", [])]
        else:
            minio_servers = []
            minio_accounts = []
            participants = []
        ws = cls(
            workspace_dir=workspace_dir,
            minio_servers=minio_servers,
            minio_accounts=minio_accounts,
            participants=participants,
        )
        ws.save()
        return ws

    # ------------------------------------------------------------------ #
    # Port allocation
    # ------------------------------------------------------------------ #

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

    def _next_minio_ports(self) -> tuple:
        used_api = {s.api_port for s in self.minio_servers}
        used_console = {s.console_port for s in self.minio_servers}
        api_port = 9000
        while api_port in used_api or api_port in used_console:
            api_port += 2
        console_port = api_port + 1
        while console_port in used_console or console_port in used_api:
            console_port += 2
        return api_port, console_port

    # ------------------------------------------------------------------ #
    # MinIO servers
    # ------------------------------------------------------------------ #

    def add_minio_server(self) -> MinioServerConfig:
        api_port, console_port = self._next_minio_ports()
        server = MinioServerConfig(
            api_port=api_port,
            console_port=console_port,
            root_user="sandboxadmin",
            root_password=secrets.token_urlsafe(16),
        )
        self.minio_servers.append(server)
        self.save()
        return server

    # ------------------------------------------------------------------ #
    # MinIO accounts (per-user IAM credentials)
    # ------------------------------------------------------------------ #

    def create_account(self, server_port: int, label: str) -> MinioAccountConfig:
        """Create a MinIO IAM user on the given server and return its credentials.

        In MinIO's IAM model the access_key IS the username; the secret_key is
        the password we generate.  We attach the built-in 'readwrite' policy so
        the Hub can create buckets and upload objects.
        """
        server = next((s for s in self.minio_servers if s.api_port == server_port), None)
        if server is None:
            raise ValueError(f"No MinIO server on port {server_port}")

        from minio.credentials import StaticProvider
        from minio.minioadmin import MinioAdmin

        admin = MinioAdmin(
            endpoint=f"localhost:{server.api_port}",
            credentials=StaticProvider(server.root_user, server.root_password),
            secure=False,
        )
        secret_key = secrets.token_urlsafe(24)
        try:
            admin.user_add(label, secret_key)
            admin.attach_policy(["readwrite"], user=label)
        except Exception as exc:
            msg = str(exc)
            if "Connection refused" in msg or "Failed to establish" in msg:
                raise RuntimeError(
                    f"Cannot reach MinIO on port {server.api_port} — is the server running?"
                ) from exc
            raise

        account = MinioAccountConfig(
            server_port=server_port,
            label=label,
            access_key=label,   # MinIO access_key == username
            secret_key=secret_key,
        )
        self.minio_accounts.append(account)
        self.save()
        return account

    # ------------------------------------------------------------------ #
    # Participants
    # ------------------------------------------------------------------ #

    def add_participant(self, nickname: str) -> ParticipantConfig:
        """Create a new participant. Cloud storage must be configured separately."""
        hub_port = self._next_hub_port()
        manager_port = self._next_manager_port()
        participant_hex = create_new_participant(self.workspace_dir, nickname)
        p = ParticipantConfig(
            hex=participant_hex,
            nickname=nickname,
            hub_port=hub_port,
            manager_port=manager_port,
        )
        self.participants.append(p)
        self.save()
        return p


def create_temp_workspace() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(prefix="small-sea-sandbox-"))


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def minio_available() -> bool:
    return shutil.which("minio") is not None

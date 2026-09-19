"""Settings and helpers shared by the local-delegate scripts.

All settings come from environment variables:

  BONSAI_JOBS_DIR          required; job folders, logs and server records live here
  BONSAI_HOME              the Bonsai demo folder holding scripts/start_llama_server.sh
                           (needed only to start the server)
  BONSAI_PORT              default 8080
  BONSAI_MODEL             server alias and Pi model id, default bonsai2
  BONSAI_CTX               context window in tokens, default 32768
  BONSAI_REASONING_BUDGET  default 512
  PI_BIN                   default pi
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

SENTINEL = "END-OF-RESULT"

PORT = int(os.environ.get("BONSAI_PORT", "8080"))
MODEL = os.environ.get("BONSAI_MODEL", "bonsai2")
CTX = int(os.environ.get("BONSAI_CTX", "32768"))
REASONING_BUDGET = os.environ.get("BONSAI_REASONING_BUDGET", "512")
PI_BIN = os.environ.get("PI_BIN", "pi")
BASE_URL = f"http://127.0.0.1:{PORT}/v1"

# Removed from Pi's environment so it cannot fall back to a paid cloud model.
CLOUD_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)


def jobs_dir():
    value = os.environ.get("BONSAI_JOBS_DIR")
    if not value:
        sys.exit("BONSAI_JOBS_DIR is not set")
    path = Path(value).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def server_models():
    """Return the model ids the server reports, or None if it does not answer."""
    try:
        with urllib.request.urlopen(f"{BASE_URL}/models", timeout=5) as response:
            body = json.load(response)
    except (OSError, ValueError):
        return None
    return [m.get("id") for m in body.get("data", [])]


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + "\n")


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

"""Low-level Git subprocess execution.

Lives below every other Cod Sync module so that repo.py, protocol.py, and
outside callers can share one Git entry point without a dependency cycle.
"""

import logging
import subprocess
from collections.abc import Mapping

logger = logging.getLogger("cod_sync")


class GitCmdFailed(Exception):
    def __init__(self, params, exit_code, out, err):
        self.params = params
        self.exit_code = exit_code
        self.out = out
        self.err = err

    def __str__(self):
        return f"ERROR. git cmd failed. `git {' '.join(self.params)}` => {self.exit_code}. o:'{self.out}' e:'{self.err}'"


def gitCmd(git_params, raise_on_error=True, env=None):
    return _run(git_params, text=True, raise_on_error=raise_on_error, env=env)


def gitCmdBinary(git_params, raise_on_error=True, env=None):
    """Run a git command whose stdout must survive as exact bytes.

    Blob contents and path names are not text: decoding them would corrupt a
    SQLite file and mangle a path that is not valid UTF-8. stderr is still
    decoded, because it only ever carries git's own diagnostics.
    """
    return _run(git_params, text=False, raise_on_error=raise_on_error, env=env)


def _run(git_params, *, text, raise_on_error, env):
    git_cmd = ["git"] + git_params
    result = subprocess.run(git_cmd, capture_output=True, text=text, env=env)
    if result.returncode != 0:
        stderr = result.stderr if text else result.stderr.decode("utf-8", "replace")
        exn = GitCmdFailed(git_params, result.returncode, result.stdout, stderr)
        if raise_on_error:
            raise exn
        else:
            logger.debug(str(exn))
    return result


def signing_git_env(base_env: Mapping[str, str], *, program: str,
                    public_key: str, extra_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a child environment with SSH commit signing configured for Git."""
    env = dict(base_env)
    env.update(extra_env or {})
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    for key, value in (("gpg.format", "ssh"), ("gpg.ssh.program", program),
                       ("user.signingkey", f"key::{public_key}"),
                       ("commit.gpgsign", "true")):
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env

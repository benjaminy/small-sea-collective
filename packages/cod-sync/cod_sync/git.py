"""Low-level Git subprocess execution.

Lives below every other Cod Sync module so that repo.py, protocol.py, and
outside callers can share one Git entry point without a dependency cycle.
"""

import logging
import subprocess

logger = logging.getLogger("cod_sync")


class GitCmdFailed(Exception):
    def __init__(self, params, exit_code, out, err):
        self.params = params
        self.exit_code = exit_code
        self.out = out
        self.err = err

    def __str__(self):
        return f"ERROR. git cmd failed. `git {' '.join(self.params)}` => {self.exit_code}. o:'{self.out}' e:'{self.err}'"


def gitCmd(git_params, raise_on_error=True):
    git_cmd = ["git"] + git_params
    result = subprocess.run(git_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        exn = GitCmdFailed(git_params, result.returncode, result.stdout, result.stderr)
        if raise_on_error:
            raise exn
        else:
            logger.debug(str(exn))
    return result

"""Shared test utilities for Hub readiness polling.

Extracted from conftest.py so it can be imported by name without pytest's
conftest resolution picking up the wrong file from a sub-package.
"""

import subprocess
import time

import httpx


def _wait_for_hub_ready(proc, url, startup_timeout=5.0):
    """Poll url until the server is ready, the process dies, or the deadline is hit.

    Raises RuntimeError in all failure cases.  Returns normally when the
    server responds with a 2xx or 3xx status.
    """
    deadline = time.monotonic() + startup_timeout
    while True:
        if proc.poll() is not None:
            raise RuntimeError(f"Small Sea Hub exited early (code {proc.returncode})")
        try:
            resp = httpx.get(url, timeout=0.25)
            if resp.is_success or resp.is_redirect:
                return
            raise RuntimeError(
                f"Hub readiness probe got unexpected status {resp.status_code} from {url}"
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.ReadTimeout):
            pass
        if time.monotonic() >= deadline:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
            raise RuntimeError(
                f"Hub at {url} did not become ready within {startup_timeout}s"
            )
        time.sleep(0.03)

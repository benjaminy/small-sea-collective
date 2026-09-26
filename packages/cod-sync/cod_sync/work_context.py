"""Encode the scope that a work signature commits to.

Put a commit context in one ``Small-Sea-Work-Context`` message trailer. Git's
SSH commit signature covers the commit message, including this trailer, so a
second signature is unnecessary. A publication link can carry the same token
in its signed extensions; its existing signature covers those extensions.
The caller must verify the signature before trusting a decoded context.
"""

import base64
import json
from dataclasses import dataclass


TRAILER = b"Small-Sea-Work-Context: "
ORIGINS = frozenset({"commit", "publication-link"})
PURPOSES = frozenset({"files-content", "files-registry", "files-merge", "core", "note-to-self"})
_FIELDS = ("version", "origin", "team_id", "berth_id", "purpose", "authority_view")


class WorkContextError(ValueError):
    """The context is missing, duplicated, or malformed."""


@dataclass(frozen=True)
class WorkContext:
    origin: str
    team_id: str
    berth_id: str
    purpose: str
    authority_view: bytes


def encode_work_context(context: WorkContext) -> str:
    """Return a canonical, unpadded base64url token for version 1."""
    if context.origin not in ORIGINS or context.purpose not in PURPOSES:
        raise WorkContextError("unknown origin or purpose")
    if not all(isinstance(value, str) and value for value in (context.team_id, context.berth_id)):
        raise WorkContextError("team and berth IDs must be nonempty strings")
    if not isinstance(context.authority_view, bytes):
        raise WorkContextError("authority view must be bytes")
    payload = dict(version=1, origin=context.origin, team_id=context.team_id,
                   berth_id=context.berth_id, purpose=context.purpose,
                   authority_view=base64.urlsafe_b64encode(context.authority_view).decode().rstrip("="))
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_work_context(token: str) -> WorkContext:
    """Reject noncanonical tokens, duplicate or missing fields, and unknown versions."""
    if not isinstance(token, str) or not token or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in token):
        raise WorkContextError("invalid context token")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))

        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise WorkContextError(f"duplicate field: {key}")
                result[key] = value
            return result

        payload = json.loads(raw.decode("ascii"), object_pairs_hook=unique_pairs)
        if not isinstance(payload, dict) or set(payload) != set(_FIELDS):
            raise WorkContextError("missing or unknown context fields")
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise WorkContextError("unsupported context version")
        view_token = payload["authority_view"]
        if not isinstance(view_token, str):
            raise WorkContextError("invalid authority view")
        view = base64.urlsafe_b64decode(view_token + "=" * (-len(view_token) % 4))
        context = WorkContext(payload["origin"], payload["team_id"],
                              payload["berth_id"], payload["purpose"], view)
        if encode_work_context(context) != token:
            raise WorkContextError("noncanonical context")
        return context
    except (ValueError, UnicodeError, KeyError, TypeError) as exc:
        if isinstance(exc, WorkContextError):
            raise
        raise WorkContextError("invalid context token") from exc


def commit_message_with_context(message: str, context: WorkContext) -> str:
    """Append the sole context trailer before Git signs a commit."""
    if context.origin != "commit":
        raise WorkContextError("commit requires commit origin")
    if any(line.startswith(TRAILER) for line in message.encode().splitlines()):
        raise WorkContextError("message already has a context trailer")
    return message.rstrip("\n") + "\n\n" + TRAILER.decode() + encode_work_context(context) + "\n"


def context_from_commit(repo, commit: str) -> WorkContext:
    """Read one context trailer from an original commit object; verify its signature separately."""
    raw = repo._run_binary(["cat-file", "commit", commit]).stdout
    return context_from_commit_bytes(raw)


def context_from_commit_bytes(raw: bytes, *, unsigned: bool = False) -> WorkContext:
    """Read the sole context from a git commit body, checking its header shape."""
    if not isinstance(raw, bytes):
        raise WorkContextError("invalid commit body")
    headers, separator, message = raw.partition(b"\n\n")
    lines = headers.split(b"\n")
    if not separator or not message or not lines[0].startswith(b"tree ") or len(lines[0]) != 45:
        raise WorkContextError("invalid commit body")
    try:
        int(lines[0][5:], 16)
    except ValueError as exc:
        raise WorkContextError("invalid commit tree") from exc
    if sum(line.startswith(b"author ") for line in lines) != 1 or sum(line.startswith(b"committer ") for line in lines) != 1:
        raise WorkContextError("invalid commit headers")
    if next(i for i, line in enumerate(lines) if line.startswith(b"author ")) >= next(
        i for i, line in enumerate(lines) if line.startswith(b"committer ")
    ):
        raise WorkContextError("invalid commit headers")
    for line in lines[1:]:
        if unsigned and (line.startswith(b"gpgsig ") or line.startswith(b"gpgsig-sha256 ")):
            raise WorkContextError("commit already signed")
        if not line or (not line.startswith(b" ") and (b" " not in line or not line.split(b" ", 1)[0].replace(b"-", b"").isalnum())):
            raise WorkContextError("invalid commit headers")
        if line.startswith(b"parent "):
            if len(line) != 47:
                raise WorkContextError("invalid commit parent")
            try:
                int(line[7:], 16)
            except ValueError as exc:
                raise WorkContextError("invalid commit parent") from exc
    trailers = [line[len(TRAILER):] for line in message.splitlines() if line.startswith(TRAILER)]
    if len(trailers) != 1:
        raise WorkContextError("commit must have exactly one context trailer")
    try:
        context = decode_work_context(trailers[0].decode("ascii"))
    except UnicodeError as exc:
        raise WorkContextError("invalid context trailer") from exc
    if context.origin != "commit":
        raise WorkContextError("wrong context origin")
    return context


def check_work_context(context: WorkContext, *, origin: str, team_id: str,
                       berth_id: str, purpose: str, authority_view: bytes) -> None:
    """Reject a signed context used outside its exact scope; dates are irrelevant.

    This compares the full context, including `authority_view`, which names
    the view the SIGNER selected. It is evidence only. Callers must not pass
    the verifier's own current view as the expected signer view: judge
    authority under the verifier's view separately (see
    small_sea_manager.berth_authority).
    """
    if context != WorkContext(origin, team_id, berth_id, purpose, authority_view):
        raise WorkContextError("work context does not match expected scope")

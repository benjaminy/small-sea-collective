from datetime import datetime, timezone, timedelta

import httpx


def is_token_expired(token_expiry: str | None) -> bool:
    """Return True if the token is expired or will expire within 5 minutes."""
    if token_expiry is None:
        return True
    expiry = datetime.fromisoformat(token_expiry)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expiry - timedelta(minutes=5)


def refresh_google_token(
        client_id: str,
        client_secret: str,
        refresh_token: str) -> tuple[str, str]:
    """Refresh a Google OAuth2 access token.

    Returns (access_token, expiry_iso) where expiry_iso is an ISO 8601 timestamp.
    """
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    resp.raise_for_status()
    body = resp.json()
    access_token = body["access_token"]
    expires_in = body.get("expires_in", 3600)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return access_token, expiry.isoformat()


def refresh_dropbox_token(
        client_id: str,
        client_secret: str,
        refresh_token: str) -> tuple[str, str]:
    """Refresh a Dropbox OAuth2 access token.

    Returns (access_token, expiry_iso) where expiry_iso is an ISO 8601 timestamp.
    """
    resp = httpx.post(
        "https://api.dropbox.com/oauth2/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    resp.raise_for_status()
    body = resp.json()
    access_token = body["access_token"]
    expires_in = body.get("expires_in", 14400)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return access_token, expiry.isoformat()

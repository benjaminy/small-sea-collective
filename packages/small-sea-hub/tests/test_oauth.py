import json
from datetime import datetime, timezone, timedelta

import httpx
import pytest
import respx

from small_sea_hub.adapters.oauth import (
    is_token_expired,
    refresh_google_token,
    refresh_dropbox_token,
)


# ---- is_token_expired ----

def test_expired_when_none():
    assert is_token_expired(None) is True


def test_expired_when_past():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert is_token_expired(past) is True


def test_expired_within_buffer():
    almost = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
    assert is_token_expired(almost) is True


def test_not_expired():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert is_token_expired(future) is False


# ---- refresh_google_token ----

@respx.mock
def test_refresh_google_token():
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "new-google-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        })
    )

    token, expiry = refresh_google_token("cid", "csecret", "refresh123")
    assert token == "new-google-token"
    assert expiry  # is an ISO string
    # Verify the expiry is roughly 1 hour from now
    exp_dt = datetime.fromisoformat(expiry)
    assert exp_dt > datetime.now(timezone.utc) + timedelta(minutes=50)


@respx.mock
def test_refresh_google_token_failure():
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(401, json={"error": "invalid_grant"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        refresh_google_token("cid", "csecret", "bad-refresh")


# ---- refresh_dropbox_token ----

@respx.mock
def test_refresh_dropbox_token():
    respx.post("https://api.dropbox.com/oauth2/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "new-dropbox-token",
            "expires_in": 14400,
            "token_type": "bearer",
        })
    )

    token, expiry = refresh_dropbox_token("cid", "csecret", "refresh456")
    assert token == "new-dropbox-token"
    exp_dt = datetime.fromisoformat(expiry)
    assert exp_dt > datetime.now(timezone.utc) + timedelta(hours=3)


@respx.mock
def test_refresh_dropbox_token_failure():
    respx.post("https://api.dropbox.com/oauth2/token").mock(
        return_value=httpx.Response(401, json={"error": "invalid_grant"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        refresh_dropbox_token("cid", "csecret", "bad-refresh")

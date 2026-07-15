"""OAuth token exchange, refresh, and storage for Zoho ERP."""
import json
import os
import time
from typing import Optional

import requests

from config import Config

TOKENS_FILE = "zoho_tokens.json"
EXPIRY_BUFFER_SECONDS = 300  # refresh if within 5 minutes of expiry


class AuthError(Exception):
    """Raised when a Zoho OAuth token request fails."""


def load_tokens() -> Optional[dict]:
    """Load stored tokens from zoho_tokens.json, or None if not present."""
    if not os.path.exists(TOKENS_FILE):
        return None
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tokens(tokens: dict) -> None:
    """Write tokens (access_token, refresh_token, expires_at) to zoho_tokens.json."""
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)


def _post_token_request(cfg: Config, data: dict) -> dict:
    """POST to {accounts_url}/oauth/v2/token and return the parsed JSON body.

    accounts_url is used as-is here -- never api_domain, and never
    /erp-normalized -- since the OAuth endpoint lives on Zoho's accounts
    host, not the ERP API host.
    """
    url = f"{cfg.accounts_url}/oauth/v2/token"
    try:
        response = requests.post(url, data=data, timeout=30)
    except requests.RequestException as exc:
        raise AuthError(f"Network error calling {url}: {exc}") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise AuthError(
            f"Zoho returned a non-JSON response (HTTP {response.status_code}): "
            f"{response.text[:500]}"
        ) from exc

    if "error" in body:
        raise AuthError(
            f"Zoho OAuth error: {body['error']}. If this is 'invalid_code' or "
            "'invalid_grant', your grant token has expired or was already used "
            "(grant tokens are one-time-use and expire in ~10 minutes) -- "
            "generate a new grant token and re-run token_exchange.py."
        )

    if response.status_code != 200 or "access_token" not in body:
        raise AuthError(
            f"Unexpected response from Zoho OAuth endpoint (HTTP {response.status_code}): {body}"
        )

    return body


def exchange_grant_token(cfg: Config) -> dict:
    """Exchange the one-time grant token for an access/refresh token pair."""
    body = _post_token_request(
        cfg,
        {
            "grant_type": "authorization_code",
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "redirect_uri": cfg.redirect_uri,
            "code": cfg.grant_token,
        },
    )
    tokens = {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token"),
        "expires_at": time.time() + body.get("expires_in", 3600),
    }
    if not tokens["refresh_token"]:
        raise AuthError(
            "Zoho did not return a refresh_token. This can happen if the grant "
            "token was already exchanged before, or the app is not configured "
            "for offline access. Generate a fresh grant token and try again."
        )
    save_tokens(tokens)
    return tokens


def refresh_access_token(cfg: Config) -> dict:
    """Use the stored refresh_token to obtain a new access_token."""
    existing = load_tokens()
    if not existing or not existing.get("refresh_token"):
        raise AuthError(
            "No refresh_token found in zoho_tokens.json. Run token_exchange.py "
            "first to complete the initial OAuth exchange."
        )

    body = _post_token_request(
        cfg,
        {
            "grant_type": "refresh_token",
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "refresh_token": existing["refresh_token"],
        },
    )
    tokens = {
        "access_token": body["access_token"],
        "refresh_token": existing["refresh_token"],
        "expires_at": time.time() + body.get("expires_in", 3600),
    }
    save_tokens(tokens)
    return tokens


def get_valid_access_token(cfg: Config) -> str:
    """Return a usable access token, refreshing it first if expired or about to expire."""
    tokens = load_tokens()
    if not tokens:
        raise AuthError(
            "No stored tokens found. Run token_exchange.py first to complete "
            "the initial OAuth exchange."
        )

    if tokens.get("expires_at", 0) - EXPIRY_BUFFER_SECONDS <= time.time():
        tokens = refresh_access_token(cfg)

    return tokens["access_token"]

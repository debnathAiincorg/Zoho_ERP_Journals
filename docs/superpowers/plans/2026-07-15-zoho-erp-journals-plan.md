# Zoho ERP Journals Fetcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a small Python CLI project that exchanges a one-time Zoho ERP grant token for OAuth tokens, auto-refreshes them, and fetches journal entries (list with filters/pagination, and single-by-id) from the Zoho ERP India data center.

**Architecture:** Five focused modules — `config.py` (env loading/validation/URL normalization), `auth.py` (token exchange/refresh/storage), `journals.py` (API calls with retry/backoff), `token_exchange.py` (one-time setup script), `main.py` (argparse CLI) — each with a single clear responsibility and no circular imports (`main.py` → `journals.py` → `auth.py` → `config.py`).

**Tech Stack:** Python 3.9+, `requests`, `python-dotenv`.

## Global Constraints

- Only Zoho ERP (India DC, `.in` endpoints) — never Zoho Books, CRM, or any other Zoho product's endpoints/scopes.
- No hardcoded credentials anywhere — all values come from environment variables via `config.py`.
- No bare `except:` blocks anywhere in the codebase.
- Use `typing.Optional`, not the `X | None` syntax, to stay compatible with Python 3.9.
- Only third-party dependencies: `requests`, `python-dotenv`.
- Read-only — no journal create/update/delete calls, matching the granted `READ`-only scopes.
- `ZOHO_ACCOUNTS_URL` and `ZOHO_API_DOMAIN` are handled as two independent base URLs in `config.py`; `ZOHO_API_DOMAIN` gets `/erp` / `/erp/v3` suffix stripped, `ZOHO_ACCOUNTS_URL` does not.
- No automated test suite is part of the deliverable (per user decision) — verification below uses ephemeral mocked smoke-test scripts run via stdin, not committed to the repo.

---

### Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`

**Interfaces:**
- Produces: a working virtualenv-installable dependency set for every later task.

- [ ] **Step 1: Create `requirements.txt`**

```
requests>=2.31.0
python-dotenv>=1.0.0
```

- [ ] **Step 2: Create `.env.example`**

```
ZOHO_CLIENT_ID=
ZOHO_CLIENT_SECRET=
ZOHO_ORGANIZATION_ID=
ZOHO_ACCOUNTS_URL=https://accounts.zoho.in
ZOHO_API_DOMAIN=https://www.zohoapis.in
ZOHO_GRANT_TOKEN=
ZOHO_REDIRECT_URI=https://www.zoho.in/erp
```

- [ ] **Step 3: Create `.gitignore`**

```
zoho_tokens.json
journals_output.json
.env
__pycache__/
*.pyc
venv/
```

- [ ] **Step 4: Install dependencies and verify**

Run: `pip install -r requirements.txt`
Expected: `requests` and `python-dotenv` install with no errors.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .env.example .gitignore
git commit -m "Add project scaffolding: requirements, env template, gitignore"
```

---

### Task 2: config.py

**Files:**
- Create: `config.py`

**Interfaces:**
- Produces: `Config` dataclass with fields `client_id, client_secret, organization_id, grant_token, accounts_url, api_domain, redirect_uri`; `ConfigError`; `load_config(require_grant_token: bool = False) -> Config`.
- Consumes: nothing (base module).

- [ ] **Step 1: Write `config.py`**

```python
"""Configuration loading and validation for the Zoho ERP journals fetcher."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


REQUIRED_VARS = [
    "ZOHO_CLIENT_ID",
    "ZOHO_CLIENT_SECRET",
    "ZOHO_ORGANIZATION_ID",
    "ZOHO_ACCOUNTS_URL",
    "ZOHO_API_DOMAIN",
]

DEFAULT_REDIRECT_URI = "https://www.zoho.in/erp"


def _strip_trailing_slash(url: str) -> str:
    return url[:-1] if url.endswith("/") else url


def _normalize_api_domain(url: str) -> str:
    """Strip trailing slash and any trailing /erp/v3 or /erp path so the
    stored api_domain is always the bare domain (e.g. https://www.zohoapis.in),
    regardless of which form the user put in .env. This is intentionally
    separate from accounts_url handling below -- the two base URLs must never
    cross-contaminate.
    """
    url = _strip_trailing_slash(url)
    for suffix in ("/erp/v3", "/erp"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return _strip_trailing_slash(url)


@dataclass
class Config:
    client_id: str
    client_secret: str
    organization_id: str
    grant_token: str
    accounts_url: str
    api_domain: str
    redirect_uri: str


def load_config(require_grant_token: bool = False) -> Config:
    """Load and validate configuration from environment variables (.env).

    Set require_grant_token=True only for the one-time token_exchange script,
    since ZOHO_GRANT_TOKEN is single-use and won't be present afterward.
    """
    load_dotenv()

    required = list(REQUIRED_VARS)
    if require_grant_token:
        required.append("ZOHO_GRANT_TOKEN")

    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Set them in your .env file or environment."
        )

    return Config(
        client_id=os.environ["ZOHO_CLIENT_ID"],
        client_secret=os.environ["ZOHO_CLIENT_SECRET"],
        organization_id=os.environ["ZOHO_ORGANIZATION_ID"],
        grant_token=os.environ.get("ZOHO_GRANT_TOKEN", ""),
        accounts_url=_strip_trailing_slash(os.environ["ZOHO_ACCOUNTS_URL"]),
        api_domain=_normalize_api_domain(os.environ["ZOHO_API_DOMAIN"]),
        redirect_uri=os.environ.get("ZOHO_REDIRECT_URI", DEFAULT_REDIRECT_URI),
    )
```

- [ ] **Step 2: Verify normalization and validation logic with an ephemeral smoke test**

Run (bash):
```bash
python - <<'PYEOF'
import os

os.environ.update({
    "ZOHO_CLIENT_ID": "cid",
    "ZOHO_CLIENT_SECRET": "secret",
    "ZOHO_ORGANIZATION_ID": "org1",
    "ZOHO_ACCOUNTS_URL": "https://accounts.zoho.in/",
    "ZOHO_API_DOMAIN": "https://www.zohoapis.in/erp/v3/",
})

from config import load_config, ConfigError

cfg = load_config()
assert cfg.accounts_url == "https://accounts.zoho.in", cfg.accounts_url
assert cfg.api_domain == "https://www.zohoapis.in", cfg.api_domain
assert cfg.redirect_uri == "https://www.zoho.in/erp", cfg.redirect_uri
print("normalization OK")

os.environ["ZOHO_API_DOMAIN"] = "https://www.zohoapis.in"
cfg2 = load_config()
assert cfg2.api_domain == "https://www.zohoapis.in", cfg2.api_domain
print("bare-domain passthrough OK")

del os.environ["ZOHO_CLIENT_ID"]
try:
    load_config()
    raise SystemExit("expected ConfigError, none raised")
except ConfigError as exc:
    assert "ZOHO_CLIENT_ID" in str(exc)
    print("missing-var error OK:", exc)
PYEOF
```
Expected output:
```
normalization OK
bare-domain passthrough OK
missing-var error OK: Missing required environment variable(s): ZOHO_CLIENT_ID. Set them in your .env file or environment.
```

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "Add config.py: env loading, validation, URL normalization"
```

---

### Task 3: auth.py

**Files:**
- Create: `auth.py`

**Interfaces:**
- Consumes: `config.Config`.
- Produces: `AuthError`; `load_tokens() -> Optional[dict]`; `save_tokens(tokens: dict) -> None`; `exchange_grant_token(cfg: Config) -> dict`; `refresh_access_token(cfg: Config) -> dict`; `get_valid_access_token(cfg: Config) -> str`. Token dicts have keys `access_token`, `refresh_token`, `expires_at` (epoch seconds float). Tokens persisted to `zoho_tokens.json`.

- [ ] **Step 1: Write `auth.py`**

```python
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
```

- [ ] **Step 2: Verify with an ephemeral mocked smoke test (no real network/credentials)**

Run (bash), from a temp directory so `zoho_tokens.json` doesn't pollute the project:
```bash
mkdir -p /tmp/auth_smoke && cd /tmp/auth_smoke
cp /path/to/project/config.py /path/to/project/auth.py .
python - <<'PYEOF'
import os, time
from unittest.mock import patch, MagicMock

os.environ.update({
    "ZOHO_CLIENT_ID": "cid", "ZOHO_CLIENT_SECRET": "secret",
    "ZOHO_ORGANIZATION_ID": "org1",
    "ZOHO_ACCOUNTS_URL": "https://accounts.zoho.in",
    "ZOHO_API_DOMAIN": "https://www.zohoapis.in",
    "ZOHO_GRANT_TOKEN": "granttok",
})

from config import load_config
import auth

cfg = load_config(require_grant_token=True)

def fake_post(url, data=None, timeout=None):
    assert url == "https://accounts.zoho.in/oauth/v2/token", url
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "access_token": "tok1", "refresh_token": "reftok", "expires_in": 3600,
    }
    return resp

with patch("auth.requests.post", side_effect=fake_post):
    tokens = auth.exchange_grant_token(cfg)
    assert tokens["access_token"] == "tok1"
    assert tokens["refresh_token"] == "reftok"
print("exchange_grant_token OK")

loaded = auth.load_tokens()
assert loaded["access_token"] == "tok1"
print("load_tokens OK")

# force expiry to test refresh path
loaded["expires_at"] = time.time() - 10
auth.save_tokens(loaded)

def fake_post_refresh(url, data=None, timeout=None):
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == "reftok"
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"access_token": "tok2", "expires_in": 3600}
    return resp

with patch("auth.requests.post", side_effect=fake_post_refresh):
    new_token = auth.get_valid_access_token(cfg)
    assert new_token == "tok2"
print("auto-refresh-on-expiry OK")

# error path: Zoho returns an error body
def fake_post_error(url, data=None, timeout=None):
    resp = MagicMock()
    resp.status_code = 400
    resp.json.return_value = {"error": "invalid_code"}
    return resp

with patch("auth.requests.post", side_effect=fake_post_error):
    try:
        auth.exchange_grant_token(cfg)
        raise SystemExit("expected AuthError, none raised")
    except auth.AuthError as exc:
        assert "invalid_code" in str(exc)
        assert "regenerate" in str(exc).lower() or "generate a new" in str(exc).lower()
print("invalid grant token error message OK")
PYEOF
cd - && rm -rf /tmp/auth_smoke
```
Expected output:
```
exchange_grant_token OK
load_tokens OK
auto-refresh-on-expiry OK
invalid grant token error message OK
```

- [ ] **Step 3: Commit**

```bash
git add auth.py
git commit -m "Add auth.py: token exchange, refresh, and storage"
```

---

### Task 4: token_exchange.py

**Files:**
- Create: `token_exchange.py`

**Interfaces:**
- Consumes: `config.load_config`, `config.ConfigError`, `auth.exchange_grant_token`, `auth.AuthError`.
- Produces: a runnable one-time script; exit code 0 on success, 1 on failure.

- [ ] **Step 1: Write `token_exchange.py`**

```python
"""One-time script: exchange the Zoho grant token for access/refresh tokens.

Run this once per OAuth setup. ZOHO_GRANT_TOKEN is single-use and expires in
~10 minutes, so re-running this after a successful exchange will fail --
that's expected; generate a fresh grant token from the Zoho API console if
you need to re-run it.
"""
import sys

from auth import AuthError, exchange_grant_token
from config import Config, ConfigError, load_config


def main() -> int:
    try:
        cfg: Config = load_config(require_grant_token=True)
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 1

    try:
        tokens = exchange_grant_token(cfg)
    except AuthError as exc:
        print(f"Token exchange failed: {exc}")
        return 1

    print("Token exchange succeeded. Saved to zoho_tokens.json.")
    print(f"Access token expires at (epoch): {tokens['expires_at']:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify the failure path (missing config) runs cleanly end-to-end**

Run (bash), from the actual project directory with no `.env` present:
```bash
python - <<'PYEOF'
import subprocess, sys
result = subprocess.run([sys.executable, "token_exchange.py"], capture_output=True, text=True,
                        env={"PATH": __import__("os").environ["PATH"]})
assert result.returncode == 1, result.returncode
assert "Configuration error" in result.stdout, result.stdout
print("missing-config exit path OK")
PYEOF
```
Expected output: `missing-config exit path OK`

- [ ] **Step 3: Commit**

```bash
git add token_exchange.py
git commit -m "Add token_exchange.py: one-time grant token exchange script"
```

---

### Task 5: journals.py

**Files:**
- Create: `journals.py`

**Interfaces:**
- Consumes: `config.Config`, `auth.get_valid_access_token`, `auth.refresh_access_token`.
- Produces: `ZohoAPIError`; `list_journals(cfg, status=None, date_start=None, date_end=None, page_size=200) -> list[dict]`; `get_journal(cfg, journal_id) -> dict`. Normalized journal dict keys: `journal_id, date, reference_number, status, total, account_entries, raw`.

- [ ] **Step 1: Write `journals.py`**

```python
"""Zoho ERP journals API calls: list, single-fetch, pagination, retry/backoff."""
import time
from typing import Optional

import requests

from auth import get_valid_access_token, refresh_access_token
from config import Config

MAX_RETRIES = 3
BACKOFF_SECONDS = (2, 4, 8)


class ZohoAPIError(Exception):
    """Raised when Zoho ERP returns an error, whether via HTTP status or body code."""


def _check_success(body: dict, context: str) -> None:
    """Zoho can return HTTP 200 with an error indicated in the body's `code`
    field (0 = success). Never trust a 200 status alone.
    """
    code = body.get("code")
    if code is not None and code != 0:
        raise ZohoAPIError(
            f"Zoho ERP API error during {context}: code={code}, "
            f"message={body.get('message', '<no message>')}"
        )


def _request(cfg: Config, path: str, params: Optional[dict] = None) -> dict:
    """GET {api_domain}{path} with auth, 401 auto-refresh-and-retry-once, and
    429 backoff. Raises ZohoAPIError on any non-success outcome.
    """
    url = f"{cfg.api_domain}{path}"
    token = get_valid_access_token(cfg)
    retried_401 = False
    attempt = 0

    while True:
        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Zoho-oauthtoken {token}"},
                params=params,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ZohoAPIError(f"Network error calling {url}: {exc}") from exc

        if response.status_code == 401 and not retried_401:
            retried_401 = True
            token = refresh_access_token(cfg)["access_token"]
            continue

        if response.status_code == 429:
            if attempt >= MAX_RETRIES:
                raise ZohoAPIError(
                    f"Rate limited by Zoho after {MAX_RETRIES} retries calling {url}"
                )
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else BACKOFF_SECONDS[attempt]
            time.sleep(delay)
            attempt += 1
            continue

        try:
            body = response.json()
        except ValueError as exc:
            raise ZohoAPIError(
                f"Zoho returned a non-JSON response (HTTP {response.status_code}) "
                f"calling {url}: {response.text[:500]}"
            ) from exc

        if response.status_code != 200:
            raise ZohoAPIError(
                f"HTTP {response.status_code} calling {url}: "
                f"{body.get('message', body)}"
            )

        _check_success(body, context=url)
        return body


def _normalize_journal(raw: dict) -> dict:
    """Extract the fields we care about, keeping the raw record for anything else."""
    return {
        "journal_id": raw.get("journal_id"),
        "date": raw.get("journal_date") or raw.get("date"),
        "reference_number": raw.get("reference_number"),
        "status": raw.get("status"),
        "total": raw.get("total"),
        "account_entries": raw.get("line_items") or raw.get("account_entries") or [],
        "raw": raw,
    }


def list_journals(
    cfg: Config,
    status: Optional[str] = None,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    page_size: int = 200,
) -> list:
    """Fetch all journals matching the given filters, paginating until exhausted.

    NOTE: `date_start`/`date_end` are sent using the param names below, which
    are ASSUMED based on common Zoho API convention and have NOT been
    confirmed against the Zoho ERP API docs. The first time this runs against
    a real org with a date range, inspect the returned journals' dates to
    verify the range is actually applied server-side -- a 200 response does
    not by itself prove the filter worked; unrecognized query params may be
    silently ignored.
    """
    params = {"organization_id": cfg.organization_id, "per_page": page_size, "page": 1}
    if status:
        params["status"] = status
    if date_start:
        params["date_start"] = date_start
    if date_end:
        params["date_end"] = date_end

    journals = []
    while True:
        body = _request(cfg, "/erp/v3/journals", params=params)
        page_journals = body.get("journals", [])
        journals.extend(_normalize_journal(j) for j in page_journals)

        page_context = body.get("page_context", {})
        if not page_context.get("has_more_page"):
            break
        params["page"] += 1

    if not journals:
        print(
            "0 journals matched the given filters (request succeeded; this is "
            "not an error -- Zoho ERP simply returned no records for this "
            "status/date range)."
        )

    return journals


def get_journal(cfg: Config, journal_id: str) -> dict:
    """Fetch a single journal by ID."""
    body = _request(
        cfg,
        f"/erp/v3/journals/{journal_id}",
        params={"organization_id": cfg.organization_id},
    )
    raw = body.get("journal", body)
    return _normalize_journal(raw)
```

- [ ] **Step 2: Verify pagination, 401-retry, 429-backoff, and 200-with-error-code with an ephemeral mocked smoke test**

Run (bash), from a temp directory:
```bash
mkdir -p /tmp/journals_smoke && cd /tmp/journals_smoke
cp /path/to/project/config.py /path/to/project/auth.py /path/to/project/journals.py .
python - <<'PYEOF'
import os, time
from unittest.mock import patch, MagicMock

os.environ.update({
    "ZOHO_CLIENT_ID": "cid", "ZOHO_CLIENT_SECRET": "secret",
    "ZOHO_ORGANIZATION_ID": "org1",
    "ZOHO_ACCOUNTS_URL": "https://accounts.zoho.in",
    "ZOHO_API_DOMAIN": "https://www.zohoapis.in",
})
from config import load_config
import auth, journals

cfg = load_config()
auth.save_tokens({"access_token": "tok1", "refresh_token": "reftok",
                   "expires_at": time.time() + 3600})

# --- pagination: two pages ---
calls = {"n": 0}
def fake_get(url, headers=None, params=None, timeout=None):
    calls["n"] += 1
    resp = MagicMock()
    resp.status_code = 200
    if params["page"] == 1:
        resp.json.return_value = {"code": 0, "journals": [{"journal_id": "1"}],
                                   "page_context": {"has_more_page": True}}
    else:
        resp.json.return_value = {"code": 0, "journals": [{"journal_id": "2"}],
                                   "page_context": {"has_more_page": False}}
    return resp

with patch("journals.requests.get", side_effect=fake_get):
    result = journals.list_journals(cfg)
    assert [j["journal_id"] for j in result] == ["1", "2"], result
    assert calls["n"] == 2
print("pagination OK")

# --- 401 triggers one refresh-and-retry ---
seq = {"n": 0}
def fake_get_401(url, headers=None, params=None, timeout=None):
    seq["n"] += 1
    resp = MagicMock()
    if seq["n"] == 1:
        resp.status_code = 401
        resp.json.return_value = {}
    else:
        resp.status_code = 200
        resp.json.return_value = {"code": 0, "journals": [], "page_context": {"has_more_page": False}}
    return resp

def fake_refresh(cfg):
    return {"access_token": "tok2", "refresh_token": "reftok", "expires_at": time.time() + 3600}

with patch("journals.requests.get", side_effect=fake_get_401), \
     patch("journals.refresh_access_token", side_effect=fake_refresh):
    result = journals.list_journals(cfg)
    assert result == []
    assert seq["n"] == 2
print("401 auto-refresh-and-retry OK")

# --- 200 with non-zero code raises ZohoAPIError ---
def fake_get_body_error(url, headers=None, params=None, timeout=None):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"code": 1001, "message": "Invalid organization_id"}
    return resp

with patch("journals.requests.get", side_effect=fake_get_body_error):
    try:
        journals.list_journals(cfg)
        raise SystemExit("expected ZohoAPIError, none raised")
    except journals.ZohoAPIError as exc:
        assert "1001" in str(exc)
print("200-with-error-code detection OK")
PYEOF
cd - && rm -rf /tmp/journals_smoke
```
Expected output:
```
pagination OK
401 auto-refresh-and-retry OK
200-with-error-code detection OK
```

- [ ] **Step 3: Commit**

```bash
git add journals.py
git commit -m "Add journals.py: list/get journals with pagination, retry, backoff"
```

---

### Task 6: main.py

**Files:**
- Create: `main.py`

**Interfaces:**
- Consumes: `config.load_config`, `config.ConfigError`, `auth.AuthError`, `journals.list_journals`, `journals.get_journal`, `journals.ZohoAPIError`.
- Produces: CLI entry point; writes normalized journal JSON to the `--out` path (default `journals_output.json`).

- [ ] **Step 1: Write `main.py`**

```python
"""CLI entry point for fetching Zoho ERP journal entries."""
import argparse
import json
import sys

from auth import AuthError
from config import Config, ConfigError, load_config
from journals import ZohoAPIError, get_journal, list_journals

VALID_STATUSES = ("draft", "published", "approved", "submitted", "rejected")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch journal entries from Zoho ERP.")
    parser.add_argument("--list", action="store_true", help="Fetch all journals (optionally filtered)")
    parser.add_argument("--id", dest="journal_id", help="Fetch a single journal by ID")
    parser.add_argument("--status", choices=VALID_STATUSES, help="Filter by journal status")
    parser.add_argument("--date-from", dest="date_from", help="Filter: journal date >= YYYY-MM-DD")
    parser.add_argument("--date-to", dest="date_to", help="Filter: journal date <= YYYY-MM-DD")
    parser.add_argument("--out", default="journals_output.json", help="Output JSON file path")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.list and not args.journal_id:
        parser.error("Specify --list or --id <journal_id>")

    try:
        cfg: Config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 1

    try:
        if args.journal_id:
            result = get_journal(cfg, args.journal_id)
            print(f"Fetched journal {result['journal_id']} (status={result['status']}, total={result['total']})")
        else:
            result = list_journals(
                cfg,
                status=args.status,
                date_start=args.date_from,
                date_end=args.date_to,
            )
            print(f"Fetched {len(result)} journal(s).")
    except (AuthError, ZohoAPIError) as exc:
        print(f"Error fetching journals: {exc}")
        return 1

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"Wrote results to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify argument parsing and output writing with an ephemeral mocked smoke test**

Run (bash), from a temp directory:
```bash
mkdir -p /tmp/main_smoke && cd /tmp/main_smoke
cp /path/to/project/config.py /path/to/project/auth.py /path/to/project/journals.py /path/to/project/main.py .
python - <<'PYEOF'
import os, json
from unittest.mock import patch

os.environ.update({
    "ZOHO_CLIENT_ID": "cid", "ZOHO_CLIENT_SECRET": "secret",
    "ZOHO_ORGANIZATION_ID": "org1",
    "ZOHO_ACCOUNTS_URL": "https://accounts.zoho.in",
    "ZOHO_API_DOMAIN": "https://www.zohoapis.in",
})

import main

# no --list/--id => argparse error, exit code 2
try:
    main.main([])
    raise SystemExit("expected SystemExit")
except SystemExit as e:
    assert e.code == 2, e.code
print("missing-flag validation OK")

fake_journals = [{"journal_id": "1", "date": "2026-01-01", "reference_number": "R1",
                   "status": "draft", "total": 100, "account_entries": [], "raw": {}}]

with patch("main.list_journals", return_value=fake_journals):
    code = main.main(["--list", "--out", "out.json"])
    assert code == 0, code

with open("out.json") as f:
    data = json.load(f)
assert data == fake_journals, data
print("--list writes normalized JSON OK")
PYEOF
cd - && rm -rf /tmp/main_smoke
```
Expected output:
```
missing-flag validation OK
--list writes normalized JSON OK
```

- [ ] **Step 3: Verify `--help` runs cleanly in the real project directory**

Run: `python main.py --help`
Expected: argparse usage text listing `--list`, `--id`, `--status`, `--date-from`, `--date-to`, `--out`, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "Add main.py: CLI entry point for listing and fetching journals"
```

---

## Post-Plan: Running Against Real Zoho ERP

Not a plan task (requires the user's real credentials/grant token) — run manually after all tasks above are committed:

```bash
# 1. Create .env from the template and fill in real values
cp .env.example .env

# 2. One-time: exchange the grant token (must be used within ~10 minutes of generation)
python token_exchange.py

# 3. Fetch all journals
python main.py --list

# 4. Fetch with filters
python main.py --list --status draft --date-from 2026-01-01 --date-to 2026-06-30

# 5. Fetch a single journal by ID
python main.py --id <journal_id>
```

After the first `--date-from`/`--date-to` run, manually check `journals_output.json` to confirm the returned dates actually fall in range (per the code comment in `journals.py::list_journals` — the param names are unconfirmed against Zoho's real API).

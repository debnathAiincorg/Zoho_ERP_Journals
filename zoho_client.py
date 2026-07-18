"""Zoho ERP client: configuration, OAuth token management, and journals API calls.

Consolidated from what used to be config.py + auth.py + journals.py. Three
sections below, in dependency order: Configuration -> OAuth -> Journals API.
"""
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests
from dotenv import find_dotenv, load_dotenv, set_key

# ============================================================================
# Configuration
# ============================================================================
# Loads and validates settings from .env: static app credentials
# (ZOHO_CLIENT_ID etc.) plus the OAuth tokens once they exist.

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
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_expires_at: Optional[float] = None


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

    expires_at_raw = os.environ.get("ZOHO_TOKEN_EXPIRES_AT")

    return Config(
        client_id=os.environ["ZOHO_CLIENT_ID"],
        client_secret=os.environ["ZOHO_CLIENT_SECRET"],
        organization_id=os.environ["ZOHO_ORGANIZATION_ID"],
        grant_token=os.environ.get("ZOHO_GRANT_TOKEN", ""),
        accounts_url=_strip_trailing_slash(os.environ["ZOHO_ACCOUNTS_URL"]),
        api_domain=_normalize_api_domain(os.environ["ZOHO_API_DOMAIN"]),
        redirect_uri=os.environ.get("ZOHO_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        access_token=os.environ.get("ZOHO_ACCESS_TOKEN") or None,
        refresh_token=os.environ.get("ZOHO_REFRESH_TOKEN") or None,
        token_expires_at=float(expires_at_raw) if expires_at_raw else None,
    )


# ============================================================================
# OAuth: token exchange, refresh, storage
# ============================================================================
# Tokens are persisted directly into the .env file (ZOHO_ACCESS_TOKEN,
# ZOHO_REFRESH_TOKEN, ZOHO_TOKEN_EXPIRES_AT) using python-dotenv's set_key(),
# which updates a single key in place without touching any other line --
# comments and unrelated variables are left untouched.

ENV_FILE = find_dotenv() or ".env"
EXPIRY_BUFFER_SECONDS = 300  # refresh if within 5 minutes of expiry


class AuthError(Exception):
    """Raised when a Zoho OAuth token request fails."""


def load_tokens() -> Optional[dict]:
    """Load stored tokens from .env, or None if not present.

    Reads os.environ directly (rather than re-parsing the file) so that
    values written earlier in the same process via save_tokens() -- which
    also updates os.environ -- are picked up immediately.
    """
    access_token = os.environ.get("ZOHO_ACCESS_TOKEN")
    refresh_token = os.environ.get("ZOHO_REFRESH_TOKEN")
    expires_at_raw = os.environ.get("ZOHO_TOKEN_EXPIRES_AT")
    if not access_token or not refresh_token:
        return None
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": float(expires_at_raw) if expires_at_raw else 0.0,
    }


def save_tokens(tokens: dict) -> None:
    """Write access_token, refresh_token, and expires_at into .env.

    Uses set_key() per variable so only these three lines are ever added or
    modified -- ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, and every other existing
    key/comment in the file are left exactly as they were. os.environ is
    updated in lockstep so the rest of this process sees the new values
    without needing to reload the file.
    """
    values = {
        "ZOHO_ACCESS_TOKEN": tokens["access_token"],
        "ZOHO_REFRESH_TOKEN": tokens["refresh_token"],
        "ZOHO_TOKEN_EXPIRES_AT": str(tokens["expires_at"]),
    }
    for key, value in values.items():
        set_key(ENV_FILE, key, value)
        os.environ[key] = value


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
            "No refresh_token found in .env. Run token_exchange.py "
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


# ============================================================================
# Journals API
# ============================================================================
# list_journals() / get_journal(), built on top of the OAuth helpers above:
# 401 auto-refresh-and-retry-once, 429 backoff, and the "200 but error in
# body" check.

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
            try:
                delay = float(retry_after) if retry_after else BACKOFF_SECONDS[attempt]
            except (TypeError, ValueError):
                # Retry-After may be an HTTP-date string (RFC 7231) rather than
                # an integer number of seconds; fall back to our own backoff.
                delay = BACKOFF_SECONDS[attempt]
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
    account_id: Optional[str] = None,
    page_size: int = 200,
) -> list:
    """Fetch all journals matching the given filters, paginating until exhausted.

    `account_id` is CONFIRMED working server-side (verified live: the
    response's page_context.search_criteria shows column_name "account_id",
    comparator "equal", matched against the target account's name) -- use it
    to get every journal that touches a given ledger account.

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
    if account_id:
        params["account_id"] = account_id

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


# ============================================================================
# Bills API
# ============================================================================
# Ledger totals built from /erp/v3/journals alone undercount against Zoho's
# own Account Transactions report, because that report aggregates every
# transaction type posting to an account -- not just Journals. Bills are a
# second, confirmed-reachable source: GET /erp/v3/bills?account_id=... is
# CONFIRMED working server-side (verified live: page_context.search_criteria
# echoes column_name "account_id", comparator "equal", matched against the
# target account's name -- e.g. "OFFICE 2 PROJECT."), and each bill's detail
# includes line_items with a per-line account_id. (/erp/v3/expenses and
# /erp/v3/creditnotes 401 with the current OAuth scope and are not available
# here; /erp/v3/vendorpayments and /erp/v3/customerpayments were checked and
# confirmed to silently ignore the account_id param entirely -- no
# search_criteria echoed at all -- matching the same unreliable-filter
# pattern seen before on those two endpoints, and structurally they settle
# already-recorded bills/invoices rather than posting fresh amounts to an
# expense account, so they're not a relevant data source for these ledgers
# even setting the filter issue aside.)

def list_bills(
    cfg: Config,
    account_id: Optional[str] = None,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    page_size: int = 200,
) -> list:
    """Fetch all bills matching the given filters, paginating until exhausted."""
    params = {"organization_id": cfg.organization_id, "per_page": page_size, "page": 1}
    if date_start:
        params["date_start"] = date_start
    if date_end:
        params["date_end"] = date_end
    if account_id:
        params["account_id"] = account_id

    bills = []
    while True:
        body = _request(cfg, "/erp/v3/bills", params=params)
        bills.extend(body.get("bills", []))

        page_context = body.get("page_context", {})
        if not page_context.get("has_more_page"):
            break
        params["page"] += 1

    return bills


def get_bill(cfg: Config, bill_id: str) -> dict:
    """Fetch a single bill by ID, including its line_items."""
    body = _request(
        cfg,
        f"/erp/v3/bills/{bill_id}",
        params={"organization_id": cfg.organization_id},
    )
    return body.get("bill", body)


# ============================================================================
# Chart of Accounts
# ============================================================================
# Confirmed live against this org: GET /erp/v3/chartofaccounts works with the
# currently granted scopes and returns account_id/account_name pairs, used to
# resolve a ledger's human-readable name to its account_id.
#
# (GET /erp/v3/reports/generalledger was tried for per-account ledger data
# and abandoned: it's a real, reachable path -- 401s, not 404s, and the bare
# /erp/v3/reports path 401s identically -- but it 401s regardless of scope,
# including after a clean grant-token regeneration with an added scope.
# Ledger-style data is fetched instead by filtering the regular
# /erp/v3/journals list by account_id -- see list_journals()'s account_id
# param above and fetch_ledgers.py, which pulls each matching journal's line
# items via get_journal().)

def get_chart_of_accounts(cfg: Config, page_size: int = 200) -> list:
    """Fetch the full Chart of Accounts, paginating until exhausted."""
    params = {"organization_id": cfg.organization_id, "per_page": page_size, "page": 1}
    accounts = []
    while True:
        body = _request(cfg, "/erp/v3/chartofaccounts", params=params)
        accounts.extend(body.get("chartofaccounts", []))

        page_context = body.get("page_context", {})
        if not page_context.get("has_more_page"):
            break
        params["page"] += 1

    return accounts


def find_account_id(accounts: list, account_name: str) -> Optional[str]:
    """Exact, case-sensitive match on account_name (e.g. a trailing '.' matters)."""
    for account in accounts:
        if account.get("account_name") == account_name:
            return account.get("account_id")
    return None

# Zoho ERP Journals Fetcher — Design Spec

Date: 2026-07-15

## Purpose

A small Python CLI project that connects to **Zoho ERP** (India data center, `.in` endpoints only —
not Zoho Books/CRM/any other Zoho product) to:
1. Exchange a one-time OAuth grant token for access/refresh tokens.
2. Auto-refresh the access token when expired.
3. Fetch journal entries (list, with filters + pagination; and single-journal-by-id).

## Project Layout

```
zoho-erp-journals/
├── .env.example
├── .gitignore
├── requirements.txt
├── config.py            # env loading + validation, URL normalization
├── auth.py               # token exchange + refresh, zoho_tokens.json read/write
├── journals.py           # list_journals(), get_journal(), shared _request() helper
├── main.py               # argparse CLI entry point
├── token_exchange.py      # one-time script for the initial code -> token exchange
└── zoho_tokens.json       # created at runtime (gitignored, not committed)
```

## config.py

Loads `.env` via `python-dotenv`. Exposes a `Config` dataclass with:

- `client_id`, `client_secret`, `organization_id`, `grant_token`
- `accounts_url` (from `ZOHO_ACCOUNTS_URL`, e.g. `https://accounts.zoho.in`)
- `api_domain` (from `ZOHO_API_DOMAIN`, e.g. `https://www.zohoapis.in`)
- `redirect_uri` (from `ZOHO_REDIRECT_URI`, defaults to `https://www.zoho.in/erp` if unset)

**Two base URLs are handled independently and must never cross-contaminate:**
- `accounts_url` (`ZOHO_ACCOUNTS_URL`) is used ONLY for `/oauth/v2/token` calls in `auth.py`. It is
  used as-is (just strip a trailing slash) — no `/erp` normalization applied to it.
- `api_domain` (`ZOHO_API_DOMAIN`) is used ONLY for the ERP data API in `journals.py`. It is
  **normalized**: strip a trailing slash, and if the user set the full path (e.g.
  `https://www.zohoapis.in/erp/v3`) instead of the bare domain, strip a trailing `/erp/v3` or `/erp`
  so the stored value is always the bare domain. `journals.py` then always appends `/erp/v3/journals`
  (or `/erp/v3/journals/{id}`) itself.

Raises `ConfigError` listing exactly which required var(s) are missing, so misconfiguration fails
fast with an actionable message.

## auth.py

- `exchange_grant_token(cfg) -> TokenSet` — POSTs to `{accounts_url}/oauth/v2/token` with
  `grant_type=authorization_code`, `client_id`, `client_secret`, `redirect_uri`, `code=grant_token`.
  On a Zoho error response body (e.g. `invalid_code`), raises `AuthError` with a message telling the
  user the grant token is one-time-use / 10-minute expiry and must be regenerated.
- `refresh_access_token(cfg) -> TokenSet` — POSTs with `grant_type=refresh_token` using the stored
  refresh token; updates `zoho_tokens.json` in place.
- `load_tokens()` / `save_tokens()` — read/write `zoho_tokens.json` (`access_token`, `refresh_token`,
  `expires_at` as epoch seconds).
- `get_valid_access_token(cfg) -> str` — the single entry point `journals.py` calls. Loads stored
  tokens; if `expires_at` is within a 5-minute buffer of now (or missing), calls
  `refresh_access_token` first. Returns a usable access token.

Both `/oauth/v2/token` calls always target `accounts_url`, never `api_domain`.

## journals.py

- Shared `_request(cfg, method, path, params=None)` helper:
  - Builds URL as `{api_domain}{path}` (path always starts with `/erp/v3/...`).
  - Attaches `Authorization: Zoho-oauthtoken {token}` using `get_valid_access_token`.
  - **On HTTP 401**: calls `refresh_access_token` once, retries the request once with the new token.
  - **On HTTP 429**: reads `Retry-After` header if present and sleeps that long; otherwise exponential
    backoff (2s, 4s, 8s), capped at 3 retries total.
  - **Does not treat HTTP 200 as automatic success.** Zoho ERP can return 200 with an error indicated
    in the JSON body. After any 2xx response, checks the body's `code` field (Zoho convention:
    `0` = success). If `code != 0`, raises `ZohoAPIError` with the body's `message`/`code`. Non-2xx
    statuses (other than the 401/429 handled above) also raise `ZohoAPIError`.

- `list_journals(cfg, status=None, date_start=None, date_end=None, page_size=200) -> list[dict]`:
  - Loops pages via `_request`, following Zoho's pagination convention (checks
    `page_context.has_more_page` in the response), accumulating results until exhausted.
  - Applies `status` as a query param when given.
  - Applies `date_start`/`date_end` as query params **with a code comment flagging that the exact
    Zoho ERP query param names for date filtering are unconfirmed** (assumed `date_start`/`date_end`
    based on common Zoho API convention, but not verified against ERP API docs). The comment
    instructs: when the first real test call is made, inspect the actual returned journal dates to
    confirm the filter is genuinely restricting the range — do not assume a 200 response means the
    filter worked.
  - Distinguishes clearly, when the accumulated list is empty, between two cases and prints/logs
    which one occurred: (a) the API call(s) succeeded (`code == 0`) and genuinely returned zero
    journals matching the filters, vs (b) an error occurred (caught as `ZohoAPIError` before reaching
    this point, so by the time we're checking "empty," it's already known-good — the print should
    say something like "0 journals matched the given filters" rather than being silent).
  - Normalizes each result to: journal ID, date, reference number, status, total amount, account
    entries (line items), keeping the raw record too in case more fields are needed later.

- `get_journal(cfg, journal_id) -> dict` — single-record fetch via the same `_request` helper and
  same 200-with-error-code check.

Output written to `journals_output.json`.

## main.py (CLI)

argparse-based:
- `--list` — fetch all journals (optionally filtered)
- `--id <journal_id>` — fetch a single journal
- `--status {draft,published,approved,submitted,rejected}` — filter
- `--date-from YYYY-MM-DD` / `--date-to YYYY-MM-DD` — date range filter
- `--out <path>` — override default output filename (default `journals_output.json`)

Prints a concise summary to stdout (count fetched, or the single journal's key fields) and writes
full JSON to disk. Top-level try/except around `ConfigError`, `AuthError`, `ZohoAPIError`,
`requests.RequestException` prints a clean, actionable message and exits non-zero — no bare excepts
anywhere in the codebase.

## token_exchange.py

Standalone one-time script for the initial grant-token → token exchange (kept separate from
`main.py` since the grant token is single-use/10-minute and this step only ever runs once per app
setup). Because re-running it with an already-used grant token will fail, it prints a clear message
on failure pointing at regenerating the grant token from the Zoho API console.

## .gitignore

An actual `.gitignore` file (not just a mention), including:
```
zoho_tokens.json
.env
__pycache__/
*.pyc
venv/
```

## Error Handling Summary

Custom exceptions: `ConfigError`, `AuthError`, `ZohoAPIError`. `requests.RequestException` is caught
narrowly at the HTTP boundary in `_request`/`auth.py` and re-raised with added context. No bare
`except:` blocks anywhere.

## Testing

Manual, against a real Zoho ERP org: run `token_exchange.py` once, then exercise `main.py --list`,
`--id <id>`, `--status`, and the date-range flags. No automated test suite for this pass (per user
decision — small integration script against a live third-party API).

## Out of Scope

- Zoho Books, Zoho CRM, or any non-ERP Zoho product/endpoint.
- Writing journals (create/update) — read-only fetch only, per the requested scopes
  (`ERP.accountants.READ` etc. — all read scopes).
- Automated test suite / mocked HTTP tests.

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

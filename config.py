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

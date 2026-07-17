"""One-time script: exchange the Zoho grant token for access/refresh tokens.

Run this once per OAuth setup. ZOHO_GRANT_TOKEN is single-use and expires in
~10 minutes, so re-running this after a successful exchange will fail --
that's expected; generate a fresh grant token from the Zoho API console if
you need to re-run it.

The resulting access_token, refresh_token, and expiry are written directly
into .env (ZOHO_ACCESS_TOKEN, ZOHO_REFRESH_TOKEN, ZOHO_TOKEN_EXPIRES_AT).
"""
import sys

from zoho_client import AuthError, Config, ConfigError, exchange_grant_token, load_config


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

    print("Token exchange succeeded. Saved to .env.")
    print(f"Access token expires at (epoch): {tokens['expires_at']:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

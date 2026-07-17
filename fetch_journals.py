"""Fetch journal entries from Zoho ERP and write them for dashboard.html.

This is the one script to run whenever you want fresh data:

    python fetch_journals.py
    python fetch_journals.py --status draft
    python fetch_journals.py --date-from 2026-01-01 --date-to 2026-06-30

Every full-list fetch is restricted to journals whose reference_number is
some format/case variant of "Pay Order" (e.g. "PAY ORDER", "Pay-Order",
"PayOrder") -- everything else (FD, FD - SM, etc.) is scanned but filtered
out before it ever reaches disk. journals_output.json (the raw data) and the
embedded JSON block inside dashboard.html
(<script type="application/json" id="journals-data">) therefore only ever
contain Pay Order records, so dashboard.html is self-contained and works
when double-clicked from file:// -- no local server needed.

Requires a completed OAuth setup (run token_exchange.py once first) -- this
script only handles ongoing auto-refresh, never the initial grant-token
exchange.
"""
import argparse
import json
import re
import sys

from zoho_client import (
    AuthError,
    Config,
    ConfigError,
    ZohoAPIError,
    get_journal,
    list_journals,
    load_config,
)

VALID_STATUSES = ("draft", "published", "approved", "submitted", "rejected")
DEFAULT_OUT = "journals_output.json"
DASHBOARD_HTML = "dashboard.html"
DATA_BLOCK_RE = re.compile(
    r'(<script type="application/json" id="journals-data">)(.*?)(</script>)',
    re.DOTALL,
)

PAY_ORDER_NORMALIZED = "payorder"


def _normalize_reference(value) -> str:
    """Strip whitespace and hyphens and lowercase, so 'PAY ORDER', 'Pay-Order',
    'PayOrder', 'pay order' etc. all collapse to the same comparable string.

    NOTE: Zoho ERP's /journals list endpoint does support a search_text query
    param (confirmed live: it does a case-insensitive "contains" match against
    reference_number and entry_number), but it's a literal substring match,
    not format-normalized -- searching "Pay Order" won't find "PayOrder" (no
    space) and vice versa (verified against this org's real data, which
    contains both). A single server-side query can't reliably catch every
    spacing/hyphenation variant, so filtering is done here instead, against
    the full fetched list.
    """
    if not value:
        return ""
    return re.sub(r"[\s-]+", "", value).lower()


def _is_pay_order(record: dict) -> bool:
    return _normalize_reference(record.get("reference_number")) == PAY_ORDER_NORMALIZED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch journal entries from Zoho ERP.")
    parser.add_argument("--id", dest="journal_id", help="Fetch a single journal by ID instead of the full list")
    parser.add_argument("--status", choices=VALID_STATUSES, help="Filter by journal status (ignored with --id)")
    parser.add_argument("--date-from", dest="date_from", help="Filter: journal date >= YYYY-MM-DD (ignored with --id)")
    parser.add_argument("--date-to", dest="date_to", help="Filter: journal date <= YYYY-MM-DD (ignored with --id)")
    parser.add_argument(
        "--out",
        default=None,
        help=(
            f"Output JSON file path (default: {DEFAULT_OUT} for the full list; "
            "journal_<id>.json for --id, so fetching one journal never "
            "overwrites the dashboard's data file)"
        ),
    )
    return parser


def _update_dashboard_html(records: list, path: str) -> bool:
    """Replace the JSON content inside dashboard.html's embedded
    <script type="application/json" id="journals-data"> block with freshly
    fetched data. Returns True if the block was found and updated, False
    otherwise (caller should warn, not crash).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        return False

    if not DATA_BLOCK_RE.search(html):
        return False

    new_json = json.dumps(records, indent=2)
    new_html = DATA_BLOCK_RE.sub(
        lambda m: m.group(1) + new_json + m.group(3), html, count=1
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_html)
    return True


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cfg: Config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 1

    try:
        if args.journal_id:
            result = get_journal(cfg, args.journal_id)
            print(f"Fetched journal {result['journal_id']} (status={result['status']}, total={result['total']})")
            out_path = args.out or f"journal_{args.journal_id}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            print(f"Wrote results to {out_path}")
            print("(This is single-journal detail, separate from the dashboard's data --")
            print(f" {DASHBOARD_HTML} was not touched.)")
        else:
            result = list_journals(
                cfg,
                status=args.status,
                date_start=args.date_from,
                date_end=args.date_to,
            )
            total_scanned = len(result)
            result = [record for record in result if _is_pay_order(record)]
            matched = len(result)
            print(f"Scanned {total_scanned} journal(s); {matched} matched the \"Pay Order\" "
                  f"reference-number filter ({total_scanned - matched} filtered out).")
            out_path = args.out or DEFAULT_OUT
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            print(f"Wrote results to {out_path}")

            if _update_dashboard_html(result, DASHBOARD_HTML):
                print(f"Updated embedded data in {DASHBOARD_HTML}")
                print()
                print("dashboard.html updated with this run's data -- just open it "
                      "directly, no server needed.")
            else:
                print(f"Warning: could not find the journals-data script block in "
                      f"{DASHBOARD_HTML}; it was not updated.")
    except (AuthError, ZohoAPIError) as exc:
        print(f"Error fetching journals: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

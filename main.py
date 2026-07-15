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

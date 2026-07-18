"""Fetch General Ledger-style entries for a fixed set of accounts from Zoho ERP.

For each name in LEDGER_NAMES below, resolves its account_id via the Chart of
Accounts (exact, case-sensitive match), then pulls matching entries from TWO
transaction-type sources and merges them:

  1. Journals: every journal touching the account (GET
     /erp/v3/journals?account_id=...), line items filtered client-side by
     account_id, debit/credit taken from each line item's debit_or_credit flag.
  2. Bills: every bill touching the account (GET /erp/v3/bills?account_id=...),
     line items filtered client-side by account_id. Bill line items carry no
     debit_or_credit flag, but a Bill always debits whatever account its line
     items are allocated to (Dr Expense/Asset, Cr Accounts Payable), so these
     are recorded as debits unconditionally.

(Journals alone undercount against Zoho's own Account Transactions report,
which aggregates every transaction type posting to an account. Bills close
part of that gap; /erp/v3/expenses -- likely the single largest remaining
source, since it's the most direct way to post an ad-hoc cost to an expense
account -- currently 401s with our OAuth scope and is not included. See the
"Bills API" section of zoho_client.py for what else was checked and ruled out
-- vendorpayments/customerpayments silently ignore the account_id filter and
are structurally the wrong transaction type for this anyway.)

Only fully-posted transactions are included: journals must have
status="published", and bills must not be "draft" or "void" (see
PUBLISHED_JOURNAL_STATUS / EXCLUDED_BILL_STATUSES below). Confirmed live
against OFFICE 1 WASHROOM PROJECT for July 2026 -- 2 draft journals were
inflating the debit total by their combined amount versus Zoho's own report.

All 3 ledgers are written into one ledgers_output.json, keyed by ledger name,
and the same data is used to refresh the embedded JSON block inside
ledgers_dashboard.html (<script type="application/json" id="ledgers-data">),
so ledgers_dashboard.html stays self-contained and works when double-clicked
from file:// -- no local server needed:

    python fetch_ledgers.py
    python fetch_ledgers.py --date-from 2026-01-01 --date-to 2026-06-30

Requires the same completed OAuth setup as fetch_journals.py. Uses only
ERP.accountants.READ -- no additional scope needed for the Journals/Bills/
Chart of Accounts endpoints used here. (The earlier /erp/v3/reports/
generalledger approach was abandoned after it kept 401ing even past a clean
grant-token regeneration with an added scope -- see the Chart of Accounts
section in zoho_client.py.)
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
    find_account_id,
    get_bill,
    get_chart_of_accounts,
    get_journal,
    list_bills,
    list_journals,
    load_config,
)

# Edit this list to add/remove ledgers -- everything else loops over it.
LEDGER_NAMES = [
    "OFFICE 1 WASHROOM PROJECT",
    "OFFICE 2 PROJECT.",  # trailing "." is part of the real account name
    "Food and Brev E",
]

# Only fully-posted transactions affect a real account balance -- confirmed
# live against OFFICE 1 WASHROOM PROJECT for July 2026: including 2 "draft"
# journals overcounted debits by exactly their combined amount (Rs 2,500.00)
# and entry count by exactly 2 versus Zoho's own Account Transactions report.
# "published" is the only posted status seen in this org's data (the other
# Zoho ERP statuses -- submitted/approved/rejected -- don't appear in
# practice here); bills use a different status vocabulary, where "draft"
# (not yet finalized) and "void" (cancelled) are the equivalent not-posted
# states -- payment status like "open"/"paid" doesn't matter, since a bill
# posts to its expense account on creation, not on payment.
PUBLISHED_JOURNAL_STATUS = "published"
EXCLUDED_BILL_STATUSES = {"draft", "void"}

DEFAULT_OUT = "ledgers_output.json"
LEDGERS_DASHBOARD_HTML = "ledgers_dashboard.html"
DATA_BLOCK_RE = re.compile(
    r'(<script type="application/json" id="ledgers-data">)(.*?)(</script>)',
    re.DOTALL,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch General Ledger-style entries for LEDGER_NAMES from Zoho ERP."
    )
    parser.add_argument("--date-from", dest="date_from", help="Filter: journal date >= YYYY-MM-DD")
    parser.add_argument("--date-to", dest="date_to", help="Filter: journal date <= YYYY-MM-DD")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"Output JSON file path (default: {DEFAULT_OUT})")
    return parser


def _in_date_range(date_value, date_from, date_to) -> bool:
    if date_from and (not date_value or date_value < date_from):
        return False
    if date_to and (not date_value or date_value > date_to):
        return False
    return True


def _line_entries_for_account(journal: dict, account_id: str) -> list:
    """Pull just the line item(s) touching account_id out of a full journal detail.

    Journal line items are almost always missing their own description --
    confirmed live, the account-level detail is recorded in the journal's
    free-text `notes` field instead. Fall back to that (same pattern as the
    reference petty-cash script: line item description, else journal notes,
    else empty), rather than shipping a blank Description column.
    """
    entries = []
    notes = journal["raw"].get("notes") or ""
    for item in journal["raw"].get("line_items", []):
        if item.get("account_id") != account_id:
            continue
        amount = item.get("amount") or 0
        is_debit = item.get("debit_or_credit") == "debit"
        entries.append({
            "source_type": "journal",
            "source_id": journal["journal_id"],
            "date": journal["date"],
            "reference_number": journal["reference_number"],
            "description": item.get("description") or notes,
            "debit_amount": amount if is_debit else 0,
            "credit_amount": amount if not is_debit else 0,
        })
    return entries


def _line_entries_for_bill_account(bill: dict, account_id: str) -> list:
    """Pull just the line item(s) touching account_id out of a full bill detail.

    Bill line items carry no debit_or_credit flag (unlike journal line
    items) -- but a Bill always debits whichever account(s) its line items
    are allocated to (Dr Expense/Asset, Cr Accounts Payable), so every
    matching line item here is unconditionally a debit.

    Same description fallback as journals: bills also have a free-text
    `notes` field (confirmed live, populated with real text) -- fall back
    to it when a line item has no description of its own. In practice
    most bill line items already carry their own description, so this
    mostly matters for the ones that don't.
    """
    entries = []
    notes = bill.get("notes") or ""
    for item in bill.get("line_items", []):
        if item.get("account_id") != account_id:
            continue
        amount = item.get("item_total") or 0
        entries.append({
            "source_type": "bill",
            "source_id": bill.get("bill_id"),
            "date": bill.get("date"),
            "reference_number": bill.get("reference_number") or "",
            "description": item.get("description") or notes,
            "debit_amount": amount,
            "credit_amount": 0,
        })
    return entries


def _update_dashboard_html(results: dict, path: str) -> bool:
    """Replace the JSON content inside ledgers_dashboard.html's embedded
    <script type="application/json" id="ledgers-data"> block with freshly
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

    new_json = json.dumps(results, indent=2)
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
        accounts = get_chart_of_accounts(cfg)
    except (AuthError, ZohoAPIError) as exc:
        print(f"Error fetching Chart of Accounts: {exc}")
        return 1

    print(f"Chart of Accounts loaded: {len(accounts)} account(s) total.")

    results = {}
    unmatched = []
    failed = []

    for name in LEDGER_NAMES:
        account_id = find_account_id(accounts, name)
        if not account_id:
            print(f"WARNING: no Chart of Accounts entry found for {name!r} -- skipping.")
            unmatched.append(name)
            continue

        try:
            journal_summaries = list_journals(cfg, account_id=account_id)
        except (AuthError, ZohoAPIError) as exc:
            print(f"ERROR listing journals for {name!r} (account_id={account_id}): {exc}")
            failed.append(name)
            continue

        journal_summaries = [
            j for j in journal_summaries if _in_date_range(j.get("date"), args.date_from, args.date_to)
        ]
        unposted_journal_count = sum(
            1 for j in journal_summaries if j.get("status") != PUBLISHED_JOURNAL_STATUS
        )
        journal_summaries = [
            j for j in journal_summaries if j.get("status") == PUBLISHED_JOURNAL_STATUS
        ]

        entries = []
        detail_errors = 0
        for summary in journal_summaries:
            try:
                detail = get_journal(cfg, summary["journal_id"])
            except (AuthError, ZohoAPIError) as exc:
                print(f"  ERROR fetching journal {summary['journal_id']} detail: {exc}")
                detail_errors += 1
                continue
            entries.extend(_line_entries_for_account(detail, account_id))

        # Bills are a best-effort second source: if listing them fails, still
        # ship the journal-derived entries rather than losing the whole ledger.
        try:
            bill_summaries = list_bills(cfg, account_id=account_id)
        except (AuthError, ZohoAPIError) as exc:
            print(f"  WARNING: could not list bills for {name!r} (account_id={account_id}): {exc}")
            bill_summaries = []

        bill_summaries = [
            b for b in bill_summaries if _in_date_range(b.get("date"), args.date_from, args.date_to)
        ]
        unposted_bill_count = sum(
            1 for b in bill_summaries if b.get("status") in EXCLUDED_BILL_STATUSES
        )
        bill_summaries = [
            b for b in bill_summaries if b.get("status") not in EXCLUDED_BILL_STATUSES
        ]
        for summary in bill_summaries:
            try:
                detail = get_bill(cfg, summary["bill_id"])
            except (AuthError, ZohoAPIError) as exc:
                print(f"  ERROR fetching bill {summary['bill_id']} detail: {exc}")
                detail_errors += 1
                continue
            entries.extend(_line_entries_for_bill_account(detail, account_id))

        results[name] = {"account_id": account_id, "entries": entries}
        exclusion_note = ""
        if unposted_journal_count or unposted_bill_count:
            exclusion_note = (
                f" [excluded {unposted_journal_count} non-published journal(s), "
                f"{unposted_bill_count} draft/void bill(s) -- not posted to the ledger]"
            )
        print(
            f"{name}: account_id={account_id}, {len(journal_summaries)} journal(s) + "
            f"{len(bill_summaries)} bill(s), {len(entries)} line entry(ies)"
            + (f" ({detail_errors} detail fetch error(s))" if detail_errors else "")
            + exclusion_note
        )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print()
    print(f"Wrote results to {args.out}")

    if _update_dashboard_html(results, LEDGERS_DASHBOARD_HTML):
        print(f"Updated embedded data in {LEDGERS_DASHBOARD_HTML}")
        print()
        print(f"{LEDGERS_DASHBOARD_HTML} updated with this run's data -- just open it "
              "directly, no server needed.")
    else:
        print(f"Warning: could not find the ledgers-data script block in "
              f"{LEDGERS_DASHBOARD_HTML}; it was not updated.")

    if unmatched:
        print(f"{len(unmatched)} ledger(s) had no matching Chart of Accounts entry: {', '.join(unmatched)}")
    if failed:
        print(f"{len(failed)} ledger(s) failed to fetch: {', '.join(failed)}")
    if not unmatched and not failed:
        print(f"All {len(LEDGER_NAMES)} ledger(s) fetched successfully.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

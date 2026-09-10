# Zoho ERP Pay Order Dashboards

Two self-contained HTML dashboards, backed by small Python scripts that pull data from the Zoho ERP API:

- **`journals_dashboard.html`** — every journal entry whose reference number is some spelling/casing of "Pay Order" (e.g. `PAY ORDER`, `Pay-Order`, `pay order`), across the whole org.
- **`ledgers_dashboard.html`** — General Ledger-style transaction history for three specific expense accounts (`OFFICE 1 WASHROOM PROJECT`, `OFFICE 2 PROJECT.`, `Food and Brev E`), each viewable independently.

Both dashboards are **plain HTML files with the data embedded inline** (`<script type="application/json">`). There is no server, no build step, and no `fetch()` call anywhere — open either file by double-clicking it, from anywhere, offline. A Python script refreshes the embedded data by fetching from Zoho and rewriting that one `<script>` block in place; everything else in the file is untouched.

## Requirements

- Python 3.9+
- A Zoho ERP organization with API access (Client ID/Secret from the [Zoho API Console](https://api-console.zoho.com/), scope `ERP.accountants.READ`)
- Dependencies: `pip install -r requirements.txt` (`requests`, `python-dotenv`)

## One-time setup

1. Copy `.env.example` to `.env` and fill in:
   - `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET` — from your Zoho API Console app
   - `ZOHO_ORGANIZATION_ID` — your Zoho ERP org ID
   - `ZOHO_ACCOUNTS_URL` (e.g. `https://accounts.zoho.in`) and `ZOHO_API_DOMAIN` (e.g. `https://www.zohoapis.in`) — match your Zoho data center
   - `ZOHO_GRANT_TOKEN` — a one-time authorization code generated from the API Console (self-client flow); this field is only needed for the next step and can be left blank afterward
2. Run the one-time token exchange:
   ```
   python token_exchange.py
   ```
   This trades the grant token for an access/refresh token pair and writes them into `.env` (`ZOHO_ACCESS_TOKEN`, `ZOHO_REFRESH_TOKEN`, `ZOHO_TOKEN_EXPIRES_AT`). Grant tokens are single-use and expire in ~10 minutes — if this fails, generate a fresh one and retry. You only need to do this once; every other script auto-refreshes the access token as needed.

## Day-to-day usage

```
python fetch_journals.py      # refresh journals_dashboard.html
python fetch_ledgers.py       # refresh ledgers_dashboard.html
```

Then just open `journals_dashboard.html` or `ledgers_dashboard.html` in a browser (double-click works fine — `file://` URLs, no server required).

### `fetch_journals.py`

Fetches every journal from Zoho, keeps only records whose reference number normalizes to "Pay Order" (whitespace/hyphens stripped, case-insensitive), writes them to `journals_output.json`, and rewrites the embedded data block in `journals_dashboard.html`.

```
python fetch_journals.py                                    # full refresh (default)
python fetch_journals.py --status draft                     # only draft journals
python fetch_journals.py --date-from 2026-01-01 --date-to 2026-06-30
python fetch_journals.py --id 3545384000001851001           # single journal detail (does NOT touch the dashboard)
python fetch_journals.py --out custom.json                  # write elsewhere instead of journals_output.json
```

### `fetch_ledgers.py`

For each of the three hardcoded ledger accounts (edit `LEDGER_NAMES` in the script to add/remove one), resolves its `account_id` via the Chart of Accounts, then merges two transaction sources:

- **Journals** touching the account, filtered to `status="published"`.
- **Bills** touching the account, excluding `status` of `"draft"` or `"void"` (a bill posts to its expense account when created, not when paid, so payment status like `open`/`paid` doesn't matter).

Both are merged into one `ledgers_output.json`, keyed by ledger name, and the same data refreshes `ledgers_dashboard.html`.

```
python fetch_ledgers.py
python fetch_ledgers.py --date-from 2026-01-01 --date-to 2026-06-30
python fetch_ledgers.py --out custom.json
```

## The dashboards

Both pages share a top nav bar — **Journals** (home) plus a **Ledgers** group linking directly to each of the three accounts — and the same visual language (light green page background, white cards with shadows, blue accent for interactive elements, status badges).

**`journals_dashboard.html`**: Search-free filter bar (Status dropdown + From/To date range + Clear filters), a sortable table (Date / Reference Number / Status / Total Amount), pagination, and KPI tiles (current date, Published count+total, Draft count+total) that recompute from whatever the filters currently show.

**`ledgers_dashboard.html`**: One ledger is shown at a time, selected via the nav (or the URL hash — `#office-1`, `#office-2`, `#food-and-brev-e`; switching hash on the same page swaps the view instantly, no reload). Filters (From/To date range + Clear filters) reset whenever you switch ledgers, since a leftover date range from one project's data would silently misrepresent another's. Table columns: Date / Reference Number / Description / Debit / Credit. Summary tiles show Total Entries / Total Debit / Total Credit / Net, recomputed from the filtered set.

## Project files

| File | Purpose |
|---|---|
| `zoho_client.py` | Shared client: `.env` config loading, OAuth token exchange/refresh/storage, and the Zoho ERP API calls (Journals, Bills, Chart of Accounts) used by both fetch scripts. |
| `token_exchange.py` | One-time script to complete the initial OAuth grant-token exchange. |
| `fetch_journals.py` | Refreshes `journals_output.json` and `journals_dashboard.html`. |
| `fetch_ledgers.py` | Refreshes `ledgers_output.json` and `ledgers_dashboard.html`. |
| `journals_dashboard.html` | The Journals dashboard (self-contained, data embedded). |
| `ledgers_dashboard.html` | The Ledgers dashboard (self-contained, data embedded). |
| `journals_output.json` | Raw fetched journal data (tracked in git). |
| `ledgers_output.json` | Raw fetched ledger data (gitignored — regenerate with `fetch_ledgers.py`). |
| `.env.example` | Template for the required environment variables. |
| `requirements.txt` | Python dependencies. |

## Notes

- **Read-only.** Nothing in this project creates, updates, or deletes anything in Zoho — every call is a `GET`, matching the `ERP.accountants.READ` scope. Safe to point at a live production org.
- Targets the **Zoho ERP India data center** (`.in` endpoints) by default — adjust `ZOHO_ACCOUNTS_URL`/`ZOHO_API_DOMAIN` in `.env` if your org is on a different data center.

## Known limitations

- **Ledger totals may still be a partial view.** `/erp/v3/expenses` and `/erp/v3/creditnotes` both return 401 under the current OAuth scope, so any cost posted to a ledger account through Zoho's Expense module (rather than a Journal or Bill) won't appear. This was verified against real numbers pulled from Zoho's own Account Transactions report for two of the three accounts — `OFFICE 1 WASHROOM PROJECT` and `OFFICE 2 PROJECT.` — where Journals + Bills matched Zoho's reported entry count and debit/credit totals exactly, suggesting this org doesn't route these particular accounts through Expenses. `Food and Brev E` was cross-checked via an exhaustive API scan (confirming the fetch logic itself misses nothing it *can* see) but not against Zoho's UI numbers directly, since none were available for it. `/erp/v3/vendorpayments` and `/erp/v3/customerpayments` were evaluated and deliberately excluded: they silently ignore the `account_id` filter parameter entirely, and are the wrong transaction type for this purpose regardless (they settle already-recorded bills/invoices rather than posting fresh amounts to an expense account).
- **`/erp/v3/reports/generalledger`** — the endpoint that would natively match Zoho's own ledger report — 401s regardless of scope, including after a clean grant-token regeneration with an added scope. Not currently usable.
- Both fetch scripts only pull **journal/bill summary data**, not full line-item detail, for the bulk list operations. `fetch_journals.py --id <journal_id>` fetches one journal's full detail (including line items) separately, writing to its own file rather than touching the dashboard.

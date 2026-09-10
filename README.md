# Zoho ERP Pay Order Dashboards

Two self-contained HTML dashboards, backed by small Python scripts that pull data from the Zoho ERP API:

- **`public/journals_dashboard.html`** — every journal entry whose reference number is some spelling/casing of "Pay Order" (e.g. `PAY ORDER`, `Pay-Order`, `pay order`), across the whole org.
- **`public/ledgers_dashboard.html`** — General Ledger-style transaction history for three specific expense accounts (`OFFICE 1 WASHROOM PROJECT`, `OFFICE 2 PROJECT.`, `Food and Brev E`), each viewable independently.

Both dashboards are **plain HTML files with the data embedded inline** (`<script type="application/json">`). There is no server and no build step — open either file by double-clicking it, from anywhere, offline. A Python script refreshes the embedded data by fetching from Zoho and rewriting that one `<script>` block in place; everything else in the file is untouched.

The data itself is never fetched by the browser. The only `fetch()` in either page is a ~36-byte poll of its own `*-version.json` fingerprint, used to notice a redeploy (see [Deployment](#deployment)); it fails harmlessly when the page is opened offline as a `file://` document, leaving the dashboard fully functional.

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
python fetch_journals.py      # refresh public/journals_dashboard.html
python fetch_ledgers.py       # refresh public/ledgers_dashboard.html
```

Run both from the **repo root** — the output paths in each script are relative to the working directory, not to the script's own location.

Then just open `public/journals_dashboard.html` or `public/ledgers_dashboard.html` in a browser (double-click works fine — `file://` URLs, no server required).

### `fetch_journals.py`

Fetches every journal from Zoho, keeps only records whose reference number normalizes to "Pay Order" (whitespace/hyphens stripped, case-insensitive), writes them to `journals_output.json`, and rewrites the embedded data block in `public/journals_dashboard.html`.

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

Both are merged into one `ledgers_output.json`, keyed by ledger name, and the same data refreshes `public/ledgers_dashboard.html`.

```
python fetch_ledgers.py
python fetch_ledgers.py --date-from 2026-01-01 --date-to 2026-06-30
python fetch_ledgers.py --out custom.json
```

## The dashboards

Both pages share a top nav bar — **Journals** (home) plus a **Ledgers** group linking directly to each of the three accounts — and the same visual language (light green page background, white cards with shadows, blue accent for interactive elements, status badges).

**`public/journals_dashboard.html`**: Search-free filter bar (Status dropdown + From/To date range + Clear filters), a sortable table (Date / Reference Number / Status / Total Amount), pagination, and KPI tiles (current date, Published count+total, Draft count+total) that recompute from whatever the filters currently show.

**`public/ledgers_dashboard.html`**: One ledger is shown at a time, selected via the nav (or the URL hash — `#office-1`, `#office-2`, `#food-and-brev-e`; switching hash on the same page swaps the view instantly, no reload). Filters (From/To date range + Clear filters) reset whenever you switch ledgers, since a leftover date range from one project's data would silently misrepresent another's. Table columns: Date / Reference Number / Description / Debit / Credit. Summary tiles show Total Entries / Total Debit / Total Credit / Net, recomputed from the filtered set.

## Deployment

The site is deployed on **Vercel** as pure static output — no build step, no serverless functions. `vercel.json` sets `outputDirectory` to `public`, so **`public/` is the only directory that ever ships**; the Python sources, `docs/`, and the raw `*_output.json` snapshots all stay off the deployment (`.vercelignore` keeps them out of the upload as well, so a misconfigured output directory still could not expose them).

Deploys are triggered by Vercel's own Git integration: `.github/workflows/fetch-zoho-data.yml` commits refreshed data to `main`, and every push to `main` redeploys. **The workflow holds no Vercel token and calls no Vercel API** — pushing to the branch is the entire trigger.

GitHub Pages is deliberately unused. Serving from both would mean two live copies drifting apart, so Pages should stay switched off in **Settings → Pages** (it is a repo setting, not a file in this repo).

### Picking up new data in an already-open tab

`vercel.json` sends `Cache-Control: max-age=0, must-revalidate` for the dashboards, so a reload can never be served a stale copy — something GitHub Pages could not do, since it pins everything to `max-age=600` with no override. That is why the old `http-equiv` cache tags and the 10-minute `<meta refresh>` are gone from both pages.

In their place, each fetch script writes a fingerprint of the data it just embedded to `public/journals-version.json` / `public/ledgers-version.json`, and each dashboard polls **its own** file every 60 seconds:

- It is a **hash of the data, not a timestamp**, so a refresh run where Zoho returned identical data never disturbs viewers — only a real change does. (The `Last updated` stamp *does* change every run, by design; keying a reload off that would interrupt people for nothing.)
- On a change, the page shows a **"Newer Zoho data has been published"** banner with `Reload` / `Not now`. `Not now` accepts that version silently but still speaks up if a later refresh differs again.
- It **auto-reloads without asking only if the view is untouched** — no filters or date range, page 1, default sort — and only after 30 seconds of no interaction. Anyone mid-analysis keeps their filters, sort order, and page until they click `Reload` themselves.
- The poll never touches Zoho. Zoho load depends solely on how often the workflow runs, so poll frequency costs nothing but a 36-byte CDN request per tab per minute.

## Project files

| File | Purpose |
|---|---|
| `zoho_client.py` | Shared client: `.env` config loading, OAuth token exchange/refresh/storage, and the Zoho ERP API calls (Journals, Bills, Chart of Accounts) used by both fetch scripts. |
| `token_exchange.py` | One-time script to complete the initial OAuth grant-token exchange. |
| `fetch_journals.py` | Refreshes `journals_output.json`, `public/journals_dashboard.html`, and `public/journals-version.json`. |
| `fetch_ledgers.py` | Refreshes `ledgers_output.json`, `public/ledgers_dashboard.html`, and `public/ledgers-version.json`. |
| `public/journals_dashboard.html` | The Journals dashboard (self-contained, data embedded). |
| `public/ledgers_dashboard.html` | The Ledgers dashboard (self-contained, data embedded). |
| `public/index.html` | Redirects `/` to the Journals dashboard. |
| `public/*-version.json` | Data fingerprints the open dashboards poll to detect a redeploy. Generated — do not hand-edit. |
| `vercel.json` | Vercel config: static output from `public/`, plus the cache headers that keep deploys from being served stale. |
| `.vercelignore` | Keeps everything outside `public/` out of the Vercel upload entirely. |
| `journals_output.json` | Raw fetched journal data (tracked in git). |
| `ledgers_output.json` | Raw fetched ledger data (tracked in git). |
| `.env.example` | Template for the required environment variables. |
| `requirements.txt` | Python dependencies. |

## Notes

- **Read-only.** Nothing in this project creates, updates, or deletes anything in Zoho — every call is a `GET`, matching the `ERP.accountants.READ` scope. Safe to point at a live production org.
- **No Zoho credential ever reaches the deployed site.** `.env` is gitignored (and never committed — verify with `git log --all -- .env`), the GitHub Action reconstructs it from GitHub Secrets into the ephemeral runner only, and `.vercelignore` excludes it from the Vercel upload on top of it living outside `public/`. Nothing under `public/` contains a client ID, secret, or token.
- **Who can read the deployed data is controlled in Vercel, not in this repo.** The embedded JSON carries the full raw Zoho records — `notes` free text, entry numbers, branch/location IDs, every amount — and a Vercel production URL is public by default. This project is therefore meant to run with **Deployment Protection turned on** (Vercel → Project → Settings → Deployment Protection). Nothing in the repo can enforce that: if protection is ever switched off, every record becomes readable by anyone who has the URL.
- Deployment Protection does **not** break the auto-refresh. The version-file poll is a same-origin `fetch()`, so it rides the same session cookie the viewer already authenticated with. If that session lapses, the poll fails quietly (a non-OK response is ignored) and the page keeps working with the data it loaded — it just stops noticing new deploys until a manual reload, which will prompt re-authentication.
- Targets the **Zoho ERP India data center** (`.in` endpoints) by default — adjust `ZOHO_ACCOUNTS_URL`/`ZOHO_API_DOMAIN` in `.env` if your org is on a different data center.

## Known limitations

- **Ledger totals may still be a partial view.** `/erp/v3/expenses` and `/erp/v3/creditnotes` both return 401 under the current OAuth scope, so any cost posted to a ledger account through Zoho's Expense module (rather than a Journal or Bill) won't appear. This was verified against real numbers pulled from Zoho's own Account Transactions report for two of the three accounts — `OFFICE 1 WASHROOM PROJECT` and `OFFICE 2 PROJECT.` — where Journals + Bills matched Zoho's reported entry count and debit/credit totals exactly, suggesting this org doesn't route these particular accounts through Expenses. `Food and Brev E` was cross-checked via an exhaustive API scan (confirming the fetch logic itself misses nothing it *can* see) but not against Zoho's UI numbers directly, since none were available for it. `/erp/v3/vendorpayments` and `/erp/v3/customerpayments` were evaluated and deliberately excluded: they silently ignore the `account_id` filter parameter entirely, and are the wrong transaction type for this purpose regardless (they settle already-recorded bills/invoices rather than posting fresh amounts to an expense account).
- **`/erp/v3/reports/generalledger`** — the endpoint that would natively match Zoho's own ledger report — 401s regardless of scope, including after a clean grant-token regeneration with an added scope. Not currently usable.
- **Per-record detail is fetched, but only partly kept.** Both scripts call a single-record endpoint on top of the bulk list, because the list responses omit fields the dashboards need. `fetch_journals.py` calls `get_journal()` once per matched Pay Order journal purely to read its `notes` into the Description column; the record it embeds is still the *list* record with that one field bolted on, which is why every entry's `account_entries` is empty (`raw.line_items` likewise). `fetch_ledgers.py` calls `get_journal()`/`get_bill()` per record out of necessity — the per-account debit/credit split exists only inside each record's `line_items`. That is one extra API call per record (200+ in a full journals run), which is why `zoho_client.py` paces every request `MIN_REQUEST_INTERVAL` apart to stay under Zoho's 100-per-minute cap. `fetch_journals.py --id <journal_id>` remains the only way to get one journal's complete detail on disk, and writes to its own file rather than touching the dashboard.

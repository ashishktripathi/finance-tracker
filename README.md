---
title: Finance Tracker
emoji: 📊
colorFrom: blue
colorTo: gray
sdk: streamlit
app_file: app/main.py
pinned: false
---

# Finance Tracker

Personal financial data platform. V1 domain: investments (Wealthsimple, 7 accounts).
Design: raw statements live in Google Drive (immutable, synced); this repo holds code +
reference data (GitHub-synced); the DuckDB warehouse is **local, disposable, rebuilt from raw**
— never sync it.

## Layout
```
Google Drive/.../Finance/raw/wealthsimple/YYYY/YYYY-MM/*.csv   <- drop statements here, ORIGINAL filenames
this repo:
  reference/accounts.yaml   <- account registry (add accounts HERE, not in code)
  reference/aliases.csv     <- security-name -> ticker fixes (e.g. "Shell Plc" -> SHEL)
  src/tracker/              <- ingest / load / (transform, analytics: phase 2+)
  app/main.py               <- Streamlit dashboard
  tests/                    <- golden-file tests; fixtures = Sept 2026 statements
  data/warehouse.duckdb     <- generated; gitignored
```

## Monthly runbook (~20 min)
1. Download each account's monthly transactions CSV from Wealthsimple
   -> drop into `Finance/raw/wealthsimple/YYYY/YYYY-MM/` (do not rename).
2. `make ingest MONTH=YYYY-MM` — read the validation report it prints:
   unparsed rows or `ALIAS?:` tickers mean reference data needs a line, not code.
3. `make report` — review the dashboard; log decisions.
4. Commit reference-data changes; tag the month.

## Setup on a new laptop
```
git clone <repo> && cd finance-tracker
make install
make rebuild RAW="<path to Finance/raw/wealthsimple>"   # rebuilds warehouse from all months
```

## Cloud deploy (Streamlit Community Cloud)
The app runs hosted at share.streamlit.io, deployed from this private repo:
- Data source in the cloud: CSVs committed under `data_cloud/wealthsimple/YYYY/YYYY-MM/`
  (the warehouse rebuilds itself on boot — same disposable-DB rule as local).
- Access: set the `APP_PASSWORD` secret in the app's Settings → Secrets
  (`APP_PASSWORD = "..."`); the app shows a password gate when it's set.
  No secret set = open local mode.
- Monthly: drop the new month's CSVs in BOTH Drive (`raw/`) and `data_cloud/`,
  commit + push → the cloud app redeploys itself with the new month.

## Rules of the codebase
- Parsers change only with a failing golden test first (`tests/`).
- New account = entry in `accounts.yaml`. New institution = new module in `src/tracker/ingest/`.
- The dashboard reads Gold/Silver tables only; no business logic in `app/`.
- `data/` and anything synced by Drive never goes in git (see .gitignore).
- Scope creep goes to FUTURE.md, not into the sprint.

## Phase status
- [x] Phase 1 — WS parser (7 accounts), DuckDB load, idempotent ingest, validation, v1 dashboard, 11 golden tests
- [ ] Phase 2 — history backfill, yfinance prices + BoC FX, XIRR/TWR vs benchmark, ACB / departure-tax meter
- [ ] Phase 3 — rules engine (cluster caps, action-plan compliance), behavior tagging, decisions journal
- [ ] Phase 4 — monthly scheduled reminder + assisted run
- [ ] V2 — TD (corporate) parser; expenses & tax domains

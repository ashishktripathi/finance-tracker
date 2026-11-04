"""CLI entrypoint: python -m tracker.pipeline ingest <raw-root> <YYYY-MM>

raw-root is the Drive data home, e.g. ".../Finance/raw/wealthsimple".
Idempotent: safe to re-run a month.
"""
from __future__ import annotations

import sys
from pathlib import Path

from tracker.ingest.wealthsimple import parse_month_dir
from tracker.load import connect, load_aliases, refresh_accounts_table, upsert_transactions, validation_report


def ingest(raw_root: str, period: str) -> None:
    year = period.split("-")[0]
    month_dir = Path(raw_root) / year / period
    df = parse_month_dir(month_dir, load_aliases())
    con = connect()
    refresh_accounts_table(con)
    stats = upsert_transactions(con, df)
    print(f"Loaded {month_dir}: {stats}")
    print(validation_report(con, period))
    con.close()


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "ingest":
        sys.exit("usage: python -m tracker.pipeline ingest <raw-root> <YYYY-MM>")
    ingest(sys.argv[2], sys.argv[3])

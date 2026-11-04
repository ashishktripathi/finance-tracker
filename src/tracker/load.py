"""Load parsed transactions into the DuckDB warehouse (idempotent) and report validation."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
DB_PATH = REPO / "data" / "warehouse.duckdb"
REFERENCE = REPO / "reference"


def load_accounts() -> pd.DataFrame:
    cfg = yaml.safe_load((REFERENCE / "accounts.yaml").read_text())
    rows = [{"account_id": k, **v} for k, v in cfg["accounts"].items()]
    return pd.DataFrame(rows)


def load_aliases() -> dict[str, str]:
    p = REFERENCE / "aliases.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    return dict(zip(df.raw_name, df.ticker))


def connect() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            txn_id TEXT PRIMARY KEY, account_id TEXT, stmt_period TEXT, row_seq INT,
            date DATE, type TEXT, ticker TEXT, security_name TEXT,
            shares DOUBLE, price DOUBLE, amount DOUBLE, balance DOUBLE,
            currency TEXT, fx_rate DOUBLE, exec_date DATE, record_date DATE, description TEXT
        )""").fetchall()
    return con


def upsert_transactions(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> dict:
    before = con.execute("SELECT count(*) FROM transactions").fetchall()[0][0]
    con.register("staged", df)
    con.execute("""
        INSERT INTO transactions
        SELECT * FROM staged s
        WHERE NOT EXISTS (SELECT 1 FROM transactions t WHERE t.txn_id = s.txn_id)
    """).fetchall()
    after = con.execute("SELECT count(*) FROM transactions").fetchall()[0][0]
    con.unregister("staged")
    return {"staged": len(df), "inserted": after - before, "skipped_dupes": len(df) - (after - before)}


def refresh_accounts_table(con: duckdb.DuckDBPyConnection) -> None:
    acc = load_accounts()
    con.register("acc_staged", acc)
    con.execute("CREATE OR REPLACE TABLE accounts AS SELECT * FROM acc_staged").fetchall()
    con.unregister("acc_staged")


def validation_report(con: duckdb.DuckDBPyConnection, period: str) -> str:
    q = lambda sql: con.execute(sql).fetchdf()  # noqa: E731
    lines = [f"=== VALIDATION — statement period {period} ==="]

    by_acct = q(f"""
        SELECT a.name, t.account_id, count(*) AS rows,
               sum(CASE WHEN t.type IN ('LOAN','RECALL') THEN 1 ELSE 0 END) AS lending_rows,
               sum(CASE WHEN t.type='UNPARSED' THEN 1 ELSE 0 END) AS unparsed
        FROM transactions t JOIN accounts a USING(account_id)
        WHERE stmt_period='{period}' GROUP BY 1,2 ORDER BY 1""")
    lines.append(by_acct.to_string(index=False))

    unparsed = q(f"SELECT account_id, date, description FROM transactions WHERE stmt_period='{period}' AND type='UNPARSED'")
    lines.append(f"\nUnparsed rows: {len(unparsed)}" + ("" if unparsed.empty else "\n" + unparsed.to_string(index=False)))

    alias_misses = q(f"SELECT DISTINCT ticker FROM transactions WHERE stmt_period='{period}' AND ticker LIKE 'ALIAS?:%'")
    lines.append(f"Unmapped security names needing aliases.csv entries: {len(alias_misses)}"
                 + ("" if alias_misses.empty else "\n" + alias_misses.to_string(index=False)))

    summary = q(f"""
        SELECT type, count(*) AS n, round(sum(amount),2) AS net_amount
        FROM transactions WHERE stmt_period='{period}' AND type NOT IN ('LOAN','RECALL')
        GROUP BY 1 ORDER BY n DESC""")
    lines.append("\nActivity summary (ex-lending):\n" + summary.to_string(index=False))
    return "\n".join(lines)

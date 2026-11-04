"""Finance Tracker dashboard (v1).

Run locally:  streamlit run app/main.py
Reads only the DuckDB warehouse; no logic beyond display lives here.
"""
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

DB = Path(__file__).resolve().parents[1] / "data" / "warehouse.duckdb"

st.set_page_config(page_title="Finance Tracker", layout="wide")
st.title("Finance Tracker")

if not DB.exists():
    st.error("No warehouse found. Run `make ingest MONTH=YYYY-MM` first.")
    st.stop()

con = duckdb.connect(str(DB), read_only=True)
q = lambda sql: con.execute(sql).fetchdf()  # noqa: E731

periods = q("SELECT DISTINCT stmt_period FROM transactions ORDER BY 1")["stmt_period"].tolist()
period = st.sidebar.selectbox("Statement period", periods, index=len(periods) - 1)
accounts = q("SELECT account_id, name, managed FROM accounts ORDER BY name")
sel_accounts = st.sidebar.multiselect(
    "Accounts", accounts.name.tolist(), default=accounts.name.tolist()
)
acct_ids = accounts[accounts.name.isin(sel_accounts)].account_id.tolist()
acct_filter = "','".join(acct_ids)

base = f"stmt_period='{period}' AND account_id IN ('{acct_filter}')"

# ---------------- KPIs ----------------
k = q(f"""
    SELECT
      round(sum(CASE WHEN type='DIV' THEN amount END),2)                       AS dividends,
      round(sum(CASE WHEN type='NRT' THEN amount END),2)                       AS withholding,
      round(sum(CASE WHEN type='BUY' THEN -amount END),2)                      AS bought,
      round(sum(CASE WHEN type='SELL' THEN amount END),2)                      AS sold,
      round(sum(CASE WHEN type IN ('FEE') THEN amount END),2)                  AS fees,
      round(sum(CASE WHEN type='FPLINT' THEN amount END),2)                    AS lending_income,
      count(DISTINCT CASE WHEN type IN ('BUY','SELL','DIV') THEN ticker END)   AS active_tickers
    FROM transactions WHERE {base}
""").iloc[0]

c = st.columns(6)
c[0].metric("Dividends", f"${k.dividends or 0:,.2f}")
c[1].metric("US withholding (NRT)", f"${k.withholding or 0:,.2f}")
c[2].metric("Bought", f"${k.bought or 0:,.2f}")
c[3].metric("Sold", f"${k.sold or 0:,.2f}")
c[4].metric("Fees", f"${k.fees or 0:,.2f}")
c[5].metric("Active tickers", int(k.active_tickers or 0))

tab_act, tab_inc, tab_trades, tab_costs, tab_raw = st.tabs(
    ["Activity", "Income", "Trades", "Costs & FX", "Raw"]
)

with tab_act:
    df = q(f"""
        SELECT a.name AS account, t.type, count(*) AS n, round(sum(t.amount),2) AS net
        FROM transactions t JOIN accounts a USING(account_id)
        WHERE {base} AND t.type NOT IN ('LOAN','RECALL')
        GROUP BY 1,2 ORDER BY 1,2
    """)
    st.plotly_chart(
        px.bar(df, x="account", y="n", color="type", title="Transactions by account"),
        use_container_width=True,
    )
    st.dataframe(df, use_container_width=True)

with tab_inc:
    df = q(f"""
        SELECT ticker, a.name AS account, round(sum(amount),2) AS dividends, max(t.currency) AS ccy
        FROM transactions t JOIN accounts a USING(account_id)
        WHERE {base} AND type='DIV' GROUP BY 1,2 ORDER BY dividends DESC
    """)
    st.plotly_chart(
        px.bar(df, x="ticker", y="dividends", color="account", title="Dividend income by security"),
        use_container_width=True,
    )
    nrt = q(f"""
        SELECT a.name AS account, round(-sum(amount),2) AS withholding_lost
        FROM transactions t JOIN accounts a USING(account_id)
        WHERE {base} AND type='NRT' GROUP BY 1 ORDER BY 2 DESC
    """)
    st.subheader("US withholding tax leak by account (move these payers to RRSP)")
    st.dataframe(nrt, use_container_width=True)

with tab_trades:
    df = q(f"""
        SELECT t.date, a.name AS account, t.type, t.ticker, t.shares, t.price,
               t.amount, t.currency
        FROM transactions t JOIN accounts a USING(account_id)
        WHERE {base} AND type IN ('BUY','SELL') ORDER BY t.date, t.ticker
    """)
    st.dataframe(df, use_container_width=True, height=500)
    flow = q(f"""
        SELECT ticker, round(sum(CASE WHEN type='BUY' THEN -amount ELSE 0 END)
                     - sum(CASE WHEN type='SELL' THEN amount ELSE 0 END),2) AS net_invested
        FROM transactions WHERE {base} AND type IN ('BUY','SELL')
        GROUP BY 1 HAVING abs(net_invested) > 100 ORDER BY net_invested DESC
    """)
    st.plotly_chart(
        px.bar(flow, x="ticker", y="net_invested",
               title="Net new money by security (+ = net buy, − = net sell)"),
        use_container_width=True,
    )

with tab_costs:
    fx = q(f"""
        SELECT date, a.name AS account, fx_rate, amount, t.currency
        FROM transactions t JOIN accounts a USING(account_id)
        WHERE {base} AND type='FXCONVERSION' ORDER BY date
    """)
    st.subheader("FX conversions (watch the rate drift — batch these)")
    st.dataframe(fx, use_container_width=True)
    other = q(f"""
        SELECT type, round(sum(amount),2) AS total
        FROM transactions WHERE {base} AND type IN ('FEE','FPLINT','TRFINTF')
        GROUP BY 1
    """)
    st.dataframe(other, use_container_width=True)

with tab_raw:
    st.dataframe(
        q(f"SELECT * FROM transactions WHERE {base} ORDER BY account_id, row_seq"),
        use_container_width=True, height=600,
    )

con.close()

"""Finance Tracker dashboard (v1).

Run locally:  streamlit run app/main.py
Reads only the DuckDB warehouse; no logic beyond display lives here.
"""
import hmac
import sys
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # so `tracker` imports without PYTHONPATH

DB = ROOT / "data" / "warehouse.duckdb"
CLOUD_DATA = ROOT / "data_cloud" / "wealthsimple"

st.set_page_config(page_title="Finance Tracker", layout="wide")


def _secret(name: str) -> str:
    try:
        return st.secrets.get(name, "")
    except Exception:  # no secrets.toml configured (plain local run)
        return ""


def check_password() -> None:
    """Gate the whole app behind APP_PASSWORD when that secret is set (cloud deploys)."""
    pw = _secret("APP_PASSWORD")
    if not pw or st.session_state.get("auth_ok"):
        return
    st.title("Finance Tracker")
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        if st.form_submit_button("Enter"):
            if hmac.compare_digest(entered, pw):
                st.session_state["auth_ok"] = True
                st.rerun()
            else:
                st.error("Wrong password")
    st.stop()


check_password()
st.title("Finance Tracker")


@st.cache_resource
def bootstrap_warehouse() -> bool:
    """On a fresh container (cloud deploy), rebuild the warehouse from CSVs
    committed under data_cloud/wealthsimple/YYYY/YYYY-MM/. Locally, the
    existing warehouse built by `make ingest` is used as-is."""
    if DB.exists():
        return True
    if not CLOUD_DATA.exists():
        return False
    from tracker.ingest.wealthsimple import parse_month_dir
    from tracker.load import connect, load_aliases, refresh_accounts_table, upsert_transactions

    month_dirs = sorted(d for d in CLOUD_DATA.glob("*/*") if d.is_dir())
    if not month_dirs:
        return False
    con = connect()
    refresh_accounts_table(con)
    aliases = load_aliases()
    for d in month_dirs:
        upsert_transactions(con, parse_month_dir(d, aliases))
    con.close()
    return True


if not bootstrap_warehouse():
    st.error("No warehouse found. Run `make ingest MONTH=YYYY-MM` locally, or commit "
             "statement CSVs under data_cloud/wealthsimple/YYYY/YYYY-MM/ for cloud deploys.")
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
c[2].metric("Bought", f"${k.bought or 0:,.0f}")
c[3].metric("Sold", f"${k.sold or 0:,.0f}")
c[4].metric("Fees", f"${k.fees or 0:,.2f}")
c[5].metric("Active tickers", int(k.active_tickers or 0))

tab_sum, tab_act, tab_inc, tab_trades, tab_costs, tab_raw = st.tabs(
    ["Summary", "Activity", "Income", "Trades", "Costs & FX", "Raw"]
)

with tab_sum:
    st.subheader(f"Month in review — {period}")
    st.caption("CAD and USD amounts are shown in their own currency (FX normalization comes in phase 2).")

    cur = q(f"""
        SELECT currency,
               round(sum(CASE WHEN type='BUY'    THEN -amount END),0) AS bought,
               round(sum(CASE WHEN type='SELL'   THEN  amount END),0) AS sold,
               round(sum(CASE WHEN type='DIV'    THEN  amount END),2) AS dividends,
               round(sum(CASE WHEN type='NRT'    THEN  amount END),2) AS withholding,
               round(sum(CASE WHEN type='FEE'    THEN  amount END),2) AS fees,
               round(sum(CASE WHEN type='FPLINT' THEN  amount END),2) AS lending
        FROM transactions WHERE {base} AND currency IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """).fillna(0)
    for _, r in cur.iterrows():
        net = (r.bought or 0) - (r.sold or 0)
        direction = "net ADDED to the market" if net >= 0 else "net TAKEN OUT of the market"
        st.markdown(
            f"**{r.currency}** &nbsp;·&nbsp; bought **${r.bought:,.0f}**, sold **${r.sold:,.0f}** "
            f"→ **${abs(net):,.0f} {direction}** &nbsp;·&nbsp; income ${r.dividends + r.lending:,.2f} "
            f"&nbsp;·&nbsp; costs ${abs(r.withholding) + abs(r.fees):,.2f}"
        )

    c1, c2 = st.columns(2)
    mv = q(f"""
        SELECT ticker,
               round(sum(CASE WHEN type='BUY' THEN -amount ELSE 0 END)
                   - sum(CASE WHEN type='SELL' THEN amount ELSE 0 END),0) AS net_invested
        FROM transactions WHERE {base} AND type IN ('BUY','SELL')
        GROUP BY 1
    """)
    with c1:
        st.markdown("**Biggest adds (net new money)**")
        st.dataframe(mv.sort_values("net_invested", ascending=False).head(5),
                     use_container_width=True, hide_index=True)
    with c2:
        st.markdown("**Biggest exits / trims**")
        st.dataframe(mv.sort_values("net_invested").head(5),
                     use_container_width=True, hide_index=True)

    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**Top income payers**")
        st.dataframe(q(f"""
            SELECT ticker, round(sum(amount),2) AS dividends, max(currency) AS ccy
            FROM transactions WHERE {base} AND type='DIV'
            GROUP BY 1 ORDER BY 2 DESC LIMIT 5
        """), use_container_width=True, hide_index=True)
    with c4:
        st.markdown("**Account cash flows**")
        st.dataframe(q(f"""
            SELECT a.name AS account,
                   round(sum(CASE WHEN t.type='BUY'  THEN -t.amount END),0) AS bought,
                   round(sum(CASE WHEN t.type='SELL' THEN  t.amount END),0) AS sold,
                   round(sum(CASE WHEN t.type='DIV'  THEN  t.amount END),2) AS dividends
            FROM transactions t JOIN accounts a USING(account_id)
            WHERE {base} GROUP BY 1 ORDER BY bought DESC NULLS LAST
        """), use_container_width=True, hide_index=True)

    # ---- auto-generated flags ----
    st.markdown("---")
    st.markdown("**What stands out**")
    flags = []

    fx = q(f"""
        SELECT count(*) AS n, round(sum(CASE WHEN amount < 0 THEN -amount END),0) AS converted,
               min(fx_rate) AS lo, max(fx_rate) AS hi
        FROM transactions WHERE {base} AND type='FXCONVERSION'
    """).iloc[0]
    if fx.n and fx.n > 1 and fx.lo and fx.hi and fx.hi > fx.lo:
        flags.append(f"🔶 **{int(fx.n)} separate FX conversions** at rates {fx.lo:.2f}–{fx.hi:.2f}. "
                     f"Each small conversion locks in whatever rate that day offers — batch them.")

    leak = q(f"""
        SELECT a.name AS account, round(-sum(t.amount),2) AS lost
        FROM transactions t JOIN accounts a USING(account_id)
        WHERE {base} AND t.type='NRT' AND a.name NOT ILIKE '%rrsp%'
        GROUP BY 1 ORDER BY 2 DESC
    """)
    if not leak.empty and leak.lost.sum() > 0:
        flags.append(f"🔶 **US withholding tax outside the RRSP: ${leak.lost.sum():,.2f}** "
                     f"({', '.join(leak.account)}). US dividend payers belong in the RRSP, "
                     f"where the 15% is waived.")

    buys_total = mv[mv.net_invested > 0].net_invested.sum()
    if buys_total > 0 and not mv.empty:
        top = mv.sort_values("net_invested", ascending=False).iloc[0]
        share = top.net_invested / buys_total
        if share > 0.35:
            flags.append(f"🔶 **{top.ticker} took {share:.0%} of this month's net new money** "
                         f"(${top.net_invested:,.0f}). One conviction is fine; check it against "
                         f"your position-size caps.")

    unp = q(f"SELECT count(*) AS n FROM transactions WHERE {base} AND type='UNPARSED'").iloc[0].n
    if unp:
        flags.append(f"🔴 **{int(unp)} unparsed rows** — run the ingest validation and add "
                     f"aliases/patterns before trusting this month's numbers.")

    if not flags:
        flags.append("✅ Nothing unusual this month: no FX drift, no withholding leak, "
                     "no concentration spike, all rows parsed.")
    for f in flags:
        st.markdown(f)

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
        WHERE {base} AND t.type='DIV' GROUP BY 1,2 ORDER BY dividends DESC
    """)
    st.plotly_chart(
        px.bar(df, x="ticker", y="dividends", color="account", title="Dividend income by security"),
        use_container_width=True,
    )
    nrt = q(f"""
        SELECT a.name AS account, round(-sum(amount),2) AS withholding_lost
        FROM transactions t JOIN accounts a USING(account_id)
        WHERE {base} AND t.type='NRT' GROUP BY 1 ORDER BY 2 DESC
    """)
    st.subheader("US withholding tax leak by account (move these payers to RRSP)")
    st.dataframe(nrt, use_container_width=True)

with tab_trades:
    df = q(f"""
        SELECT t.date, a.name AS account, t.type, t.ticker, t.shares, t.price,
               t.amount, t.currency
        FROM transactions t JOIN accounts a USING(account_id)
        WHERE {base} AND t.type IN ('BUY','SELL') ORDER BY t.date, t.ticker
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
        WHERE {base} AND t.type='FXCONVERSION' ORDER BY date
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

"""Golden-file tests: the September 2026 statements are the contract.

If Wealthsimple changes their format, these fail loudly and point at the file.
"""
from pathlib import Path

import pytest

from tracker.ingest.wealthsimple import parse_file, parse_month_dir

FIXTURES = Path(__file__).parent / "fixtures" / "wealthsimple" / "2026-09"
ALIASES = {"Shell Plc": "SHEL"}


def df_for(name_part: str):
    (path,) = [p for p in FIXTURES.glob("*.csv") if name_part in p.name]
    return parse_file(path, ALIASES)


def test_all_seven_files_parse_without_unparsed_rows():
    df = parse_month_dir(FIXTURES, ALIASES)
    assert df.account_id.nunique() == 7
    unparsed = df[df.type == "UNPARSED"]
    assert unparsed.empty, unparsed.description.tolist()


def test_tfsa_trade_parsing():
    df = df_for("HQ5HC7ZK3")
    shop = df[(df.type == "SELL") & (df.ticker == "SHOP")]
    assert len(shop) == 3
    assert shop.shares.sum() == pytest.approx(119.0)
    assert shop.amount.sum() == pytest.approx(3540.60 + 9712.00 + 9078.00)
    dfn = df[(df.type == "BUY") & (df.ticker == "DFN")]
    assert dfn.shares.sum() == pytest.approx(417.2677 + 243.6053 + 221.0 + 4.4003)
    # SNDK single share at pre-split price
    sndk = df[(df.type == "BUY") & (df.ticker == "SNDK")].iloc[0]
    assert sndk.price == pytest.approx(1725.87)
    assert sndk.currency == "USD"


def test_shell_alias_resolves():
    df = df_for("HQ5HC7ZK3")
    shell = df[df.security_name == "Shell Plc"]
    assert (shell.ticker == "SHEL").all()


def test_dividend_fx_rate_captured():
    df = df_for("HQ5HC7ZK3")
    paas = df[(df.type == "DIV") & (df.ticker == "PAAS")].iloc[0]
    assert paas.fx_rate == pytest.approx(1.3894)
    assert paas.record_date == "2026-08-24"


def test_nrt_rows_have_no_ticker_but_amounts():
    df = df_for("HQ5HC7ZK3")
    nrt = df[df.type == "NRT"]
    assert len(nrt) == 2                      # VRT and META withholding
    assert nrt.amount.sum() == pytest.approx(-0.48)


def test_fx_conversions_capture_rate_and_both_sides():
    df = df_for("HQ5HC7ZK3")
    fx = df[df.type == "FXCONVERSION"]
    assert len(fx) == 8                       # 4 CAD-side + 4 USD-side rows
    assert set(fx.fx_rate.round(2)) == {1.40, 1.41, 1.44}


def test_loan_rows_isolated_and_zero_amount():
    df = df_for("HQBS9FH02")
    lending = df[df.type.isin(["LOAN", "RECALL"])]
    assert (lending.amount == 0).all()
    assert lending.ticker.notna().all()


def test_autoinvest_currency_column_quirk():
    """In the auto-invest file some LOAN/RECALL rows carry a *ticker* in the currency column."""
    df = df_for("WZ0PZNS05")
    weird = df[(df.type.isin(["LOAN", "RECALL"])) & (df.currency.isna())]
    assert len(weird) > 0                     # quirk exists and is handled (currency -> None)
    assert set(df.currency.dropna()) <= {"CAD", "USD"}


def test_fees_captured():
    df_auto = df_for("WZ0PZNS05")
    fee = df_auto[df_auto.type == "FEE"]
    assert fee.amount.sum() == pytest.approx(-8.08)
    df_inc = df_for("WK8MHR800")
    assert df_inc[df_inc.type == "FEE"].amount.sum() == pytest.approx(-7.30)


def test_loc_transfers_in():
    df = df_for("HQCTB0B05")
    tr = df[df.type == "TRFINTF"]
    assert tr.amount.sum() == pytest.approx(25000.0)


def test_idempotent_txn_ids_unique():
    df = parse_month_dir(FIXTURES, ALIASES)
    assert df.txn_id.is_unique

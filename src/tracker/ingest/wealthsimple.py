"""Wealthsimple monthly-statement CSV parser.

Input:  one CSV per account per month, filename pattern:
        <Account label>-monthly-statement-transactions-<ACCOUNTID>CAD-<YYYY-MM-DD>.csv
Output: a tidy DataFrame of typed transactions (one row per statement line).

Design rules
------------
- Raw files are never modified; parsing is pure.
- Every row gets a deterministic txn_id (hash of account, row order, content) so
  re-ingesting a month is idempotent.
- Unknown patterns never crash the load: they land with type='UNPARSED' and are
  surfaced by the validation report. Fix the parser (with a test), re-run.
"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd

FILENAME_RE = re.compile(
    r"^(?P<label>.+?)-monthly-statement-transactions-(?P<account>[A-Z0-9]+?)CAD-(?P<period>\d{4}-\d{2})-\d{2}\.csv$"
)

# --- description patterns -------------------------------------------------
TRADE_RE = re.compile(
    r"^(?P<ticker>[A-Za-z0-9.\-/]+|N/A)\s+-\s+(?P<name>.*?):\s+(?P<verb>Bought|Sold)\s+"
    r"(?P<shares>[\d,]+\.?\d*)\s+shares at \$(?P<price>[\d,]+\.?\d*) per share"
    r"\s*\(executed at (?P<exec_date>\d{4}-\d{2}-\d{2})\)"
    r"(?:,\s*FX Rate:\s*(?P<fx>[\d.]+))?",
)
DIV_RE = re.compile(
    r"^(?P<ticker>[A-Za-z0-9.\-/]+)\s+-\s+(?P<name>.*?):\s+Cash dividend distribution, "
    r"received on (?P<recv>\d{4}-\d{2}-\d{2}), record date of (?P<record>\d{4}-\d{2}-\d{2})"
    r"(?:,\s*FX Rate:\s*(?P<fx>[\d.]+))?",
)
LOAN_RE = re.compile(
    r"^(?P<ticker>[A-Za-z0-9.\-/]+)\s+-\s+(?P<name>.*?):\s+(?P<shares>[\d,]+\.?\d*)\s+Shares on loan"
    r"\s*\(executed at (?P<exec_date>\d{4}-\d{2}-\d{2})\)",
)
RECALL_RE = re.compile(
    r"^(?P<ticker>[A-Za-z0-9.\-/]+)\s+-\s+(?P<name>.*?):\s+Loan of (?P<shares>[\d,]+\.?\d*)\s+shares terminated"
    r"\s*\(executed at (?P<exec_date>\d{4}-\d{2}-\d{2})\)",
)
FX_RE = re.compile(
    r"^Convert (?P<from_ccy>CAD|USD)\s*\(executed at (?P<exec_date>\d{4}-\d{2}-\d{2})\)"
    r"\s*-\s*\$1USD = \$(?P<rate>[\d.]+)CAD",
)
NRT_RE = re.compile(r"^Non-resident tax \(executed at (?P<exec_date>\d{4}-\d{2}-\d{2})\)")

VALID_CCY = {"CAD", "USD"}


def _num(s: str | None) -> float | None:
    if s is None or s == "":
        return None
    return float(s.replace(",", ""))


@dataclass
class Txn:
    txn_id: str
    account_id: str
    stmt_period: str      # YYYY-MM of the statement file
    row_seq: int          # original row order within the file (audit trail)
    date: str
    type: str             # BUY SELL DIV NRT FXCONVERSION FPLINT FEE TRFINTF LOAN RECALL UNPARSED ...
    ticker: str | None
    security_name: str | None
    shares: float | None
    price: float | None
    amount: float
    balance: float | None
    currency: str | None  # CAD/USD; None where the statement puts a ticker here (loan rows)
    fx_rate: float | None
    exec_date: str | None
    record_date: str | None
    description: str


def parse_file(path: Path, aliases: dict[str, str] | None = None) -> pd.DataFrame:
    m = FILENAME_RE.match(path.name)
    if not m:
        raise ValueError(f"Filename does not match Wealthsimple pattern: {path.name}")
    account = m.group("account")
    period = m.group("period")
    aliases = aliases or {}

    rows: list[Txn] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for seq, rec in enumerate(reader, start=1):
            if not rec.get("date"):          # blank separator between CAD and USD sections
                continue
            desc = (rec.get("description") or "").strip()
            ttype = (rec.get("transaction") or "").strip()
            ccy = (rec.get("currency") or "").strip()
            currency = ccy if ccy in VALID_CCY else None   # loan rows sometimes carry a ticker here

            ticker = name = exec_date = record_date = None
            shares = price = fx = None

            if ttype in ("BUY", "SELL") and (g := TRADE_RE.match(desc)):
                ticker, name = g["ticker"], g["name"]
                shares, price = _num(g["shares"]), _num(g["price"])
                exec_date, fx = g["exec_date"], _num(g["fx"])
            elif ttype == "DIV" and (g := DIV_RE.match(desc)):
                ticker, name = g["ticker"], g["name"]
                record_date, fx = g["record"], _num(g["fx"])
            elif ttype == "LOAN" and (g := LOAN_RE.match(desc)):
                ticker, name, shares, exec_date = g["ticker"], g["name"], _num(g["shares"]), g["exec_date"]
            elif ttype == "RECALL" and (g := RECALL_RE.match(desc)):
                ticker, name, shares, exec_date = g["ticker"], g["name"], _num(g["shares"]), g["exec_date"]
            elif ttype == "FXCONVERSION" and (g := FX_RE.match(desc)):
                fx, exec_date = _num(g["rate"]), g["exec_date"]
            elif ttype == "NRT" and (g := NRT_RE.match(desc)):
                exec_date = g["exec_date"]
            elif ttype in ("FPLINT", "FEE", "TRFINTF"):
                pass                           # amount/date are enough
            elif desc and ttype:
                ttype = "UNPARSED"             # surfaced by validation, never dropped

            if ticker == "N/A" and name:
                ticker = aliases.get(name, f"ALIAS?:{name}")

            raw_key = f"{account}|{period}|{seq}|{rec['date']}|{ttype}|{rec['amount']}|{desc}"
            rows.append(Txn(
                txn_id=hashlib.sha1(raw_key.encode()).hexdigest()[:16],
                account_id=account, stmt_period=period, row_seq=seq,
                date=rec["date"], type=ttype, ticker=ticker, security_name=name,
                shares=shares, price=price, amount=_num(rec["amount"]) or 0.0,
                balance=_num(rec.get("balance")), currency=currency, fx_rate=fx,
                exec_date=exec_date, record_date=record_date, description=desc,
            ))

    return pd.DataFrame([asdict(r) for r in rows])


def parse_month_dir(month_dir: Path, aliases: dict[str, str] | None = None) -> pd.DataFrame:
    """Parse every statement CSV found in a YYYY-MM folder."""
    frames = [parse_file(p, aliases) for p in sorted(month_dir.glob("*.csv"))]
    if not frames:
        raise FileNotFoundError(f"No statement CSVs in {month_dir}")
    return pd.concat(frames, ignore_index=True)

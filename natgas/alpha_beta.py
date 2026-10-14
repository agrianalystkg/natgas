# -*- coding: utf-8 -*-
"""
alpha_beta.py — alpha/beta decomposition vs a previously saved benchmark run.

Concept (per legendo): every APPEND=1 backtest saves its monthly returns as a
column in Sheet3 of the shared results workbook (RESULTS_XLSX). A later run can
then declare `beta = "<run_name>"` in its config, and we regress

    r_strategy = alpha + beta * r_benchmark + eps

on the INTERSECTION of available months (histories may differ — e.g. a
10-year fundamental strategy vs a 30-year CTA trend benchmark; only the
overlapping months are used).

    beta            = slope  -> how much of the strategy IS generic CTA trend
    alpha (ann. %)  = intercept x 12 -> excess return the benchmark can't explain
    r2              -> how much of the variance the benchmark explains

Pure pandas/numpy, unit-testable. No zipline imports.
"""

import os
import numpy as np
import pandas as pd

MIN_OVERLAP_MONTHS = 24   # below this, beta/alpha are statistically meaningless


def load_monthly_returns_book(path):
    """Sheet3 -> DataFrame indexed by Month (tz-naive), one column per run.
    Missing file/sheet/column -> empty DataFrame (never raises)."""
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_excel(path, sheet_name="Sheet3")
    except Exception:
        return pd.DataFrame()
    if "Month" not in df.columns:
        return pd.DataFrame()
    df["Month"] = pd.to_datetime(df["Month"])
    return df.set_index("Month").sort_index()


def _to_naive_monthly(series):
    s = series.dropna().copy()
    s.index = pd.to_datetime(s.index)
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)
    return s


def compute_alpha_beta(strategy_monthly, bench_monthly,
                       min_overlap=MIN_OVERLAP_MONTHS):
    """OLS of strategy on benchmark over the overlapping months.

    Returns dict(beta, alpha_ann, r2, overlap_months). NaNs with
    overlap_months=n when the overlap is too small or benchmark is flat."""
    s = _to_naive_monthly(strategy_monthly).rename("s")
    b = _to_naive_monthly(bench_monthly).rename("b")
    df = pd.concat([s, b], axis=1, join="inner").dropna()
    n = len(df)
    res = {"beta": np.nan, "alpha_ann": np.nan, "r2": np.nan,
           "overlap_months": n}
    if n < min_overlap:
        return res
    var_b = df["b"].var()
    if not np.isfinite(var_b) or var_b == 0:
        return res
    beta = df["s"].cov(df["b"]) / var_b
    alpha_m = df["s"].mean() - beta * df["b"].mean()   # OLS intercept
    res.update({
        "beta": float(beta),
        "alpha_ann": float(alpha_m * 12),              # annualized % return
        "r2": float(df["s"].corr(df["b"]) ** 2),
    })
    return res


def append_monthly_returns_column(book, run_name, monthly_ret):
    """Return the Sheet3 book (Month-indexed) with run_name's monthly returns
    merged in (outer join on Month). An existing column with the same name is
    replaced, never duplicated."""
    col = _to_naive_monthly(monthly_ret).rename(run_name).to_frame()
    col.index.name = "Month"
    if book is None or book.empty:
        return col.sort_index()
    book = book.copy()
    if run_name in book.columns:
        book = book.drop(columns=[run_name])
    return book.join(col, how="outer").sort_index()

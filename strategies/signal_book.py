# -*- coding: utf-8 -*-
"""
signal_book.py — persist and load each run's daily signed conviction
(`direction * trendstrength`) from Results.xlsx.

Two sheets (so the combiner can use either):
  Signals_Raw     : the raw signed conviction exactly as logged (range ~ +/-cap)
  Signals_Scaled  : raw / mean(|raw|)  -> average absolute value = 1
                    (a common forecast scale, so weights are comparable)

All pure pandas; no zipline imports.
"""
import os
import numpy as np
import pandas as pd

SHEET_RAW = "Signals_Raw"
SHEET_SCALED = "Signals_Scaled"


def _clean(series, name):
    s = pd.Series(series).dropna().astype(float).copy()
    s.index = pd.to_datetime(s.index)
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.name = name
    return s


def rescale(series, name=None):
    """Divide by mean(|series|) so the average absolute forecast is 1."""
    s = _clean(series, name or getattr(series, "name", "signal"))
    m = float(np.nanmean(np.abs(s.values))) if len(s) else np.nan
    if m and np.isfinite(m) and m > 0:
        return (s / m).rename(s.name)
    return s


def load_book(path, sheet):
    """Return a Date-indexed DataFrame (one column per run); empty if missing."""
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_excel(path, sheet_name=sheet)
    except Exception:
        return pd.DataFrame()
    if "Date" not in df.columns:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"])
    return df.set_index("Date").sort_index()


def append_column(book, run_name, series):
    """Add/replace one run's daily signal column (outer-join on Date)."""
    col = _clean(series, run_name).to_frame()
    if book is None or book.empty:
        out = col
    else:
        book = book.drop(columns=[run_name], errors="ignore")
        out = book.join(col, how="outer")
    out.index.name = "Date"
    return out.sort_index()

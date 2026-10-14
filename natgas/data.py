# -*- coding: utf-8 -*-
"""
continuous_prices.py — read the locally built continuous-future price library
(see build_continuous_prices.py). These are zipline's own 'mul'-adjusted
prices, i.e. the exact series the backtests trade on.

    continuous_prices.csv         date-indexed; columns  <ROOT>_off<k>
    continuous_prices_detail.csv  long: date, root, offset, contract, raw_close, is_roll

Pure pandas — safe to import from any analyze() block.
"""
import functools
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, 'continuous_prices.csv')
DEFAULT_DETAIL_PATH = os.path.join(HERE, 'continuous_prices_detail.csv')


@functools.lru_cache(maxsize=4)
def _load_all(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index.name = 'date'
    return df.sort_index()


def col_name(root, offset):
    return f'{root.upper()}_off{int(offset)}'


def load_prices(root, offset, path=DEFAULT_PATH):
    """Return a naive-date-indexed pd.Series of the 'mul'-adjusted continuous
    close for (root, offset), or an empty Series if it was not built."""
    df = _load_all(path)
    col = col_name(root, offset)
    if df.empty or col not in df.columns:
        return pd.Series(dtype='float64', name=col)
    s = df[col].dropna().copy()
    s.index = s.index.tz_localize(None) if getattr(s.index, 'tz', None) else s.index
    return s.rename(col)


def available(path=DEFAULT_PATH):
    """List of buildable <ROOT>_off<k> columns."""
    return list(_load_all(path).columns)


@functools.lru_cache(maxsize=2)
def _load_detail(path):
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    df['date'] = pd.to_datetime(df['date'])
    return df


# barchart continuous CSVs — FALLBACK only, used when a (root, offset) was not
# built into the zipline library (e.g. thin chains past their depth).
# Set BARCHART_CONT_DIR to the folder holding the *_continuous_daily.csv files
# to enable the fallback series (optional; the zipline 'mul' library is preferred).
CONT_DIR = os.environ.get("BARCHART_CONT_DIR", "")
CONT_FILES = {
    'QY': 'eex_butter_continuous_daily.csv',
    'MR': 'eex_smp_continuous_daily.csv',
    'W5': 'eex_whey_continuous_daily.csv',
    'BD': 'cme_butter_continuous_daily.csv',
    'BJ': 'cme_cheese_continuous_daily.csv',
    'DL': 'cme_class_iii_continuous_daily.csv',
    'DK': 'cme_class_iv_continuous_daily.csv',
    'DG': 'cme_dry_whey_continuous_daily.csv',
    'DF': 'cme_nfdm_continuous_daily.csv',
    'K6': 'sgx_butter_continuous_daily.csv',
    'K4': 'sgx_smp_continuous_daily.csv',
    'K3': 'sgx_wmp_continuous_daily.csv',
    'NG': 'nymex_natural_gas_continuous_daily.csv',
    'ZC': 'cbot_corn_continuous_daily.csv',
    'ZS': 'cbot_soybeans_continuous_daily.csv',
    'ZM': 'cbot_soybean_meal_continuous_daily.csv',
    'ZW': 'cbot_wheat_continuous_daily.csv',
    'ML': 'matif_milling_wheat_continuous_daily.csv',
    'XR': 'euronext_rapeseed_continuous_daily.csv',
    'CB': 'ice_brent_continuous_daily.csv',
}


def _naive_ts(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize(None) if getattr(ts, 'tzinfo', None) is not None else ts


def load_barchart(root, start=None):
    """barchart `c3_panama` backadjusted close for a root (fallback series)."""
    f = CONT_FILES.get(root.upper())
    if not f:
        return pd.Series(dtype='float64', name=root.upper())
    path = os.path.join(CONT_DIR, f)
    if not os.path.exists(path):
        return pd.Series(dtype='float64', name=root.upper())
    s = pd.read_csv(path, usecols=['date', 'c3_panama'],
                    parse_dates=['date'], index_col=0)['c3_panama'].dropna()
    if start is not None:
        s = s[s.index >= _naive_ts(start)]
    return s.rename(root.upper())


def load_chart_price(root, offset, start=None):
    """Price series + label for the chart: zipline 'mul' if built, else barchart.

    `start` may be tz-aware (e.g. returns.index[0] from pyfolio); it is coerced
    to naive to match the price index. Returns (series, label) — the label says
    which source was used so the chart legend is honest about it.
    """
    s = load_prices(root, offset)
    if start is not None and len(s):
        s = s[s.index >= _naive_ts(start)]
    if len(s):
        return s, f'{root.upper()} zipline mul (off {int(offset)})'
    bc = load_barchart(root, start=start)
    return bc, f'{root.upper()} barchart c3_panama (fallback)'


def load_detail(root, offset, path=DEFAULT_DETAIL_PATH):
    """Diagnostics for (root, offset): active contract, raw close, roll flag."""
    df = _load_detail(path)
    if df.empty:
        return df
    return df[(df['root'] == root.upper()) & (df['offset'] == int(offset))]\
        .set_index('date').sort_index()

# -*- coding: utf-8 -*-
"""
carver_costs.py — Rob Carver risk-adjusted trading-cost analytics for our
zipline-reloaded backtests.

Measures the ACTUAL costs the backtest charged (ex-post) and expresses them in
Sharpe-ratio units, decomposed into:
    commission (trade legs) + slippage (trade legs) + rolling (roll legs)
so that:  Gross Sharpe - Total costs (SR) = Net Sharpe   (exact, pro-rata).

IMPORTANT zipline-reloaded quirk: perf['transactions'] entries always carry
commission=None (see zipline/finance/transaction.py to_dict). The cost models
are deterministic, so we mirror them exactly:
    commission_ccy = |amount| * params['commission'] * 2          (if enabled)
    slippage_ccy   = |amount| * asset.price_multiplier * spread (if enabled)
        spread = params['slippage_roll'] on roll days, else params['slippage']

Roll days are detected from perf['positions'] (held contract set per root
changes A -> B), allowing a small gap (close A, open B within GAP sessions).
This mirrors the INTENT of ROLL_MODE in RollAwareFixedSlippage.

Pure pandas/numpy — no zipline imports, unit-testable outside a backtest.
"""

import numpy as np
import pandas as pd

ROLL_GAP_SESSIONS = 3   # close-old / open-new may land on adjacent sessions
ANN = 252


# ── harvesting ───────────────────────────────────────────────────────────────

def _harvest_txns(perf):
    """Flatten perf['transactions'] -> DataFrame[dt, root, asset, amount, price, mult]."""
    rows = []
    for dt, txn_list in perf['transactions'].items():
        for t in txn_list:
            asset = t['sid']          # zipline-reloaded puts the Asset in 'sid'
            rows.append({
                'dt': dt,
                'root': asset.root_symbol,
                'asset': asset,
                'amount': t['amount'],
                'price': t['price'],
                'mult': float(getattr(asset, 'price_multiplier', 1.0) or 1.0),
            })
    if not rows:
        return pd.DataFrame(columns=['dt', 'root', 'asset', 'amount', 'price', 'mult'])
    return pd.DataFrame(rows)


def _naive_ts(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize(None) if ts.tz is not None else ts


def _held_by_root_day(perf):
    """perf['positions'] -> dict[day][root] = {asset: amount} (amount != 0).
    Day keys are tz-naive so they compare equal to harvested txn dts."""
    held = {}
    for dt, pos_list in perf['positions'].items():
        by_root = {}
        for p in pos_list:
            amt = p.get('amount', 0)
            if amt == 0:
                continue
            asset = p['sid']
            by_root.setdefault(asset.root_symbol, {})[asset] = amt
        held[_naive_ts(dt)] = by_root
    return held


def _roll_days(held):
    """Detect roll days per root from position-set transitions.

    A roll = held contract A replaced by a different contract B of the same
    root, either same session or with a flat gap of <= ROLL_GAP_SESSIONS.
    Returns dict[root] = set of days on which txns count as roll legs,
    plus the number of roll events per root.
    """
    days = sorted(held.keys())
    roots = sorted({r for d in held.values() for r in d})
    roll_day_set = {r: set() for r in roots}
    roll_events = {r: 0 for r in roots}

    for r in roots:
        prev_assets = set()
        prev_day = None
        gap_day = None          # day we went flat after holding something
        gap_from = set()        # contracts held before the gap
        for d in days:
            cur = held[d].get(r, {})
            cur_assets = set(cur)
            if prev_assets and cur_assets and cur_assets != prev_assets:
                # same-day swap A -> B (any removed + any added)
                if cur_assets - prev_assets and prev_assets - cur_assets:
                    roll_day_set[r].add(d)
                    roll_events[r] += 1
            elif prev_assets and not cur_assets:
                # went flat: maybe a roll with a gap, maybe a signal exit
                gap_day, gap_from = d, prev_assets
            elif not prev_assets and cur_assets and gap_day is not None:
                # re-entered after a gap: roll only if soon AND different contract
                n_sessions = days.index(d) - days.index(gap_day)
                if n_sessions <= ROLL_GAP_SESSIONS and cur_assets != gap_from:
                    roll_day_set[r].add(gap_day)
                    roll_day_set[r].add(d)
                    roll_events[r] += 1
                gap_day, gap_from = None, set()
            elif cur_assets:
                gap_day, gap_from = None, set()
            prev_assets = cur_assets
            prev_day = d
    return roll_day_set, roll_events


# ── cost accounting ──────────────────────────────────────────────────────────

def _txn_costs(txns, roll_day_set, contract_params, enable_commission, enable_slippage):
    """Attach per-txn commission / slippage (currency) and the roll flag."""
    comm, slip, is_roll = [], [], []
    for _, t in txns.iterrows():
        params = contract_params.get(t['root'], {})
        roll = t['dt'] in roll_day_set.get(t['root'], set())
        c = abs(t['amount']) * params.get('commission', 0.0) * 2 if enable_commission else 0.0
        if enable_slippage:
            spread = params.get('slippage_roll' if roll else 'slippage', 0.0)
            s = abs(t['amount']) * t['mult'] * spread
        else:
            s = 0.0
        comm.append(c)
        slip.append(s)
        is_roll.append(roll)
    txns = txns.copy()
    txns['commission_ccy'] = comm
    txns['slippage_ccy'] = slip
    txns['is_roll'] = is_roll
    txns['cost_ccy'] = txns['commission_ccy'] + txns['slippage_ccy']
    return txns


# ── main entry point ─────────────────────────────────────────────────────────

def carver_cost_report(perf, contract_params, enable_commission, enable_slippage,
                       net_stats):
    """
    Parameters
    ----------
    perf             : zipline perf DataFrame (needs 'transactions', 'positions',
                       'portfolio_value' columns)
    contract_params  : CONTRACT_PARAMS dict from the backtest script
    enable_commission / enable_slippage : the script's cost flags
    net_stats        : pf.timeseries.perf_stats(returns) Series (net, as printed)

    Returns a dict with summary values + a per-root detail DataFrame.
    """
    net_pv = perf['portfolio_value'].astype(float)
    net_pv.index = net_pv.index.tz_localize(None)
    n_days = len(net_pv)
    years = n_days / ANN
    mean_pv = float(net_pv.mean())

    net_rets = net_pv.pct_change().dropna()
    net_vol = float(net_stats['Annual volatility'])
    net_sharpe = float(net_stats['Sharpe ratio'])

    txns = _harvest_txns(perf)
    held = _held_by_root_day(perf)
    roll_day_set, roll_events = _roll_days(held)

    out = {
        'years': years,
        'gross_annual_return': float(net_stats['Annual return']),
        'gross_sharpe': net_sharpe,
        'commission_sr': 0.0, 'slippage_sr': 0.0, 'rolling_sr': 0.0,
        'total_sr': 0.0, 'turnover': None, 'rolls_per_year': 0.0,
        'total_costs_ccy': 0.0,
        'per_root': pd.DataFrame(),
    }
    if txns.empty or n_days < 2:
        return out

    txns['dt'] = txns['dt'].map(_naive_ts)
    txns = _txn_costs(txns, roll_day_set, contract_params,
                      enable_commission, enable_slippage)

    # ── gross equity reconstruction: net = gross - cumulative costs ──────────
    daily_cost = txns.groupby('dt')['cost_ccy'].sum()
    cum_cost = daily_cost.reindex(net_pv.index, fill_value=0.0).cumsum()
    gross_pv = net_pv + cum_cost
    gross_rets = gross_pv.pct_change().dropna()

    gross_annual_return = (gross_pv.iloc[-1] / gross_pv.iloc[0]) ** (ANN / n_days) - 1
    gross_sharpe = (gross_rets.mean() / gross_rets.std() * np.sqrt(ANN)
                    if gross_rets.std() > 0 else np.nan)

    # ── category drags in SR units, reconciled to (gross SR - net SR) ────────
    trade = txns[~txns['is_roll']]
    roll = txns[txns['is_roll']]
    raw = {
        'commission': trade['commission_ccy'].sum(),
        'slippage': trade['slippage_ccy'].sum(),
        'rolling': roll['cost_ccy'].sum(),
    }
    raw_drag = {k: (v / years) / mean_pv / net_vol if (net_vol and mean_pv) else 0.0
                for k, v in raw.items()}
    measured_total = (gross_sharpe - net_sharpe)
    raw_total = sum(raw_drag.values())
    if raw_total > 0 and not np.isnan(measured_total):
        scale = measured_total / raw_total
        drags = {k: v * scale for k, v in raw_drag.items()}
        total_sr = measured_total
    else:
        drags = raw_drag
        total_sr = raw_total

    # ── turnover (Carver): non-roll contracts traded / avg |position| ────────
    daily_abs_contracts = pd.Series(
        {d: sum(abs(a) for by_root in day.values() for a in by_root.values())
         for d, day in held.items()})
    avg_abs_pos = float(daily_abs_contracts.mean())
    traded_contracts = float(trade['amount'].abs().sum())
    turnover = (traded_contracts / years) / avg_abs_pos if avg_abs_pos > 0 else None

    total_rolls = sum(roll_events.values())
    rolls_per_year = total_rolls / years if years > 0 else 0.0

    # ── per-root detail ──────────────────────────────────────────────────────
    prows = []
    for root, g in txns.groupby('root'):
        gt = g[~g['is_roll']]
        # avg |position| per root (contracts)
        pos_series = pd.Series(
            {d: sum(abs(a) for a in day.get(root, {}).values())
             for d, day in held.items()})
        avg_pos_root = float(pos_series.mean())
        to_root = (gt['amount'].abs().sum() / years) / avg_pos_root if avg_pos_root > 0 else np.nan
        # Carver risk-adjusted cost per trade: mean(cost% of notional) / sigma_root
        notional = g['price'].abs() * g['mult']
        cost_pct = (g['cost_ccy'] / (notional * g['amount'].abs())).replace(
            [np.inf, -np.inf], np.nan).dropna()
        px = g.sort_values('dt').groupby('dt')['price'].last()
        sigma_root = px.pct_change().std() * np.sqrt(ANN) if len(px) > 10 else np.nan
        cpt_sr = (cost_pct.mean() / sigma_root
                  if len(cost_pct) and sigma_root and not np.isnan(sigma_root) else np.nan)
        drag_root = ((g['cost_ccy'].sum() / years) / mean_pv / net_vol
                     if (net_vol and mean_pv) else 0.0)
        prows.append({
            'root': root,
            'trades': int(len(gt)),
            'rolls': int(roll_events.get(root, 0)),
            'turnover': to_root,
            'cost/trade (SR)': cpt_sr,
            'drag (SR)': drag_root,
        })
    per_root = pd.DataFrame(prows).set_index('root') if prows else pd.DataFrame()

    out.update({
        'gross_annual_return': gross_annual_return,
        'gross_sharpe': gross_sharpe,
        'commission_sr': drags['commission'],
        'slippage_sr': drags['slippage'],
        'rolling_sr': drags['rolling'],
        'total_sr': total_sr,
        'turnover': turnover,
        'rolls_per_year': rolls_per_year,
        'total_costs_ccy': float(txns['cost_ccy'].sum()),
        'per_root': per_root,
    })
    return out


def carver_summary_rows(rep):
    """Format the report as rows for the STRATEGY SUMMARY table."""
    def sr(x):
        return f"{x:,.3f}"

    turnover = f"{rep['turnover']:,.1f}x /yr" if rep['turnover'] is not None else "-"
    warn = ""
    if rep['gross_sharpe'] and rep['gross_sharpe'] > 0 and \
            rep['total_sr'] > rep['gross_sharpe'] / 3.0:
        warn = "⚠ >1/3 of gross SR"
    return [
        ("Annual gross return",      f"{rep['gross_annual_return']*100:,.2f}%", ""),
        ("Gross Sharpe ratio",       f"{rep['gross_sharpe']:,.2f}",              ""),
        ("Commission costs (SR)",    sr(rep['commission_sr']),                   ""),
        ("Slippage costs (SR)",      sr(rep['slippage_sr']),                     ""),
        ("Rolling costs (SR)",       sr(rep['rolling_sr']),                      ""),
        ("Total costs (SR)",         sr(rep['total_sr']),                        warn),
        ("Annual turnover",          turnover,                                   ""),
        ("Rolls per year",           f"{rep['rolls_per_year']:,.1f}",            ""),
        ("", "", ""),
    ]

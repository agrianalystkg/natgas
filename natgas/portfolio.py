# -*- coding: utf-8 -*-
"""
strategy_combiner.py — combine the NG strategies into one portfolio.

Reads the daily equity curves from  new scripts/backtests/Results.xlsx (Sheet2),
normalises each stream to its first valid value, weights + sums them
(renormalising weights across whichever streams already exist on each date),
and reports combined + standalone metrics, the monthly correlation matrix and
the diversification multiplier. With APPEND = 1 it also writes the combined
book back to Sheet1/Sheet2/Sheet3 so it can be used as a benchmark for
alpha/beta.

Default book (third contract, offset 2):
    45% trend following   — six EWMAC speed pairs, equal weight
    30% fundamentals      — GWDD weather (SMA7, Nov-Mar) + EIA storage (winter)
    25% carry / curve     — simple + adjusted carry, summer + all-year

✎  Edit PORTFOLIO below (that's the only thing you normally touch) and re-run.
    Weights don't need to sum to 1 — they're normalised.

Strategies are matched by the *descriptor* of the run_name, i.e. the part after
"NG | ". The timestamp is ignored, so a re-run with a fresh timestamp still
matches. Requires `seaborn`, `openpyxl`. Runs from Spyder (%runfile) or CLI.
"""
import os
import warnings

import numpy as np
import pandas as pd
import pyfolio as pf
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from scipy import stats

try:
    import seaborn as sns
    HAS_SNS = True
except Exception:
    HAS_SNS = False

warnings.filterwarnings('ignore')

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RESULTS_XLSX = os.environ.get("RESULTS_XLSX", os.path.join(REPO, "results", "Results.xlsx"))

APPEND = 0                  # 1 = also write the combined book to Results.xlsx
PORTFOLIO_START = '2000-01-01'   # weather/fund sleeves start 2000-01 -> common start
PORTFOLIO_END = None
STARTING_CAPITAL = 100_000
PORTFOLIO_NAME = 'NG Combined 45/30/25 (off2)'
PLOTS = True

# benchmark run_name (Sheet3 column) for the alpha/beta rows, or None to skip.
# use a full run_name from Results.xlsx Sheet3, e.g.
#   BETA = '2026-10-12 09:47:21 | NG | fast=16 slow=64'
beta = "16/64 simple"

# ── PORTFOLIO ─────────────────────────────────────────────────────────────────
# key   = run descriptor (run_name after "NG | ", copied from the ng_lab log)
# value = weight (relative; normalised to sum to 1 before use)
# ✎ EDIT THE WEIGHTS BELOW
PORTFOLIO = {
    # ── trend following — EWMAC speed sweep (45%) ─────────────────────────────
    'fast=2 slow=8':                              0.075,
    'fast=4 slow=16':                             0.075,
    'fast=8 slow=32':                             0.075,
    'fast=16 slow=64':                            0.075,
    'fast=32 slow=128':                           0.075,
    'fast=64 slow=256':                           0.075,
    # ── fundamentals (30%) ────────────────────────────────────────────────────
    'fast=GWDD SMA7 Nov-Mar slow=GWDD normal':    0.15,
    'fast=EIA weekly stocks slow=winter only':    0.15,
    # ── carry / curve (25%) ───────────────────────────────────────────────────
    'fast=carry rule simple slow=summer':         0.0625,
    'fast=carry rule simple slow=all year':       0.0625,
    'fast=carry rule adjusted slow=summer':       0.0625,
    'fast=carry rule adjusted slow=all year':     0.0625,
}

# cosmetic pretty names for tables/plots (does not affect the maths)
LABELS = {
    'fast=2 slow=8':                              'Trend 2/8',
    'fast=4 slow=16':                             'Trend 4/16',
    'fast=8 slow=32':                             'Trend 8/32',
    'fast=16 slow=64':                            'Trend 16/64',
    'fast=32 slow=128':                           'Trend 32/128',
    'fast=64 slow=256':                           'Trend 64/256',
    'fast=GWDD SMA7 Nov-Mar slow=GWDD normal':    'Weather GWDD SMA7 Nov-Mar',
    'fast=EIA weekly stocks slow=winter only':    'EIA storage winter',
    'fast=carry rule simple slow=summer':         'Carry simple summer',
    'fast=carry rule simple slow=all year':       'Carry simple all-year',
    'fast=carry rule adjusted slow=summer':       'Carry adjusted summer',
    'fast=carry rule adjusted slow=all year':     'Carry adjusted all-year',
}


def _descriptor(run_name):
    """'<ts> | NG | fast=.. slow=..' -> 'fast=.. slow=..' (last ' | ' segment)."""
    return str(run_name).split(' | ')[-1].strip()


def build_portfolio(scheme='equal'):
    """Return {descriptor: normalised weight} for the chosen weighting scheme."""
    if scheme != 'equal':
        raise ValueError(scheme)
    w = pd.Series(PORTFOLIO, dtype=float)
    return (w / w.sum()).to_dict()


# ══════════════════════════════════════════════════════════════════════════════
# LOAD
# ══════════════════════════════════════════════════════════════════════════════
def load_equity(descriptors):
    """Sheet2 equity for the requested descriptors, columns renamed to the
    descriptors themselves. Matched by run-name descriptor so the timestamp in
    the workbook's run_name is irrelevant."""
    xl = pd.read_excel(RESULTS_XLSX, sheet_name='Sheet2')
    xl['Date'] = pd.to_datetime(xl['Date'])
    xl = xl.set_index('Date').sort_index()
    xl = xl.apply(pd.to_numeric, errors='coerce')
    by_desc = {}
    for c in xl.columns:
        by_desc.setdefault(_descriptor(c), c)
    missing = [d for d in descriptors if d not in by_desc]
    if missing:
        raise KeyError('not in Sheet2 (by descriptor):\n  ' + '\n  '.join(missing))
    out = xl[[by_desc[d] for d in descriptors]]
    out.columns = list(descriptors)
    return out


def combine(eq, weights):
    """Normalise each stream to its first valid value, weight, sum
    (renormalising weights across streams that already exist on each date)."""
    normed = eq.div(eq.apply(lambda c: c.dropna().iloc[0] if c.dropna().size else np.nan))
    w = pd.Series(weights)
    avail = normed.notna()
    wsum = avail.mul(w, axis=1).sum(axis=1)
    combined = (normed.fillna(0).mul(w, axis=1)).sum(axis=1) / wsum.replace(0, np.nan)
    combined = combined.dropna()
    if PORTFOLIO_START:
        combined = combined.loc[PORTFOLIO_START:]
    if PORTFOLIO_END:
        combined = combined.loc[:PORTFOLIO_END]
    combined = combined / combined.iloc[0] * STARTING_CAPITAL
    indiv = normed.reindex(combined.index).dropna(how='all')
    indiv = indiv / indiv.iloc[0] * STARTING_CAPITAL
    return combined, indiv


# ══════════════════════════════════════════════════════════════════════════════
# METRICS
# ══════════════════════════════════════════════════════════════════════════════
def stats_from_eq(eq):
    d = eq.pct_change().dropna()
    n = len(d)
    ann = (eq.iloc[-1] / eq.iloc[0]) ** (252 / n) - 1 if n else np.nan
    vol = d.std(ddof=0) * np.sqrt(252)
    sharpe = d.mean() / d.std() * np.sqrt(252) if d.std() else np.nan
    down = np.where(d < 0, d, 0)
    dstd = np.std(down) * np.sqrt(252)
    sortino = ann / dstd if dstd else np.nan
    dd = eq / eq.cummax() - 1
    m = eq.resample('ME').last().pct_change().dropna()
    t = (m.mean() / (m.std(ddof=1) / np.sqrt(len(m)))) if m.std(ddof=1) else np.nan
    p = 1 - stats.t.cdf(t, df=len(m) - 1)
    y = np.log(eq.values); x = np.arange(len(y))
    b = np.polyfit(x, y, 1); yh = b[0] * x + b[1]
    stab = 1 - np.sum((y - yh) ** 2) / np.sum((y - y.mean()) ** 2) if np.sum((y - y.mean()) ** 2) else np.nan
    return dict(ann=ann, vol=vol, sharpe=sharpe, sortino=sortino,
                max_dd=dd.min(), avg_dd=dd[dd < 0].mean(), stability=stab,
                t=t, p=p, daily=d, monthly=m)


def table(rows, title):
    df = pd.DataFrame(rows)
    print(f'\n=== {title} ===')
    print(df.round(3).to_string(index=False))
    return df


def print_standalone(indiv):
    rows = []
    for c in indiv.columns:
        s = stats_from_eq(indiv[c].dropna())
        rows.append(dict(strategy=LABELS.get(c, c), ann_pct=s['ann'] * 100,
                         vol_pct=s['vol'] * 100, sharpe=s['sharpe'],
                         max_dd_pct=s['max_dd'] * 100, t=s['t'], p=s['p']))
    return table(rows, 'standalone strategy stats')


# ══════════════════════════════════════════════════════════════════════════════
# PLOTS
# ══════════════════════════════════════════════════════════════════════════════
def _freq_returns(indiv, freq):
    df = indiv.apply(pd.to_numeric, errors='coerce')
    if freq == 'Daily':
        return df.pct_change().dropna(how='all')
    if freq == 'Weekly':
        return df.resample('W').last().pct_change().dropna(how='all')
    return df.resample('ME').last().pct_change().dropna(how='all')


def _show(tag):
    out_dir = os.path.join(REPO, 'artifacts')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, tag)
    plt.savefig(out, dpi=140, bbox_inches='tight')
    print('saved', out)
    plt.show()


def plot_equity(combined, indiv, weights):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9), sharex=True,
                                   gridspec_kw={'height_ratios': [2, 1]})
    for c in indiv.columns:
        ax1.plot(indiv.index, indiv[c] / indiv[c].iloc[0], lw=0.8, alpha=0.5,
                 ls='--', label=f'{LABELS.get(c, c)} ({weights[c]:.0%})')
    ax1.plot(combined.index, combined / combined.iloc[0], lw=2.2, color='black',
             label='Combined')
    ax1.set_yscale('log'); ax1.axhline(1, color='grey', lw=0.7, ls=':')
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{(x-1)*100:+.0f}%"))
    ax1.set_ylabel('Cumulative return (log)')
    ax1.set_title(f'NG combined portfolio — {PORTFOLIO_NAME}', fontweight='bold')
    ax1.legend(fontsize=8, loc='upper left'); ax1.grid(alpha=0.3)
    dd = (combined / combined.cummax() - 1) * 100
    ax2.fill_between(dd.index, dd, 0, color='#e74c3c', alpha=0.5)
    ax2.plot(dd.index, dd, color='#e74c3c', lw=0.8)
    ax2.set_ylabel('Drawdown %'); ax2.grid(alpha=0.3)
    plt.tight_layout(); _show('combined_equity.png')


def plot_rolling_sharpe(daily, m):
    n = pd.Series(range(1, len(daily) + 1), index=daily.index)
    sr = (daily.expanding().mean() / daily.expanding().std()) * np.sqrt(252)
    se = np.sqrt(252) / np.sqrt(n)
    ub, lb = sr + 2 * se, sr - 2 * se
    sr, ub, lb = sr.iloc[252:], ub.iloc[252:], lb.iloc[252:]
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(sr.index, sr, color='#2196F3', lw=1.5, label='Cumulative avg Sharpe')
    ax.plot(ub.index, ub, '--', color='#2196F3', lw=0.8, alpha=0.6, label='+2σ')
    ax.plot(lb.index, lb, '--', color='#2196F3', lw=0.8, alpha=0.6, label='−2σ')
    ax.fill_between(sr.index, lb, ub, alpha=0.1, color='#2196F3')
    ax.axhline(0, color='black', lw=0.8)
    passed = lb[lb > 0]
    if len(passed):
        ax.axvline(passed.index[0], color='green', lw=1.2, ls=':',
                   label=f'passed {passed.index[0]:%Y-%m}')
    ax.set_title(f'Rolling Sharpe (expanding)  |  t={m["t"]:.2f}  p={m["p"]:.3f}')
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    plt.tight_layout(); _show('combined_rolling_sharpe.png')


def plot_correlation(indiv):
    freqs = ['Daily', 'Weekly', 'Monthly']
    fig, axes = plt.subplots(1, 3, figsize=(22, 7))
    fig.suptitle(f'Correlation — {PORTFOLIO_NAME}', fontweight='bold', y=1.01)
    for ax, f in zip(axes, freqs):
        corr = _freq_returns(indiv, f).corr()
        labels = [LABELS.get(c, c) for c in corr.columns]
        if HAS_SNS:
            sns.heatmap(corr, ax=ax, cmap='RdYlGn', vmin=-1, vmax=1, annot=True,
                        fmt='.2f', annot_kws={'size': 8}, linewidths=0.4,
                        xticklabels=labels, yticklabels=labels,
                        cbar_kws={'shrink': 0.75, 'label': 'r'})
        else:
            im = ax.imshow(corr.values, cmap='RdYlGn', vmin=-1, vmax=1)
            ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels, rotation=40, ha='right', fontsize=8)
            ax.set_yticks(range(len(labels))); ax.set_yticklabels(labels, fontsize=8)
            for i in range(len(labels)):
                for j in range(len(labels)):
                    ax.text(j, i, f'{corr.values[i, j]:.2f}', ha='center', va='center', fontsize=7)
            fig.colorbar(im, ax=ax, shrink=0.75)
        ax.set_title(f'{f} returns')
    plt.tight_layout(); _show('combined_correlation.png')


def plot_returns_and_rolling_sharpe(daily):
    rs6 = (daily.rolling(126).mean() / daily.rolling(126).std()) * np.sqrt(252)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
    colors = ['#2e7d32' if r >= 0 else '#c62828' for r in daily]
    ax1.bar(daily.index, daily.values, color=colors, width=1.0, alpha=0.85)
    ax1.axhline(0, color='black', lw=0.6); ax1.set_ylabel('Daily returns')
    ax1.set_title('Daily returns'); ax1.grid(alpha=0.2)
    ax2.plot(rs6.index, rs6, color='#e67e22', lw=1.2)
    ax2.axhline(rs6.mean(), color='#2196F3', lw=1.0, ls='--', label=f'mean {rs6.mean():.2f}')
    ax2.axhline(0, color='black', lw=0.6); ax2.set_title('Rolling Sharpe (6-month)')
    ax2.legend(fontsize=8); ax2.grid(alpha=0.2)
    plt.tight_layout(); _show('combined_returns_rolling_sharpe.png')


def plot_drawdown_periods(daily, top_n=5):
    cum = (1 + daily).cumprod(); dd = cum / cum.cummax() - 1
    periods, in_dd, start = [], False, None
    for dt, v in dd.items():
        if not in_dd and v < 0:
            in_dd, start, dmin, dmin_d = True, dt, v, dt
        elif in_dd:
            if v < dmin:
                dmin, dmin_d = v, dt
            if v == 0:
                periods.append((start, dmin_d, dt, dmin)); in_dd = False
    if in_dd:
        periods.append((start, dmin_d, daily.index[-1], dmin))
    periods.sort(key=lambda x: x[3]); top = periods[:top_n]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    ax1.plot(cum.index, cum.values - 1, color='#2196F3', lw=1.4)
    colors = plt.cm.Reds(np.linspace(0.4, 0.85, max(len(top), 1)))
    for i, (s, tr, e, dep) in enumerate(top):
        ax1.axvspan(s, e, alpha=0.25, color=colors[i], label=f'DD#{i+1} {dep*100:.1f}%')
    ax1.axhline(0, color='black', lw=0.6); ax1.set_title(f'Top {top_n} drawdowns')
    ax1.legend(fontsize=7); ax1.grid(alpha=0.2)
    ax2.fill_between(dd.index, dd.values * 100, 0, color='#e74c3c', alpha=0.55)
    ax2.plot(dd.index, dd.values * 100, color='#e74c3c', lw=0.8)
    ax2.set_ylabel('Drawdown %'); ax2.grid(alpha=0.2)
    plt.tight_layout(); _show('combined_drawdown.png')


def plot_monthly_annual_dist(monthly):
    mr = monthly.copy(); mr.index = pd.to_datetime(mr.index)
    piv = pd.DataFrame({'year': mr.index.year, 'month': mr.index.month,
                        'ret': mr.values * 100}).pivot(index='year', columns='month', values='ret')
    piv.columns = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][:piv.shape[1]]
    annual = (mr + 1).resample('YE').prod() - 1
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(22, max(5, len(piv) * 0.5 + 1)))
    vmax = max(abs(piv.values[~np.isnan(piv.values)]).max(), 1)
    if HAS_SNS:
        sns.heatmap(piv, ax=ax1, cmap='RdYlGn', center=0, vmin=-vmax, vmax=vmax,
                    annot=True, fmt='.1f', annot_kws={'size': 8}, linewidths=0.3,
                    cbar_kws={'shrink': 0.6, 'label': '%'})
    else:
        ax1.imshow(piv.values, cmap='RdYlGn', vmin=-vmax, vmax=vmax, aspect='auto')
        ax1.set_xticks(range(piv.shape[1])); ax1.set_xticklabels(piv.columns, rotation=45)
        ax1.set_yticks(range(len(piv))); ax1.set_yticklabels(piv.index)
    ax1.set_title('Monthly returns (%)'); ax1.set_xlabel('Month'); ax1.set_ylabel('Year')
    acol = ['#2e7d32' if v >= 0 else '#c62828' for v in annual * 100]
    ax2.barh(annual.index.year.astype(str), annual.values * 100, color=acol, alpha=0.85)
    ax2.axvline(annual.mean() * 100, color='#e74c3c', lw=1.2, ls='--', label=f'mean {annual.mean()*100:.1f}%')
    ax2.axvline(0, color='black', lw=0.8); ax2.set_title('Annual returns'); ax2.legend(fontsize=8); ax2.grid(alpha=0.2, axis='x')
    ax3.hist(mr.values * 100, bins=30, color='#2196F3', alpha=0.75, edgecolor='white')
    ax3.axvline(mr.mean() * 100, color='#e74c3c', lw=1.2, ls='--', label=f'mean {mr.mean()*100:.2f}%')
    ax3.set_title('Monthly return distribution'); ax3.legend(fontsize=8); ax3.grid(alpha=0.2)
    plt.tight_layout(); _show('combined_monthly_annual_dist.png')


def plot_all(combined, indiv, weights, m):
    for fn, args in [(plot_equity, (combined, indiv, weights)),
                     (plot_rolling_sharpe, (m['daily'], m)),
                     (plot_correlation, (indiv,)),
                     (plot_returns_and_rolling_sharpe, (m['daily'],)),
                     (plot_drawdown_periods, (m['daily'],)),
                     (plot_monthly_annual_dist, (m['monthly'],))]:
        try:
            fn(*args)
        except Exception as e:
            print(f'plot warning ({fn.__name__}): {e}')


# ══════════════════════════════════════════════════════════════════════════════
# SAVE (Sheet1/2/3, suite-compatible)
# ══════════════════════════════════════════════════════════════════════════════
def save_to_excel(combined, m, weights):
    from . import alpha_beta as _ab

    run_name = PORTFOLIO_NAME
    monthly = combined.resample('ME').last().pct_change().dropna()
    summary = {
        'Timestamp': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
        'run_name': run_name,
        'contracts': 'combined',
        'portfolio_components': ', '.join(
            f'{LABELS.get(k, k)}={v:.0%}' for k, v in weights.items()),
        'start': str(combined.index[0].date()), 'end': str(combined.index[-1].date()),
        'StartPortfolioValue': float(combined.iloc[0]),
        'EndPortfolioValue': float(combined.iloc[-1]),
        'CumulativeReturn_%': (combined.iloc[-1] / combined.iloc[0] - 1) * 100,
        'AnnualReturn_%': m['ann'] * 100, 'AnnualVolatility_%': m['vol'] * 100,
        'Sharpe': m['sharpe'], 'Sortino': m['sortino'], 'Stability_R2': m['stability'],
        'MaxDrawdown_%': m['max_dd'] * 100, 'AvgDrawdown_%': m['avg_dd'] * 100,
        'T_statistic': m['t'], 'P_value_OneTailed': m['p'], 'PASS_p_lt_0_05': m['p'] < 0.05,
    }
    equity = combined.reset_index()
    equity.columns = ['Date', run_name]
    equity['Date'] = pd.to_datetime(equity['Date']).dt.tz_localize(None)

    s1 = pd.read_excel(RESULTS_XLSX, sheet_name='Sheet1') if os.path.exists(RESULTS_XLSX) else pd.DataFrame()
    if 'run_name' in s1.columns:
        s1 = s1[s1['run_name'] != run_name]          # idempotent re-save
    s1 = pd.concat([s1, pd.DataFrame([summary])], ignore_index=True) if len(s1) else pd.DataFrame([summary])
    s2 = pd.read_excel(RESULTS_XLSX, sheet_name='Sheet2') if os.path.exists(RESULTS_XLSX) else pd.DataFrame({'Date': []})
    s2['Date'] = pd.to_datetime(s2['Date'])
    s2 = s2.drop(columns=[run_name], errors='ignore')
    s2 = pd.merge(s2, equity, on='Date', how='outer').sort_values('Date').reset_index(drop=True)
    book = _ab.load_monthly_returns_book(RESULTS_XLSX)
    book = book.drop(columns=[run_name], errors='ignore')
    s3 = _ab.append_monthly_returns_column(book, run_name, monthly)
    with pd.ExcelWriter(RESULTS_XLSX, engine='openpyxl') as w:
        s1.to_excel(w, sheet_name='Sheet1', index=False)
        s2.to_excel(w, sheet_name='Sheet2', index=False)
        s3.reset_index().to_excel(w, sheet_name='Sheet3', index=False)
    print(f'\n✓ combined book saved to {RESULTS_XLSX} as "{run_name}"')


# ══════════════════════════════════════════════════════════════════════════════
# SUITE-STYLE SUMMARY TABLE  (same metrics as the backtest scripts)
# ══════════════════════════════════════════════════════════════════════════════
def _calc_drawdown_stats(returns):
    cum = (1 + returns).cumprod()
    dd = (cum - cum.cummax()) / cum.cummax()
    return dd[dd < 0].mean() * 100, dd.min() * 100


def _calc_fat_tails(returns):
    G = 4.43
    r = returns[returns != 0]
    d = r - r.mean()
    p01, p30, p70, p99 = np.percentile(d, [1, 30, 70, 99])
    return (p01 / p30) / G, (p99 / p70) / G


def _calc_sharpe_ttest(returns):
    t = returns.mean() / returns.std() * np.sqrt(len(returns))
    p = stats.t.sf(t, df=len(returns) - 1)
    return t, p


def suite_summary(combined, beta=None):
    """Exactly the backtest scripts' STRATEGY SUMMARY (minus the Carver cost
    block, which needs transactions). alpha/beta rows require `beta`."""
    returns = combined.pct_change().dropna()
    s = pf.timeseries.perf_stats(returns)
    monthly = returns.resample('M').apply(lambda x: (1 + x).prod() - 1)
    monthly_skew = monthly.dropna().skew()
    avg_dd, _ = _calc_drawdown_stats(returns)
    lower_tail, upper_tail = _calc_fat_tails(returns)
    t_stat, p_value = _calc_sharpe_ttest(returns)
    passed = p_value < 0.025

    rows = []
    if beta:
        try:
            from . import alpha_beta as _ab
            book = _ab.load_monthly_returns_book(RESULTS_XLSX)
            if beta in book.columns:
                ab = _ab.compute_alpha_beta(monthly, book[beta].dropna())
                if not np.isnan(ab.get('beta', np.nan)):
                    rows += [("Beta to benchmark", f"{ab['beta']:,.3f}", beta),
                             ("Alpha vs bench (ann.)", f"{ab['alpha_ann']*100:,.2f}%",
                              f"n={ab['overlap_months']}m overlap"),
                             ("R2 to benchmark", f"{ab['r2']:,.3f}", "")]
                else:
                    rows += [("Beta to benchmark", "-", beta),
                             ("Alpha vs bench (ann.)", "-",
                              f"overlap {ab.get('overlap_months', 0)}m < 24m"),
                             ("R2 to benchmark", "-", "")]
            else:
                rows += [("Beta to benchmark", "-", beta),
                         ("Alpha vs bench (ann.)", "-",
                          "not in Sheet3 - save benchmark with APPEND=1 first"),
                         ("R2 to benchmark", "-", "")]
        except Exception as e:
            rows += [("Beta to benchmark", "-", f"alpha/beta error: {e}")]

    rows += [
        ("Annual net return", f"{s['Annual return']*100:,.2f}%", ""),
        ("Cumulative returns", f"{s['Cumulative returns']*100:,.2f}%", ""),
        ("Annual volatility", f"{s['Annual volatility']*100:,.2f}%", ""),
        ("Sharpe ratio", f"{s['Sharpe ratio']:,.2f}", ""),
        ("Sortino ratio", f"{s['Sortino ratio']:,.2f}", ""),
        ("Stability", f"{s['Stability']:,.2f}", ""),
        ("Max drawdown", f"{s['Max drawdown']*100:,.2f}%", ""),
        ("Avg drawdown", f"{avg_dd:,.2f}%", ""),
        ("Skew (monthly)", f"{monthly_skew:,.2f}", ""),
        ("Tail ratio (95/5)", f"{s['Tail ratio']:,.2f}", ""),
        ("Lower tail ratio (1/30/4.43)", f"{lower_tail:,.3f}", "⚠" if lower_tail > 1.5 else "✓"),
        ("Upper tail ratio (99/70/4.43)", f"{upper_tail:,.3f}", "⚠" if upper_tail > 1.5 else "✓"),
        ("t-statistic", f"{t_stat:,.3f}", ""),
        ("p-value (one-tailed)", f"{p_value*100:,.2f}%",
         "✅ PASSED" if passed else "❌ NOT PASSED"),
    ]
    df = pd.DataFrame(rows, columns=["Metric", "Value", ""])
    print("\n=== STRATEGY SUMMARY ===")
    print(df.to_string(index=False))
    return df


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    weights = build_portfolio('equal')
    print(f'Portfolio {PORTFOLIO_NAME}: {len(weights)} strategies, '
          f'weights sum={sum(weights.values()):.3f}')

    eq = load_equity(list(weights))
    combined, indiv = combine(eq, weights)

    print('\nComponents:')
    for k, v in weights.items():
        print(f'  {LABELS.get(k, k):<28} {v:5.1%}')

    print_standalone(indiv)

    m = stats_from_eq(combined)
    suite_summary(combined, beta)

    # correlation + diversification multiplier
    mret = indiv.resample('ME').last().pct_change().dropna()
    C = mret.corr().values
    w = np.array([weights[c] for c in indiv.columns])
    pf = float(np.sqrt(w @ C @ w))
    print(f'\nDiversification multiplier (1/(wᵀCw)^½): {1/pf:.3f}x')
    table([dict(a=LABELS.get(a, a),
                **{LABELS.get(a2, a2): round(C[i][j], 2) for j, a2 in enumerate(indiv.columns)})
           for i, a in enumerate(indiv.columns)], 'monthly return correlation')

    if PLOTS:
        plot_all(combined, indiv, weights, m)

    if APPEND == 1:
        save_to_excel(combined, m, weights)


if __name__ == '__main__':
    main()

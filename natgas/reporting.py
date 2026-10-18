# -*- coding: utf-8 -*-
"""
reporting_charts.py — shared, redesigned strategy charts (Kimi 2026-10-13).

Replaces the duplicated chart block that lived inside every backtest script's
`analyze()`. Two pages:

  PAGE 1  "market & equity"
    * ONE combined market chart: zipline's 'mul' continuous price (left axis)
      + position exposure % (green/red fill, semi-transparent) and trend
      strength (line) on a second axis — so long/short is visible right next
      to the price.
    * Cumulative return / equity on a LOG scale, with the top-5 drawdown
      periods shaded subtly (replaces the old standalone "top-5 drawdowns"
      panel, which just re-drew the equity curve).
    * Daily returns (unchanged idea).
    * Rolling Sharpe, 6-month (unchanged idea).

  PAGE 2  "risk & distribution"
    * t-test rolling Sharpe with 2σ bands (was printed after the summary;
      now it replaces the old top-5 drawdown panel).
    * Underwater curve.
    * Monthly heatmap / annual returns / monthly distribution (unchanged).

Callers pass already-computed pieces; this module does no zipline imports.
"""
import warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.gridspec import GridSpec
from scipy import stats
import pyfolio as pf


# ─────────────────────────────────────────────────────────────────────────────
# small helpers
# ─────────────────────────────────────────────────────────────────────────────
def _naive_index(obj):
    idx = pd.DatetimeIndex(obj.index)
    return idx.tz_localize(None) if idx.tz is not None else idx


def make_direction(trend_log, direction_log):
    """Business-day direction series from the script's direction_log dict.
    (Kept here so all scripts build it identically.)"""
    dir_log = pd.Series(direction_log)
    dir_log.index = _naive_index(dir_log)
    if dir_log.empty:
        return dir_log
    return dir_log.reindex(
        pd.date_range(dir_log.index[0], dir_log.index[-1], freq='B'),
        method='ffill').fillna(0)


def _daily_notional(positions, perf):
    """Total futures notional on the full daily calendar; missing days (which
    pyfolio drops because the book was flat) are 0."""
    pv = perf['portfolio_value'].copy()
    pv.index = _naive_index(pv)
    futures = positions.drop(columns=['cash'], errors='ignore')
    notional = futures.sum(axis=1)
    notional.index = _naive_index(notional)
    return notional.reindex(pv.index).fillna(0.0), pv


def compute_exposure(positions, perf, direction=None):
    """Position notional / portfolio value, in %, on the FULL daily calendar.

    Important: pyfolio's `positions` frame drops every day with no open
    position (an empty list per day is discarded), so it only contains the days
    the strategy actually holds something. If we plotted that series directly,
    `fill_between` would bridge the flat gaps with straight lines and paint huge
    fake wedges across periods the strategy is flat. So we reindex onto the
    daily portfolio-value index and treat missing days as FLAT (notional = 0).

    If `direction` is omitted it is taken from the sign of the notional itself.
    """
    notional, pv = _daily_notional(positions, perf)
    exposure = notional / pv * 100
    if direction is None:
        direction = np.sign(notional)
    d = direction.copy()
    d.index = _naive_index(d)
    exposure = exposure * (d.reindex(exposure.index, method='ffill') != 0)
    return exposure


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 1
# ─────────────────────────────────────────────────────────────────────────────
def _shade_direction(ax, direction, alpha=0.06):
    """Faint full-height green/red bands showing long/short. Skips flat (0)."""
    if direction is None or len(direction) == 0:
        return
    d = direction.copy()
    d.index = _naive_index(d)
    d = d.reindex(pd.date_range(d.index[0], d.index[-1], freq='B')).ffill().fillna(0)
    prev, start = None, None
    for dt, val in d.items():
        if val != prev:
            if prev not in (None, 0) and start is not None:
                ax.axvspan(start, dt, alpha=alpha, linewidth=0, zorder=0,
                           color='green' if prev > 0 else 'red')
            start, prev = dt, val
    if prev not in (None, 0) and start is not None:
        ax.axvspan(start, d.index[-1], alpha=alpha, linewidth=0, zorder=0,
                   color='green' if prev > 0 else 'red')


def plot_market_and_exposure(ax, price_series, exposure, ts_scaled, label,
                             price_log=True, direction=None, style='fill'):
    """Combined price (left) + exposure & trend strength (right).

    style:
      'fill'  - green/red area fill + thin exposure line   (default / least
                invasive; the fill is drawn on the FULL daily calendar so it no
                longer bridges the flat off-season gaps)
      'light' - the same but with a fainter fill
      'line'  - no fill; thin sign-coloured line + faint direction bands
    """
    if style == 'line':
        _shade_direction(ax, direction)

    price_line = None
    if price_series is not None and len(price_series):
        price_line, = ax.plot(price_series.index, price_series.values,
                              color='steelblue', lw=1.2, zorder=3, label=label)
    if price_log:
        ax.set_yscale('log')
        ax.grid(True, alpha=0.2, which='both')
    else:
        ax.grid(True, alpha=0.2)
    ax.set_ylabel('Price (backadjusted' + (', log)' if price_log else ')'))
    ax.set_title(f'{label} — price vs position exposure', fontsize=11)

    ax2 = ax.twinx()
    ts_line = None
    if exposure is not None and len(exposure):
        if style == 'line':
            # exposure as a thin line COLOURED BY SIGN (green long / red short)
            pos = exposure.where(exposure >= 0)
            neg = exposure.where(exposure <= 0)
            ax2.plot(pos.index, pos.values, color='green', lw=1.1, zorder=3)
            ax2.plot(neg.index, neg.values, color='red', lw=1.1, zorder=3)
        else:
            alpha = 0.22 if style == 'fill' else 0.10
            ax2.fill_between(exposure.index, exposure.clip(lower=0), 0,
                             color='green', alpha=alpha, linewidth=0, zorder=1)
            ax2.fill_between(exposure.index, exposure.clip(upper=0), 0,
                             color='red', alpha=alpha, linewidth=0, zorder=1)
            ax2.plot(exposure.index, exposure, color='black',
                     lw=0.5 if style == 'fill' else 0.7,
                     alpha=0.35 if style == 'fill' else 0.5, zorder=2)

        if ts_scaled is not None and len(ts_scaled):
            ts = ts_scaled.reindex(exposure.index, method='ffill')
            if style == 'line':
                ts_line, = ax2.plot(ts.index, ts.values, color='#888888',
                                    lw=0.8, alpha=0.8, linestyle='--',
                                    zorder=4, label='Trend strength ×50')
            else:
                ts_line, = ax2.plot(ts.index, ts.values, color='navy',
                                    lw=1.0, alpha=0.55, zorder=4,
                                    label='Trend strength ×50')
    ax2.axhline(0, color='black', lw=0.6, alpha=0.4)
    ax2.set_ylabel('Exposure % / trend strength')

    handles = ([price_line] if price_line is not None else [])
    if style == 'line':
        handles += [plt.Line2D([], [], color='green', lw=1.5, label='Long exposure'),
                    plt.Line2D([], [], color='red', lw=1.5, label='Short exposure')]
    else:
        a = 0.22 if style == 'fill' else 0.10
        handles += [Patch(facecolor='green', alpha=a, label='Long exposure'),
                    Patch(facecolor='red', alpha=a, label='Short exposure')]
    if ts_line is not None:
        handles.append(ts_line)
    ax.legend(handles=handles, loc='upper left', fontsize=8)
    return ax2


def plot_equity_log(ax, returns, top_dd=5):
    """Cumulative return (= growth of 1) on a log axis, with the top-5
    drawdown periods shaded subtly instead of a separate panel."""
    eq = (1 + returns).cumprod()
    ax.plot(eq.index, eq.values, color='forestgreen', lw=1.4, label='Backtest')
    ax.set_yscale('log')
    ax.set_ylabel('Cumulative return (log)')
    ax.set_title('Cumulative returns (log) — top-5 drawdowns shaded', fontsize=10)
    ax.grid(True, alpha=0.2, which='both')

    try:
        dd = pf.timeseries.gen_drawdown_table(returns, top=top_dd)
    except Exception:
        dd = None
    if dd is not None and len(dd):
        for _, row in dd.iterrows():
            peak = row.get('Peak date')
            rec = row.get('Recovery date')
            valley = row.get('Valley date')
            if pd.isna(peak):
                continue
            end = rec if pd.notna(rec) else (valley if pd.notna(valley) else eq.index[-1])
            ax.axvspan(peak, end, color='red', alpha=0.06, linewidth=0)
            if pd.notna(valley):
                ax.axvline(valley, color='red', alpha=0.30, lw=0.6, ls=':')
    ax.legend(handles=[ax.lines[0],
                       Patch(facecolor='red', alpha=0.10, label=f'Top-{top_dd} drawdowns')],
              loc='upper left', fontsize=8)
    return ax


def draw_sharpe_ttest(ax, rolling_sr, upper_band, lower_band,
                      t_stat, p_value, burnin=252):
    """Rolling/expanding Sharpe with mechanical ±2σ bands (drawn on `ax`)."""
    rolling_sr = rolling_sr.dropna()
    upper_band = upper_band.reindex(rolling_sr.index)
    lower_band = lower_band.reindex(rolling_sr.index)
    rolling_sr = rolling_sr.iloc[burnin:]
    upper_band = upper_band.iloc[burnin:]
    lower_band = lower_band.iloc[burnin:]

    ax.plot(rolling_sr.index, rolling_sr, color='#2196F3', lw=1.5,
            label='Cumulative avg Sharpe')
    ax.plot(upper_band.index, upper_band, color='#2196F3', lw=0.8,
            linestyle='--', alpha=0.6, label='±2σ')
    ax.plot(lower_band.index, lower_band, color='#2196F3', lw=0.8,
            linestyle='--', alpha=0.6)
    ax.fill_between(rolling_sr.index, lower_band, upper_band,
                    alpha=0.1, color='#2196F3')
    ax.axhline(0, color='black', lw=0.8)

    passed = lower_band[lower_band > 0]
    if len(passed) > 0:
        pass_date = passed.index[0]
        ax.axvline(pass_date, color='green', lw=1.2, linestyle=':',
                   label=f'T-test passed ({pass_date.strftime("%Y-%m")})')

    passed_str = (f"PASSED (p={p_value:.3f})" if p_value < 0.025
                  else f"not passed (p={p_value:.3f})")
    ax.set_title(f'Rolling Sharpe with 2σ bands  |  t={t_stat:.2f}  |  '
                 f'T-Test: {passed_str}', fontsize=11)
    ax.set_ylabel('Sharpe ratio')
    ax.legend(loc='upper left', fontsize=8)
    ax.grid(True, alpha=0.3)
    return ax


def page1(returns, positions, perf, price_series, ts_scaled, label,
          price_log=True, direction=None, style='fill'):
    """Market + exposure + equity(log, shaded) + daily returns + rolling Sharpe.

    If `price_series` is empty/None (e.g. a multi-root portfolio with no single
    price), the market panel is dropped and a 3-panel page is drawn."""
    has_price = price_series is not None and len(price_series) > 0
    if has_price:
        fig, axes = plt.subplots(4, 1, figsize=(14, 16),
                                 gridspec_kw={'height_ratios': [3.2, 3, 2, 2]})
        ax_market, ax_eq, ax_dr, ax_sh = axes
        notional, _pv = _daily_notional(positions, perf)
        _dir = direction if direction is not None else np.sign(notional)
        exposure = compute_exposure(positions, perf, _dir)
        plot_market_and_exposure(ax_market, price_series, exposure, ts_scaled,
                                 label, price_log=price_log, direction=_dir,
                                 style=style)
    else:
        fig, axes = plt.subplots(3, 1, figsize=(14, 12))
        ax_eq, ax_dr, ax_sh = axes

    plot_equity_log(ax_eq, returns)
    with _capture():
        pf.plotting.plot_returns(returns, ax=ax_dr)
        pf.plotting.plot_rolling_sharpe(returns, ax=ax_sh)
    plt.tight_layout(h_pad=3.0)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 2
# ─────────────────────────────────────────────────────────────────────────────
def page2(returns, ttest):
    from IPython.utils import io as ipio
    # calc_sharpe_ttest returns (t_stat, p_value, rolling_sr, upper, lower)
    t_stat, p_value, rolling_sr, upper_band, lower_band = ttest

    fig = plt.figure(figsize=(14, 20))
    gs = GridSpec(3, 3, figure=fig, height_ratios=[1, 1, 1.2],
                  hspace=0.4, wspace=0.35)
    ax1 = fig.add_subplot(gs[0, :])   # t-test (was: top-5 drawdowns)
    ax2 = fig.add_subplot(gs[1, :])   # underwater
    ax3 = fig.add_subplot(gs[2, 0])   # monthly heatmap
    ax4 = fig.add_subplot(gs[2, 1])   # annual returns
    ax5 = fig.add_subplot(gs[2, 2])   # monthly distribution

    draw_sharpe_ttest(ax1, rolling_sr, upper_band, lower_band, t_stat, p_value)

    with ipio.capture_output():
        pf.plotting.plot_drawdown_underwater(returns, ax=ax2)
        pf.plotting.plot_monthly_returns_heatmap(returns, ax=ax3)
        pf.plotting.plot_annual_returns(returns, ax=ax4)
        pf.plotting.plot_monthly_returns_dist(returns, ax=ax5)
    return fig


class _capture:
    """Local capture_output clone (avoids importing IPython at module import)."""
    def __enter__(self):
        from IPython.utils import io as ipio
        self._c = ipio.capture_output()
        return self._c.__enter__()

    def __exit__(self, *exc):
        return self._c.__exit__(*exc)

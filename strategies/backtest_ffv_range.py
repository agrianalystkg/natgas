# -*- coding: utf-8 -*-
"""
backtest_ffv_range.py — NG fair-value (FFV) range strategy.

Replicates the FFV range rule for natgas on the shared `cmdty_all` bundle.

    mae   = ffv_mid_today - ffv_lower_today                 # distance mid -> lower band
    SHORT if price > ffv_mid  AND  ffv_mid < ffv_mid_4w     # rich  + fair value falling
    LONG  if price < ffv_mid  AND  ffv_mid > ffv_mid_4w     # cheap + fair value rising
    else flat
    strength = |price - ffv_mid| / (mae * mae_strength), capped at max_ts

NG ADAPTATION (important): the FFV is keyed on the contract's DELIVERY month,
not its expiration date. NYMEX NG terminates in the month BEFORE delivery
(e.g. NG_1990_06 trades until 1990-05-14), so we key FFV on the delivery month
taken from the symbol (NG_YYYY_MM), which is what `ffv contract_date` (first of
delivery month) actually is.

Inputs : data/FFV_history_natgas_brent_sim.csv  (run_date, contract_date, ffv_mid, ffv_lower, ...)
Bundle : cmdty_all (NYMEX NG), us_futures calendar
Output : backtests/Results.xlsx  (APPEND=1), BACKTEST_ffv_range_portfolio_value.csv
"""
from zipline.utils.calendar_utils import get_calendar

import warnings
warnings.filterwarnings('ignore')
import os
import zipline
from zipline.api import future_symbol,  \
    set_commission, set_slippage, schedule_function, date_rules, \
    time_rules, continuous_future, order_target, get_datetime, \
    get_open_orders, cancel_order
from datetime import datetime
import pytz
import matplotlib.pyplot as plt
import pyfolio as pf
import pandas as pd
import numpy as np

# ══════════════════════════════════════════════════════════════════════════════
# FFV input
# ══════════════════════════════════════════════════════════════════════════════
import os as _osb
_REPO = _osb.path.dirname(_osb.path.dirname(_osb.path.abspath(__file__)))
FFV_CSV = _osb.path.join(_REPO, "data", "FFV_history_natgas_brent_sim.csv")

FFV = pd.read_csv(FFV_CSV)
FFV['run_date'] = pd.to_datetime(FFV['run_date'], dayfirst=False)
FFV['contract_date'] = pd.to_datetime(FFV['contract_date'], dayfirst=False)

FFV_mid_sparse = FFV.pivot(index='run_date', columns='contract_date', values='ffv_mid')
FFV_lower_sparse = FFV.pivot(index='run_date', columns='contract_date', values='ffv_lower')
FFV_upper_sparse = FFV.pivot(index='run_date', columns='contract_date', values='ffv_upper')

FFV_mid = FFV_mid_sparse.resample('B').last().ffill()
FFV_lower = FFV_lower_sparse.resample('B').last().ffill()
FFV_upper = FFV_upper_sparse.resample('B').last().ffill()

# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════
APPEND = 0
ROLL_MODE = False
contracts = ['NG']

CONTRACT_PARAMS = {
    'NG': {'slippage': 0.001, 'slippage_roll': 0.0005, 'commission': 1.25},
}

start = pd.Timestamp('2000-01-01')
end = pd.Timestamp('2026-06-01')
roll = 'mul'
starting_portfolio = 100_000_000
risk_factor = (1 / 100)
vola_window = 32

# ── FFV range rule parameters ────────────────────────────────────────────────
mae_entry = 0.0          # entry threshold in MAE units
mae_strength = 0.5     # strength = |px - ffv_mid| / (mae * mae_strength)
max_ts =  3           # strength cap
ffv_slope_days = 28      # business days for the FFV direction filter

# ── reformulation switches (2 = band entry, 4 = drop the FFV-slope filter) ────
entry_mode = 'mid'       # 'mid'  -> dislocation from ffv_mid by mae_entry*mae
                         # 'band' -> price beyond ffv_lower / ffv_upper (+ band_buffer*mae)
use_ffv_slope = True     # FFV-direction filter on/off (False = drop the slope condition)
band_buffer = 0.0        # extra MAE beyond the band required for a 'band' entry

tradedcontract = 2       # THIRD contract (portfolio sweet spot)
signalcontract = tradedcontract

position_changing_treshold = 0.15
enable_commission = True
enable_slippage = True

fast_ma = "FFV RANGE"
slow_ma = f"{mae_entry:g}xmae signal, ts {mae_strength:g}xmae, max ts {max_ts:g}"

RESULTS_XLSX = _osb.path.join(_REPO, "results", "Results.xlsx")
beta = None


def delivery_month_first(contract):
    """First calendar day of the contract's DELIVERY month (NG expires pre-delivery)."""
    try:
        parts = str(contract.symbol).split('_')      # 'NG_2026_11'
        return pd.Timestamp(int(parts[1]), int(parts[2]), 1)
    except Exception:
        exp = pd.Timestamp(contract.expiration_date)
        if exp.tz is not None:
            exp = exp.tz_localize(None)
        return (exp + pd.DateOffset(months=1)).replace(day=1)


# ══════════════════════════════════════════════════════════════════════════════
# Reporting widget
# ══════════════════════════════════════════════════════════════════════════════
def report_result(context, data):
    context.months += 1
    context.portfolio_values.append(context.portfolio.portfolio_value)
    today = get_datetime().date()
    ann_ret = np.power(context.portfolio.portfolio_value / starting_portfolio,
                       12 / context.months) - 1
    portfolio_value = context.portfolio.portfolio_value
    if len(context.portfolio_values) >= 3:
        vals = np.array(context.portfolio_values)
        monthly_returns = np.diff(vals) / vals[:-1]
        sharpe = (monthly_returns.mean() / monthly_returns.std()) * np.sqrt(12)
        sharpe_str = f"Sharpe: {sharpe:>+.2f} | "
    else:
        sharpe_str = "Sharpe:     -- | "
    total_months = (end.year - start.year) * 12 + (end.month - start.month)
    pct_done = context.months / total_months
    bar_len = 25
    filled = int(bar_len * pct_done)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(f"\r[{bar}] {pct_done:>5.1%} | {today} | €{portfolio_value:>10,.0f} | "
          f"{ann_ret:>+.2%} ann. | {sharpe_str}", end="", flush=True)


# ══════════════════════════════════════════════════════════════════════════════
# Rolls
# ══════════════════════════════════════════════════════════════════════════════
def roll_futures(context, data):
    global ROLL_MODE
    open_orders = get_open_orders()
    for held_contract in context.portfolio.positions:
        if held_contract in open_orders:
            continue
        continuation = continuous_future(held_contract.root_symbol,
                                         offset=tradedcontract,
                                         roll='calendar', adjustment=roll)
        cont_contract = data.current(continuation, 'contract')
        if cont_contract != held_contract:
            ROLL_MODE = True
            if not data.can_trade(cont_contract):
                continue
            pos_size = context.portfolio.positions[held_contract].amount
            order_target(held_contract, 0)
            order_target(cont_contract, pos_size)
        else:
            ROLL_MODE = False


# ══════════════════════════════════════════════════════════════════════════════
# Sizing
# ══════════════════════════════════════════════════════════════════════════════
def position_size(portfolio_value, std, point_value, trendstrenght):
    target_variation = portfolio_value * risk_factor
    contract_variation = std * point_value
    contracts = target_variation / contract_variation * trendstrenght
    return int(np.nan_to_num(contracts))


# ══════════════════════════════════════════════════════════════════════════════
# Cost models (identical to the suite)
# ══════════════════════════════════════════════════════════════════════════════
from zipline.finance.slippage import SlippageModel


class RollAwareFixedSlippage(SlippageModel):
    def process_order(self, data, order):
        asset = order.asset
        if not data.can_trade(asset):
            return None, None
        px = data.current(asset, 'price')
        if px is None or np.isnan(px):
            return None, None
        if not enable_slippage:
            return px, order.amount
        params = CONTRACT_PARAMS.get(asset.root_symbol,
                                     {'slippage': 10.0, 'slippage_roll': 3.3})
        spread = params['slippage_roll'] if ROLL_MODE else params['slippage']
        direction = 1 if order.amount > 0 else -1
        return px + direction * spread, order.amount


from zipline.finance.commission import CommissionModel


class PerContractCustomCommission(CommissionModel):
    def calculate(self, order, transaction):
        if not enable_commission:
            return 0.0
        root = order.asset.root_symbol
        params = CONTRACT_PARAMS.get(root, {'commission': 2.5})
        return abs(transaction.amount) * params['commission'] * 2


# ══════════════════════════════════════════════════════════════════════════════
# initialize
# ══════════════════════════════════════════════════════════════════════════════
def initialize(context):
    context.ffv_mid = FFV_mid
    context.ffv_lower = FFV_lower
    context.ffv_upper = FFV_upper
    context.trendstrenght_log = {}
    context.direction_log = {}
    context.portfolio_values = []
    context.lookback = 1285

    set_commission(us_futures=PerContractCustomCommission())
    set_slippage(us_futures=RollAwareFixedSlippage())

    context.universe = [
        continuous_future(m, offset=signalcontract, roll='calendar', adjustment=roll)
        for m in contracts
    ]
    context.trade_universe = {
        m: continuous_future(m, offset=tradedcontract, roll='calendar', adjustment=roll)
        for m in contracts
    }

    schedule_function(daily_trade, date_rules.every_day(), time_rules.market_close())
    context.months = 0
    schedule_function(func=report_result, date_rule=date_rules.month_start(),
                      time_rule=time_rules.market_close())


# ══════════════════════════════════════════════════════════════════════════════
# analyze
# ══════════════════════════════════════════════════════════════════════════════
from IPython.utils import io as ipio
from scipy import stats


def calc_drawdown_stats(returns):
    cum = (1 + returns).cumprod()
    dd = (cum - cum.cummax()) / cum.cummax()
    return dd[dd < 0].mean() * 100, dd.min() * 100


def calc_fat_tails(returns):
    GAUSSIAN = 4.43
    r = returns[returns != 0]
    d = r - r.mean()
    p01, p30, p70, p99 = np.percentile(d, [1, 30, 70, 99])
    return (p01 / p30) / GAUSSIAN, (p99 / p70) / GAUSSIAN


def calc_sharpe_ttest(returns):
    m = returns.expanding().mean()
    s = returns.expanding().std()
    n = pd.Series(range(1, len(returns) + 1), index=returns.index)
    sr = (m / s) * np.sqrt(252)
    se = np.sqrt(252) / np.sqrt(n)
    t = returns.mean() / returns.std() * np.sqrt(len(returns))
    p = stats.t.sf(t, df=len(returns) - 1)
    return t, p, sr, sr + 2 * se, sr - 2 * se


def analyze(context, perf):
    print("\n✅ Backtest complete!\n")
    ts_log = pd.Series(context.trendstrenght_log)
    ts_log.index = ts_log.index.tz_localize(None)
    global SIGNAL_HISTORY
    SIGNAL_HISTORY = ts_log.copy()
    ts_abs_mean = float(ts_log.abs().mean()) if len(ts_log) else np.nan

    returns, positions, transactions = pf.utils.extract_rets_pos_txn_from_zipline(perf)
    monthly_returns = returns.resample('M').apply(lambda x: (1 + x).prod() - 1)
    monthly_skew = monthly_returns.dropna().skew()

    stats_d = pf.timeseries.perf_stats(returns)
    avg_dd, _ = calc_drawdown_stats(returns)
    lower_tail, upper_tail = calc_fat_tails(returns)
    t_stat, p_value, rolling_sr, ub, lb = calc_sharpe_ttest(returns)
    passed = p_value < 0.025

    import sys as _sys
    if os.path.dirname(os.path.abspath(__file__)) not in _sys.path:
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from carver_costs import carver_cost_report, carver_summary_rows
        carver = carver_cost_report(perf, CONTRACT_PARAMS, enable_commission,
                                    enable_slippage, stats_d)
    except Exception as e:
        carver = None
        print('carver warning:', e)

    global ALPHA_BETA
    ALPHA_BETA = {}
    if beta:
        try:
            import alpha_beta as _ab
            book = _ab.load_monthly_returns_book(RESULTS_XLSX)
            if beta in book.columns:
                ALPHA_BETA = _ab.compute_alpha_beta(monthly_returns, book[beta].dropna())
                ALPHA_BETA['benchmark'] = beta
        except Exception as e:
            print('alpha/beta warning:', e)

    rows = (carver_summary_rows(carver) if carver else []) + [
        ("Annual net return", f"{stats_d['Annual return']*100:,.2f}%", ""),
        ("Cumulative returns", f"{stats_d['Cumulative returns']*100:,.2f}%", ""),
        ("Annual volatility", f"{stats_d['Annual volatility']*100:,.2f}%", ""),
        ("Sharpe ratio", f"{stats_d['Sharpe ratio']:,.2f}", ""),
        ("Sortino ratio", f"{stats_d['Sortino ratio']:,.2f}", ""),
        ("Stability", f"{stats_d['Stability']:,.2f}", ""),
        ("Max drawdown", f"{stats_d['Max drawdown']*100:,.2f}%", ""),
        ("Avg drawdown", f"{avg_dd:,.2f}%", ""),
        ("Skew (monthly)", f"{monthly_skew:,.2f}", ""),
        ("Tail ratio (95/5)", f"{stats_d['Tail ratio']:,.2f}", ""),
        ("Lower tail ratio (1/30/4.43)", f"{lower_tail:,.3f}", "⚠" if lower_tail > 1.5 else "✓"),
        ("Upper tail ratio (99/70/4.43)", f"{upper_tail:,.3f}", "⚠" if upper_tail > 1.5 else "✓"),
        ("t-statistic", f"{t_stat:,.3f}", ""),
        ("p-value (one-tailed)", f"{p_value*100:,.2f}%", "✅ PASSED" if passed else "❌ NOT PASSED"),
        ("Avg abs strength", f"{ts_abs_mean:,.2f}", ""),
    ]
    df = pd.DataFrame(rows, columns=["Metric", "Value", ""])

    global SUMMARY_EXTRA
    _c = carver or {}
    SUMMARY_EXTRA = {
        'GrossAnnualReturn_pct': _c.get('gross_annual_return', np.nan) * 100,
        'GrossSharpe': _c.get('gross_sharpe', np.nan),
        'CommissionCost_SR': _c.get('commission_sr', np.nan),
        'SlippageCost_SR': _c.get('slippage_sr', np.nan),
        'RollingCost_SR': _c.get('rolling_sr', np.nan),
        'TotalCost_SR': _c.get('total_sr', np.nan),
        'AnnualTurnover_x': (_c.get('turnover') if _c.get('turnover') is not None else np.nan),
        'RollsPerYear': _c.get('rolls_per_year', np.nan),
        'TrendStrength_MeanAbs': ts_abs_mean,
        'TrendStrength_p95': np.nan,
        'TrendStrength_CapFrac': np.nan,
    }

    print("\n=== STRATEGY SUMMARY ===")
    print(df.to_string(index=False))

    ts_scaled = ts_log * 50
    # ── Redesigned charts (shared helper: reporting_charts.py) ──────────────
    import sys as _sys
    import os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    import reporting_charts as rc
    import continuous_prices as cp
    if len(contracts) == 1:
        price_series, price_label = cp.load_chart_price(
            contracts[0], tradedcontract, start=returns.index[0])
    else:
        price_series, price_label = None, ''
    rc.page1(returns, positions, perf, price_series, ts_scaled, price_label)
    plt.show()
    rc.page2(returns, (t_stat, p_value, rolling_sr, ub, lb))
    plt.show()



# ══════════════════════════════════════════════════════════════════════════════
# daily trading logic
# ══════════════════════════════════════════════════════════════════════════════
def daily_trade(context, data):
    today = get_datetime().tz_localize(None).normalize()

    hist = data.history(context.universe, fields=['close'], frequency='1d',
                        bar_count=context.lookback)
    open_pos = {pos.root_symbol: pos for pos in context.portfolio.positions}

    ffv_hist = context.ffv_mid.loc[:today]
    ffv_low_hist = context.ffv_lower.loc[:today]
    ffv_up_hist = context.ffv_upper.loc[:today]
    n_rows = len(ffv_hist)
    if n_rows == 0:
        return
    row = ffv_hist.iloc[-1]
    row_low = ffv_low_hist.iloc[-1]
    row_up = ffv_up_hist.iloc[-1]
    row_slope = ffv_hist.iloc[-min(ffv_slope_days, n_rows)]

    for continuation in context.universe:
        root = continuation.root_symbol
        h = hist.xs(continuation, level=1)

        contract = data.current(continuation, 'contract')
        key = delivery_month_first(contract)

        ffv_mid_today = row.get(key, np.nan)
        ffv_mid_4w = row_slope.get(key, np.nan)
        ffv_lower_today = row_low.get(key, np.nan)
        ffv_upper_today = row_up.get(key, np.nan)
        mae = ffv_mid_today - ffv_lower_today

        ret = h['close'].pct_change()
        std_fast = ret.ewm(span=vola_window, adjust=False).std().iloc[-1]
        std_slow = ret.rolling(1260, min_periods=vola_window).std().iloc[-1]
        std_blended = std_fast if pd.isna(std_slow) else 0.3 * std_slow + 0.7 * std_fast
        std = std_blended * h['close'].iloc[-1]
        if std is None or std == 0 or (std != std):
            continue

        px = h['close'].iloc[-1]

        # ── FFV range rule ───────────────────────────────────────────────────
        if not (np.isfinite(ffv_mid_today) and np.isfinite(ffv_mid_4w)
                and np.isfinite(ffv_lower_today) and np.isfinite(ffv_upper_today)
                and np.isfinite(mae) and mae > 0):
            direction = 0
            trendstrenght = 0.0
        else:
            if entry_mode == 'band':
                long_entry = px < (ffv_lower_today - band_buffer * mae)
                short_entry = px > (ffv_upper_today + band_buffer * mae)
            else:
                long_entry = px < (ffv_mid_today - mae_entry * mae)
                short_entry = px > (ffv_mid_today + mae_entry * mae)
            if use_ffv_slope:
                up_ok = ffv_mid_today > ffv_mid_4w
                dn_ok = ffv_mid_today < ffv_mid_4w
            else:
                up_ok = dn_ok = True
            if long_entry and up_ok:
                direction = 1
            elif short_entry and dn_ok:
                direction = -1
            else:
                direction = 0
            trendstrenght = min(abs(px - ffv_mid_today) / (mae * mae_strength), max_ts)

        context.direction_log[get_datetime()] = direction
        context.trendstrenght_log[get_datetime()] = direction * trendstrenght

        if root in open_pos:
            held = open_pos[root]
            current_amount = context.portfolio.positions[held].amount
            order_contract = held
        else:
            current_amount = 0
            order_contract = data.current(context.trade_universe[root], 'contract')

        if not data.can_trade(order_contract):
            for _o in get_open_orders().get(order_contract, []):
                cancel_order(_o)
            continue

        target_contracts = int(position_size(context.portfolio.portfolio_value, std,
                                             contract.price_multiplier, trendstrenght))
        target_amount = direction * target_contracts

        if current_amount == 0:
            order_target(order_contract, target_amount)
        elif target_amount == 0:
            order_target(order_contract, 0)
        else:
            change_ratio = abs(target_amount - current_amount) / abs(current_amount)
            if change_ratio >= position_changing_treshold:
                order_target(order_contract, target_amount)

    if len(context.portfolio.positions) > 0:
        roll_futures(context, data)


perf = zipline.run_algorithm(
    start=start, end=end, initialize=initialize, analyze=analyze,
    capital_base=starting_portfolio, data_frequency='daily',
    bundle='cmdty_all',
    trading_calendar=get_calendar('us_futures'),
)

# (portfolio CSV dump removed for the public repo)

# ══════════════════════════════════════════════════════════════════════════════
# APPEND to Results.xlsx  (Sheet1 summary / Sheet2 equity / Sheet3 monthly)
# ══════════════════════════════════════════════════════════════════════════════
if APPEND == 1:
    import alpha_beta as _ab

    def append_zipline_results_to_excel(perf, config, results_path=RESULTS_XLSX):
        ts = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        equity = perf[['portfolio_value']].copy()
        equity = equity.reset_index().rename(columns={'index': 'Date',
                                                      'portfolio_value': 'PortfolioValue'})
        equity['Date'] = pd.to_datetime(equity['Date']).dt.tz_localize(None)
        run_name = config.get("run_name") or \
            f"{ts} | {config.get('contracts','?')} | fast={config.get('fast_ma','?')} slow={config.get('slow_ma','?')}"
        equity = equity.rename(columns={'PortfolioValue': run_name})

        pv0, pv1 = float(equity[run_name].iloc[0]), float(equity[run_name].iloc[-1])
        eq_series = equity[run_name].astype(float)
        daily_ret = eq_series.pct_change().dropna()
        n = len(daily_ret)
        cumulative_return_pct = (pv1 / pv0 - 1.0) * 100.0
        ann_return = (pv1 / pv0) ** (252 / n) - 1.0 if n else np.nan
        ann_vol = daily_ret.std(ddof=0) * np.sqrt(252)
        _sharpe_ret = perf['returns'].astype(float).dropna()
        sharpe = (_sharpe_ret.mean() / _sharpe_ret.std(ddof=1)) * np.sqrt(252) if _sharpe_ret.std(ddof=1) else np.nan
        downside = np.where(daily_ret < 0, daily_ret, 0)
        downside_std = np.std(downside) * np.sqrt(252)
        sortino = ann_return / downside_std if downside_std else np.nan
        peak = eq_series.cummax(); dd = eq_series / peak - 1.0
        max_drawdown = dd.min() * 100.0; avg_drawdown = dd[dd < 0].mean() * 100.0

        monthly_eq = equity.set_index('Date')[run_name].astype(float).resample("M").last()
        monthly_ret = monthly_eq.pct_change().dropna()
        skew_monthly = stats.skew(monthly_ret) if len(monthly_ret) >= 3 else np.nan
        if len(monthly_ret) > 0:
            q95, q5 = np.percentile(monthly_ret, [95, 5])
            tail_ratio = abs(q95) / abs(q5) if q5 else np.nan
            active = daily_ret[daily_ret != 0]; dmean = active - active.mean()
            p1, p30, p70, p99 = np.percentile(dmean, [1, 30, 70, 99])
            lower_tail_ratio = (p1 / p30) / 4.43
            upper_tail_ratio = (p99 / p70) / 4.43
            t_stat = monthly_ret.mean() / (monthly_ret.std(ddof=1) / np.sqrt(len(monthly_ret)))
            p_value = 1.0 - stats.t.cdf(t_stat, df=len(monthly_ret) - 1)
        else:
            tail_ratio = lower_tail_ratio = upper_tail_ratio = t_stat = p_value = np.nan
        pass_flag = bool(pd.notna(p_value) and p_value < 0.05)
        y = np.log(eq_series.values); x = np.arange(len(y))
        b = np.polyfit(x, y, 1); yh = b[0] * x + b[1]
        stability_r2 = 1 - np.sum((y - yh) ** 2) / np.sum((y - y.mean()) ** 2)

        ab = globals().get("ALPHA_BETA", {}) or {}
        summary_row = {
            "Timestamp": ts, **config, "run_name": run_name,
            "StartPortfolioValue": pv0, "EndPortfolioValue": pv1,
            "CumulativeReturn_%": cumulative_return_pct, "AnnualReturn_%": ann_return * 100,
            "AnnualVolatility_%": ann_vol * 100, "Sharpe": sharpe, "Sortino": sortino,
            "Stability_R2": stability_r2, "MaxDrawdown_%": max_drawdown,
            "AvgDrawdown_%": avg_drawdown, "Skew_Monthly": skew_monthly,
            "TailRatio_95_5": tail_ratio, "LowerTailRatio_1_30": lower_tail_ratio,
            "UpperTailRatio_99_70": upper_tail_ratio, "T_statistic": t_stat,
            "P_value_OneTailed": p_value, "PASS_p_lt_0_05": pass_flag,
            "Benchmark_run": ab.get("benchmark"),
            "Beta_to_benchmark": ab.get("beta"),
            "Alpha_annualized_%": (ab["alpha_ann"] * 100 if pd.notna(ab.get("alpha_ann")) else np.nan),
            "R2_to_benchmark": ab.get("r2"),
            "Alpha_beta_overlap_months": ab.get("overlap_months"),
        }

        try:
            summary_row.update(globals().get("SUMMARY_EXTRA", {}) or {})
        except Exception:
            pass
        ns = pd.DataFrame([summary_row])
        os.makedirs(os.path.dirname(results_path), exist_ok=True)
        if os.path.exists(results_path):
            try:
                s1 = pd.read_excel(results_path, sheet_name="Sheet1")
                s1 = pd.concat([s1, ns], ignore_index=True)
            except Exception:
                s1 = ns
            try:
                s2 = pd.read_excel(results_path, sheet_name="Sheet2")
                s2['Date'] = pd.to_datetime(s2['Date'])
                s2 = s2.drop(columns=[run_name], errors='ignore')
                s2 = pd.merge(s2, equity, on="Date", how="outer").sort_values("Date").reset_index(drop=True)
            except Exception:
                s2 = equity
        else:
            s1, s2 = ns, equity
        book = _ab.load_monthly_returns_book(results_path)
        book = book.drop(columns=[run_name], errors='ignore')
        s3 = _ab.append_monthly_returns_column(book, run_name, monthly_ret)
        # <<< SIGNALS: persist raw + rescaled daily conviction (new sheets)
        import signal_book as _sb
        _sig = globals().get("SIGNAL_HISTORY")
        _raw_out = _scal_out = None
        if _sig is not None and len(_sig):
            _raw_out = _sb.append_column(_sb.load_book(results_path, _sb.SHEET_RAW), run_name, _sig)
            _scal_out = _sb.append_column(_sb.load_book(results_path, _sb.SHEET_SCALED), run_name, _sb.rescale(_sig))

        with pd.ExcelWriter(results_path, engine="openpyxl") as writer:
            s1.to_excel(writer, sheet_name="Sheet1", index=False)
            s2.to_excel(writer, sheet_name="Sheet2", index=False)
            s3.reset_index().to_excel(writer, sheet_name="Sheet3", index=False)
            if _raw_out is not None:
                _raw_out.reset_index().to_excel(writer, sheet_name=_sb.SHEET_RAW, index=False)
                _scal_out.reset_index().to_excel(writer, sheet_name=_sb.SHEET_SCALED, index=False)
        print(f"\n✓ Results appended to: {results_path}\n  New run: {run_name}")

    config = {
        "run_name": None, "contracts": ",".join(contracts),
        "start": str(start.date()), "end": str(end.date()), "strategy": "ffv_range",
        "roll": roll, "beta": beta, "tradedcontract": tradedcontract,
        "mae_entry": mae_entry, "mae_strength": mae_strength, "max_ts": max_ts,
        "ffv_slope_days": ffv_slope_days, "entry_mode": entry_mode,
        "use_ffv_slope": use_ffv_slope, "band_buffer": band_buffer,
        "starting_portfolio": starting_portfolio,
        "risk_factor": risk_factor, "vola_window": vola_window,
        "fast_ma": fast_ma, "slow_ma": slow_ma,
        "enable_commission": enable_commission, "enable_slippage": enable_slippage,
        "position_changing_treshold": position_changing_treshold,
    }
    append_zipline_results_to_excel(perf, config)

# -*- coding: utf-8 -*-
"""
04e_gas_yoy_price_model_ukgas.py
--------------------------------
UK-gas variant of 04 — kept as a SEPARATE model.

Same gas expiry-price construction and LOG-return target as 04, but the
exogenous set is:
    Storage YoY  +  UK gas YoY  +  log Price 12m ago

UK gas history  = monthly average of the front-month ICE UK NBP (NF) contract.
Forward UK gas  = the CURRENT UK gas forward curve (latest settle per delivery
                  month), so the forward FFV uses a market-implied path
                  instead of a flat / seasonal-naive one.

Config identical to 04d: var_window = 120 months, time_decay = 0.985
(half-life ~45.9 months).

Input : <barchart>/nymex_natgas_master.csv
        <barchart>/uk_natural_gas_master.csv
        data/gas_snd_monthly.xlsx
Output: data/04e_pooled_yoy_ukgas_model.xlsx
        data/plots\\04e_pooled_*.png
        data/FFV_history_natgas_ukgas.csv

Structure mirrors 04:
    00 methods -> 01 prices + UK gas -> 02 fundamentals/xy -> 03 MODEL_CONFIGS
    -> 04+05 forecasting -> 05b price-level reconstruction -> 06 save
    -> 07 FFV visualization.

Spyder-friendly: run cell by cell with #%% separators.
"""

import os
import numpy as np
import pandas as pd
from warnings import simplefilter
simplefilter(action="ignore", category=pd.errors.PerformanceWarning)
import statsmodels.api as sm
import matplotlib
# matplotlib.use('Agg')   # <- uncomment for headless runs; Spyder: leave commented
import matplotlib.pyplot as plt

# ============================================================
# RUN SETTINGS
# ============================================================
SND_DIR    = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
PRICES_CSV = os.environ.get("NG_PRICES_CSV", os.path.join(SND_DIR, "raw", "nymex_natgas_master.csv"))
UKGAS_CSV  = os.environ.get("UKGAS_CSV",  os.path.join(SND_DIR, "raw", "uk_natural_gas_master.csv"))
FUND_XLSX  = os.path.join(SND_DIR, "gas_snd_monthly.xlsx")
PLOT_DIR   = os.path.join(SND_DIR, "plots")
OUT_XLSX   = os.path.join(SND_DIR, "04e_pooled_yoy_ukgas_model.xlsx")

N_LAST_DAYS  = 25      # avg settle over last N traded days of the pricing window
MIN_OBS      = 8       # min traded days in window for a valid price point
# --- CME termination-aware pricing window ---
# CME rule: NG trading terminates on the 3rd last business day of the month
# PRIOR to the contract month (NOV contract is priced in OCTOBER).
# Window = N_LAST_DAYS traded days ending BUFFER_DAYS business days before
# termination; price is indexed by its FORMATION month (M-1) and matched
# with M-1 fundamentals.
TERMINATION_FROM_END = 3      # CME spec: 3rd last business day of M-1
BUFFER_DAYS          = 5      # safety buffer vs illiquid final prints
USE_PARTIAL_WINDOW   = False  # True = accept a partially traded window as actual
STOR_COL     = "stor_total_us"   # storage series for the YoY exog
TIGHT_Q      = 0.80   # deficit quantile that marks "genuinely tight"; sets the
                      # threshold of the convex tight-tail hinge (option 2).
FUTURE_M     = 12      # months to forecast beyond last actual

SHOW_PLOTS   = True    # True = display figures interactively (Spyder: leave True).
                       # Plots are ALWAYS written to PLOT_DIR regardless of this flag.
                       # For a headless run: uncomment matplotlib.use('Agg') above and/or
                       # set SHOW_PLOTS = False.

#%% 00 DEFINING FORECASTING METHODS 
def exp_time_weights(index, decay=0.97):
    """Exponential time-decay weights: newest obs weight 1.0, older decay**k."""
    n = len(index)
    w = decay ** np.arange(n)[::-1]
    return pd.Series(w, index=index)


def rolling_trend_forecast(df, y_col, seasonal_cols=None, deg=1, window=36,
                           start_date=None, forecast_future=True):
    """Rolling polynomial trend model with out-of-sample forecasts."""
    if seasonal_cols is None:
        seasonal_cols = []

    df_use = df[[y_col] + list(seasonal_cols)].dropna().sort_index().copy()
    n = len(df_use)

    if start_date is not None:
        start_date_ts = pd.to_datetime(start_date)
        valid_indices = df_use.index >= start_date_ts
        start_idx = max(valid_indices.argmax(), window) if valid_indices.any() else window
    else:
        start_idx = window

    fitted = pd.Series(index=df_use.index, dtype=float, name='trend_fitted')

    for i in range(start_idx, n):
        train = df_use.iloc[i - window: i].copy()
        t = np.arange(len(train), dtype=float)
        t_center = t - t.mean()
        for p in range(1, deg + 1):
            train[f'_t{p}'] = t_center ** p

        x_cols = [f'_t{p}' for p in range(1, deg + 1)] + list(seasonal_cols)
        model = sm.OLS(train[y_col], sm.add_constant(train[x_cols], has_constant='add')).fit()

        t_next = t_center[-1] + 1.0
        X_next = pd.DataFrame({f'_t{p}': [t_next ** p] for p in range(1, deg + 1)},
                              index=[df_use.index[i]])
        for col in seasonal_cols:
            X_next[col] = df_use.iloc[i][col]
        X_next = sm.add_constant(X_next, has_constant='add')
        X_next = X_next.reindex(columns=model.model.exog_names).fillna(0.0)
        fitted.iloc[i] = model.predict(X_next).iloc[0]

    if forecast_future:
        last_actual = df_use[y_col].last_valid_index()
        future_dates = df.loc[df.index > last_actual].index
        if len(future_dates) > 0:
            train_final = df_use.iloc[-window:].copy()
            t = np.arange(len(train_final), dtype=float)
            t_center = t - t.mean()
            t_last = t_center[-1]
            for p in range(1, deg + 1):
                train_final[f'_t{p}'] = t_center ** p
            x_cols = [f'_t{p}' for p in range(1, deg + 1)] + list(seasonal_cols)
            final_model = sm.OLS(train_final[y_col],
                                 sm.add_constant(train_final[x_cols], has_constant='add')).fit()
            for idx, future_date in enumerate(future_dates, start=1):
                if future_date in df.index:
                    t_future = t_last + float(idx)
                    X_future = pd.DataFrame(
                        {f'_t{p}': [t_future ** p] for p in range(1, deg + 1)},
                        index=[future_date])
                    for col in seasonal_cols:
                        X_future[col] = df.loc[future_date, col] if col in df.columns else 0
                    X_future = sm.add_constant(X_future, has_constant='add')
                    X_future = X_future.reindex(columns=final_model.model.exog_names).fillna(0.0)
                    fitted.loc[future_date] = final_model.predict(X_future).iloc[0]

    results = pd.DataFrame({'actual': df_use[y_col], 'trend_fitted': fitted})
    results['residual'] = results['actual'] - results['trend_fitted']

    mask = results['trend_fitted'].notna() & results['actual'].notna()
    if mask.sum() > 0:
        actual = results.loc[mask, 'actual']
        fitted_vals = results.loc[mask, 'trend_fitted']
        residuals = actual - fitted_vals
        stats_df = pd.DataFrame({
            'RMSE': [np.sqrt(np.mean(residuals ** 2))],
            'RMSE_%': [100 * np.sqrt(np.mean(residuals ** 2)) / actual.mean()],
            'MAE': [np.mean(np.abs(residuals))],
            'MAE_%': [100 * np.mean(np.abs(residuals)) / actual.mean()],
            'R²': [1 - np.sum(residuals ** 2) / np.sum((actual - actual.mean()) ** 2)],
            'n_obs': [mask.sum()]}, index=['Trend_Model'])
    else:
        stats_df = pd.DataFrame()
    return results, stats_df


def rolling_variable_forecast(df, y_col, x_cols, window=48, time_decay=None,
                              start_date=None, forecast_future=True, nonneg_cols=None):
    """Rolling regression with optional time decay; one-step-ahead OOS forecasts.

    nonneg_cols: columns whose fitted coefficient is constrained to be >= 0.
    If the unconstrained fit turns one negative, that term is dropped and the
    window is refit -- so a convex term may only ADD upside, never invert.
    """
    df_use = df[[y_col] + x_cols].dropna().sort_index().copy()
    n = len(df_use)

    def _fit(train, cols):
        w = exp_time_weights(train.index, decay=time_decay) if time_decay else None
        Y = train[y_col]
        X = sm.add_constant(train[cols], has_constant='add')
        m = sm.OLS(Y, X).fit() if w is None else sm.WLS(Y, X, weights=w).fit()
        if nonneg_cols:
            drop = [c for c in nonneg_cols if c in m.params.index and m.params[c] < 0]
            if drop:
                cols = [c for c in cols if c not in drop]
                X = sm.add_constant(train[cols], has_constant='add')
                m = sm.OLS(Y, X).fit() if w is None else sm.WLS(Y, X, weights=w).fit()
        return m, cols

    if start_date is not None:
        start_date_ts = pd.to_datetime(start_date)
        valid_indices = df_use.index >= start_date_ts
        start_idx = max(valid_indices.argmax(), window) if valid_indices.any() else window
    else:
        start_idx = window

    fitted = pd.Series(index=df_use.index, dtype=float, name='fitted')

    for i in range(start_idx, n):
        train = df_use.iloc[i - window: i]
        model, cols_used = _fit(train, x_cols)
        X_next = sm.add_constant(df_use.loc[[df_use.index[i]], cols_used], has_constant='add')
        X_next = X_next.reindex(columns=model.model.exog_names).fillna(0.0)
        fitted.iloc[i] = model.predict(X_next).iloc[0]

    train_final = df_use.iloc[-window:]
    final_model, _ = _fit(train_final, x_cols)

    if forecast_future:
        last_actual = df_use[y_col].last_valid_index()
        future_dates = df.loc[df.index > last_actual].index
        for future_date in future_dates:
            if all(col in df.columns for col in x_cols):
                X_future = df.loc[[future_date], x_cols]
                if not X_future.isna().any().any():
                    X_future = sm.add_constant(X_future, has_constant='add')
                    X_future = X_future.reindex(columns=final_model.model.exog_names).fillna(0.0)
                    fitted.loc[future_date] = final_model.predict(X_future).iloc[0]

    results = pd.DataFrame({'actual': df_use[y_col], 'fitted': fitted})
    results['residual'] = results['actual'] - results['fitted']

    mask = results['fitted'].notna() & results['actual'].notna()
    stats_df = None
    if mask.sum() > 0:
        actual = results.loc[mask, 'actual']
        fitted_vals = results.loc[mask, 'fitted']
        residuals = actual - fitted_vals
        rmse = np.sqrt(np.mean(residuals ** 2))
        mae = np.mean(np.abs(residuals))
        stats_df = pd.DataFrame({
            'RMSE': [rmse], 'RMSE_%': [100 * rmse / actual.abs().mean()],
            'MAE': [mae], 'MAE_%': [100 * mae / actual.abs().mean()],
            'R²': [1 - np.sum(residuals ** 2) / np.sum((actual - actual.mean()) ** 2)],
            'n_obs': [mask.sum()],
            'directional_acc': [(np.sign(actual) == np.sign(fitted_vals)).mean()],
        }, index=['Variable_Model'])
    return results, stats_df, final_model

#%% 01 LOADING PRICES  (CME termination-aware, formation-month indexed)
px = pd.read_csv(PRICES_CSV, parse_dates=["date"])
px = px[px["settle"] > 0]

from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay
US_BD = CustomBusinessDay(calendar=USFederalHolidayCalendar())
LAST_DATA_DATE = px["date"].max()

def pricing_window_end(deliv):
    """(TERMINATION_FROM_END + BUFFER_DAYS)-th last business day of month M-1."""
    k = TERMINATION_FROM_END + BUFFER_DAYS
    bdays = pd.date_range(deliv - pd.offsets.MonthBegin(1),
                          deliv - pd.Timedelta(days=1), freq=US_BD)
    return bdays[-k] if len(bdays) >= k else None

def expiry_price(g):
    """Avg settle over the N_LAST_DAYS traded days of the pricing window."""
    g = g.sort_values("date")
    deliv = pd.Timestamp(int(g["delivery_year"].iloc[0]),
                         int(g["delivery_month"].iloc[0]), 1)
    w_end = pricing_window_end(deliv)
    if w_end is None:
        return None
    if not USE_PARTIAL_WINDOW and w_end > LAST_DATA_DATE:
        return None                      # window not complete -> forecast target
    traded_all = g[g["date"] <= w_end]
    traded = traded_all[traded_all["has_traded"]]
    # pre-2001 contracts: has_traded flag is unreliable -> fall back to all rows
    use = traded if len(traded) >= MIN_OBS else traded_all
    if len(use) < MIN_OBS:
        return None
    return pd.Series({"deliv": deliv,
                      "expiry_px": use.tail(N_LAST_DAYS)["settle"].mean()})

expiry = (px.groupby("contract", group_keys=False)
            .apply(expiry_price, include_groups=False)
            .dropna(subset=["expiry_px"])
            .reset_index())
expiry["formation"] = expiry["deliv"] - pd.offsets.MonthBegin(1)   # month M-1
print(f"Valid expiry prices: {len(expiry)} contracts "
      f"({expiry['deliv'].min():%Y-%m} -> {expiry['deliv'].max():%Y-%m}), "
      f"indexed by formation month (contract month - 1)")

price_m = expiry.set_index("formation")["expiry_px"].sort_index()   # pooled monthly

# --- UK gas (ICE NBP, NF): history + current forward curve ---
uk = pd.read_csv(UKGAS_CSV, parse_dates=["date"],
                 usecols=["contract", "delivery_year", "delivery_month", "date", "settle"])
uk = uk[uk["settle"] > 0].copy()
uk["delivery"] = pd.to_datetime(dict(year=uk.delivery_year,
                                     month=uk.delivery_month, day=1))

# history: monthly average of the front-month contract
uk["rank"] = (uk["delivery"] - uk["date"]).dt.days
front = uk.loc[uk[uk["rank"] >= 0].groupby("date")["rank"].idxmin()]
uk_hist = front.set_index("date")["settle"].resample("MS").mean()
uk_hist.index = pd.to_datetime(uk_hist.index).to_period("M").to_timestamp()

# forward curve: latest settle per delivery month (as of the last data date)
uk_fwd = (uk.sort_values("date").groupby("delivery")["settle"].last()
            .loc[lambda s: s.index > uk_hist.last_valid_index()])
print(f"UK gas history: {uk_hist.index.min():%Y-%m} -> {uk_hist.index.max():%Y-%m}; "
      f"forward curve: {uk_fwd.index.min():%Y-%m} -> {uk_fwd.index.max():%Y-%m} "
      f"({len(uk_fwd)} months)")
del uk, front

#%% 02 LOADING FUNDAMENTALS + building xy
fund = pd.read_excel(FUND_XLSX, sheet_name="data", index_col=0, parse_dates=True)
fund.index = pd.to_datetime(fund.index).to_period("M").to_timestamp()

# calendar: full price history + fundamentals + FUTURE_M months ahead
last_px = price_m.index.max()
full_idx = fund.index.union(price_m.index)
future_idx = pd.date_range(max(fund.index.max(), last_px) + pd.offsets.MonthBegin(1),
                           periods=FUTURE_M, freq="MS")
xy = pd.DataFrame(index=full_idx.union(future_idx))
xy["Price"] = price_m
xy["Price_12m_ago"] = (xy["Price"].shift(12))
# xy["Price_12m_ago2"] = xy["Price_12m_ago2"] **2
xy["Storage"] = fund[STOR_COL]

# UK gas: history, then fill forward months from the CURRENT forward curve
xy["UKgas"] = uk_hist
xy["ukgas_is_forward"] = False
for d in xy.index[xy["UKgas"].isna()]:
    if d in uk_fwd.index:
        xy.loc[d, "UKgas"] = uk_fwd.loc[d]
        xy.loc[d, "ukgas_is_forward"] = True

# storage beyond last actual: seasonal-naive fill (same month last year)
stor_last = xy["Storage"].last_valid_index()
xy["storage_is_estimate"] = False
for d in xy.index[xy.index > stor_last]:
    ly = d - pd.DateOffset(years=1)
    if ly in xy.index and pd.notna(xy.loc[ly, "Storage"]):
        xy.loc[d, "Storage"] = xy.loc[ly, "Storage"]
        xy.loc[d, "storage_is_estimate"] = True

# Transforms. The model target is the LOG 12-month return of price; the
# lagged regressor is the log price 12 months ago (coherent log-log spec).
# "Price YoY" (simple return) is kept only as a reference column.
xy["LogPrice"]          = np.log(xy["Price"])
xy["Log Price_12m_ago"] = np.log(xy["Price_12m_ago"])
xy["Log Price YoY"]     = xy["LogPrice"] - xy["LogPrice"].shift(12)
xy["Price YoY"]         = xy["Price"].pct_change(12, fill_method=None)
xy["Storage YoY"]       = np.log(xy["Storage"] / xy["Storage"].shift(12))
xy["UKgas YoY"]         = np.log(xy["UKgas"] / xy["UKgas"].shift(12))

# --- Nonlinear storage-tightness candidates ------------------------------
# deficit = -(Storage YoY): positive = tighter than last year.
# All candidates are linear in deficit on the LOOSE side and convex on the
# TIGHT side (no 1/x blow-up near zero like (1/StorageYoY)**3).
# Expected coefficient sign: POSITIVE (tighter storage -> higher price YoY).
d_stor = -xy["Storage YoY"]
xy["Storage tight cubic"]  = np.where(d_stor > 0, d_stor ** 3, d_stor)
xy["Storage tight quad"]   = np.where(d_stor > 0, d_stor ** 2, d_stor)
xy["Storage deficit kink"] = np.maximum(d_stor, 0)

# Convex TIGHT-TAIL-ONLY hinge (option 2): zero unless storage is genuinely
# tight, then quadratic in the excess deficit. Cutoff = TIGHT_Q quantile of
# the actual deficit history (an ex-ante design constant, like a "low storage"
# threshold). Being 0 on ~80% of months it does not collide with the linear term.
_hist_d = d_stor[~xy["storage_is_estimate"].astype(bool)].dropna()
TIGHT_D = float(_hist_d.quantile(TIGHT_Q))
xy["Storage tight hinge"] = np.maximum(d_stor - TIGHT_D, 0.0) ** 2
print(f"Tight-tail hinge cutoff (q{TIGHT_Q:.0%} of deficit) = {TIGHT_D:+.3f}")

print("\nCorr of storage variants with Price YoY (full overlap):")
for c in ["Storage YoY", "Storage tight cubic", "Storage tight quad",
          "Storage deficit kink", "Storage tight hinge"]:
    print(f"  {c:22s} {xy['Price YoY'].corr(xy[c]):+.3f}")

#%% 03 MODEL CONFIGURATION  (same idea as COUNTRY_CONFIGS)
MODEL_CONFIGS = {
    'Log Price YoY': {
        'exog_vars':    ['Storage YoY',
                         'UKgas YoY',
                         'Log Price_12m_ago'],
        'use_trend':    False,     # True = rolling poly trend first, then model residual
        'trend_window': 105,
        'var_window':   120,       # months (10y)
        'time_decay':   0.985,     # half-life ~45.9 months
        'start_date':   '2000-01-01',
        'recon':        'log',     # price level = Price(t-12) * exp(YoY forecast)
    },
}

#%% 04+05 FORECASTING - runs all configs
all_results = {}

for MODEL, cfg in MODEL_CONFIGS.items():
    print("\n" + "=" * 60)
    print(f"RUNNING MODEL FOR: {MODEL}")
    print("=" * 60)

    Y_COL = MODEL

    # --- TREND (optional) ---
    if cfg['use_trend']:
        trend_results, trend_stats = rolling_trend_forecast(
            df=xy, y_col=Y_COL, seasonal_cols=[],
            deg=1, window=cfg['trend_window'],
            start_date=cfg['start_date'], forecast_future=True)
        print("\nTREND MODEL STATISTICS")
        print(trend_stats.round(3))
        xy[f'{Y_COL}_detrended'] = trend_results['residual']
        xy[f'{Y_COL}_trend']     = trend_results['trend_fitted']
        Y_COL_TARGET = f'{Y_COL}_detrended'
    else:
        Y_COL_TARGET = Y_COL

    # --- VARIABLE ---
    var_results, var_stats, final_model = rolling_variable_forecast(
        df=xy, y_col=Y_COL_TARGET, x_cols=cfg['exog_vars'],
        window=cfg['var_window'], time_decay=cfg['time_decay'],
        start_date=cfg['start_date'], forecast_future=True,
        nonneg_cols=cfg.get('nonneg_cols'))
    print("\nVARIABLE MODEL STATISTICS")
    print(var_stats.round(3))
    print("\nFinal-model coefficients / t-stats (HAC = Newey-West, maxlags=12):")
    _hac = final_model.get_robustcov_results(cov_type='HAC', maxlags=12)
    print(pd.DataFrame({"coef": final_model.params.round(4),
                        "t_iid": final_model.tvalues.round(2),
                        "t_HAC": _hac.tvalues.round(2)}))

    # --- EXPANDING R² AT EVERY POINT ---
    rolling_r2 = pd.Series(index=var_results.index, dtype=float)
    valid = var_results[var_results['fitted'].notna() & var_results['actual'].notna()]
    for i in range(10, len(valid)):
        w = valid.iloc[:i]
        rolling_r2.loc[valid.index[i]] = np.corrcoef(w['actual'], w['fitted'])[0, 1] ** 2

    # --- COMBINE ---
    if cfg['use_trend']:
        xy[f'{Y_COL}_forecast'] = xy[f'{Y_COL}_trend'] + var_results['fitted']
    else:
        xy[f'{Y_COL}_forecast'] = var_results['fitted']

    last_actual = xy["Price"].last_valid_index()

    all_results[MODEL] = {'trend_results': trend_results if cfg['use_trend'] else None,
                          'var_results': var_results,
                          'var_stats': var_stats,
                          'rolling_r2': rolling_r2}

    # --- PLOT: performance over time (3 panels) ---
    plot_start = var_results['fitted'].first_valid_index()
    xy_plot = xy.loc[plot_start:]

    fig, axes = plt.subplots(3, 1, figsize=(14, 12))
    pd.DataFrame({'Actual': xy_plot[Y_COL],
                  'Forecast': xy_plot[f'{Y_COL}_forecast']}).plot(ax=axes[0], alpha=0.7)
    axes[0].axvline(x=last_actual, color='red', linestyle='--', alpha=0.5,
                    label='Last actual price')
    axes[0].axhline(y=0, color='black', linewidth=0.5)
    axes[0].set_title(f'{Y_COL} (pooled, all expiry months): Actual vs Forecast')
    axes[0].set_ylabel('Price YoY change')
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)
    (xy_plot[Y_COL] - xy_plot[f'{Y_COL}_forecast']).plot(ax=axes[1], color='red', alpha=0.7)
    axes[1].axhline(y=0, color='black', linewidth=0.5)
    axes[1].set_title(f'{Y_COL}: Forecast Errors')
    axes[1].set_ylabel('Error (YoY)')
    axes[1].grid(True, alpha=0.3)
    r2_plot = rolling_r2.dropna()
    axes[2].plot(r2_plot.index, r2_plot.values, color='steelblue', linewidth=1.5)
    axes[2].axhline(y=var_stats['R²'].iloc[0], color='black', linestyle='--',
                    linewidth=0.8, label=f'Overall R² ({var_stats["R²"].iloc[0]:.3f})')
    axes[2].axhline(y=0.8, color='red', linestyle=':', linewidth=0.8,
                    label='0.8 threshold')
    axes[2].set_title(f'{Y_COL}: Expanding R² (cumulative from first forecast)')
    axes[2].set_ylabel('R²')
    axes[2].set_xlim(r2_plot.index.min(), r2_plot.index.max())
    axes[2].set_ylim(max(0, r2_plot.min() - 0.05), min(1, r2_plot.max() + 0.05))
    axes[2].yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:.2f}'))
    axes[2].legend()
    axes[2].grid(True, alpha=0.3)
    plt.tight_layout()
    os.makedirs(PLOT_DIR, exist_ok=True)
    plt.savefig(os.path.join(PLOT_DIR, "04e_pooled_performance.png"), dpi=110)
    if SHOW_PLOTS:
        plt.show()
    plt.close()

    # --- PRINT FUTURE FORECASTS ---
    print(f"\nFUTURE FORECASTS - {MODEL}")
    future_mask = xy.index > last_actual
    print(xy.loc[future_mask, [f'{Y_COL}_forecast']].round(3))

#%% 05b RECONSTRUCT PRICE LEVEL FROM YOY FORECASTS
for MODEL, cfg in MODEL_CONFIGS.items():
    Y_COL = MODEL

    # implied price level from the YoY forecast
    fitted_yoy  = xy[f'{Y_COL}_forecast']
    price_base  = xy['Price'].shift(12)
    if cfg.get('recon') == 'log':
        xy[f'{Y_COL}_price_level'] = price_base * np.exp(fitted_yoy)
    else:
        xy[f'{Y_COL}_price_level'] = price_base * (1 + fitted_yoy)

    last_price   = xy['Price'].last_valid_index()
    hist_mask    = xy.index <= last_price
    future_mask  = xy.index > last_price
    actual_price = xy.loc[hist_mask, 'Price']
    hist_fitted  = xy.loc[hist_mask, f'{Y_COL}_price_level']
    future_forecast = xy.loc[future_mask, f'{Y_COL}_price_level'].dropna()

    plot_start   = hist_fitted.first_valid_index()
    hist_fitted  = hist_fitted.loc[plot_start:]
    actual_price = actual_price.loc[plot_start:]

    mask = hist_fitted.notna() & actual_price.notna()
    res  = actual_price[mask] - hist_fitted[mask]
    mae  = np.mean(np.abs(res))

    x_start = hist_fitted.first_valid_index()
    x_end   = future_forecast.index.max() if len(future_forecast) else last_price

    fig, axes = plt.subplots(2, 1, figsize=(14, 8))
    ax = axes[0]
    actual_price.plot(ax=ax, label='Actual Price', color='steelblue', linewidth=1.5)
    hist_fitted.plot(ax=ax, label='Fitted Price Level (rolling)', color='darkorange',
                     alpha=0.8, linewidth=1.2)
    future_forecast.plot(ax=ax, label='Forecast Price Level', color='green',
                         linestyle='--', linewidth=2)
    ax.fill_between(hist_fitted.index, hist_fitted - mae, hist_fitted + mae,
                    alpha=0.15, color='darkorange', label=f'±MAE (${mae:.2f}/MMBtu)')
    if len(future_forecast):
        ax.fill_between(future_forecast.index, future_forecast - mae,
                        future_forecast + mae, alpha=0.15, color='green')
    ax.axvline(x=last_price, color='red', linestyle='--', alpha=0.5, label='Last actual')
    ax.set_title('Natgas (pooled): Actual vs Fitted/Forecast Price Level')
    ax.set_ylabel('Price ($/MMBtu)')
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.set_xlim(x_start, x_end)

    ax = axes[1]
    (actual_price - hist_fitted).plot(ax=ax, color='red', alpha=0.7)
    ax.axhline(y=0, color='black', linewidth=0.8)
    ax.set_title('Price Level Forecast Error (Actual - Fitted)')
    ax.set_ylabel('Error ($/MMBtu)')
    ax.set_xlim(x_start, x_end)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, "04e_pooled_price_level.png"), dpi=110)
    if SHOW_PLOTS:
        plt.show()
    plt.close()

    print(f"\nFUTURE PRICE LEVEL FORECASTS - {MODEL}")
    print(future_forecast.round(2).to_frame('Forecast_Price_Level'))

    if mask.sum() > 0:
        rmse = np.sqrt(np.mean(res ** 2))
        r2   = np.corrcoef(actual_price[mask], hist_fitted[mask])[0, 1] ** 2
        mean_price = actual_price[mask].mean()
        print(f"\nPRICE LEVEL STATISTICS (historical fitted vs actual)")
        print(f"  RMSE:   {rmse:.2f} $/MMBtu  ({100 * rmse / mean_price:.1f}% of mean price)")
        print(f"  MAE:    {mae:.2f} $/MMBtu  ({100 * mae / mean_price:.1f}% of mean price)")
        print(f"  R²:     {r2:.3f}  (squared correlation)")
        print(f"  n_obs:  {mask.sum()}")

#%% 05c SENSITIVITY ANALYSIS  (rolling hyper-parameters)
# Refit over a grid of (var_window, time_decay) and score the reconstructed
# PRICE LEVEL out-of-sample. A robust choice sits on a broad plateau, not on
# a slope toward the edge of the grid. Stored for the 06 save.
SENS_WINDOWS = [48, 60, 72, 84, 96, 108, 120]
SENS_DECAYS  = [0.85, 0.88, 0.90, 0.92, 0.94, 0.96, 0.97, 0.98, 0.985, 0.99]

sens_rmse = pd.DataFrame(index=SENS_WINDOWS, columns=SENS_DECAYS, dtype=float)
sens_bias = sens_rmse.copy()
for _MODEL, _cfg in MODEL_CONFIGS.items():
    for _w in SENS_WINDOWS:
        for _dec in SENS_DECAYS:
            _res, _, _ = rolling_variable_forecast(
                xy, _MODEL, _cfg['exog_vars'], window=_w, time_decay=_dec,
                start_date=_cfg['start_date'], forecast_future=False)
            _base = xy['Price'].shift(12)
            _lvl = (_base * np.exp(_res['fitted']) if _cfg.get('recon') == 'log'
                    else _base * (1 + _res['fitted']))
            _m = xy['Price'].notna() & _lvl.notna()
            _a, _f = xy['Price'][_m], _lvl[_m]
            sens_rmse.loc[_w, _dec] = np.sqrt(np.mean((_a - _f) ** 2))
            sens_bias.loc[_w, _dec] = np.mean(_f - _a)
    print(f"\nSENSITIVITY - {_MODEL}: price-level RMSE  (rows=var_window, cols=time_decay)")
    print(sens_rmse.round(3).to_string())
    _best = sens_rmse.stack().idxmin()
    print(f"chosen (window={_cfg['var_window']}, decay={_cfg['time_decay']}): "
          f"RMSE={sens_rmse.loc[_cfg['var_window'], _cfg['time_decay']]:.3f}")
    print(f"grid min RMSE={sens_rmse.stack().min():.3f} at window={_best[0]}, decay={_best[1]}")

#%% 06 SAVE RESULTS
MODEL_KEY = list(MODEL_CONFIGS)[0]
out = xy[["Price", "Price YoY", "Log Price YoY", "Storage", "Storage YoY",
          "storage_is_estimate", "UKgas", "UKgas YoY", "ukgas_is_forward",
          f"{MODEL_KEY}_forecast", f"{MODEL_KEY}_price_level"]].copy()
with pd.ExcelWriter(OUT_XLSX) as xw:
    for MODEL in MODEL_CONFIGS:
        all_results[MODEL]['var_stats'].round(4).to_excel(xw, sheet_name="stats")
        all_results[MODEL]['rolling_r2'].rename("expanding_R2").to_frame() \
            .to_excel(xw, sheet_name="expanding_R2")
    out.round(5).to_excel(xw, sheet_name="results")
    if 'sens_rmse' in globals():
        sens_rmse.round(4).to_excel(xw, sheet_name="sensitivity_RMSE")
        sens_bias.round(4).to_excel(xw, sheet_name="sensitivity_bias")
print(f"\nSaved: {OUT_XLSX}")
print(f"Plots: {PLOT_DIR}\\04e_pooled_performance.png, 04e_pooled_price_level.png")

#%% 07 FFV VISUALIZATION
# Appends today's model forecast to a persistent FFV history file,
# then plots: 4 per-contract charts (futures price + historical FFV steps)
# and 1 bottom chart (price level forecast + NG forward curve)
#
# NOTE: with termination-aware pricing the target month T is the FORMATION
# month — the contract delivering in T+1. So forecast month T -> contract T+1.

import os
from datetime import date

# ── CONFIG ───────────────────────────────────────────────────────────────────
FFV_HISTORY_PATH  = os.path.join(SND_DIR, "FFV_history_natgas_ukgas.csv")
TODAY             = pd.Timestamp(date.today())
OFFSET_MONTHS     = 1    # forecast month T -> contract delivering in T+1
N_CHART_CONTRACTS = 4    # next N monthly contracts get their own chart

# ── ROBUST CSV HELPERS (single source of truth) ──────
_MONTH_MAP = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
}

def _label_to_contract_date(label):
    """'Apr/26' or 'APR/26' -> Timestamp('2026-04-01'). Always authoritative."""
    parts = label.strip().split('/')
    return pd.Timestamp(year=2000 + int(parts[1]),
                        month=_MONTH_MAP[parts[0].lower()], day=1)

def _fix_run_date(val):
    """Heal run_date if Excel swapped DD/MM to MM/DD (result would be future)."""
    if pd.isna(val):
        return pd.NaT
    d = pd.to_datetime(val, errors='coerce')
    if pd.isna(d):
        return pd.NaT
    if d > TODAY:
        try:
            return pd.Timestamp(year=d.year, month=d.day, day=d.month)
        except Exception:
            return pd.NaT
    return d

def _load_history(path):
    df = pd.read_csv(path)
    df['run_date']      = df['run_date'].apply(_fix_run_date)
    df['contract_date'] = df['contract_label'].apply(_label_to_contract_date)
    bad = df['run_date'].dropna()
    bad = bad[bad > TODAY]
    if not bad.empty:
        print(f"WARNING: {len(bad)} run_date(s) still in the future after healing.")
    return df

def _save_history(df, path):
    df = df.copy()
    df['contract_date'] = df['contract_label'].apply(_label_to_contract_date)
    df['run_date']      = pd.to_datetime(df['run_date']).dt.strftime('%Y-%m-%d')
    df['contract_date'] = df['contract_date'].dt.strftime('%Y-%m-%d')
    df.to_csv(path, index=False)

# ── CONTRACTS: next 12 monthly for history, next 4 for charts ─────────────────
def get_next_monthly_contracts(ref_date, n=12):
    contracts, m, y = [], ref_date.month, ref_date.year
    while len(contracts) < n:
        m += 1
        if m > 12:
            m, y = 1, y + 1
        contracts.append(pd.Timestamp(year=y, month=m, day=1))
    return contracts

ALL_CONTRACTS    = {c: c.strftime('%b/%y').upper()
                    for c in get_next_monthly_contracts(TODAY, n=12)}
ACTIVE_CONTRACTS = {c: c.strftime('%b/%y').upper()
                    for c in get_next_monthly_contracts(TODAY, n=N_CHART_CONTRACTS)}

print("Active contracts (charts):", ", ".join(ACTIVE_CONTRACTS.values()))

# ── EXTRACT TODAY'S FFV FORECASTS ─────────────────────────────────────────────
Y_COL = list(MODEL_CONFIGS)[0]
LVL   = f'{Y_COL}_price_level'

hist_fitted_07  = xy[LVL].loc[xy.index <= last_price]
actual_price_07 = xy['Price'].loc[xy.index <= last_price]
mask_07 = hist_fitted_07.notna() & actual_price_07.notna()
mae_07  = float(np.mean(np.abs(actual_price_07[mask_07] - hist_fitted_07[mask_07])))
print(f"\nMAE used for FFV bands: ${mae_07:.2f}/MMBtu")

today_ffv_rows = []
for contract_date, label in ALL_CONTRACTS.items():
    forecast_month = contract_date - pd.DateOffset(months=OFFSET_MONTHS)
    forecast_month = pd.Timestamp(forecast_month.year, forecast_month.month, 1)
    if forecast_month in xy.index:
        ffv_mid = xy.loc[forecast_month, LVL]
    else:
        candidates = xy.index[xy.index >= forecast_month]
        ffv_mid = xy.loc[candidates[0], LVL] if len(candidates) else np.nan
    if pd.notna(ffv_mid):
        today_ffv_rows.append({
            'run_date': TODAY, 'contract_date': contract_date,
            'contract_label': label,
            'ffv_mid':   round(float(ffv_mid), 2),
            'ffv_lower': round(float(ffv_mid) - mae_07, 2),
            'ffv_upper': round(float(ffv_mid) + mae_07, 2)})
        print(f"  {label}: FFV = {ffv_mid:.2f}  "
              f"[{ffv_mid - mae_07:.2f} - {ffv_mid + mae_07:.2f}] $/MMBtu")

today_ffv_df = pd.DataFrame(today_ffv_rows)

# ── APPEND TO / CREATE HISTORY FILE ───────────────────────────────────────────
if os.path.exists(FFV_HISTORY_PATH):
    import shutil
    shutil.copy2(FFV_HISTORY_PATH, FFV_HISTORY_PATH.replace('.csv', '_backup.csv'))
    ffv_history = _load_history(FFV_HISTORY_PATH)
    n_before = len(ffv_history)
    ffv_history = ffv_history[ffv_history['run_date'].dt.date != TODAY.date()]
    print(f"Dropped {n_before - len(ffv_history)} existing rows for today, "
          f"keeping {len(ffv_history)} historical rows.")
    ffv_history = pd.concat([ffv_history, today_ffv_df], ignore_index=True)
else:
    ffv_history = today_ffv_df.copy()
    print("No existing history file - creating new one.")
_save_history(ffv_history, FFV_HISTORY_PATH)
print(f"FFV history saved - {len(ffv_history)} rows total.")

# ── NG CONTRACT PRICES from the barchart master (already loaded as px) ────────
prices_raw = px.rename(columns={'date': 'future_date'})[
    ['future_date', 'settle', 'delivery_year', 'delivery_month']].copy()
prices_raw['contract_date'] = pd.to_datetime(
    dict(year=prices_raw['delivery_year'], month=prices_raw['delivery_month'], day=1))

# ── HELPER: stepped FFV bands ────────────────────────
def build_ffv_steps(history_df, contract_date, prices_df):
    runs = (history_df[history_df['contract_date'] == contract_date]
            .sort_values('run_date').reset_index(drop=True))
    if runs.empty:
        return []
    segments = []
    for i, row in runs.iterrows():
        if i == 0:
            cp = prices_df[prices_df['contract_date'] == contract_date]
            seg_start = cp['future_date'].min() if not cp.empty else row['run_date']
        else:
            seg_start = row['run_date']
        seg_end = runs.loc[i + 1, 'run_date'] if i < len(runs) - 1 \
                  else row['run_date'] + pd.DateOffset(months=2)
        segments.append({'start': seg_start, 'end': seg_end, 'mid': row['ffv_mid'],
                         'lower': row['ffv_lower'], 'upper': row['ffv_upper']})
    return segments

# ── COLOUR SCHEME ────────────────────────────────────
C_ACTUAL = '#C0392B'; C_FFV = '#2C3E50'; C_BAND = '#7F8C8D'
C_FWD    = '#2980B9'; C_FIT = '#555555'

# ── FIGURE LAYOUT: 4 small contract charts + 1 wide bottom chart ──────────────
fig = plt.figure(figsize=(16, 13))
fig.text(0.01, 0.99, f"Run: {pd.Timestamp.now().strftime('%d %b %Y  %H:%M')}",
         fontsize=7, color='#888888', va='top', ha='left', transform=fig.transFigure)
gs = fig.add_gridspec(3, 2, height_ratios=[1.1, 1.1, 1.3], hspace=0.5, wspace=0.3)
axes_small = [fig.add_subplot(gs[r, c]) for r in (0, 1) for c in (0, 1)]
ax_bottom  = fig.add_subplot(gs[2, :])

# ── SMALL CHARTS ──────────────────────────────────────────────────────────────
for ax, (contract_date, label) in zip(axes_small, ACTIVE_CONTRACTS.items()):
    segments = build_ffv_steps(ffv_history, contract_date, prices_raw)
    if segments:
        all_dates, all_mids, all_uppers, all_lowers = [], [], [], []
        for seg in segments:
            dates = pd.date_range(seg['start'], seg['end'], freq='D')
            all_dates.append(dates)
            all_mids.append([seg['mid']] * len(dates))
            all_uppers.append([seg['upper']] * len(dates))
            all_lowers.append([seg['lower']] * len(dates))
        all_dates  = np.concatenate([d.values for d in all_dates])
        all_mids   = np.concatenate(all_mids)
        all_uppers = np.concatenate(all_uppers)
        all_lowers = np.concatenate(all_lowers)
        ax.fill_between(all_dates, all_lowers, all_uppers, alpha=0.25,
                        color=C_BAND, zorder=1)
        ax.plot(all_dates, all_uppers, color=C_BAND, linestyle=':', linewidth=1.6,
                zorder=2, label=f"FFV ±MAE (${mae_07:.2f})")
        ax.plot(all_dates, all_lowers, color=C_BAND, linestyle=':', linewidth=1.6,
                zorder=2, label='_nolegend_')
        ffv_mid_val = today_ffv_df.loc[today_ffv_df['contract_label'] == label,
                                       'ffv_mid']
        ffv_mid_val = float(ffv_mid_val.iloc[0]) if not ffv_mid_val.empty \
                        else float('nan')
        ax.plot(all_dates, all_mids, color=C_FFV, linewidth=0.6, zorder=3,
                alpha=0.6, label=f'FFV mid = {ffv_mid_val:.2f} $')

    cp = prices_raw[prices_raw['contract_date'] == contract_date] \
             .set_index('future_date').sort_index()
    if not cp.empty:
        last_px_c = float(cp['settle'].iloc[-1])
        ax.plot(cp.index, cp['settle'], color=C_ACTUAL, linewidth=1.6, zorder=5,
                label=f'NG {label} = {last_px_c:.2f} $')
        ax.set_xlim(cp.index.min(), max(cp.index.max(), TODAY) + pd.DateOffset(weeks=2))

    ax.set_title(f'Natgas {label}', fontsize=10, fontweight='bold')
    ax.set_ylabel('$/MMBtu', fontsize=8)
    ax.tick_params(axis='x', labelsize=7, rotation=30)
    ax.tick_params(axis='y', labelsize=8)
    ax.grid(True, alpha=0.2)
    ax.legend(fontsize=7, loc='lower left', framealpha=0.7)

# ── BOTTOM CHART: FFV vs actual expiry prices + NG forward curve ─────────────
ax = ax_bottom
hist_fit = xy[LVL].loc[xy.index <= last_price]
fut_fc   = xy[LVL].loc[xy.index > last_price].dropna()
plot_start = hist_fit.first_valid_index()
hist_fit   = hist_fit.loc[plot_start:]

contemp_plot = xy['Price'].loc[plot_start:]
contemp_plot = contemp_plot[contemp_plot.index >= pd.Timestamp('2023-01-01')]
hist_fit_s   = hist_fit[hist_fit.index >= pd.Timestamp('2023-01-01')]

# bridge hist -> forecast
if not fut_fc.empty and hist_fit_s.index.max() not in fut_fc.index:
    fut_fc = pd.concat([hist_fit_s.iloc[-1:], fut_fc]).sort_index()

# NG forward curve = latest settle per contract, shifted to formation-month
# space (delivery month - 1) so it lines up with the price/forecast index
forward_curve = (prices_raw.sort_values('future_date')
                 .groupby('contract_date', as_index=False).last()
                 .rename(columns={'contract_date': 'delivery', 'settle': 'fwd'}))
forward_curve['formation'] = forward_curve['delivery'] - pd.offsets.MonthBegin(1)
last_confirmed = contemp_plot.dropna().index.max() \
                 if not contemp_plot.dropna().empty else TODAY
forward_curve = forward_curve[forward_curve['formation'] > last_confirmed] \
                .sort_values('formation')

ax.plot(contemp_plot.index, contemp_plot.values, color=C_ACTUAL, linewidth=1.8,
        label='NG expiry price (avg pricing window)', zorder=5)
if not forward_curve.empty:
    last_val = float(contemp_plot.dropna().iloc[-1]) \
               if not contemp_plot.dropna().empty else np.nan
    bridge = pd.DataFrame({'formation': [last_confirmed], 'fwd': [last_val]})
    forward_curve = pd.concat([bridge, forward_curve], ignore_index=True) \
                      .sort_values('formation')
    ax.plot(forward_curve['formation'], forward_curve['fwd'], color=C_ACTUAL,
            linewidth=1.6, linestyle=':', marker='o', markersize=3,
            label='NG forward curve (latest settle)', zorder=4)

ax.plot(hist_fit_s.index, hist_fit_s.values, color=C_FIT, linewidth=1.2,
        alpha=0.9, label='FFV fitted')
ax.fill_between(hist_fit_s.index, hist_fit_s - mae_07, hist_fit_s + mae_07,
                alpha=0.15, color=C_BAND, label=f'±MAE (${mae_07:.2f})')
if not fut_fc.empty:
    ax.plot(fut_fc.index, fut_fc.values, color=C_FIT, linewidth=1.6,
            linestyle='--', label='FFV forecast')
    ax.fill_between(fut_fc.index, fut_fc - mae_07, fut_fc + mae_07,
                    alpha=0.15, color=C_BAND)

x_end = (fut_fc.index.max() if not fut_fc.empty else TODAY) + pd.DateOffset(months=1)
ax.set_xlim(pd.Timestamp('2023-01-01'), x_end)
ax.set_title('FFV Natgas ($/MMBtu) — FFV vs Forward curve',
             fontsize=11, fontweight='bold')
ax.set_ylabel('$/MMBtu', fontsize=9)
ax.tick_params(axis='x', labelsize=8, rotation=20)
ax.grid(True, alpha=0.2)
ax.legend(fontsize=8, loc='upper left', framealpha=0.8)

plt.tight_layout(rect=[0, 0, 1, 0.97])
out_png = os.path.join(PLOT_DIR, "04e_pooled_FFV.png")
plt.savefig(out_png, dpi=150)
if SHOW_PLOTS:
    plt.show()
plt.close()
print(f"Saved: {out_png}")

# %%

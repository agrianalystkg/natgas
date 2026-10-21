# -*- coding: utf-8 -*-
"""
09e_gas_ffv_simulation_ukgas.py
-------------------------------
Historical, point-in-time FFV simulation for natgas — UK-GAS variant of 09.

Walks FORWARD DAY BY DAY (2000 -> today). At each day `d` it builds the natgas
FFV using ONLY information knowable at `d`:

    price                 : real-time (<= d), price-12m always known
    monthly fundamentals  : through month M-2, released on the LAST BUSINESS
                            DAY of month M   (your 30-Sep -> July rule)
    weekly stocks         : EIA weekly survey, through the last Thursday <= d
    weather               : enters via the storage nowcast (weekly splice)
    UK gas                : history for completed months; for the forward
                            formation months the UK GAS FORWARD CURVE as of d

FFV model = 04e_gas_yoy_price_model_ukgas.py:
    target   Log Price YoY = log P_F - log P_{F-12}      (formation month F)
    exog     Storage YoY, UKgas YoY, Log Price_12m_ago
    fit      rolling WLS, window 120, decay 0.985
    recon    P_F_hat = P_{F-12} * exp(YoY_hat)

Outputs:
    narrow  FFV_history_natgas_ukgas_sim.csv     run_date, contract_date, label,
                                                 ffv_mid, ffv_lower, ffv_upper
    wide    natgas_fundamental_history_ukgas.xlsx sheets 'long' and 'wide'
            (storage path T..T-23 + 12m ratio)

Inputs: <barchart>/nymex_natgas_master.csv
        <barchart>/uk_natural_gas_master.csv
        data/gas_snd_monthly.xlsx
        data/eia_weekly_inventory.xlsx
"""
import os
import numpy as np
import pandas as pd
import statsmodels.api as sm
from warnings import simplefilter
simplefilter("ignore")

SND_DIR    = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
PRICES_CSV = os.environ.get("NG_PRICES_CSV", os.path.join(SND_DIR, "raw", "nymex_natgas_master.csv"))
UKGAS_CSV  = os.environ.get("UKGAS_CSV",  os.path.join(SND_DIR, "raw", "uk_natural_gas_master.csv"))
FUND_XLSX  = os.path.join(SND_DIR, "gas_snd_monthly.xlsx")
WKLY       = os.path.join(SND_DIR, "eia_weekly_inventory.xlsx")
OUT_NARROW = os.path.join(SND_DIR, "FFV_history_natgas_ukgas_sim.csv")
OUT_WIDE   = os.path.join(SND_DIR, "natgas_fundamental_history_ukgas.xlsx")

N_LAST_DAYS = 25
MIN_OBS     = 8
TERMINATION_FROM_END = 3
BUFFER_DAYS = 5
STOR_COL    = "stor_total_us"
WINDOW      = 120       # rolling fit window (months)
DECAY       = 0.985
TIGHT_Q     = 0.80
RELEASE_LAG = 2         # month m released on last business day of month m+RELEASE_LAG
N_CONTRACTS = 12
SIM_START   = pd.Timestamp("2000-01-01")
WINDOW_MONTHS = 24      # wide snapshot depth (T .. T-23)
OFFSET_MONTHS = 1       # formation month F  -> contract delivering F+1
TEST_N_DATES = None      # None = full daily sim

from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay
US_BD = CustomBusinessDay(calendar=USFederalHolidayCalendar())

# ================================================================ 1. PRICES (formation-indexed, from 04)
px = pd.read_csv(PRICES_CSV, parse_dates=["date"])
px = px[px["settle"] > 0]
LAST_DATA_DATE = px["date"].max()

def pricing_window_end(deliv):
    k = TERMINATION_FROM_END + BUFFER_DAYS
    bd = pd.date_range(deliv - pd.offsets.MonthBegin(1), deliv - pd.Timedelta(days=1), freq=US_BD)
    return bd[-k] if len(bd) >= k else None

def expiry_price(g):
    g = g.sort_values("date")
    deliv = pd.Timestamp(int(g["delivery_year"].iloc[0]), int(g["delivery_month"].iloc[0]), 1)
    w_end = pricing_window_end(deliv)
    if w_end is None or w_end > LAST_DATA_DATE:
        return None
    traded = g[(g["date"] <= w_end) & g["has_traded"]]
    use = traded if len(traded) >= MIN_OBS else g[g["date"] <= w_end]
    if len(use) < MIN_OBS:
        return None
    return pd.Series({"deliv": deliv, "expiry_px": use.tail(N_LAST_DAYS)["settle"].mean()})

expiry = (px.groupby("contract", group_keys=False)
            .apply(expiry_price, include_groups=False)
            .dropna(subset=["expiry_px"]).reset_index())
expiry["formation"] = expiry["deliv"] - pd.offsets.MonthBegin(1)
price_m = expiry.set_index("formation")["expiry_px"].sort_index()
# window-complete date per formation month (approx: end of formation month)
print(f"prices: {len(expiry)} contracts, formation {price_m.index.min():%Y-%m} -> {price_m.index.max():%Y-%m}")

# ================================================================ 1b. UK GAS (history + daily forward curve)
uk = pd.read_csv(UKGAS_CSV, parse_dates=["date"],
                 usecols=["delivery_year", "delivery_month", "date", "settle"])
uk = uk[uk["settle"] > 0].copy()
uk["delivery"] = pd.to_datetime(dict(year=uk.delivery_year,
                                     month=uk.delivery_month, day=1))
# monthly front-month history
uk["rank"] = (uk["delivery"] - uk["date"]).dt.days
_front = uk.loc[uk[uk["rank"] >= 0].groupby("date")["rank"].idxmin()]
uk_hist = _front.set_index("date")["settle"].resample("MS").mean()
uk_hist.index = pd.to_datetime(uk_hist.index).to_period("M").to_timestamp()
# daily forward-curve matrix: latest settle per delivery month, ffilled along dates
_uf = uk[(uk["date"] >= pd.Timestamp("1997-01-01")) &
         (uk["delivery"] <= pd.Timestamp("2033-12-01"))]
uk_pivot = (_uf.pivot_table(index="date", columns="delivery", values="settle", aggfunc="last")
              .sort_index().ffill())
print(f"UK gas history: {uk_hist.index.min():%Y-%m} -> {uk_hist.index.max():%Y-%m}; "
      f"curve matrix: {uk_pivot.index.min().date()} -> {uk_pivot.index.max().date()} "
      f"x {uk_pivot.shape[1]} deliveries")
del uk, _front, _uf

# ================================================================ 2. FUNDAMENTALS + WEEKLY
fund = pd.read_excel(FUND_XLSX, sheet_name="data", index_col=0, parse_dates=True)
fund.index = pd.to_datetime(fund.index).to_period("M").to_timestamp()
fund_stor = fund[STOR_COL].dropna()

wk_raw = pd.read_excel(WKLY, header=None)
_h = next(i for i in range(6) if any("week" in str(x).lower() for x in wk_raw.iloc[i].tolist()))
wk = pd.read_excel(WKLY, skiprows=_h); wk.columns = [str(c) for c in wk.columns]
_wd = [c for c in wk.columns if "week" in c.lower()][0]
_wv = [c for c in wk.columns if "lower" in c.lower()][0]
wk["date"] = pd.to_datetime(wk[_wd], errors="coerce")
wk["lvl"] = pd.to_numeric(wk[_wv].astype(str).str.replace(",", ""), errors="coerce")
wk = wk.dropna(subset=["date", "lvl"]).sort_values("date").set_index("date")["lvl"]   # Bcf
wk_end = wk.resample("ME").last(); wk_end.index = wk_end.index.to_period("M").to_timestamp()
print(f"fund: {fund_stor.index.min():%Y-%m} -> {fund_stor.index.max():%Y-%m} | weekly -> {wk.index.max().date()}")

# ---- daily weather anomaly + weekly model (for the DAILY weather-driven storage nowcast) ----
RHO = 0.84                  # daily persistence decay of the temperature/HDD anomaly
MAXPERSIST = 14             # cap: beyond 2 weeks -> normal (your rule)
_gw = pd.read_parquet(os.path.join(SND_DIR, "us_daily_anomaly.parquet"))[["date", "gwdd_anom"]]
_gw["date"] = pd.to_datetime(_gw["date"]); _gw = _gw.set_index("date")["gwdd_anom"].sort_index()

wk_chg = wk.diff()
wk_anom = _gw.resample("W-FRI").sum().reindex(wk.index)   # Sat..Fri, labelled by Friday
wkpan = pd.DataFrame({"dchg": wk_chg, "anom": wk_anom})
_ph = 2 * np.pi * (wkpan.index.dayofyear - 1) / 365.25
for _k in range(1, 7):
    wkpan[f"sn{_k}"] = np.sin(_k * _ph); wkpan[f"cs{_k}"] = np.cos(_k * _ph)
WS = [c for c in wkpan.columns if c.startswith(("sn", "cs"))]
wkpan["dchg_lag1"] = wkpan["dchg"].shift(1)

_WK_FIT = {}
def weekly_fit(vintage_m):
    if vintage_m in _WK_FIT:
        return _WK_FIT[vintage_m]
    sub = wkpan.dropna(subset=["dchg", "anom", "dchg_lag1"] + WS).sort_index()
    sub = sub.loc[sub.index <= vintage_m + pd.offsets.MonthEnd(0)].iloc[-260:]
    if len(sub) < 60:
        _WK_FIT[vintage_m] = None
        return None
    X = sm.add_constant(sub[["anom", "dchg_lag1"] + WS], has_constant="add")
    fit = sm.WLS(sub["dchg"], X, weights=0.995 ** np.arange(len(sub))[::-1]).fit()
    p = fit.params
    _WK_FIT[vintage_m] = np.array([p.get("const", 0.0), p.get("anom", 0.0), p.get("dchg_lag1", 0.0)]
                                  + [p.get(f"sn{k}", 0.0) for k in range(1, 7)]
                                  + [p.get(f"cs{k}", 0.0) for k in range(1, 7)])
    return _WK_FIT[vintage_m]

def weekly_pred(vintage_m, friday, anom_val, prev_dchg):
    coef = weekly_fit(vintage_m)
    if coef is None:
        return 0.0
    ph = 2 * np.pi * (friday.dayofyear - 1) / 365.25
    sn = np.array([np.sin(k * ph) for k in range(1, 7)])
    cs = np.array([np.cos(k * ph) for k in range(1, 7)])
    return float(coef[0] + coef[1] * anom_val + coef[2] * prev_dchg
                 + float(np.dot(coef[3:9], sn)) + float(np.dot(coef[9:15], cs)))

_GWI = _gw.index
def _val_on(day):
    j = _GWI.searchsorted(day, side="right") - 1
    return float(_gw.iloc[j]) if (j >= 0 and _GWI[j] == day) else np.nan

def expected_weekly_anom(friday, d):
    """Weekly HDD anomaly for the week ending `friday`, as known at `d`: realized days + persistence."""
    jd = _GWI.searchsorted(d, side="right") - 1
    a_d = float(_gw.iloc[jd]) if jd >= 0 else 0.0
    tot = 0.0
    for off in range(7):
        day = friday - pd.Timedelta(days=6 - off)
        if day <= d:
            v = _val_on(day); tot += 0.0 if not np.isfinite(v) else v
        else:
            k = (day - d).days
            if k <= MAXPERSIST:
                tot += (RHO ** k) * a_d
    return tot

def _last_friday(month_start):
    end = month_start + pd.offsets.MonthEnd(0)
    while end.weekday() != 4:            # 4 = Friday
        end = end - pd.Timedelta(days=1)
    return end

# seasonal climatology of monthly storage (for the far-horizon continuity)
clim = fund_stor.groupby(fund_stor.index.month).mean()

# ================================================================ 2b. MONTHLY WEATHER EXOG + COMPONENTS (for the balance forward)
WX_M = os.path.join(SND_DIR, "wx_monthly_exog.parquet")
COMPONENTS = ["prod_dry", "imp_total", "exp_total", "cons_total"]
wx = pd.read_parquet(WX_M); wx.index = pd.to_datetime(wx.index)
comp_df = fund[COMPONENTS + [STOR_COL]].join(wx[["gwdd_anom", "gwcdd_anom"]], how="left")
SEAS = []
for _m in range(1, 12):
    _c = f"m{_m:02d}"; comp_df[_c] = (comp_df.index.month == _m).astype(float); SEAS.append(_c)

def _component_forward(vintage_m):
    """Weather-driven balance forward of storage from vintage month (final-model, normal weather ahead)."""
    proj = {}
    for y in COMPONENTS:
        sub = comp_df[[y] + SEAS + ["gwdd_anom", "gwcdd_anom"]].dropna()
        sub = sub.loc[sub.index <= vintage_m]
        if len(sub) < 24:
            return None
        tr = sub.iloc[-60:].copy()
        t = np.arange(len(tr), dtype=float); tc = t - t.mean(); tr["t1"] = tc
        X = sm.add_constant(tr[["t1"] + SEAS + ["gwdd_anom", "gwcdd_anom"]], has_constant="add")
        mt = sm.WLS(tr[y], X, weights=0.98 ** np.arange(len(tr))[::-1]).fit()
        resid = tr[y] - mt.predict(X)
        r = resid.iloc[-48:]
        ms = sm.OLS(r.values[1:], sm.add_constant(r.values[:-1], has_constant="add")).fit()
        cur = resid.iloc[-1]
        vals = {}
        for h in range(1, 17):
            F = vintage_m + pd.offsets.MonthBegin(h)
            row = {"const": 1.0, "t1": tc[-1] + h}
            for c in SEAS:
                row[c] = 1.0 if F.month == int(c[1:]) else 0.0
            row["gwdd_anom"] = 0.0; row["gwcdd_anom"] = 0.0
            Xf = pd.DataFrame([row]).reindex(columns=mt.model.exog_names).fillna(0.0)
            trend_val = mt.predict(Xf).iloc[0]
            cur = float(ms.params[0]) + float(ms.params[1]) * cur
            vals[F] = trend_val + cur
        proj[y] = pd.Series(vals)
    delta = proj["prod_dry"] + proj["imp_total"] - proj["exp_total"] - proj["cons_total"]
    anchor = fund_stor.loc[:vintage_m].iloc[-1]
    return anchor + delta.cumsum()

_BAL_FWD = {}
def balance_forward(vintage_m):
    if vintage_m not in _BAL_FWD:
        _BAL_FWD[vintage_m] = _component_forward(vintage_m)
    return _BAL_FWD[vintage_m]


def last_release_date(ref_month):
    """month m data released on the last business day of month m+LAG."""
    m2 = ref_month + pd.offsets.MonthBegin(RELEASE_LAG)
    end = (m2 + pd.offsets.MonthEnd(0))
    bd = pd.date_range(m2, end, freq=US_BD)
    return bd[-1]

def last_released_month(d):
    """Latest reference month with release_date <= d."""
    cand = pd.Timestamp(d).to_period("M").to_timestamp()
    for _ in range(RELEASE_LAG + 2):
        if last_release_date(cand) <= pd.Timestamp(d):
            return cand
        cand = cand - pd.offsets.MonthBegin(1)
    return None

def storage_asof(d):
    """Best-available monthly storage (MMcf) knowable at d — WEATHER-DRIVEN, refreshed daily.

    Forward chain priority per month-end Friday: weekly weather nowcast -> realized
    survey -> monthly balance forward -> level-anchored seasonal climatology.
    """
    m = last_released_month(d)
    s = fund_stor.loc[:m].copy()
    if m is None or len(s) == 0:
        return s
    anchor = s.iloc[-1]
    m_end_fri = _last_friday(m)
    j_anchor = wk.index.searchsorted(m_end_fri, side="right") - 1
    w_anchor = float(wk.iloc[j_anchor]) if j_anchor >= 0 else None

    # weekly weather nowcast levels (only if the weekly model is fittable at this vintage)
    lvls = {}
    if weekly_fit(m) is not None and w_anchor is not None:
        j = wk.index.searchsorted(d - pd.Timedelta(days=6), side="right") - 1
        if j >= 0:
            f_last = wk.index[j]
            lvl = float(wk.loc[f_last]); prev = float(wk_chg.get(f_last, 0.0) or 0.0)
            lvls[f_last] = lvl; f = f_last
            for _ in range(30):
                f = f + pd.Timedelta(weeks=1)
                delta = weekly_pred(m, f, expected_weekly_anom(f, d), prev if np.isfinite(prev) else 0.0)
                lvl = lvl + delta; lvls[f] = lvl; prev = delta
    f_avail = wk.index[wk.index.searchsorted(d - pd.Timedelta(days=6), side="right") - 1] \
        if wk.index.searchsorted(d - pd.Timedelta(days=6), side="right") > 0 else None
    bf = balance_forward(m)

    for k in range(1, N_CONTRACTS + 5):
        M_ = m + pd.offsets.MonthBegin(k)
        ef = _last_friday(M_)
        if w_anchor is not None and ef in lvls:                       # 1. weekly weather nowcast
            s.loc[M_] = anchor + (lvls[ef] - w_anchor) * 1000.0
        elif w_anchor is not None and f_avail is not None and ef <= f_avail and ef in wk.index:
            s.loc[M_] = anchor + (wk.loc[ef] - w_anchor) * 1000.0     # 2. realized survey
        elif bf is not None and M_ in bf.index:                       # 3. balance forward
            pm = M_ - pd.offsets.MonthBegin(1)
            base, bp = s.get(pm, np.nan), bf.get(pm, np.nan)
            if np.isfinite(base) and np.isfinite(bp):
                s.loc[M_] = base + (bf.loc[M_] - bp)
        else:                                                          # 4. climatology fallback
            pm = M_ - pd.offsets.MonthBegin(1)
            base = s.get(pm, np.nan)
            if np.isfinite(base):
                s.loc[M_] = base + (clim.get(M_.month, 0.0) - clim.get(pm.month, 0.0))
    return s.sort_index()

# ================================================================ 4. TRAINING PANEL + PER-VINTAGE FIT
def build_features(stor_series, m):
    """Monthly panel with target & exog, restricted to realized months (<= m)."""
    idx = price_m.index[(price_m.index >= price_m.index.min() + pd.DateOffset(months=12))]
    xy = pd.DataFrame(index=idx)
    xy["Price"] = price_m
    xy["Price_12m_ago"] = price_m.shift(12)
    xy["Storage"] = stor_series.reindex(xy.index)
    xy["Storage_12m_ago"] = xy["Storage"].shift(12)
    xy["Log YoY"] = np.log(xy["Price"]) - np.log(xy["Price_12m_ago"])
    xy["Log Price_12m_ago"] = np.log(xy["Price_12m_ago"])
    xy["Storage YoY"] = np.log(xy["Storage"] / xy["Storage_12m_ago"])
    xy["UKgas"] = uk_hist.reindex(xy.index)
    xy["UKgas YoY"] = np.log(xy["UKgas"] / xy["UKgas"].shift(12))
    return xy

def fit_vintage(xy, m):
    """WLS: Log YoY ~ Storage YoY + UKgas YoY + Log Price_12m_ago, months <= m."""
    cols = ["Log YoY", "Storage YoY", "UKgas YoY", "Log Price_12m_ago"]
    rows = xy.loc[xy.index <= m, cols].dropna()
    if len(rows) < WINDOW:
        rows = xy.loc[:, cols].dropna()
    if len(rows) < 24:
        return None
    rows = rows.iloc[-WINDOW:]
    w = DECAY ** np.arange(len(rows))[::-1]
    X = sm.add_constant(rows[["Storage YoY", "UKgas YoY", "Log Price_12m_ago"]], has_constant="add")
    return sm.WLS(rows["Log YoY"], X, weights=w).fit()

# ================================================================ 5. DAILY SIMULATION
def next_contract_formation_months(d, n=N_CONTRACTS):
    # mirror 04: next 12 delivery months = current+1..current+12 -> formations current..current+11
    f0 = pd.Timestamp(d).to_period("M").to_timestamp()
    return [f0 + pd.offsets.MonthBegin(k) for k in range(n)]

def label(delivery):
    return delivery.strftime("%b/%y").upper()

sim_dates = pd.date_range(SIM_START, LAST_DATA_DATE, freq="D")
if TEST_N_DATES:
    sim_dates = sim_dates[:: max(1, len(sim_dates) // TEST_N_DATES)]
print(f"simulating {len(sim_dates)} days: {sim_dates.min().date()} -> {sim_dates.max().date()}")

# daily UK gas forward curve aligned to the sim dates
uk_curve = uk_pivot.reindex(sim_dates, method="ffill")

narrow, wide, long = [], [], []

for d in sim_dates:
    m = last_released_month(d)
    if m is None or m < pd.Timestamp("1995-01-01"):
        continue
    stor = storage_asof(d)
    curve = uk_curve.loc[d] if d in uk_curve.index else None
    xy = build_features(stor, m)
    fit = fit_vintage(xy, m)
    if fit is None:
        continue

    # ---- forward FFV for the next N contracts (formation months) ----
    coef = fit.params
    def pred(stor_yoy, ukgas_yoy, logp):
        return (coef.get("const", 0.0)
                + coef.get("Storage YoY", 0.0) * stor_yoy
                + coef.get("UKgas YoY", 0.0) * ukgas_yoy
                + coef.get("Log Price_12m_ago", 0.0) * logp)

    hist_mae = np.nan
    _hc = ["Log YoY", "Storage YoY", "UKgas YoY", "Log Price_12m_ago"]
    hist = xy.loc[xy.index <= m].dropna(subset=_hc)
    if len(hist):
        preds = (coef.get("const", 0.0)
                 + coef.get("Storage YoY", 0.0) * hist["Storage YoY"]
                 + coef.get("UKgas YoY", 0.0) * hist["UKgas YoY"]
                 + coef.get("Log Price_12m_ago", 0.0) * hist["Log Price_12m_ago"])
        lvl_pred = hist["Price_12m_ago"] * np.exp(preds)
        hist_mae = float(np.nanmean(np.abs(hist["Price"] - lvl_pred)))

    for F in next_contract_formation_months(d, N_CONTRACTS):
        if F not in stor.index or (F - pd.DateOffset(months=12)) not in xy.index:
            continue
        base = price_m.get(F - pd.DateOffset(months=12), np.nan)
        s_F = stor.get(F, np.nan); s_F12 = stor.get(F - pd.DateOffset(months=12), np.nan)
        if not np.isfinite([base, s_F, s_F12]).all() or base <= 0 or s_F <= 0 or s_F12 <= 0:
            continue
        # UK gas: forward curve as of d for the formation month, realized 12m-ago
        uk_F = curve.get(F, np.nan) if curve is not None else np.nan
        if not np.isfinite(uk_F):
            uk_F = uk_hist.get(F, np.nan)
        uk_F12 = uk_hist.get(F - pd.DateOffset(months=12), np.nan)
        if not np.isfinite([uk_F, uk_F12]).all() or uk_F <= 0 or uk_F12 <= 0:
            continue

        sy = np.log(s_F / s_F12)
        by = np.log(uk_F / uk_F12)
        yoy = pred(sy, by, np.log(base))
        ffv = base * np.exp(yoy)
        delivery = F + pd.offsets.MonthBegin(OFFSET_MONTHS)
        narrow.append({"run_date": pd.Timestamp(d), "contract_date": delivery,
                       "contract_label": label(delivery), "ffv_mid": round(float(ffv), 4),
                       "ffv_lower": round(float(ffv - (hist_mae if np.isfinite(hist_mae) else 0)), 4),
                       "ffv_upper": round(float(ffv + (hist_mae if np.isfinite(hist_mae) else 0)), 4)})

    # ---- wide snapshot: storage path T .. T-23 (T = CURRENT month) + 12m ratio ----
    Tcur = pd.Timestamp(d).to_period("M").to_timestamp()
    row = {"run_date": pd.Timestamp(d), "reference_month": Tcur}
    for k in range(WINDOW_MONTHS):
        mm = Tcur - pd.offsets.MonthBegin(k)
        lbl = "T" if k == 0 else f"T-{k}"
        val = stor.get(mm, np.nan)
        row[f"stor_{lbl}"] = val
        long.append({"run_date": pd.Timestamp(d), "reference_month": mm, "months_back": k,
                     "storage": val, "is_forecast": bool(mm > m if m is not None else True)})
    cur12 = sum(row[f"stor_{'T' if k==0 else 'T-'+str(k)}"] for k in range(12))
    pri12 = sum(row[f"stor_T-{k}"] for k in range(12, 24))
    row["stor_12m_current"] = cur12
    row["stor_12m_prior"] = pri12
    row["stor_ratio_12m"] = (cur12 / pri12) if pri12 else np.nan
    wide.append(row)

narrow_df = pd.DataFrame(narrow).sort_values(["run_date", "contract_date"]).reset_index(drop=True)
wide_df = pd.DataFrame(wide).sort_values("run_date").reset_index(drop=True)
long_df = pd.DataFrame(long).sort_values(["run_date", "months_back"]).reset_index(drop=True)
narrow_df.to_csv(OUT_NARROW, index=False)
with pd.ExcelWriter(OUT_WIDE) as xw:
    long_df.to_excel(xw, sheet_name="long", index=False)
    wide_df.to_excel(xw, sheet_name="wide", index=False)
print(f"\nsaved {OUT_NARROW}  ({len(narrow_df):,} rows)")
print(f"saved {OUT_WIDE}  (long {len(long_df):,} rows; wide {len(wide_df):,} snapshots)")
print("\nnarrow head:"); print(narrow_df.head(4).to_string(index=False))
print("\nnarrow tail:"); print(narrow_df.tail(4).to_string(index=False))

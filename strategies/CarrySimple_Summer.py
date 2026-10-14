import os as _os_boiler, sys as _sys_boiler
_HERE = _os_boiler.path.dirname(_os_boiler.path.abspath(__file__))
_REPO = _os_boiler.path.dirname(_HERE)
_sys_boiler.path.insert(0, _HERE)
import warnings
warnings.filterwarnings('ignore')
import zipline
from zipline.api import future_symbol,  \
    set_commission, set_slippage, schedule_function, date_rules, \
    time_rules, continuous_future, order_target
from datetime import datetime
import pytz
import matplotlib.pyplot as plt
import pyfolio as pf
import pandas as pd
import numpy as np  
from zipline.finance.commission import PerTrade, PerContract
from zipline.finance.slippage import VolumeShareSlippage, \
    FixedSlippage, VolatilityVolumeShare
from zipline.utils.calendar_utils import get_calendar   # <<< CHANGED: for trading_calendar in run_algorithm
APPEND = 0
ROLL_MODE = False
"""
General model configuration
"""
contracts = [   # <<< CHANGED: roots remapped to cmdty_all (barchart) codes
    # 'QY',     # EEX Butter        (was FABT)
    # 'MR',     # EEX SMP           (was FASM)
    # 'W5',     # EEX Whey
    # 'BD',     # CME Butter        (was CB)
    # 'BJ',     # CME Cheese        (was CSC)
    # 'DL',     # CME Class III Milk
    # 'DK',     # CME Class IV Milk
    # 'DG',     # CME Dry Whey      (was DWY)
    # 'DF',     # CME NFDM          (was NF)
    # 'K6',     # SGX Butter        (was SBTR)
    # 'K4',     # SGX SMP           (was SSMP)
    # 'K3',     # SGX WMP           (was SWMP)
    # ── grains & energy ──
    'NG',     # NYMEX Natural Gas    (1990+)
    # 'ZC',     # CBOT Corn            (1996+)
    # 'ZS',     # CBOT Soybeans        (1996+)
    # 'ZM',     # CBOT Soybean Meal    (1996+)
    # 'ZW',     # CBOT Wheat           (1994+)
    # 'ML',     # MATIF Milling Wheat  (2001+)
    # 'XR',     # Euronext Rapeseed    (2001+)
    # 'CB',     # ICE Brent Crude      (1996+)  (barchart code - NOT butter!)
]
CONTRACT_PARAMS = {  # <<< CHANGED: keys remapped to cmdty_all roots, values carried over 1:1; W5/DL/DK + grains/energy are placeholders - calibrate
    'QY': {'slippage': 30,  'slippage_roll': 13,  'commission': 1.25},   # was FABT
    'MR': {'slippage': 20,  'slippage_roll': 10,  'commission': 1.25},   # was FASM
    'W5': {'slippage': 30,  'slippage_roll': 10,  'commission': 5},   # placeholder (EEX whey)
    'BD': {'slippage': 1,  'slippage_roll': 0.3,  'commission': 0.05},   # was CB
    'BJ': {'slippage': 0.0002,  'slippage_roll': 0.0001,  'commission': 0.0005},   # was CSC
    'DL': {'slippage': 0.05,  'slippage_roll': 0.015,  'commission': 0.022},   # placeholder (Class III)
    'DK': {'slippage': 0.05,  'slippage_roll': 0.015,  'commission': 0.022},   # placeholder (Class IV)
    'DG': {'slippage': 0.3,  'slippage_roll': 0.15,  'commission': 0.022},   # was DWY
    'DF': {'slippage': 0.75,  'slippage_roll': 0.3,  'commission': 0.022},   # was NF
    'K6': {'slippage': 35,  'slippage_roll': 16,  'commission': 2.5},   # was SBTR
    'K4': {'slippage': 5,  'slippage_roll': 5,  'commission': 2.5},   # was SSMP
    'K3': {'slippage': 5,  'slippage_roll': 3,  'commission': 2.5},   # was SWMP
    # ── grains & energy placeholders — CALIBRATE before trusting P&L ──
    'NG': {'slippage': 0.001, 'slippage_roll': 0.0005, 'commission': 1.25},  # placeholder (natgas, $/mmBtu)
    'ZC': {'slippage': 0.5,  'slippage_roll': 0.25,  'commission': 2.5},  # placeholder (corn, cents/bu)
    'ZS': {'slippage': 0.5,  'slippage_roll': 0.25,  'commission': 2.5},  # placeholder (soybeans, cents/bu)
    'ZM': {'slippage': 0.3,  'slippage_roll': 0.1,   'commission': 2.5},  # placeholder (soymeal, $/ton)
    'ZW': {'slippage': 0.2,  'slippage_roll': 0,  'commission': 2.5},  # placeholder (wheat, cents/bu)
    'ML': {'slippage': 0.5,  'slippage_roll': 0.25,  'commission': 2.5},  # placeholder (MATIF wheat, EUR/t)
    'XR': {'slippage': 1.0,  'slippage_roll': 0.5,   'commission': 2.5},  # placeholder (rapeseed, EUR/t)
    'CB': {'slippage': 0.03, 'slippage_roll': 0.01, 'commission': 2.5},  # placeholder (ICE Brent, $/bbl) - CALIBRATE
}
start = pd.Timestamp('2000-01-01')
end = pd.Timestamp('2026-06-01')
roll='mul'
starting_portfolio = 100_000_000
risk_factor = (1 #%
/100)
vola_window = 32
fast_ma = 'carry rule simple'
slow_ma = 'all year'
enable_commission = True   
enable_slippage = True
carry1= 2
carry2= 3
tradedcontract = carry2
spancarry=3
position_changing_treshold = 0.15

# ── Seasonal gate (optional) ────────────────────────────────────────────────
# Only hold positions during these months; flat (sleeps) the rest of the year.

season_months = (5, 6, 7, 8, 9, 10)

use_season = True

RESULTS_XLSX = _os_boiler.path.join(_REPO, "results", "Results.xlsx")
# Exact run_name of a previously APPEND-saved run (see Sheet3 column headers)
# to use as the CTA-trend benchmark for alpha/beta, e.g. a plain 8/32 EWMAC run.
# None -> alpha/beta rows are skipped.
beta = "16/64 simple"
#%% Defining live reporting widget / def_report_result
def report_result(context, data):
    context.months += 1
    context.portfolio_values.append(context.portfolio.portfolio_value)
    
    today = zipline.api.get_datetime().date()
    ann_ret = np.power(
        context.portfolio.portfolio_value / starting_portfolio,
        12 / context.months
    ) - 1
    portfolio_value = context.portfolio.portfolio_value

    # Calculate Sharpe if enough history
    if len(context.portfolio_values) >= 3:
        vals = np.array(context.portfolio_values)
        monthly_returns = np.diff(vals) / vals[:-1]
        sharpe = (monthly_returns.mean() / monthly_returns.std()) * np.sqrt(12)
        sharpe_str = f"Sharpe: {sharpe:>+.2f} | "
    else:
        sharpe_str = "Sharpe:     -- | "

    # Progress bar
    total_months = (end.year - start.year) * 12 + (end.month - start.month)
    pct_done = context.months / total_months
    bar_len = 25
    filled = int(bar_len * pct_done)
    bar = "█" * filled + "░" * (bar_len - filled)

    line = (
        f"\r[{bar}] {pct_done:>5.1%} | "
        f"{today} | "
        f"€{portfolio_value:>10,.0f} | "
        f"{ann_ret:>+.2%} ann. | "
        f"{sharpe_str}"
    )
    print(line, end="", flush=True)
#%% Defining future rolling logic / def roll_futures
def roll_futures(context, data):
    global ROLL_MODE # Added with the new feauture for rolls
    open_orders = zipline.api.get_open_orders() #/// Getting open_orders from API
    for held_contract in context.portfolio.positions:
        # don't roll positions that are set to change by core logic /// This part compares our held contracts with open orders and skips if we already have an order generated for the contract
        if held_contract in open_orders: 
            continue
        
        # Save some time by only checking rolls for /// Kicked this one out for now, considering the schedule function
        # contracts stopping trading in the next days /// Checks when the contract will autoclose. To be examined with extension.py logic
        # days_to_auto_close = (
        #     held_contract.auto_close_date.date() - data.current_session.date()
        # ).days
        # if days_to_auto_close > 5:
        #     continue        
        
        # Make a continuation / We are generating the continuation here. Continuation is a separate structure from the rest of the code
        continuation = continuous_future(   
                held_contract.root_symbol, 
                offset=tradedcontract, 
                roll='calendar', 
                adjustment=roll
                )
        
        # Get the current contract of the continuation /// Here we are checking out what contract is the current active one in cont. by using the data method
        continuation_contract = data.current(continuation, 'contract')
        
        if continuation_contract != held_contract: # /// We are checking whether continuation is the same as the contract that we hold. If it is not, we have to roll
            ROLL_MODE = True   #Added for the roll slippage 
        # Check how many contracts we hold #/// We take the same size as we had before the roll, we check how much we had in held_contract
            pos_size = context.portfolio.positions[held_contract].amount  
            # Close current position  # /// Here we are closing the position
            order_target(held_contract, 0)
            # Open new position /// Here we are opening the new one
            order_target(continuation_contract, pos_size)  
        else:ROLL_MODE = False #Added for the roll slippage
            
#%% Defining sizing logic / def position_size        
def position_size(portfolio_value, std, point_value, trendstrenght): #/// We will adjust all the names here to the Systematic Trading Framework names
    target_variation = portfolio_value * risk_factor #/// Daily volatility target: portfolio * % you are risking daily per position
    contract_variation = std * point_value #// volatility in price units per lot (all lots are put to 1 in the bundle for now)
    contracts = target_variation / contract_variation * trendstrenght #// How many countracts do we need to achieve that volatility in price units
    return int(np.nan_to_num(contracts))  #// Protection against std=0 and contracts going to infinity
#%% Addition: Roll slippage fix
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
        fill_price = px + direction * (spread / 2.0)

        return fill_price, order.amount
from zipline.finance.commission import CommissionModel
class PerContractCustomCommission(CommissionModel):
    def calculate(self, order, transaction):
        if not enable_commission:
            return 0.0
        root = order.asset.root_symbol
        params = CONTRACT_PARAMS.get(root, {'commission': 2.5})
        return abs(transaction.amount) * params['commission'] * 2
#%% Defining initialize, variables to be used throughout zipline / def initialize 
def initialize(context):
    # These are added from class RollAwareFixedSlippage untill Cost Settings
    context.trendstrenght_log = {}
    
    """
    Cost Settings
    """
    context.portfolio_values = []
    
    context.lookback = 1285
    
    # if enable_commission: #// You can add costs or skip them
    #     comm_model = PerContract(cost=comission_value, exchange_fee=comission_value)
    # else:
    #     comm_model = PerTrade(cost=0.0)
        
    # set_commission(us_futures=comm_model)
    
    # if enable_slippage: #// Commented out due to new model
    #     context.slippage_model = RollAwareFixedSlippage(
    #         spread_trade=slippage_defined,   # your normal spread
    #         spread_roll=slippage_roll                 # roll spread (example)
    #     )
    # else:
    #     context.slippage_model = RollAwareFixedSlippage(
    #         spread_trade=0,   # your normal spread
    #         spread_roll=0                 # roll spread (example)
    #     )      
        
    set_commission(us_futures=PerContractCustomCommission())
    set_slippage(us_futures=RollAwareFixedSlippage())
    
    """
    Markets to trade /// We define the markets we trade in portfolio
    """ 
    

    # Make a list of all the markets
    markets = contracts
    
    # Make a list of all continuations /// Here we are defining how to make a continuous contract for each producti n portfolio. It saves descriptor objects. The real continuation will be determined later when we call it
    context.universe = [              #/// This is esentially a fancy way to do a loop which stores a formula that we use later in trade. Each elemnt that we store in list will look something like ContinuousFuture(FABT, offset=3, roll='calendar'), 
        continuous_future(market, offset=carry1, roll='calendar', adjustment=roll)
            for market in markets
    ]
    context.universe2 = [              #/// This is esentially a fancy way to do a loop which stores a formula that we use later in trade. Each elemnt that we store in list will look something like ContinuousFuture(FABT, offset=3, roll='calendar'), 
        continuous_future(market, offset=carry2, roll='calendar', adjustment=roll)
            for market in markets
    ]
    
    # Continuation used to resolve the contract we actually hold/trade: orders
    # always target tradedcontract, independent of the carry signal legs above.
    context.trade_universe = {
        market: continuous_future(market, offset=tradedcontract, roll='calendar', adjustment=roll)
            for market in markets
    }
    
    # We'll use these to keep track of best position reading /// Not sure what this is, to be investigated. Kicked out, it's for the breakout strategy
    # Used to calculate stop points.
    # context.highest_in_position = {market: 0 for market in markets} 
    # context.lowest_in_position = {market: 0 for market in markets}    
    
    # Schedule the daily trading /// Scheduling function to run daily_trade every day
    schedule_function(daily_trade, date_rules.every_day(), time_rules.market_close())
    
    # We'll just use this for the progress output ///This is for the infamous widget
    # during the backtest. Doesn't impact anything.
    context.months = 0    
    # Schedule monthly report output /// Also for the infamous widget
    schedule_function(
        func=report_result,
        date_rule=date_rules.month_start(),
        time_rule=time_rules.market_close()
    )
    
    
# def analyze(context, perf): #// / Performance analytics
#     returns, positions, transactions = pf.utils.extract_rets_pos_txn_from_zipline(perf)
#     pf.create_returns_tear_sheet(returns, benchmark_rets=None)
    
from IPython.utils import io as ipio
#%% Defining analysis output / def analyze
from IPython.utils import io as ipio
import pyfolio as pf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy import stats


# ─────────────────────────────────────────────────────────────────────────────
# METRIC 1: Drawdown statistics
# ─────────────────────────────────────────────────────────────────────────────
def calc_drawdown_stats(returns):
    """
    Returns average and max drawdown as positive percentages.
    """
    cum = (1 + returns).cumprod()
    rolling_max = cum.cummax()
    drawdown = (cum - rolling_max) / rolling_max  # always <= 0

    max_dd  = drawdown.min()      # most negative
    avg_dd  = drawdown[drawdown < 0].mean()  # average of underwater periods only

    return avg_dd * 100, max_dd * 100  # return as %


# ─────────────────────────────────────────────────────────────────────────────
# METRIC 2: Fat tail ratios (Carver, "Systematic Trading")
# ─────────────────────────────────────────────────────────────────────────────
def calc_fat_tails(returns):
    """
    Carver fat tail methodology:
    - Demean returns
    - Compute percentiles: 1st, 30th, 70th, 99th
    - Lower tail ratio = (1st pct / 30th pct) / 4.43
    - Upper tail ratio = (99th pct / 70th pct) / 4.43
    - 4.43 is the Gaussian benchmark (both ratios = 4.43 for normal distribution)
    - Values > 1 indicate fatter tails than normal
    """
    GAUSSIAN_RATIO = 4.43
    returns_active = returns[returns != 0]
    demeaned = returns_active - returns_active.mean()
    
    p01  = np.percentile(demeaned, 1)
    p30  = np.percentile(demeaned, 30)
    p70  = np.percentile(demeaned, 70)
    p99  = np.percentile(demeaned, 99)
    
    lower_ratio = (p01 / p30) / GAUSSIAN_RATIO
    upper_ratio = (p99 / p70) / GAUSSIAN_RATIO
    return lower_ratio, upper_ratio, p01, p30, p70, p99


# ─────────────────────────────────────────────────────────────────────────────
# METRIC 3: Sharpe T-Test (daily returns)
# ─────────────────────────────────────────────────────────────────────────────
def calc_sharpe_ttest(returns, window=252):
    """
    Rolling Sharpe ratio with 2-sigma confidence bands.
    Uses expanding window (all available history at each point).

    Returns:
      - t_stat: final t-statistic
      - p_value: one-tailed p-value (H0: SR <= 0)
      - rolling_sr: Series of rolling annualised Sharpe
      - upper_band, lower_band: 2-sigma confidence intervals
    """
    # Step 1: cumulative Sharpe at each day
    # Each day: SR = (mean of ALL returns so far / std of ALL returns so far) * sqrt(252)
    expanding_mean = returns.expanding().mean()
    expanding_std  = returns.expanding().std()
    n              = pd.Series(range(1, len(returns) + 1), index=returns.index)
    rolling_sr     = (expanding_mean / expanding_std) * np.sqrt(252)

    # Step 2: standard error of the mean annualised into Sharpe units
    # Purely a function of sample size - mechanically shrinks as 1/sqrt(n)
    # Guaranteed monotonically narrowing bands regardless of return behaviour
    se = np.sqrt(252) / np.sqrt(n)

    upper_band = rolling_sr + 2 * se
    lower_band = rolling_sr - 2 * se

    # Final t-test using all daily returns
    n_obs    = len(returns)
    sr_daily = returns.mean() / returns.std()
    t_stat   = sr_daily * np.sqrt(n_obs)
    p_value  = stats.t.sf(t_stat, df=n_obs - 1)  # one-tailed

    return t_stat, p_value, rolling_sr, upper_band, lower_band


# ─────────────────────────────────────────────────────────────────────────────
# CHART: Rolling Sharpe with confidence bands
# ─────────────────────────────────────────────────────────────────────────────
def plot_sharpe_ttest(rolling_sr, upper_band, lower_band, t_stat, p_value):

    # Skip year 1 only - std needs at least 2 annual estimates to be meaningful
    # Drop leading NaN from forward-fill before year 1 data arrives
    first_valid = rolling_sr.first_valid_index()
    rolling_sr = rolling_sr[first_valid:]
    upper_band = upper_band[first_valid:]
    lower_band = lower_band[first_valid:]

    # Skip first 252 days (1 year) - ensures first visible SR already has
    # a full year of data behind it, eliminating early noise spikes
    BURNIN = 252
    rolling_sr = rolling_sr.iloc[BURNIN:]
    upper_band = upper_band.iloc[BURNIN:]
    lower_band = lower_band.iloc[BURNIN:]

    fig, ax = plt.subplots(figsize=(12, 4))

    ax.plot(rolling_sr.index,  rolling_sr,  color='#2196F3', lw=1.5,
            label='Cumulative avg Sharpe')
    ax.plot(upper_band.index,  upper_band,  color='#2196F3', lw=0.8,
            linestyle='--', alpha=0.6, label='+2σ')
    ax.plot(lower_band.index,  lower_band,  color='#2196F3', lw=0.8,
            linestyle='--', alpha=0.6, label='−2σ')
    ax.fill_between(rolling_sr.index, lower_band, upper_band,
                    alpha=0.1, color='#2196F3')
    ax.axhline(0, color='black', lw=0.8, linestyle='-')

    # Mark where lower band crosses above zero (T-test passed)
    passed = lower_band[lower_band > 0]
    if len(passed) > 0:
        pass_date = passed.index[0]
        ax.axvline(pass_date, color='green', lw=1.2, linestyle=':',
                   label=f'T-test passed ({pass_date.strftime("%Y-%m")})')

    passed_str = f"PASSED (p={p_value:.3f})" if p_value < 0.025 else f"not passed (p={p_value:.3f})"
    ax.set_title(f'Rolling Sharpe Ratio with 2σ Confidence Bands  |  '
                 f't={t_stat:.2f}  |  T-Test: {passed_str}',
                 fontsize=11)
    ax.set_ylabel('Sharpe Ratio')
    ax.legend(loc='upper left', fontsize=8)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()

def analyze(context, perf):
    print()
    print("✅ Backtest complete!\n")
    # Store trendstrenght log from context
    ts_log = pd.Series(context.trendstrenght_log)
    ts_log.index = ts_log.index.tz_localize(None)
    ts_scaled = ts_log * 50  # scale to match exposure axis
    # Signal diagnostic (target mean|trendstrenght| ~ 1)
    ts_abs = ts_log.abs()
    ts_mean_abs = float(ts_abs.mean()) if len(ts_abs) else np.nan
    ts_p95 = float(ts_abs.quantile(0.95)) if len(ts_abs) else np.nan
    ts_cap_frac = float((ts_abs >= 1.999).mean()) if len(ts_abs) else np.nan
    import os as _os
    returns, positions, transactions = pf.utils.extract_rets_pos_txn_from_zipline(perf)
    monthly_returns = returns.resample('M').apply(lambda x: (1 + x).prod() - 1)
    monthly_skew = monthly_returns.dropna().skew()

    # ── Compute all metrics ───────────────────────────────────────────────────
    stats      = pf.timeseries.perf_stats(returns)
    avg_dd, _  = calc_drawdown_stats(returns)
    lower_tail, upper_tail, p01, p30, p70, p99 = calc_fat_tails(returns)
    t_stat, p_value, rolling_sr, upper_band, lower_band = calc_sharpe_ttest(returns)
    passed     = p_value < 0.025

    # <<< CHANGED: Carver risk-adjusted cost block (helper: carver_costs.py,
    # same folder). Measures ACTUAL costs charged by the backtest, expressed
    # in Sharpe-ratio units. No strategy-logic changes.
    import sys as _sys
    try:
        _here = _os.path.dirname(_os.path.abspath(__file__))
    except NameError:
        _here = _HERE
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    from carver_costs import carver_cost_report, carver_summary_rows
    _carver = carver_cost_report(perf, CONTRACT_PARAMS, enable_commission,
                                 enable_slippage, stats)
    globals()['CARVER'] = _carver

    # <<< CHANGED: alpha/beta vs benchmark run (helper: alpha_beta.py).
    # Regresses this strategy's monthly returns on the benchmark run's monthly
    # returns (Sheet3 of RESULTS_XLSX) over the OVERLAPPING months only, so a
    # short-history strategy vs a long-history benchmark works fine.
    global ALPHA_BETA
    ALPHA_BETA = {}
    if beta:
        import alpha_beta as _ab
        _book = _ab.load_monthly_returns_book(RESULTS_XLSX)
        if beta in _book.columns:
            ALPHA_BETA = _ab.compute_alpha_beta(monthly_returns,
                                                _book[beta].dropna())
            ALPHA_BETA['benchmark'] = beta
        else:
            ALPHA_BETA = {'benchmark': beta, 'beta': np.nan,
                          'alpha_ann': np.nan, 'r2': np.nan, 'overlap_months': 0,
                          'error': 'not in Sheet3 - save benchmark with APPEND=1 first'}

    # ── Single unified table ──────────────────────────────────────────────────
    rows = carver_summary_rows(_carver) + [
        ("Annual net return",                 f"{stats['Annual return']*100:,.2f}%",      ""),
        ("Cumulative returns",              f"{stats['Cumulative returns']*100:,.2f}%", ""),
        ("Annual volatility",               f"{stats['Annual volatility']*100:,.2f}%",  ""),
        ("Sharpe ratio",                    f"{stats['Sharpe ratio']:,.2f}",             ""),
        ("Sortino ratio",                   f"{stats['Sortino ratio']:,.2f}",            ""),
        ("Stability",                       f"{stats['Stability']:,.2f}",                ""),
        ("Max drawdown",                    f"{stats['Max drawdown']*100:,.2f}%",        ""),
        ("Avg drawdown",                    f"{avg_dd:,.2f}%",                           ""),
        ("Skew (monthly)",                  f"{monthly_skew:,.2f}",                      ""),
        ("Tail ratio (95/5)",               f"{stats['Tail ratio']:,.2f}",               ""),
        ("Lower tail ratio (1/30/4.43)",    f"{lower_tail:,.3f}",                        "⚠" if lower_tail > 1.5 else "✓"),
        ("Upper tail ratio (99/70/4.43)",   f"{upper_tail:,.3f}",                        "⚠" if upper_tail > 1.5 else "✓"),
        ("t-statistic",                     f"{t_stat:,.3f}",                            ""),
        ("p-value (one-tailed)",            f"{p_value*100:,.2f}%",                      "✅ PASSED" if passed else "❌ NOT PASSED"),
        ("Trend strength |mean|",           f"{ts_mean_abs:,.3f}",                       f"p95={ts_p95:,.2f}, at cap={ts_cap_frac:.1%}"),
    ]

    # <<< CHANGED: alpha/beta rows, placed right after the Carver cost block
    if beta:
        _bm = ALPHA_BETA.get('benchmark', '')
        if ALPHA_BETA.get('error'):
            _note = ALPHA_BETA['error']
            _ab_rows = [("Beta to benchmark",     "-", _bm),
                        ("Alpha vs bench (ann.)", "-", _note),
                        ("R2 to benchmark",       "-", "")]
        elif np.isnan(ALPHA_BETA.get('beta', np.nan)):
            _note = f"overlap {ALPHA_BETA.get('overlap_months', 0)}m < 24m"
            _ab_rows = [("Beta to benchmark",     "-", _bm),
                        ("Alpha vs bench (ann.)", "-", _note),
                        ("R2 to benchmark",       "-", "")]
        else:
            _ab_rows = [
                ("Beta to benchmark",     f"{ALPHA_BETA['beta']:,.3f}",
                 _bm),
                ("Alpha vs bench (ann.)", f"{ALPHA_BETA['alpha_ann']*100:,.2f}%",
                 f"n={ALPHA_BETA['overlap_months']}m overlap"),
                ("R2 to benchmark",       f"{ALPHA_BETA['r2']:,.3f}", ""),
            ]
        _n_carver = len(carver_summary_rows(_carver))
        rows = rows[:_n_carver] + _ab_rows + rows[_n_carver:]

    df = pd.DataFrame(rows, columns=["Metric", "Value", ""])
    # <<< CHANGED: expose every extra summary stat to the APPEND block
    global SUMMARY_EXTRA
    SUMMARY_EXTRA = {
        'GrossAnnualReturn_pct': _carver.get('gross_annual_return', np.nan) * 100,
        'GrossSharpe': _carver.get('gross_sharpe', np.nan),
        'CommissionCost_SR': _carver.get('commission_sr', np.nan),
        'SlippageCost_SR': _carver.get('slippage_sr', np.nan),
        'RollingCost_SR': _carver.get('rolling_sr', np.nan),
        'TotalCost_SR': _carver.get('total_sr', np.nan),
        'AnnualTurnover_x': (_carver.get('turnover') if _carver.get('turnover') is not None else np.nan),
        'RollsPerYear': _carver.get('rolls_per_year', np.nan),
        'TrendStrength_MeanAbs': ts_mean_abs,
        'TrendStrength_p95': ts_p95,
        'TrendStrength_CapFrac': ts_cap_frac,
    }

    print("\n=== STRATEGY SUMMARY ===")
    print(df.to_string(index=False))

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
    _fig1 = rc.page1(returns, positions, perf, price_series, ts_scaled, price_label)
    plt.show()
    _fig2 = rc.page2(returns, (t_stat, p_value, rolling_sr, upper_band, lower_band))
    plt.show()

    
    """def analyze(context, perf):
    print()
    print("✅ Backtest complete!\n")
    returns, positions, transactions = pf.utils.extract_rets_pos_txn_from_zipline(perf)

    with ipio.capture_output():
        pf.create_returns_tear_sheet(
            returns,
            benchmark_rets=None
        )

    stats = pf.timeseries.perf_stats(returns)

    pct_metrics = {
        "Annual return",
        "Cumulative returns",
        "Annual volatility",
        "Max drawdown",
        "Daily value at risk",
    }

    rows = []
    for k, v in stats.items():
        if k in pct_metrics:
            rows.append((k, f"{v*100:,.2f}%"))
        else:
            rows.append((k, f"{v:,.2f}"))

    stats_table = pd.DataFrame(rows, columns=["Metric", "Value"])
    print("\n=== PYFOLIO KEY RATIOS (FORMATTED) ===")
    print(stats_table.to_string(index=False))"""

#%% Defining daily trading logic / def daily_trade
def daily_trade(context, data):
    today = zipline.api.get_datetime().tz_localize(None).normalize()
    # <<< CHANGED: seasonal gate - outside the allowed months force direction/strength to 0
    # so target_amount is 0 and the order block flattens any open position.
    in_season = (today.month in season_months) if use_season else True

    hist = data.history(
        context.universe, 
        fields=['close'], 
        frequency='1d', 
        bar_count=context.lookback,
    )
    hist2 = data.history(
        context.universe2, 
        fields=['close'], 
        frequency='1d', 
        bar_count=context.lookback,
    )

    open_pos = {
        pos.root_symbol: pos 
        for pos in context.portfolio.positions
    } 
    
    for continuation, continuation2 in zip(context.universe, context.universe2):
        root = continuation2.root_symbol  # same as cont_far.root_symbol if built in parallel        
        h = hist.xs(continuation, level=1)
        h2 = hist2.xs(continuation2, level=1)
        
        # carry = (h['close'] - h2['close']).iloc[-1]
        # carry = (h['close'] - h2['close']).ewm(span=1).mean().iloc[-1]
        
        carry_series = (h['close'] - h2['close']).ewm(span=spancarry).mean()
        carry = carry_series.iloc[-1]  # used for direction only
        
        want_long = carry > 0
        direction = 1 if want_long else -1
        if not in_season:
            direction = 0
        
        ret = h2['close'].pct_change()
        std_fast = ret.ewm(span=vola_window, adjust=False).std().iloc[-1]
        std_slow = ret.rolling(1260, min_periods=vola_window).std().iloc[-1]
        std_blended = std_fast if pd.isna(std_slow) else 0.2 * std_slow + 0.8 * std_fast
        # std_blended = max(0.01, std_blended)
        std = std_blended * h2['close'].iloc[-1]
        if std is None or std == 0 or (std != std):
            continue
        
        std_annualized = std*16

        # annualizedcarry = carry * (12/(5-3))
        
        # risk_adjusted_carry = annualizedcarry / std_annualized
        
        annualizedcarry_series = carry_series * (12/(carry2-carry1))
        risk_adjusted_carry = (annualizedcarry_series / std_annualized).ewm(span=3).mean().iloc[-1]
        
        trendstrenght = abs(risk_adjusted_carry) / 0.35
        trendstrenght = min(trendstrenght, 2.0)
        if not in_season:
            trendstrenght = 0.0
        context.trendstrenght_log[zipline.api.get_datetime()] = direction * trendstrenght
        # contract is used for price_multiplier
        contract = data.current(continuation2, 'contract')

        # order_contract determines where orders go
        if root in open_pos:
            held = open_pos[root]
            current_amount = context.portfolio.positions[held].amount
            order_contract = held
        else:
            current_amount = 0
            # always enter on the contract we actually hold (tradedcontract),
            # not the carry signal leg
            order_contract = data.current(context.trade_universe[root], 'contract')

        target_contracts = position_size(
            context.portfolio.portfolio_value,
            std,
            contract.price_multiplier,
            trendstrenght=trendstrenght
        )
        target_contracts = int(target_contracts)
        target_amount = direction * target_contracts

        if current_amount == 0:
            order_target(order_contract, target_amount)
        elif target_amount == 0:
            order_target(order_contract, 0)
        else:
            change_ratio = abs(target_amount - current_amount) / abs(current_amount)
            if change_ratio >= position_changing_treshold:
                order_target(order_contract, target_amount)

        # Diagnostic logging
       

    if len(context.portfolio.positions) > 0:
        roll_futures(context, data) 
        
perf = zipline.run_algorithm(
    start=start,
    end=end,
    initialize=initialize,
    analyze=analyze,
    capital_base=starting_portfolio,
    data_frequency='daily',
    bundle='cmdty_all',   # <<< CHANGED: was 'dairy_futures' (dairy + grains + energy)
    trading_calendar=get_calendar('us_futures'),   # <<< CHANGED: sim calendar must match the ingest calendar (docs/02 §5)
)


#%% Old def daily_trade                        
"""def daily_trade(context, data):
    # Get continuation data /// Here we are getting all the continuation data which we defined in context.universe
    hist = data.history(
        context.universe, 
        fields=['close'], 
        frequency='1d', 
        bar_count=context.lookback,
    )

    # Calculate trend /// Here Clenow is calculating if fast ma is above slow ma. The returns are true or false (it's a regime calc for each market in hist)
    # hist['trend'] = hist['close'].ewm(span=fast_ma).mean() > hist['close'].ewm(span=slow_ma).mean()    
    
    # Make dictionary of open positions /// Here we are getting dictionary (asset) values of all of our positions COMMENTED OUT AS WE DON'T NEED IT IN SIMPLE VERSION
    # open_pos = {
    #     pos.root_symbol: pos 
    #     for pos in context.portfolio.positions
    # } 
    
    # Iterate markets, check for trades /// Here we loop through continuations in order to decide actions
    for continuation in context.universe:
        
        # Get root symbol of continuation
        # root = continuation.root_symbol #/// We just get the root symbol, it's a zipline method COMMENTED OUT AS WE DON'T NEED IT IN SIMPLE VERSION
        
        # Slice off history for just this market /// Here is is unclear why 2 is used and why .xs /Okay apaprently this slices into 2 columns
        # h = hist.xs(continuation, 2)  
        # h = hist[continuation]
        h = hist.xs(continuation, level=1)   # axis defaults to 0 (rows)
        
        # h = hist[root]
        
        # Calculate moving averages
        fast = h['close'].ewm(span=fast_ma).mean()
        slow = h['close'].ewm(span=slow_ma).mean()
        ewmac = (fast.iloc[-1] - slow.iloc[-1])
        
        # Detect crossover (today vs yesterday)
        # bullish_cross = (fast.iloc[-1] > slow.iloc[-1]) and (fast.iloc[-2] <= slow.iloc[-2])
        # bearish_cross = (fast.iloc[-1] < slow.iloc[-1]) and (fast.iloc[-2] >= slow.iloc[-2])
        
        # Regime / direction (today) /// Basically we add this because we always wanna be long in this condition and we rescale based on volatility
        want_long = fast.iloc[-1] > slow.iloc[-1]
        direction = 1 if want_long else -1
        
        ret = h['close'].pct_change()
        std_fast=ret.ewm(span=vola_window, adjust=False).std().iloc[-1]
        std_slow = ret.rolling(1260, min_periods=vola_window).std().iloc[-1]
        std_blended=0.3*std_slow+0.7*std_fast
        # std_blended=max(0.02,std_blended)
        std = std_blended * h['close'].iloc[-1] #/// This is basically in price units now
        if std is None or std == 0 or (std != std):  # guard against 0 / NaN
            continue
        # Trend strenght definition
        trendstrenghtMULT = {
        (2, 8): 0.77,
        (4, 16): 1.15,
        (8, 32): 1.7,
        (16, 64): 2.5,
        (32, 128): 3.74,
        (64, 256): 5.28
        }
        
        scale = trendstrenghtMULT[(fast_ma, slow_ma)]
        trendstrenght = ( abs(ewmac) / std ) / scale

        trendstrenght = min(trendstrenght, 2.0)
        # ///// Calculate volatility (for scaling) / old volatility calculation from Clenow ////
        # # std = h['close'].diff()[-vola_window:].std()
        # if std is None or std == 0 or (std != std):  # guard against 0 / NaN
        #     continue
        # ///// Calculate volatility (for scaling) / old volatility calculation from Clenow ////
        
        # Resolve actual tradable contract
        contract = data.current(continuation, 'contract')

        # Determine current position (if any)
        position = context.portfolio.positions.get(contract, None) #COMMENTED OUT AS WE DON'T NEED IT IN SIMPLE VERSION
        current_amount = position.amount if position else 0 #COMMENTED OUT AS WE DON'T NEED IT IN SIMPLE VERSION
        
        target_contracts = position_size(
            context.portfolio.portfolio_value,
            std,
            contract.price_multiplier,
            trendstrenght=trendstrenght
        )
        target_contracts = int(target_contracts)  # ensure integer contracts
        target_amount = direction * target_contracts
       
        
        # Always rebalance to target (this is the key change /// We are changing this part to rebalance only when sizably different position is needed
        # order_target(contract, target_amount)
        if current_amount == 0:
            # No position - always enter
            order_target(contract, target_amount)
        elif target_amount == 0:
            # Signal gone - always exit
            order_target(contract, 0)
        else:
            # Existing position - only resize if change exceeds 20%
            change_ratio = abs(target_amount - current_amount) / abs(current_amount)
            if change_ratio >= position_changing_treshold:
                order_target(contract, target_amount)

    # Roll if needed
    if len(context.portfolio.positions) > 0:
        roll_futures(context, data)  """              


#%% Results XLSX

if APPEND==1:
    import os
    import numpy as np
    import pandas as pd
    from scipy import stats
    
    # RESULTS_XLSX now lives at module level (shared with the alpha/beta reader)
    import alpha_beta as _ab
    
    def append_zipline_results_to_excel(perf: pd.DataFrame, config: dict, results_path: str = RESULTS_XLSX):
        """
        Appends one backtest run to:
          Sheet1 = summary/config row (with full performance stats)
          Sheet2 = equity curve column (Date + one new column)
        """
    
        ts = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    
        # ============================================================
        # EQUITY CURVE (Sheet2)
        # ============================================================
        equity = perf[['portfolio_value']].copy()
        equity = equity.reset_index().rename(columns={'index': 'Date', 'portfolio_value': 'PortfolioValue'})
        equity['Date'] = pd.to_datetime(equity['Date']).dt.tz_localize(None)
    
        run_name = config.get("run_name")
        if not run_name:
            run_name = f"{ts} | {config.get('contracts','?')} | fast={config.get('fast_ma','?')} slow={config.get('slow_ma','?')}"
    
        equity = equity.rename(columns={'PortfolioValue': run_name})
    
        pv0 = float(equity[run_name].iloc[0])
        pv1 = float(equity[run_name].iloc[-1])
    
        # ============================================================
        # CORE RETURN SERIES
        # ============================================================
        eq_series = equity[run_name].astype(float)
        daily_ret = eq_series.pct_change().dropna()
    
        TRADING_DAYS = 252
    
        cumulative_return_pct = (pv1 / pv0 - 1.0) * 100.0 if pv0 != 0 else np.nan
        ann_return = (pv1 / pv0) ** (TRADING_DAYS / len(daily_ret)) - 1.0 if len(daily_ret) > 0 else np.nan
        ann_return_pct = ann_return * 100.0 if pd.notna(ann_return) else np.nan
        ann_vol = daily_ret.std(ddof=0) * np.sqrt(TRADING_DAYS) if len(daily_ret) > 1 else np.nan
        ann_vol_pct = ann_vol * 100.0 if pd.notna(ann_vol) else np.nan
    
        _sharpe_ret = perf['returns'].astype(float).dropna()
    
        sharpe = (_sharpe_ret.mean() / _sharpe_ret.std(ddof=1)) * np.sqrt(TRADING_DAYS) if _sharpe_ret.std(ddof=1) else np.nan
        downside = np.where(daily_ret < 0, daily_ret, 0)
        downside_std = np.std(downside) * np.sqrt(TRADING_DAYS) if len(daily_ret) > 0 else np.nan
        sortino = ann_return / downside_std if downside_std and downside_std != 0 else np.nan
    
        # ============================================================
        # DRAWDOWN
        # ============================================================
        peak = eq_series.cummax()
        dd = eq_series / peak - 1.0
        max_drawdown = dd.min() * 100.0
        avg_drawdown = dd[dd < 0].mean() * 100.0  # only underwater periods
    
        # ============================================================
        # MONTHLY METRICS
        # ============================================================
        # ============================================================
    #    MONTHLY METRICS (fix: use Date as index)
        # ============================================================
        eq_with_dates = equity[['Date', run_name]].copy()
        eq_with_dates = eq_with_dates.set_index('Date')[run_name].astype(float)
        
        monthly_eq = eq_with_dates.resample("M").last()
        monthly_ret = monthly_eq.pct_change().dropna()
    
        skew_monthly = stats.skew(monthly_ret) if len(monthly_ret) >= 3 else np.nan
    
        if len(monthly_ret) > 0:
            q95 = np.percentile(monthly_ret, 95)
            q5 = np.percentile(monthly_ret, 5)
            tail_ratio = abs(q95) / abs(q5) if q5 != 0 else np.nan
    
            daily_ret_active = daily_ret[daily_ret != 0]
            demeaned = daily_ret_active - daily_ret_active.mean()
            
            p1  = np.percentile(demeaned, 1)
            p30 = np.percentile(demeaned, 30)
            p70 = np.percentile(demeaned, 70)
            p99 = np.percentile(demeaned, 99)
            
            lower_tail_ratio = (p1 / p30) / 4.43
            upper_tail_ratio = (p99 / p70) / 4.43
    
            t_stat = monthly_ret.mean() / (monthly_ret.std(ddof=1) / np.sqrt(len(monthly_ret))) if monthly_ret.std(ddof=1) != 0 else np.nan
            p_value = 1.0 - stats.t.cdf(t_stat, df=len(monthly_ret)-1)
        else:
            tail_ratio = np.nan
            lower_tail_ratio = np.nan
            upper_tail_ratio = np.nan
            t_stat = np.nan
            p_value = np.nan
    
        pass_flag = True if pd.notna(p_value) and p_value < 0.05 else False
    
        # ============================================================
        # STABILITY (R² of log equity)
        # ============================================================
        if len(eq_series) >= 3:
            y = np.log(eq_series.values)
            x = np.arange(len(y))
            beta = np.polyfit(x, y, 1)
            y_hat = beta[0] * x + beta[1]
            ss_res = np.sum((y - y_hat) ** 2)
            ss_tot = np.sum((y - y.mean()) ** 2)
            stability_r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
        else:
            stability_r2 = np.nan
    
        # ============================================================
        # BUILD SUMMARY ROW (Sheet1)
        # ============================================================
        # <<< CHANGED: alpha/beta results computed in analyze() (module global)
        _ab_res = globals().get("ALPHA_BETA", {}) or {}
        summary_row = {
            "Timestamp": ts,
            **config,
            "run_name": run_name,   # ✅ overwrite the None from config with the resolved name
            "StartPortfolioValue": pv0,
            "EndPortfolioValue": pv1,
            "CumulativeReturn_%": cumulative_return_pct,
            "AnnualReturn_%": ann_return_pct,
            "AnnualVolatility_%": ann_vol_pct,
            "Sharpe": sharpe,
            "Sortino": sortino,
            "Stability_R2": stability_r2,
            "MaxDrawdown_%": max_drawdown,
            "AvgDrawdown_%": avg_drawdown,
            "Skew_Monthly": skew_monthly,
            "TailRatio_95_5": tail_ratio,
            "LowerTailRatio_1_30": lower_tail_ratio,
            "UpperTailRatio_99_70": upper_tail_ratio,
            "T_statistic": t_stat,
            "P_value_OneTailed": p_value,
            "PASS_p_lt_0_05": pass_flag,
            # <<< CHANGED: alpha/beta decomposition vs benchmark run (if beta set)
            "Benchmark_run": _ab_res.get("benchmark"),
            "Beta_to_benchmark": _ab_res.get("beta"),
            "Alpha_annualized_%": (_ab_res["alpha_ann"] * 100
                                   if pd.notna(_ab_res.get("alpha_ann")) else np.nan),
            "R2_to_benchmark": _ab_res.get("r2"),
            "Alpha_beta_overlap_months": _ab_res.get("overlap_months"),
            "Gross_Sharpe": (globals().get("CARVER") or {}).get("gross_sharpe"),
            "Commission_SR": (globals().get("CARVER") or {}).get("commission_sr"),
            "Slippage_SR": (globals().get("CARVER") or {}).get("slippage_sr"),
            "Rolling_SR": (globals().get("CARVER") or {}).get("rolling_sr"),
            "TotalCosts_SR": (globals().get("CARVER") or {}).get("total_sr"),
            "AnnualTurnover": (globals().get("CARVER") or {}).get("turnover"),
            "RollsPerYear": (globals().get("CARVER") or {}).get("rolls_per_year"),
        }
    
        # <<< CHANGED: fold in ALL extra summary stats from analyze()
        try:
            summary_row.update(globals().get("SUMMARY_EXTRA", {}) or {})
        except Exception:
            pass
        new_summary_df = pd.DataFrame([summary_row])
    
        # ============================================================
        # APPEND TO EXISTING WORKBOOK
        # ============================================================
        os.makedirs(os.path.dirname(results_path), exist_ok=True)   # <<< CHANGED: auto-create the backtests folder
        try:
            if os.path.exists(results_path):
    
                try:
                    existing_summary = pd.read_excel(results_path, sheet_name="Sheet1")
                    summary_out = pd.concat([existing_summary, new_summary_df], ignore_index=True)
                except:
                    summary_out = new_summary_df
    
                try:
                    existing_equity = pd.read_excel(results_path, sheet_name="Sheet2")
                    existing_equity['Date'] = pd.to_datetime(existing_equity['Date'])
                except:
                    existing_equity = pd.DataFrame({"Date": []})
    
                if len(existing_equity) == 0:
                    equity_out = equity
                else:
                    equity_out = (
                        pd.merge(existing_equity, equity, on="Date", how="outer")
                        .sort_values("Date")
                        .reset_index(drop=True)
                    )
            else:
                summary_out = new_summary_df
                equity_out = equity
    
            # <<< CHANGED: Sheet3 = monthly returns library (one column per run)
            monthly_book = _ab.load_monthly_returns_book(results_path)
            monthly_out = _ab.append_monthly_returns_column(monthly_book,
                                                            run_name, monthly_ret)

            with pd.ExcelWriter(results_path, engine="openpyxl") as writer:
                summary_out.to_excel(writer, sheet_name="Sheet1", index=False)
                equity_out.to_excel(writer, sheet_name="Sheet2", index=False)
                monthly_out.reset_index().to_excel(writer, sheet_name="Sheet3", index=False)

            print(f"\n✓ Results appended to: {results_path}")
            print(f"  Sheet1 rows: {len(summary_out):,}")
            print(f"  Sheet2 cols: {len(equity_out.columns):,}")
            print(f"  Sheet3 cols: {len(monthly_out.columns):,} (monthly returns)")
            print(f"  New run column: {run_name}")
    
        except Exception as e:
            print(f"\n✗ Error appending results to Excel: {e}")
            import traceback
            traceback.print_exc()
    
    
    # Build config dict (unchanged)
    config = {
        "run_name": "carry simple summer",
        "contracts": ",".join(contracts),
        "start": str(start.date()),
        "end": str(end.date()),
        "roll": roll,
        "beta": beta,   # <<< CHANGED: benchmark run_name for alpha/beta
        "starting_portfolio": starting_portfolio,
        "risk_factor": risk_factor,
        "vola_window": vola_window,
        "fast_ma": fast_ma,
        "slow_ma": slow_ma,
        "enable_commission": enable_commission,
        "enable_slippage": enable_slippage,
        # "comission_value": comission_value,
        # "slippage_defined": slippage_defined,
        # "slippage_roll": slippage_roll,
        "position_changing_treshold": position_changing_treshold,
        "use_season": use_season,           # <<< CHANGED
        "season_months": ",".join(map(str, season_months)),  # <<< CHANGED
    }
    
    append_zipline_results_to_excel(perf, config)
else:
    pass
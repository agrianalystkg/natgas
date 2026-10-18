# FFV forecasting

The fair-value model behind the FFV range strategy.

| File | Role |
|---|---|
| `04d_gas_yoy_price_model_brent.py` | fits the pooled fair-value model — log-price YoY on storage YoY, Brent YoY and price 12 months ago (rolling WLS, 120-month window, decay 0.985); writes `data/04d_pooled_yoy_brent_model.xlsx` and the FFV history |
| `09d_gas_ffv_simulation_brent.py` | walks forward day by day and builds the FFV using only information knowable then — monthly fundamentals through M-2, EIA weekly stocks, weather, and the crude forward curve; writes `data/FFV_history_natgas_brent_sim.csv` |
| [`../backtest_ffv_range.py`](../backtest_ffv_range.py) | the trading rule: rich + falling mid → short, cheap + rising mid → long, conviction from the distance to the mid (capped at `max_ts`) |

The inputs to the model — the Barchart per-contract masters (`data/raw/`) and
`data/gas_snd_monthly.xlsx` — are not shipped. The point-in-time FFV history they
produce **is** shipped, in [`../../data/`](../../data), so the FFV backtest can be
read and checked without the proprietary price data.

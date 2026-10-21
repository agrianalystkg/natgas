# FFV forecasting

The fair-value models behind the two FFV range sleeves — one anchored on Brent,
one on UK gas (NBP).

| File | Role |
|---|---|
| `04d_gas_yoy_price_model_brent.py` | fits the pooled fair-value model with Brent as the exogenous gas price — log-price YoY on storage YoY, Brent YoY and price 12 months ago (rolling WLS, 120-month window, decay 0.985) |
| `09d_gas_ffv_simulation_brent.py` | walks forward day by day and builds the Brent-anchored FFV using only information knowable then — monthly fundamentals through M-2, EIA weekly stocks, weather, and the crude forward curve; writes `data/FFV_history_natgas_brent_sim.csv` |
| `04e_gas_yoy_price_model_ukgas.py` | same model with the UK gas forward curve as the exogenous gas price |
| `09e_gas_ffv_simulation_ukgas.py` | the UK-gas point-in-time simulation; writes `data/FFV_history_natgas_ukgas_sim.csv` |
| [`../backtest_ffv_range.py`](../backtest_ffv_range.py) | the trading rule for both anchors: rich + falling mid → short, cheap + rising mid → long, conviction from the distance to the mid (capped at 3) |

Run the two variants with:

```bash
FFV_MODEL=brent python backtest_ffv_range.py
FFV_MODEL=ukgas python backtest_ffv_range.py
```

The inputs to the models — the Barchart per-contract masters (`data/raw/`),
the UK gas master and `data/gas_snd_monthly.xlsx` — are not shipped. The
point-in-time FFV histories they produce **are** shipped, in
[`../../data/`](../../data), so the backtest can be read and checked without the
proprietary price data.

# Strategies

The per-strategy backtests that produced `results/Results.xlsx` — one script per
trading rule in the book.

| File | Rule |
|---|---|
| `EWMAC_2_8.py` · `EWMAC_4_16.py` · `EWMAC_8_32.py` · `EWMAC_16_64.py` · `EWMAC_32_128.py` · `EWMAC_64_256.py` | the six trend speeds |
| `CarrySimple_AllYear.py` · `CarrySimple_Summer.py` | carry, raw (all-year / summer-only) |
| `CarryAdjusted_AllYear.py` · `CarryAdjusted_Summer.py` | carry, seasonally adjusted |
| `Weather_GWDD_SMA7_NovMar.py` | weather — realised GWDD anomaly |
| `Inventory_EIA_winter.py` | storage — after the EIA release |
| `Inventory_Nowcast_winter.py` | storage — before the release (nowcast) |

Each is a self-contained zipline-reloaded backtest: continuous futures, calendar
rolls, vol-target sizing, the cost model, the shared reporting and the results
writer. The cleaned, reusable version of that machinery lives in
[`../natgas/`](../natgas); the four shims here (`carver_costs.py`,
`reporting_charts.py`, `continuous_prices.py`, `alpha_beta.py`) simply re-export
it so the scripts can keep their original imports.

**Running them** needs the ingested zipline bundle (`cmdty_all`, built from the
Barchart CSVs) and the fundamental inputs in [`../data/`](../data). The price data
and the bundle are not redistributed here.

```bash
python EWMAC_16_64.py      # writes a new row to results/Results.xlsx (APPEND=1)
```

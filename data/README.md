# Data

## Where it comes from

| Input | Source | Notes |
|---|---|---|
| Futures settlement prices | Barchart | per-contract OHLC; **not redistributed** (licensing) |
| Natural-gas storage | [EIA Weekly Natural Gas Storage Report](https://www.eia.gov/naturalgas/storage/) | Lower-48 stocks, Bcf, weekly |
| Weather | [NOAA](https://www.ncei.noaa.gov/) | daily temperatures → population-weighted gas-weighted degree days (GWDD) |

## Shipped samples (public data)

So the input formats are visible, a few small public-derived files are included:

| File | What it is |
|---|---|
| `us_daily_anomaly.parquet` | daily US temperature anomaly + GWDD (`date, wtmean, norm_t, anom_t, gwdd18, norm_gwdd, gwdd_anom`) — the weather signal |
| `eia_weekly_inventory.xlsx` | the EIA weekly Lower-48 storage series (raw workbook, col C = week-ending Friday, col D = Bcf) |
| `weekly_storage_nowcast_hist.csv` | a history of the storage nowcast used by the "before the release" rule (`run_date, week_ending, pred_delta, pred_level, …`) |

## Not shipped

The **futures price data** (Barchart) and the **ingested zipline bundle** are not
redistributed. They are what a full re-run of `../strategies/` needs; the shipped
`../results/Results.xlsx` lets you rebuild every figure and table in the top-level
README without them.

# Strategy metrics

_Generated from `results/Results.xlsx` (`Sheet1` for the metrics, `Sheet2` for
the t-test). One row per run; the current book is
`NG Combined Signals 50/16/34 (scaled)`. The t-statistic is the daily Sharpe
times the square root of the number of observations; the p-value is the
one-tailed normal probability. These are the same values used in the README
tables._

| Run | Ann return | Ann vol | Sharpe | Max DD | t-stat | p (1-tailed) | Turnover | Costs (SR) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 16/64 simple | 4.63% | 19.21% | 0.33 | -43.30% | 1.73 | 4.21% | — | — |
| EWMAC 2/8 | -0.58% | 19.53% | 0.07 | -58.37% | 0.35 | 36.22% | 109.0x | 0.094 |
| EWMAC 4/16 | 1.04% | 19.28% | 0.15 | -56.46% | 0.78 | 21.75% | 55.9x | 0.056 |
| EWMAC 8/32 | 3.72% | 19.00% | 0.29 | -58.92% | 1.49 | 6.76% | 29.5x | 0.035 |
| EWMAC 16/64 | 5.40% | 19.16% | 0.37 | -56.49% | 1.93 | 2.70% | 16.4x | 0.024 |
| EWMAC 32/128 | 5.03% | 19.21% | 0.35 | -47.30% | 1.83 | 3.37% | 10.1x | 0.018 |
| EWMAC 64/256 | 4.38% | 20.25% | 0.31 | -50.23% | 1.63 | 5.16% | 7.1x | 0.017 |
| carry simple summer | 2.07% | 18.24% | 0.20 | -59.47% | 1.06 | 14.47% | 16.0x | 0.020 |
| carry simple all year | -0.04% | 25.32% | 0.12 | -78.95% | 0.65 | 25.81% | 14.7x | 0.035 |
| carry adjusted summer | 1.92% | 14.59% | 0.20 | -61.05% | 1.06 | 14.50% | 12.6x | 0.020 |
| carry adjusted all year | 1.18% | 21.31% | 0.16 | -73.82% | 0.84 | 20.01% | 12.2x | 0.028 |
| weather GWDD SMA7 Nov-Apr | 4.33% | 11.84% | 0.42 | -38.16% | 2.17 | 1.51% | 74.2x | 0.054 |
| inventory EIA winter | 6.48% | 16.59% | 0.46 | -43.53% | 2.40 | 0.82% | 13.8x | 0.013 |
| inventory nowcast winter | 6.63% | 16.08% | 0.48 | -43.90% | 2.38 | 0.85% | 13.6x | 0.014 |
| FFV Range | 9.32% | 25.48% | 0.48 | -53.81% | 2.48 | 0.65% | — | 0.031 |
| gas nowcast v2 winter | 6.83% | 16.30% | 0.49 | -43.21% | 2.42 | 0.77% | 13.2x | 0.014 |
| gas nowcast v3 winter | 6.91% | 16.29% | 0.49 | -41.76% | 2.44 | 0.73% | 13.9x | 0.014 |
| NG Combined 50/16/34 (off3) | 5.35% | 12.44% | 0.48 | -31.21% | 2.50 | 0.61% | — | — |
| NG Combined Signals 50/16/34 (scaled) | 9.13% | 20.18% | 0.53 | -36.75% | 2.78 | 0.27% | 27.6x | 0.040 |

## Current book

`NG Combined Signals 50/16/34 (scaled)` — trend 50 % / carry 16 % / fundamentals
34 %, with FFV at 14 %. The book combines the daily signed convictions, applies
the causal forecast diversification multiplier, then sizes, rolls and costs the
result exactly like a single rule.

| Metric | Value |
|---|---:|
| Annual return (net) | 9.13% |
| Annual volatility | 20.18% |
| Sharpe ratio | 0.53 |
| Max drawdown | -36.75% |
| t-statistic | 2.78 |
| p-value (one-tailed) | 0.27% |
| Annual turnover | 27.6x |
| Costs (Sharpe units) | 0.040 |

"""natgas — a small, reusable commodity-futures backtesting toolkit.

Modules
-------
data        load the continuous-future price library (zipline 'mul'-adjusted)
costs       Carver-style cost accounting (commission / slippage / roll legs)
alpha_beta  alpha/beta regression of a strategy against a benchmark
portfolio   combine strategy streams into a book + correlations
reporting   the shared two-page strategy charts (market/exposure + risk)

The per-strategy backtests that use this package live in ``../strategies/``.
"""
__version__ = "0.1.0"

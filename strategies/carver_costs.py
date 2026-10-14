"""Compatibility shim: the backtests import these names; the cleaned
implementation lives in the `natgas` package.
"""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from natgas.costs import *  # noqa: F401,F403

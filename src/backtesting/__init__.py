"""
Backtesting Module

Test strategies on historical data.
"""
from .engine import BacktestEngine, BacktestConfig
from .result import BacktestResult, Trade
from .visualization import BacktestVisualizer

__all__ = [
    "BacktestEngine",
    "BacktestConfig",
    "BacktestResult",
    "Trade",
    "BacktestVisualizer",
]

"""
Strategy Module

Define trading strategies based on indicator signals.
"""
from .base import Strategy, StrategySignal, ActionType
from .manager import StrategyManager
from .supertrend_cross import SupertrendCrossStrategy

__all__ = [
    "Strategy",
    "StrategySignal",
    "ActionType",
    "StrategyManager",
    "SupertrendCrossStrategy",
]

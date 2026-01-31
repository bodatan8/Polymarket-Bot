"""
Signal generation and data feeds.

This module provides:
- RealTimePriceFeed: WebSocket-based real-time crypto price feeds
- VolumeDetector: Volume anomaly detection
- SignalAggregator: Combines multiple signals into trading decisions
- LivePredictor: Mean-reversion signal generator for 7-min predictions
- PaperTrader: Paper trading simulation with risk management
"""
from .price_feed import RealTimePriceFeed
from .volume_detector import VolumeDetector
from .aggregator import SignalAggregator
from .live_predictor import LivePredictor, PredictionSignal, PredictionDirection
from .paper_trader import PaperTrader, PaperPosition, TradeType, PaperRiskLimits

__all__ = [
    "RealTimePriceFeed",
    "VolumeDetector", 
    "SignalAggregator",
    "LivePredictor",
    "PredictionSignal",
    "PredictionDirection",
    "PaperTrader",
    "PaperPosition",
    "TradeType",
    "PaperRiskLimits",
]

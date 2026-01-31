"""
Centralized configuration for the 15-minute market maker.
"""
from dataclasses import dataclass
from typing import Tuple


@dataclass
class TradingConfig:
    """Trading configuration."""
    # Base settings
    base_bet_size_usd: float = 10.0
    cycle_interval_seconds: int = 15
    bankroll_usd: float = 50000.0  # Consistent bankroll
    
    # Mode selection
    high_frequency_mode: bool = True
    
    # High-frequency mode settings
    hf_min_edge: float = 0.001  # 0.1% minimum edge (very aggressive)
    hf_min_confidence: float = 0.25  # 25% confidence (very low - match active traders)
    hf_min_signal_strength: float = 0.01  # 1% signal strength (minimal)
    hf_bet_size_multiplier: float = 0.5  # 0.5x base size
    hf_max_positions_per_cycle: int = 20
    hf_timing_window: Tuple[int, int] = (30, 1800)  # 30s to 30min (catch available markets, avoid very far out)
    hf_min_market_uncertainty: float = 0.01  # 1% from 50% (more permissive)
    hf_min_polymarket_volume: float = 0.0  # No minimum volume requirement
    hf_cycle_interval: int = 5  # 5 seconds
    
    # Quality mode settings
    quality_min_edge_multiplier: float = 1.0  # Use dynamic edge calculator
    quality_timing_window: Tuple[int, int] = (60, 420)  # 1-7 minutes
    quality_cycle_interval: int = 15  # 15 seconds
    
    # Aggressive sizing (applies to both modes)
    aggressive_sizing_enabled: bool = True
    aggressive_edge_threshold: float = 0.05  # 5% edge
    aggressive_confidence_threshold: float = 0.70  # 70% confidence
    max_aggressive_size_percent: float = 0.30  # 30% of bankroll
    edge_multiplier: float = 2.0  # Scale bet size with edge
    
    # Risk limits (consistent across all modes)
    max_daily_loss_usd: float = 5000.0
    max_drawdown_percent: float = 25.0
    max_position_size_usd: float = 15000.0  # 30% of $50k bankroll
    min_position_size_usd: float = 0.50
    max_total_exposure_usd: float = 40000.0  # 80% of bankroll
    max_positions_per_asset: int = 5
    max_open_positions: int = 30
    max_correlation_exposure: float = 0.7
    stop_loss_percent: float = 50.0


# Global configuration instance
config = TradingConfig()

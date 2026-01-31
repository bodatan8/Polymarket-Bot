"""
Supertrend Cross Strategy

A multi-timeframe strategy using Supertrend indicator.

Logic:
- BUY: Supertrend flips bullish on fast timeframe AND bullish on slow timeframe
- SELL: Supertrend flips bearish on fast timeframe AND bearish on slow timeframe
- HOLD: Otherwise

This is a trend-following strategy that waits for alignment across timeframes.
"""
import logging
from typing import Optional

from src.indicators import IndicatorManager, SignalType
from .base import Strategy, StrategySignal, ActionType

logger = logging.getLogger(__name__)


class SupertrendCrossStrategy(Strategy):
    """
    Multi-timeframe Supertrend cross strategy.
    
    Parameters:
        fast_timeframe: Fast timeframe for entry signals (default: "1m")
        slow_timeframe: Slow timeframe for trend confirmation (default: "1h")
        indicator_name: Name of supertrend indicator (default: "supertrend")
        require_flip: Require flip on fast timeframe (default: True)
    
    Example:
        strategy = SupertrendCrossStrategy(
            fast_timeframe="1m",
            slow_timeframe="1h"
        )
        
        # When 1m supertrend flips bullish AND 1h supertrend is bullish -> BUY
        # When 1m supertrend flips bearish AND 1h supertrend is bearish -> SELL
    """
    
    def __init__(
        self,
        name: str = "supertrend_cross",
        fast_timeframe: str = "1m",
        slow_timeframe: str = "1h",
        indicator_name: str = "supertrend",
        require_flip: bool = True
    ):
        """
        Initialize Supertrend Cross strategy.
        
        Args:
            name: Strategy name
            fast_timeframe: Fast timeframe (entry)
            slow_timeframe: Slow timeframe (confirmation)
            indicator_name: Name of supertrend indicator
            require_flip: If True, requires flip on fast TF; if False, just needs alignment
        """
        super().__init__(
            name,
            fast_timeframe=fast_timeframe,
            slow_timeframe=slow_timeframe,
            indicator_name=indicator_name,
            require_flip=require_flip
        )
        
        self.fast_timeframe = fast_timeframe
        self.slow_timeframe = slow_timeframe
        self.indicator_name = indicator_name
        self.require_flip = require_flip
        
        # Required indicators
        self.required_indicators = [
            (indicator_name, fast_timeframe),
            (indicator_name, slow_timeframe),
        ]
        
        self.required_timeframes = [fast_timeframe, slow_timeframe]
    
    def evaluate(
        self,
        manager: IndicatorManager,
        asset: str
    ) -> StrategySignal:
        """
        Evaluate Supertrend cross strategy.
        
        Returns:
            BUY when fast flips bullish + slow is bullish
            SELL when fast flips bearish + slow is bearish
            HOLD otherwise
        """
        # Get signals
        fast_signal = manager.get_signal(self.indicator_name, self.fast_timeframe, asset)
        slow_signal = manager.get_signal(self.indicator_name, self.slow_timeframe, asset)
        
        # Default hold
        default_signal = StrategySignal(
            action=ActionType.HOLD,
            strength=0.0,
            asset=asset,
            reason="No signal",
            indicators={
                f"{self.indicator_name}_{self.fast_timeframe}": fast_signal.signal_type.value if fast_signal else None,
                f"{self.indicator_name}_{self.slow_timeframe}": slow_signal.signal_type.value if slow_signal else None,
            }
        )
        
        if not fast_signal or not slow_signal:
            default_signal.reason = "Missing indicator signals"
            return default_signal
        
        # Check for bullish entry
        fast_bullish = (
            fast_signal.signal_type == SignalType.FLIP_BULLISH
            if self.require_flip
            else fast_signal.is_bullish
        )
        slow_bullish = slow_signal.is_bullish
        
        if fast_bullish and slow_bullish:
            strength = (fast_signal.strength + slow_signal.strength) / 2
            
            reason = (
                f"{self.fast_timeframe} Supertrend {'flipped' if self.require_flip else 'is'} bullish, "
                f"{self.slow_timeframe} Supertrend is bullish"
            )
            
            return StrategySignal(
                action=ActionType.BUY,
                strength=strength,
                asset=asset,
                reason=reason,
                indicators={
                    f"{self.indicator_name}_{self.fast_timeframe}": {
                        "type": fast_signal.signal_type.value,
                        "value": fast_signal.value,
                        "strength": fast_signal.strength,
                    },
                    f"{self.indicator_name}_{self.slow_timeframe}": {
                        "type": slow_signal.signal_type.value,
                        "value": slow_signal.value,
                        "strength": slow_signal.strength,
                    },
                },
                metadata={
                    "fast_timeframe": self.fast_timeframe,
                    "slow_timeframe": self.slow_timeframe,
                }
            )
        
        # Check for bearish entry
        fast_bearish = (
            fast_signal.signal_type == SignalType.FLIP_BEARISH
            if self.require_flip
            else fast_signal.is_bearish
        )
        slow_bearish = slow_signal.is_bearish
        
        if fast_bearish and slow_bearish:
            strength = (fast_signal.strength + slow_signal.strength) / 2
            
            reason = (
                f"{self.fast_timeframe} Supertrend {'flipped' if self.require_flip else 'is'} bearish, "
                f"{self.slow_timeframe} Supertrend is bearish"
            )
            
            return StrategySignal(
                action=ActionType.SELL,
                strength=strength,
                asset=asset,
                reason=reason,
                indicators={
                    f"{self.indicator_name}_{self.fast_timeframe}": {
                        "type": fast_signal.signal_type.value,
                        "value": fast_signal.value,
                        "strength": fast_signal.strength,
                    },
                    f"{self.indicator_name}_{self.slow_timeframe}": {
                        "type": slow_signal.signal_type.value,
                        "value": slow_signal.value,
                        "strength": slow_signal.strength,
                    },
                },
                metadata={
                    "fast_timeframe": self.fast_timeframe,
                    "slow_timeframe": self.slow_timeframe,
                }
            )
        
        # No signal - provide context
        default_signal.reason = (
            f"{self.fast_timeframe}: {fast_signal.signal_type.value}, "
            f"{self.slow_timeframe}: {slow_signal.signal_type.value} - "
            f"Waiting for alignment"
        )
        default_signal.strength = 0.3
        
        return default_signal


class SupertrendWithSRStrategy(Strategy):
    """
    Supertrend + Support/Resistance strategy.
    
    Combines trend direction with S/R levels for better entries.
    
    Logic:
    - BUY: Supertrend bullish AND price near support
    - SELL: Supertrend bearish AND price near resistance
    """
    
    def __init__(
        self,
        name: str = "supertrend_sr",
        timeframe: str = "1h",
        sr_distance_threshold: float = 1.0  # % distance to S/R
    ):
        super().__init__(
            name,
            timeframe=timeframe,
            sr_distance_threshold=sr_distance_threshold
        )
        
        self.timeframe = timeframe
        self.sr_distance_threshold = sr_distance_threshold
        
        self.required_indicators = [
            ("supertrend", timeframe),
            ("sr", timeframe),
        ]
        self.required_timeframes = [timeframe]
    
    def evaluate(
        self,
        manager: IndicatorManager,
        asset: str
    ) -> StrategySignal:
        """Evaluate Supertrend + S/R strategy."""
        st_signal = manager.get_signal("supertrend", self.timeframe, asset)
        sr_signal = manager.get_signal("sr", self.timeframe, asset)
        
        if not st_signal or not sr_signal:
            return StrategySignal(
                action=ActionType.HOLD,
                strength=0.0,
                asset=asset,
                reason="Missing signals"
            )
        
        # Get S/R metadata
        sr_distance = sr_signal.metadata.get("distance", 100)
        sr_nearest = sr_signal.metadata.get("nearest", "unknown")
        
        # Bullish: Supertrend up + near support
        if (st_signal.is_bullish and 
            sr_nearest == "support" and 
            sr_distance < self.sr_distance_threshold):
            
            return StrategySignal(
                action=ActionType.BUY,
                strength=(st_signal.strength + sr_signal.strength) / 2,
                asset=asset,
                reason=f"Supertrend bullish + near support ({sr_distance:.1f}%)",
                indicators={
                    "supertrend": st_signal.signal_type.value,
                    "sr": sr_signal.signal_type.value,
                }
            )
        
        # Bearish: Supertrend down + near resistance
        if (st_signal.is_bearish and 
            sr_nearest == "resistance" and 
            sr_distance < self.sr_distance_threshold):
            
            return StrategySignal(
                action=ActionType.SELL,
                strength=(st_signal.strength + sr_signal.strength) / 2,
                asset=asset,
                reason=f"Supertrend bearish + near resistance ({sr_distance:.1f}%)",
                indicators={
                    "supertrend": st_signal.signal_type.value,
                    "sr": sr_signal.signal_type.value,
                }
            )
        
        return StrategySignal(
            action=ActionType.HOLD,
            strength=0.3,
            asset=asset,
            reason="Waiting for trend + S/R alignment",
            indicators={
                "supertrend": st_signal.signal_type.value,
                "sr_nearest": sr_nearest,
                "sr_distance": sr_distance,
            }
        )

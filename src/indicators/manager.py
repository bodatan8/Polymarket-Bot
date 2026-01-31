"""
Indicator Manager

Manages multiple indicators across multiple timeframes.
Provides a unified interface for calculating and querying indicator values.
"""
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

import pandas as pd

from .base import Indicator, IndicatorSignal, SignalType

logger = logging.getLogger(__name__)


@dataclass
class IndicatorConfig:
    """Configuration for a registered indicator."""
    indicator: Indicator
    timeframe: str
    asset: str = "default"


@dataclass
class IndicatorState:
    """Current state of an indicator."""
    name: str
    timeframe: str
    asset: str
    signal: IndicatorSignal
    data: Optional[pd.DataFrame] = None
    updated_at: Optional[pd.Timestamp] = None


class IndicatorManager:
    """
    Manages indicators across multiple timeframes and assets.
    
    Usage:
        manager = IndicatorManager()
        
        # Register indicators
        manager.add_indicator(SupertrendIndicator(period=10), "1m", "BTCUSDT")
        manager.add_indicator(SupertrendIndicator(period=10), "1h", "BTCUSDT")
        manager.add_indicator(SupportResistanceIndicator(), "1h", "BTCUSDT")
        
        # Update with new data
        manager.update("BTCUSDT", "1m", ohlcv_data_1m)
        manager.update("BTCUSDT", "1h", ohlcv_data_1h)
        
        # Get signals
        st_1m = manager.get_signal("supertrend", "1m", "BTCUSDT")
        st_1h = manager.get_signal("supertrend", "1h", "BTCUSDT")
        
        # Get all signals for an asset
        all_signals = manager.get_all_signals("BTCUSDT")
    """
    
    def __init__(self):
        """Initialize indicator manager."""
        # indicators[(name, timeframe, asset)] = IndicatorConfig
        self._indicators: Dict[tuple, IndicatorConfig] = {}
        
        # Cached data and states
        self._data: Dict[tuple, pd.DataFrame] = {}  # (asset, timeframe) -> DataFrame
        self._states: Dict[tuple, IndicatorState] = {}  # (name, timeframe, asset) -> State
    
    def add_indicator(
        self,
        indicator: Indicator,
        timeframe: str,
        asset: str = "default"
    ) -> None:
        """
        Register an indicator for a specific timeframe and asset.
        
        Args:
            indicator: Indicator instance
            timeframe: Timeframe (e.g., "1m", "1h", "1d")
            asset: Asset symbol (e.g., "BTCUSDT")
        """
        key = (indicator.name, timeframe, asset)
        self._indicators[key] = IndicatorConfig(
            indicator=indicator,
            timeframe=timeframe,
            asset=asset
        )
        logger.info(f"Registered {indicator.name} for {asset} {timeframe}")
    
    def remove_indicator(
        self,
        name: str,
        timeframe: str,
        asset: str = "default"
    ) -> bool:
        """
        Remove a registered indicator.
        
        Returns:
            True if removed, False if not found
        """
        key = (name, timeframe, asset)
        if key in self._indicators:
            del self._indicators[key]
            if key in self._states:
                del self._states[key]
            return True
        return False
    
    def update(
        self,
        asset: str,
        timeframe: str,
        data: pd.DataFrame
    ) -> Dict[str, IndicatorSignal]:
        """
        Update indicators with new data.
        
        Args:
            asset: Asset symbol
            timeframe: Timeframe
            data: OHLCV DataFrame
        
        Returns:
            Dict of indicator_name -> signal
        """
        # Cache raw data
        data_key = (asset, timeframe)
        self._data[data_key] = data
        
        signals = {}
        
        # Find all indicators for this asset/timeframe
        for key, config in self._indicators.items():
            name, tf, a = key
            
            if tf == timeframe and a == asset:
                try:
                    # Calculate indicator
                    indicator_data = config.indicator.calculate(data)
                    
                    # Get signal
                    signal = config.indicator.get_signal(indicator_data)
                    
                    # Update state
                    self._states[key] = IndicatorState(
                        name=name,
                        timeframe=timeframe,
                        asset=asset,
                        signal=signal,
                        data=indicator_data,
                        updated_at=pd.Timestamp.now()
                    )
                    
                    signals[name] = signal
                    
                except Exception as e:
                    logger.error(f"Error updating {name} for {asset} {timeframe}: {e}")
        
        return signals
    
    def get_signal(
        self,
        name: str,
        timeframe: str,
        asset: str = "default"
    ) -> Optional[IndicatorSignal]:
        """
        Get the current signal for an indicator.
        
        Args:
            name: Indicator name
            timeframe: Timeframe
            asset: Asset symbol
        
        Returns:
            IndicatorSignal or None if not found
        """
        key = (name, timeframe, asset)
        state = self._states.get(key)
        return state.signal if state else None
    
    def get_state(
        self,
        name: str,
        timeframe: str,
        asset: str = "default"
    ) -> Optional[IndicatorState]:
        """Get full state for an indicator."""
        key = (name, timeframe, asset)
        return self._states.get(key)
    
    def get_all_signals(
        self,
        asset: str = "default"
    ) -> Dict[str, Dict[str, IndicatorSignal]]:
        """
        Get all signals for an asset.
        
        Returns:
            Dict of timeframe -> {indicator_name -> signal}
        """
        result: Dict[str, Dict[str, IndicatorSignal]] = {}
        
        for key, state in self._states.items():
            name, timeframe, a = key
            
            if a == asset:
                if timeframe not in result:
                    result[timeframe] = {}
                result[timeframe][name] = state.signal
        
        return result
    
    def get_data(
        self,
        asset: str,
        timeframe: str
    ) -> Optional[pd.DataFrame]:
        """Get cached OHLCV data for an asset/timeframe."""
        return self._data.get((asset, timeframe))
    
    def get_indicator_data(
        self,
        name: str,
        timeframe: str,
        asset: str = "default"
    ) -> Optional[pd.DataFrame]:
        """Get calculated indicator data (OHLCV + indicator columns)."""
        key = (name, timeframe, asset)
        state = self._states.get(key)
        return state.data if state else None
    
    def list_indicators(
        self,
        asset: Optional[str] = None,
        timeframe: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List registered indicators.
        
        Args:
            asset: Filter by asset (optional)
            timeframe: Filter by timeframe (optional)
        
        Returns:
            List of indicator info dicts
        """
        result = []
        
        for key, config in self._indicators.items():
            name, tf, a = key
            
            if asset and a != asset:
                continue
            if timeframe and tf != timeframe:
                continue
            
            state = self._states.get(key)
            
            result.append({
                "name": name,
                "timeframe": tf,
                "asset": a,
                "indicator_type": config.indicator.__class__.__name__,
                "params": config.indicator.params,
                "has_signal": state is not None,
                "signal_type": state.signal.signal_type.value if state else None,
            })
        
        return result
    
    def get_combined_signal(
        self,
        asset: str,
        indicators: List[tuple]  # [(name, timeframe), ...]
    ) -> Dict[str, Any]:
        """
        Get combined analysis from multiple indicators.
        
        Args:
            asset: Asset symbol
            indicators: List of (name, timeframe) tuples
        
        Returns:
            Dict with combined analysis
        """
        signals = []
        
        for name, timeframe in indicators:
            signal = self.get_signal(name, timeframe, asset)
            if signal:
                signals.append({
                    "name": name,
                    "timeframe": timeframe,
                    "signal": signal
                })
        
        if not signals:
            return {
                "direction": "neutral",
                "strength": 0.0,
                "signals": []
            }
        
        # Count bullish/bearish
        bullish = sum(1 for s in signals if s["signal"].is_bullish)
        bearish = sum(1 for s in signals if s["signal"].is_bearish)
        
        # Count flips (stronger signals)
        flips_bullish = sum(1 for s in signals if s["signal"].signal_type == SignalType.FLIP_BULLISH)
        flips_bearish = sum(1 for s in signals if s["signal"].signal_type == SignalType.FLIP_BEARISH)
        
        # Average strength
        avg_strength = sum(s["signal"].strength for s in signals) / len(signals)
        
        # Determine overall direction
        if bullish > bearish:
            direction = "bullish"
        elif bearish > bullish:
            direction = "bearish"
        else:
            direction = "neutral"
        
        # Boost strength if flips align
        if flips_bullish > 0 and direction == "bullish":
            avg_strength = min(1.0, avg_strength + 0.2)
        if flips_bearish > 0 and direction == "bearish":
            avg_strength = min(1.0, avg_strength + 0.2)
        
        return {
            "direction": direction,
            "strength": avg_strength,
            "bullish_count": bullish,
            "bearish_count": bearish,
            "flips_bullish": flips_bullish,
            "flips_bearish": flips_bearish,
            "signals": [
                {
                    "name": s["name"],
                    "timeframe": s["timeframe"],
                    "type": s["signal"].signal_type.value,
                    "strength": s["signal"].strength,
                }
                for s in signals
            ]
        }

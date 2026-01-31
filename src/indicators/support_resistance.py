"""
Support and Resistance Indicator

Identifies key price levels where price has historically:
- Found support (bounced up from)
- Found resistance (bounced down from)

Methods:
1. Pivot points (swing highs/lows)
2. Volume profile (high volume price levels)
3. Price clustering (frequently visited levels)
"""
import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .base import Indicator, IndicatorSignal, SignalType

logger = logging.getLogger(__name__)


@dataclass
class PriceLevel:
    """A support or resistance level."""
    price: float
    level_type: str  # "support" or "resistance"
    strength: float  # 0-1, how many times tested
    touches: int  # Number of times price touched this level
    last_touch: int  # Index of last touch


class SupportResistanceIndicator(Indicator):
    """
    Identifies support and resistance levels.
    
    Parameters:
        window: Lookback window for finding pivots (default: 20)
        tolerance: Price tolerance for level clustering (default: 0.002 = 0.2%)
        min_touches: Minimum touches to confirm a level (default: 2)
    
    Columns added:
        - {name}_nearest_support: Nearest support level below current price
        - {name}_nearest_resistance: Nearest resistance level above current price
        - {name}_distance_support: Distance to nearest support (%)
        - {name}_distance_resistance: Distance to nearest resistance (%)
    """
    
    def __init__(
        self,
        name: str = "sr",
        window: int = 20,
        tolerance: float = 0.002,
        min_touches: int = 2
    ):
        """
        Initialize Support/Resistance indicator.
        
        Args:
            name: Indicator name
            window: Pivot point window
            tolerance: Price clustering tolerance
            min_touches: Minimum touches for level
        """
        super().__init__(
            name,
            window=window,
            tolerance=tolerance,
            min_touches=min_touches
        )
        self.window = window
        self.tolerance = tolerance
        self.min_touches = min_touches
        
        # Cache for levels
        self._levels: List[PriceLevel] = []
    
    def _find_pivot_highs(self, data: pd.DataFrame) -> pd.Series:
        """Find local maxima (pivot highs)."""
        highs = data["high"]
        
        # A pivot high has lower highs on both sides
        pivot_highs = pd.Series(index=data.index, dtype=float)
        pivot_highs[:] = np.nan
        
        for i in range(self.window, len(data) - self.window):
            window_before = highs.iloc[i - self.window:i]
            window_after = highs.iloc[i + 1:i + self.window + 1]
            current = highs.iloc[i]
            
            if current > window_before.max() and current > window_after.max():
                pivot_highs.iloc[i] = current
        
        return pivot_highs
    
    def _find_pivot_lows(self, data: pd.DataFrame) -> pd.Series:
        """Find local minima (pivot lows)."""
        lows = data["low"]
        
        pivot_lows = pd.Series(index=data.index, dtype=float)
        pivot_lows[:] = np.nan
        
        for i in range(self.window, len(data) - self.window):
            window_before = lows.iloc[i - self.window:i]
            window_after = lows.iloc[i + 1:i + self.window + 1]
            current = lows.iloc[i]
            
            if current < window_before.min() and current < window_after.min():
                pivot_lows.iloc[i] = current
        
        return pivot_lows
    
    def _cluster_levels(self, prices: List[float]) -> List[Tuple[float, int]]:
        """
        Cluster similar price levels together.
        
        Returns:
            List of (average_price, count) tuples
        """
        if not prices:
            return []
        
        prices = sorted(prices)
        clusters = []
        current_cluster = [prices[0]]
        
        for price in prices[1:]:
            # Check if price is within tolerance of cluster
            cluster_avg = sum(current_cluster) / len(current_cluster)
            if abs(price - cluster_avg) / cluster_avg <= self.tolerance:
                current_cluster.append(price)
            else:
                clusters.append((
                    sum(current_cluster) / len(current_cluster),
                    len(current_cluster)
                ))
                current_cluster = [price]
        
        # Add last cluster
        clusters.append((
            sum(current_cluster) / len(current_cluster),
            len(current_cluster)
        ))
        
        return clusters
    
    def _identify_levels(self, data: pd.DataFrame) -> List[PriceLevel]:
        """Identify support and resistance levels."""
        pivot_highs = self._find_pivot_highs(data)
        pivot_lows = self._find_pivot_lows(data)
        
        # Collect all pivot points
        resistance_prices = pivot_highs.dropna().tolist()
        support_prices = pivot_lows.dropna().tolist()
        
        # Cluster similar levels
        resistance_clusters = self._cluster_levels(resistance_prices)
        support_clusters = self._cluster_levels(support_prices)
        
        levels = []
        
        # Create resistance levels
        for price, touches in resistance_clusters:
            if touches >= self.min_touches:
                levels.append(PriceLevel(
                    price=price,
                    level_type="resistance",
                    strength=min(1.0, touches / 5),  # Max strength at 5 touches
                    touches=touches,
                    last_touch=len(data) - 1  # Simplified
                ))
        
        # Create support levels
        for price, touches in support_clusters:
            if touches >= self.min_touches:
                levels.append(PriceLevel(
                    price=price,
                    level_type="support",
                    strength=min(1.0, touches / 5),
                    touches=touches,
                    last_touch=len(data) - 1
                ))
        
        return sorted(levels, key=lambda x: x.price)
    
    def calculate(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate support and resistance levels.
        
        Args:
            data: OHLCV DataFrame
        
        Returns:
            DataFrame with S/R columns added
        """
        if not self.validate_data(data) or len(data) < self.window * 2:
            logger.warning("Insufficient data for S/R calculation")
            return data
        
        df = data.copy()
        
        # Identify levels
        self._levels = self._identify_levels(df)
        
        # Calculate nearest levels for each bar
        support_col = f"{self.name}_nearest_support"
        resistance_col = f"{self.name}_nearest_resistance"
        dist_support_col = f"{self.name}_distance_support"
        dist_resistance_col = f"{self.name}_distance_resistance"
        
        df[support_col] = np.nan
        df[resistance_col] = np.nan
        df[dist_support_col] = np.nan
        df[dist_resistance_col] = np.nan
        
        for i in range(len(df)):
            close = df["close"].iloc[i]
            
            # Find nearest support (below current price)
            supports = [l for l in self._levels if l.level_type == "support" and l.price < close]
            if supports:
                nearest_support = max(supports, key=lambda x: x.price)
                df.iloc[i, df.columns.get_loc(support_col)] = nearest_support.price
                df.iloc[i, df.columns.get_loc(dist_support_col)] = (close - nearest_support.price) / close * 100
            
            # Find nearest resistance (above current price)
            resistances = [l for l in self._levels if l.level_type == "resistance" and l.price > close]
            if resistances:
                nearest_resistance = min(resistances, key=lambda x: x.price)
                df.iloc[i, df.columns.get_loc(resistance_col)] = nearest_resistance.price
                df.iloc[i, df.columns.get_loc(dist_resistance_col)] = (nearest_resistance.price - close) / close * 100
        
        return df
    
    def get_signal(self, data: pd.DataFrame) -> IndicatorSignal:
        """
        Generate signal based on proximity to S/R levels.
        
        - BULLISH: Price near strong support
        - BEARISH: Price near strong resistance
        - FLIP_BULLISH: Price just bounced off support
        - FLIP_BEARISH: Price just rejected at resistance
        """
        dist_support_col = f"{self.name}_distance_support"
        dist_resistance_col = f"{self.name}_distance_resistance"
        
        if dist_support_col not in data.columns or len(data) < 2:
            return IndicatorSignal(
                signal_type=SignalType.NEUTRAL,
                value=0.0,
                strength=0.0
            )
        
        current_close = float(data["close"].iloc[-1])
        dist_support = data[dist_support_col].iloc[-1]
        dist_resistance = data[dist_resistance_col].iloc[-1]
        
        # Handle NaN
        dist_support = dist_support if pd.notna(dist_support) else 100
        dist_resistance = dist_resistance if pd.notna(dist_resistance) else 100
        
        # Check for bounce off support (price was falling, now rising)
        price_change = (data["close"].iloc[-1] - data["close"].iloc[-2]) / data["close"].iloc[-2]
        prev_change = (data["close"].iloc[-2] - data["close"].iloc[-3]) / data["close"].iloc[-3] if len(data) > 2 else 0
        
        # Near support and bouncing up
        if dist_support < 1.0 and price_change > 0 and prev_change < 0:
            return IndicatorSignal(
                signal_type=SignalType.FLIP_BULLISH,
                value=current_close,
                strength=1.0 - dist_support / 2,
                metadata={"trigger": "support_bounce", "distance": dist_support}
            )
        
        # Near resistance and rejecting
        if dist_resistance < 1.0 and price_change < 0 and prev_change > 0:
            return IndicatorSignal(
                signal_type=SignalType.FLIP_BEARISH,
                value=current_close,
                strength=1.0 - dist_resistance / 2,
                metadata={"trigger": "resistance_rejection", "distance": dist_resistance}
            )
        
        # General proximity signals
        if dist_support < dist_resistance:
            return IndicatorSignal(
                signal_type=SignalType.BULLISH,
                value=current_close,
                strength=max(0.3, 1.0 - dist_support / 5),
                metadata={"nearest": "support", "distance": dist_support}
            )
        else:
            return IndicatorSignal(
                signal_type=SignalType.BEARISH,
                value=current_close,
                strength=max(0.3, 1.0 - dist_resistance / 5),
                metadata={"nearest": "resistance", "distance": dist_resistance}
            )
    
    def get_levels(self) -> List[PriceLevel]:
        """Get all identified S/R levels."""
        return self._levels
    
    def get_support_levels(self) -> List[PriceLevel]:
        """Get only support levels."""
        return [l for l in self._levels if l.level_type == "support"]
    
    def get_resistance_levels(self) -> List[PriceLevel]:
        """Get only resistance levels."""
        return [l for l in self._levels if l.level_type == "resistance"]

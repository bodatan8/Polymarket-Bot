"""
Liquidity Indicator

Identifies liquidity zones where stop losses are likely clustered.
These zones often act as magnets for price, as market makers hunt stops.

Methods:
1. Swing point liquidity (above swing highs, below swing lows)
2. Equal highs/lows (obvious stop-loss levels)
3. Round numbers (psychological levels)
4. Volume imbalances (low volume zones = liquidity voids)
"""
import logging
from typing import List, Optional
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .base import Indicator, IndicatorSignal, SignalType

logger = logging.getLogger(__name__)


@dataclass
class LiquidityZone:
    """A zone of accumulated liquidity."""
    price_low: float
    price_high: float
    zone_type: str  # "buy_side" (above highs) or "sell_side" (below lows)
    strength: float  # 0-1, estimated liquidity
    swept: bool  # Has price already swept this zone?


class LiquidityIndicator(Indicator):
    """
    Identifies liquidity zones for smart money concepts.
    
    Parameters:
        window: Lookback for finding swing points (default: 10)
        zone_buffer: Buffer above/below swing points (default: 0.001 = 0.1%)
    
    Columns added:
        - {name}_buy_liquidity: Nearest buy-side liquidity above
        - {name}_sell_liquidity: Nearest sell-side liquidity below
        - {name}_distance_buy: Distance to buy-side liquidity (%)
        - {name}_distance_sell: Distance to sell-side liquidity (%)
        - {name}_in_zone: Whether price is currently in a liquidity zone
    """
    
    def __init__(
        self,
        name: str = "liquidity",
        window: int = 10,
        zone_buffer: float = 0.001
    ):
        """
        Initialize Liquidity indicator.
        
        Args:
            name: Indicator name
            window: Swing point window
            zone_buffer: Buffer for zone boundaries
        """
        super().__init__(name, window=window, zone_buffer=zone_buffer)
        self.window = window
        self.zone_buffer = zone_buffer
        
        self._zones: List[LiquidityZone] = []
    
    def _find_swing_highs(self, data: pd.DataFrame) -> List[float]:
        """Find swing high prices (local maxima)."""
        highs = data["high"].values
        swing_highs = []
        
        for i in range(self.window, len(highs) - self.window):
            window_before = highs[i - self.window:i]
            window_after = highs[i + 1:i + self.window + 1]
            current = highs[i]
            
            if current > max(window_before) and current > max(window_after):
                swing_highs.append(current)
        
        return swing_highs
    
    def _find_swing_lows(self, data: pd.DataFrame) -> List[float]:
        """Find swing low prices (local minima)."""
        lows = data["low"].values
        swing_lows = []
        
        for i in range(self.window, len(lows) - self.window):
            window_before = lows[i - self.window:i]
            window_after = lows[i + 1:i + self.window + 1]
            current = lows[i]
            
            if current < min(window_before) and current < min(window_after):
                swing_lows.append(current)
        
        return swing_lows
    
    def _find_equal_levels(self, prices: List[float], tolerance: float = 0.001) -> List[float]:
        """Find clusters of similar prices (equal highs/lows)."""
        if len(prices) < 2:
            return []
        
        equal_levels = []
        prices = sorted(prices)
        
        for i, price in enumerate(prices):
            # Count similar prices
            similar = [p for p in prices if abs(p - price) / price <= tolerance]
            if len(similar) >= 2:
                avg_price = sum(similar) / len(similar)
                if avg_price not in equal_levels:
                    equal_levels.append(avg_price)
        
        return equal_levels
    
    def _find_round_numbers(self, data: pd.DataFrame) -> List[float]:
        """Find significant round numbers in price range."""
        high = data["high"].max()
        low = data["low"].min()
        
        # Determine round number interval based on price range
        price_range = high - low
        
        if price_range > 10000:
            interval = 1000
        elif price_range > 1000:
            interval = 100
        elif price_range > 100:
            interval = 10
        elif price_range > 10:
            interval = 1
        else:
            interval = 0.1
        
        round_numbers = []
        current = int(low / interval) * interval
        
        while current <= high:
            if low <= current <= high:
                round_numbers.append(current)
            current += interval
        
        return round_numbers
    
    def _identify_zones(self, data: pd.DataFrame) -> List[LiquidityZone]:
        """Identify all liquidity zones."""
        zones = []
        current_price = float(data["close"].iloc[-1])
        
        # Swing point liquidity
        swing_highs = self._find_swing_highs(data)
        swing_lows = self._find_swing_lows(data)
        
        # Buy-side liquidity (above swing highs)
        for high in swing_highs:
            zone_low = high
            zone_high = high * (1 + self.zone_buffer)
            swept = current_price > zone_high
            
            zones.append(LiquidityZone(
                price_low=zone_low,
                price_high=zone_high,
                zone_type="buy_side",
                strength=0.6,
                swept=swept
            ))
        
        # Sell-side liquidity (below swing lows)
        for low in swing_lows:
            zone_high = low
            zone_low = low * (1 - self.zone_buffer)
            swept = current_price < zone_low
            
            zones.append(LiquidityZone(
                price_low=zone_low,
                price_high=zone_high,
                zone_type="sell_side",
                strength=0.6,
                swept=swept
            ))
        
        # Equal highs/lows (stronger liquidity)
        equal_highs = self._find_equal_levels(swing_highs)
        for high in equal_highs:
            zone_low = high
            zone_high = high * (1 + self.zone_buffer)
            swept = current_price > zone_high
            
            zones.append(LiquidityZone(
                price_low=zone_low,
                price_high=zone_high,
                zone_type="buy_side",
                strength=0.9,  # Stronger because equal highs
                swept=swept
            ))
        
        equal_lows = self._find_equal_levels(swing_lows)
        for low in equal_lows:
            zone_high = low
            zone_low = low * (1 - self.zone_buffer)
            swept = current_price < zone_low
            
            zones.append(LiquidityZone(
                price_low=zone_low,
                price_high=zone_high,
                zone_type="sell_side",
                strength=0.9,
                swept=swept
            ))
        
        return zones
    
    def calculate(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate liquidity zones.
        
        Args:
            data: OHLCV DataFrame
        
        Returns:
            DataFrame with liquidity columns added
        """
        if not self.validate_data(data) or len(data) < self.window * 2:
            logger.warning("Insufficient data for Liquidity calculation")
            return data
        
        df = data.copy()
        
        # Identify zones
        self._zones = self._identify_zones(df)
        
        # Columns
        buy_liq_col = f"{self.name}_buy_liquidity"
        sell_liq_col = f"{self.name}_sell_liquidity"
        dist_buy_col = f"{self.name}_distance_buy"
        dist_sell_col = f"{self.name}_distance_sell"
        in_zone_col = f"{self.name}_in_zone"
        
        df[buy_liq_col] = np.nan
        df[sell_liq_col] = np.nan
        df[dist_buy_col] = np.nan
        df[dist_sell_col] = np.nan
        df[in_zone_col] = False
        
        for i in range(len(df)):
            close = df["close"].iloc[i]
            
            # Find nearest unswept buy-side liquidity above
            buy_zones = [z for z in self._zones 
                        if z.zone_type == "buy_side" and z.price_low > close and not z.swept]
            if buy_zones:
                nearest_buy = min(buy_zones, key=lambda z: z.price_low)
                df.iloc[i, df.columns.get_loc(buy_liq_col)] = nearest_buy.price_low
                df.iloc[i, df.columns.get_loc(dist_buy_col)] = (nearest_buy.price_low - close) / close * 100
            
            # Find nearest unswept sell-side liquidity below
            sell_zones = [z for z in self._zones 
                         if z.zone_type == "sell_side" and z.price_high < close and not z.swept]
            if sell_zones:
                nearest_sell = max(sell_zones, key=lambda z: z.price_high)
                df.iloc[i, df.columns.get_loc(sell_liq_col)] = nearest_sell.price_high
                df.iloc[i, df.columns.get_loc(dist_sell_col)] = (close - nearest_sell.price_high) / close * 100
            
            # Check if in a zone
            for zone in self._zones:
                if zone.price_low <= close <= zone.price_high:
                    df.iloc[i, df.columns.get_loc(in_zone_col)] = True
                    break
        
        return df
    
    def get_signal(self, data: pd.DataFrame) -> IndicatorSignal:
        """
        Generate signal based on liquidity zones.
        
        - FLIP_BULLISH: Price swept sell-side liquidity (potential reversal up)
        - FLIP_BEARISH: Price swept buy-side liquidity (potential reversal down)
        - BULLISH: Price approaching sell-side liquidity (might grab and go up)
        - BEARISH: Price approaching buy-side liquidity (might grab and go down)
        """
        dist_buy_col = f"{self.name}_distance_buy"
        dist_sell_col = f"{self.name}_distance_sell"
        in_zone_col = f"{self.name}_in_zone"
        
        if dist_buy_col not in data.columns or len(data) < 2:
            return IndicatorSignal(
                signal_type=SignalType.NEUTRAL,
                value=0.0,
                strength=0.0
            )
        
        current_close = float(data["close"].iloc[-1])
        prev_close = float(data["close"].iloc[-2])
        
        dist_buy = data[dist_buy_col].iloc[-1]
        dist_sell = data[dist_sell_col].iloc[-1]
        in_zone = bool(data[in_zone_col].iloc[-1])
        
        # Handle NaN
        dist_buy = dist_buy if pd.notna(dist_buy) else 100
        dist_sell = dist_sell if pd.notna(dist_sell) else 100
        
        # Check for liquidity sweep (price entered zone and reversed)
        prev_in_zone = bool(data[in_zone_col].iloc[-2]) if len(data) > 1 else False
        
        # Swept sell-side and reversing up
        if dist_sell < 0.5 and current_close > prev_close:
            return IndicatorSignal(
                signal_type=SignalType.FLIP_BULLISH,
                value=current_close,
                strength=0.9,
                metadata={"trigger": "sell_side_sweep", "distance": dist_sell}
            )
        
        # Swept buy-side and reversing down
        if dist_buy < 0.5 and current_close < prev_close:
            return IndicatorSignal(
                signal_type=SignalType.FLIP_BEARISH,
                value=current_close,
                strength=0.9,
                metadata={"trigger": "buy_side_sweep", "distance": dist_buy}
            )
        
        # Approaching liquidity
        if dist_sell < dist_buy and dist_sell < 2:
            return IndicatorSignal(
                signal_type=SignalType.BULLISH,
                value=current_close,
                strength=1.0 - dist_sell / 5,
                metadata={"approaching": "sell_side", "distance": dist_sell}
            )
        elif dist_buy < 2:
            return IndicatorSignal(
                signal_type=SignalType.BEARISH,
                value=current_close,
                strength=1.0 - dist_buy / 5,
                metadata={"approaching": "buy_side", "distance": dist_buy}
            )
        
        return IndicatorSignal(
            signal_type=SignalType.NEUTRAL,
            value=current_close,
            strength=0.3,
            metadata={"dist_buy": dist_buy, "dist_sell": dist_sell}
        )
    
    def get_zones(self) -> List[LiquidityZone]:
        """Get all liquidity zones."""
        return self._zones
    
    def get_unswept_zones(self) -> List[LiquidityZone]:
        """Get only unswept liquidity zones."""
        return [z for z in self._zones if not z.swept]

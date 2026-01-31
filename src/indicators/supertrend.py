"""
Supertrend Indicator

A trend-following indicator based on ATR (Average True Range).
Provides clear bullish/bearish signals with trend direction.

Formula:
- Basic Upper Band = (High + Low) / 2 + Multiplier * ATR
- Basic Lower Band = (High + Low) / 2 - Multiplier * ATR
- Final bands are adjusted based on trend direction
- Bullish when price closes above upper band
- Bearish when price closes below lower band
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

from .base import Indicator, IndicatorSignal, SignalType

logger = logging.getLogger(__name__)


class SupertrendIndicator(Indicator):
    """
    Supertrend trend-following indicator.
    
    Parameters:
        period: ATR period (default: 10)
        multiplier: ATR multiplier (default: 3.0)
    
    Columns added:
        - {name}_atr: Average True Range
        - {name}_upper: Upper band
        - {name}_lower: Lower band
        - {name}_trend: Trend direction (1 = bullish, -1 = bearish)
        - {name}_value: Supertrend line value
    """
    
    def __init__(
        self,
        name: str = "supertrend",
        period: int = 10,
        multiplier: float = 3.0
    ):
        """
        Initialize Supertrend indicator.
        
        Args:
            name: Indicator name
            period: ATR period
            multiplier: ATR multiplier
        """
        super().__init__(name, period=period, multiplier=multiplier)
        self.period = period
        self.multiplier = multiplier
    
    def calculate(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate Supertrend values.
        
        Args:
            data: OHLCV DataFrame
        
        Returns:
            DataFrame with Supertrend columns added
        """
        if not self.validate_data(data):
            logger.warning("Invalid data for Supertrend calculation")
            return data
        
        df = data.copy()
        
        # Calculate True Range
        df["tr"] = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                abs(df["high"] - df["close"].shift(1)),
                abs(df["low"] - df["close"].shift(1))
            )
        )
        
        # Calculate ATR
        atr_col = f"{self.name}_atr"
        df[atr_col] = df["tr"].rolling(window=self.period).mean()
        
        # Calculate basic bands
        hl2 = (df["high"] + df["low"]) / 2
        basic_upper = hl2 + self.multiplier * df[atr_col]
        basic_lower = hl2 - self.multiplier * df[atr_col]
        
        # Initialize final bands
        upper_col = f"{self.name}_upper"
        lower_col = f"{self.name}_lower"
        trend_col = f"{self.name}_trend"
        value_col = f"{self.name}_value"
        
        df[upper_col] = basic_upper
        df[lower_col] = basic_lower
        df[trend_col] = 1  # Start bullish
        
        # Calculate final bands and trend
        for i in range(1, len(df)):
            # Upper band
            if basic_upper.iloc[i] < df[upper_col].iloc[i - 1] or df["close"].iloc[i - 1] > df[upper_col].iloc[i - 1]:
                df.loc[df.index[i], upper_col] = basic_upper.iloc[i]
            else:
                df.loc[df.index[i], upper_col] = df[upper_col].iloc[i - 1]
            
            # Lower band
            if basic_lower.iloc[i] > df[lower_col].iloc[i - 1] or df["close"].iloc[i - 1] < df[lower_col].iloc[i - 1]:
                df.loc[df.index[i], lower_col] = basic_lower.iloc[i]
            else:
                df.loc[df.index[i], lower_col] = df[lower_col].iloc[i - 1]
            
            # Trend
            if df[trend_col].iloc[i - 1] == -1 and df["close"].iloc[i] > df[upper_col].iloc[i - 1]:
                df.loc[df.index[i], trend_col] = 1
            elif df[trend_col].iloc[i - 1] == 1 and df["close"].iloc[i] < df[lower_col].iloc[i - 1]:
                df.loc[df.index[i], trend_col] = -1
            else:
                df.loc[df.index[i], trend_col] = df[trend_col].iloc[i - 1]
        
        # Supertrend value (lower band when bullish, upper band when bearish)
        df[value_col] = np.where(
            df[trend_col] == 1,
            df[lower_col],
            df[upper_col]
        )
        
        # Clean up
        df.drop(columns=["tr"], inplace=True)
        
        return df
    
    def get_signal(self, data: pd.DataFrame) -> IndicatorSignal:
        """
        Generate signal from Supertrend.
        
        Returns:
            FLIP_BULLISH: Just crossed above (trend changed from -1 to 1)
            FLIP_BEARISH: Just crossed below (trend changed from 1 to -1)
            BULLISH: Currently in uptrend
            BEARISH: Currently in downtrend
        """
        trend_col = f"{self.name}_trend"
        value_col = f"{self.name}_value"
        
        if trend_col not in data.columns or len(data) < 2:
            return IndicatorSignal(
                signal_type=SignalType.NEUTRAL,
                value=0.0,
                strength=0.0
            )
        
        current_trend = int(data[trend_col].iloc[-1])
        previous_trend = int(data[trend_col].iloc[-2])
        current_value = float(data[value_col].iloc[-1])
        current_close = float(data["close"].iloc[-1])
        
        # Check for trend flip
        if current_trend == 1 and previous_trend == -1:
            signal_type = SignalType.FLIP_BULLISH
            strength = 1.0
        elif current_trend == -1 and previous_trend == 1:
            signal_type = SignalType.FLIP_BEARISH
            strength = 1.0
        elif current_trend == 1:
            signal_type = SignalType.BULLISH
            # Strength based on distance from Supertrend line
            distance = (current_close - current_value) / current_close
            strength = min(1.0, abs(distance) * 20)  # Scale to 0-1
        else:
            signal_type = SignalType.BEARISH
            distance = (current_value - current_close) / current_close
            strength = min(1.0, abs(distance) * 20)
        
        return IndicatorSignal(
            signal_type=signal_type,
            value=current_value,
            strength=strength,
            metadata={
                "trend": current_trend,
                "close": current_close,
                "distance_pct": abs(current_close - current_value) / current_close * 100,
            }
        )
    
    def get_trend(self, data: pd.DataFrame) -> int:
        """
        Get current trend direction.
        
        Returns:
            1 for bullish, -1 for bearish, 0 if unknown
        """
        trend_col = f"{self.name}_trend"
        if trend_col in data.columns and len(data) > 0:
            return int(data[trend_col].iloc[-1])
        return 0


def calculate_supertrend(
    data: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0
) -> pd.DataFrame:
    """
    Convenience function to calculate Supertrend.
    
    Args:
        data: OHLCV DataFrame
        period: ATR period
        multiplier: ATR multiplier
    
    Returns:
        DataFrame with Supertrend columns
    """
    indicator = SupertrendIndicator(period=period, multiplier=multiplier)
    return indicator.calculate(data)

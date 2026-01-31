"""
Live Signal Predictor

Generates prediction signals for 7-15 minute windows using mean-reversion strategy.
Based on backtesting results showing 62% accuracy with optimal parameters.

Best Parameters (from testing):
- EMA8 distance > 0.4% (price extended from mean)
- RSI < 35 (oversold) OR > 65 (overbought)
- Volatility > 1.3x ATR
- Best hours: 4, 9, 11, 20, 21 UTC
"""
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from enum import Enum
import aiohttp

logger = logging.getLogger(__name__)


class PredictionDirection(Enum):
    UP = "UP"
    DOWN = "DOWN"
    NO_SIGNAL = "NO_SIGNAL"


@dataclass
class PredictionSignal:
    """A prediction signal for the next 7-15 minutes."""
    symbol: str
    direction: PredictionDirection
    confidence: float  # 0-1
    accuracy_estimate: float  # Expected accuracy based on backtest
    timestamp: datetime
    expiry_minutes: int  # How long this prediction is valid
    
    # Indicator values that triggered the signal
    rsi: float
    ema8_distance: float  # % distance from EMA8
    volatility_ratio: float  # Current TR / ATR
    hour_utc: int
    
    # Signal components
    is_rsi_extreme: bool
    is_ema_extended: bool
    is_high_volatility: bool
    is_good_hour: bool
    
    reasoning: str
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['direction'] = self.direction.value
        d['timestamp'] = self.timestamp.isoformat()
        return d


class LivePredictor:
    """
    Generates live prediction signals using mean-reversion strategy.
    
    Strategy: When price is extended from EMA8 with RSI at extremes,
    predict mean reversion in the next 7-15 minutes.
    """
    
    # Best hours for mean reversion (UTC)
    BEST_HOURS = [4, 9, 11, 20, 21, 0, 12, 16, 15, 19, 23]
    WORST_HOURS = [5, 6, 14, 2]  # Avoid these
    
    # Thresholds
    RSI_OVERSOLD = 35
    RSI_OVERBOUGHT = 65
    EMA_DISTANCE_THRESHOLD = 0.4  # 0.4% from EMA8
    VOLATILITY_THRESHOLD = 1.3  # 1.3x ATR
    
    # Conservative thresholds (higher confidence)
    RSI_VERY_OVERSOLD = 25
    RSI_VERY_OVERBOUGHT = 75
    EMA_DISTANCE_HIGH = 0.5
    
    def __init__(self, binance_api_url: str = "https://api.binance.com", session: Optional[aiohttp.ClientSession] = None):
        self.binance_url = binance_api_url
        self._price_cache: Dict[str, List[Dict]] = {}
        self._session = session  # Reusable session for speed
    
    async def fetch_recent_candles(
        self, 
        symbol: str = "BTCUSDT", 
        interval: str = "1m", 
        limit: int = 100
    ) -> List[Dict]:
        """Fetch recent candles from Binance."""
        url = f"{self.binance_url}/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
        
        # Use provided session or create one
        if self._session and not self._session.closed:
            session = self._session
            close_session = False
        else:
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
            close_session = True
        
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.error(f"Binance API error: {resp.status}")
                    return []
                
                data = await resp.json()
                
                candles = []
                for k in data:
                    candles.append({
                        "timestamp": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    })
                
                return candles
        finally:
            if close_session:
                await session.close()
    
    def _calc_rsi(self, closes: List[float], period: int = 14) -> float:
        """Calculate RSI from close prices."""
        if len(closes) < period + 1:
            return 50.0  # Neutral if not enough data
        
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas[-period:]]
        losses = [-d if d < 0 else 0 for d in deltas[-period:]]
        
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def _calc_ema(self, values: List[float], period: int) -> float:
        """Calculate EMA."""
        if len(values) < period:
            return values[-1] if values else 0
        
        multiplier = 2 / (period + 1)
        ema = sum(values[:period]) / period  # SMA for first value
        
        for value in values[period:]:
            ema = (value - ema) * multiplier + ema
        
        return ema
    
    def _calc_atr(self, candles: List[Dict], period: int = 14) -> float:
        """Calculate ATR."""
        if len(candles) < period + 1:
            return 0
        
        trs = []
        for i in range(1, len(candles)):
            high = candles[i]["high"]
            low = candles[i]["low"]
            prev_close = candles[i-1]["close"]
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            trs.append(tr)
        
        return sum(trs[-period:]) / period
    
    async def generate_signal(
        self, 
        symbol: str = "BTCUSDT",
        expiry_minutes: int = 7
    ) -> PredictionSignal:
        """Generate a prediction signal for the given symbol."""
        
        # Fetch recent data
        candles = await self.fetch_recent_candles(symbol, "1m", 100)
        
        if len(candles) < 30:
            return self._no_signal(symbol, expiry_minutes, "Insufficient data")
        
        # Current values
        current = candles[-1]
        closes = [c["close"] for c in candles]
        current_price = closes[-1]
        
        # Calculate indicators
        rsi = self._calc_rsi(closes, 14)
        ema8 = self._calc_ema(closes, 8)
        ema8_distance = (current_price - ema8) / ema8 * 100  # Percentage
        
        atr = self._calc_atr(candles, 14)
        current_tr = max(
            current["high"] - current["low"],
            abs(current["high"] - candles[-2]["close"]),
            abs(current["low"] - candles[-2]["close"])
        )
        volatility_ratio = current_tr / atr if atr > 0 else 1.0
        
        hour_utc = datetime.now(timezone.utc).hour
        
        # Check conditions
        is_rsi_oversold = rsi < self.RSI_OVERSOLD
        is_rsi_overbought = rsi > self.RSI_OVERBOUGHT
        is_rsi_extreme = is_rsi_oversold or is_rsi_overbought
        
        is_ema_below = ema8_distance < -self.EMA_DISTANCE_THRESHOLD
        is_ema_above = ema8_distance > self.EMA_DISTANCE_THRESHOLD
        is_ema_extended = is_ema_below or is_ema_above
        
        is_high_volatility = volatility_ratio > self.VOLATILITY_THRESHOLD
        is_good_hour = hour_utc in self.BEST_HOURS
        is_bad_hour = hour_utc in self.WORST_HOURS
        
        # Decision logic (mean reversion)
        direction = PredictionDirection.NO_SIGNAL
        confidence = 0.0
        accuracy_estimate = 0.50
        reasoning_parts = []
        
        # Skip bad hours
        if is_bad_hour:
            return self._no_signal(
                symbol, expiry_minutes, 
                f"Hour {hour_utc} UTC has low accuracy - skipping"
            )
        
        # Strong signal: All conditions met
        if is_rsi_extreme and is_ema_extended and is_high_volatility and is_good_hour:
            if is_rsi_oversold and is_ema_below:
                direction = PredictionDirection.UP
                confidence = 0.85
                accuracy_estimate = 0.62
                reasoning_parts = [
                    f"RSI oversold ({rsi:.1f})",
                    f"Price {abs(ema8_distance):.2f}% below EMA8",
                    f"High volatility ({volatility_ratio:.1f}x ATR)",
                    f"Good hour ({hour_utc} UTC)"
                ]
            elif is_rsi_overbought and is_ema_above:
                direction = PredictionDirection.DOWN
                confidence = 0.85
                accuracy_estimate = 0.62
                reasoning_parts = [
                    f"RSI overbought ({rsi:.1f})",
                    f"Price {ema8_distance:.2f}% above EMA8",
                    f"High volatility ({volatility_ratio:.1f}x ATR)",
                    f"Good hour ({hour_utc} UTC)"
                ]
        
        # Medium signal: RSI extreme + EMA extended (without vol/hour requirements)
        elif is_rsi_extreme and is_ema_extended:
            if is_rsi_oversold and is_ema_below:
                direction = PredictionDirection.UP
                confidence = 0.65
                accuracy_estimate = 0.56
                reasoning_parts = [
                    f"RSI oversold ({rsi:.1f})",
                    f"Price {abs(ema8_distance):.2f}% below EMA8"
                ]
            elif is_rsi_overbought and is_ema_above:
                direction = PredictionDirection.DOWN
                confidence = 0.65
                accuracy_estimate = 0.56
                reasoning_parts = [
                    f"RSI overbought ({rsi:.1f})",
                    f"Price {ema8_distance:.2f}% above EMA8"
                ]
        
        # Weak signal: Just EMA extended significantly (0.5%+)
        elif abs(ema8_distance) > 0.5:
            if ema8_distance < -0.5:
                direction = PredictionDirection.UP
                confidence = 0.50
                accuracy_estimate = 0.55
                reasoning_parts = [f"Price {abs(ema8_distance):.2f}% below EMA8"]
            else:
                direction = PredictionDirection.DOWN
                confidence = 0.50
                accuracy_estimate = 0.55
                reasoning_parts = [f"Price {ema8_distance:.2f}% above EMA8"]
        
        # No signal
        if direction == PredictionDirection.NO_SIGNAL:
            return self._no_signal(
                symbol, expiry_minutes,
                f"No extreme conditions (RSI: {rsi:.1f}, EMA dist: {ema8_distance:.2f}%)"
            )
        
        return PredictionSignal(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            accuracy_estimate=accuracy_estimate,
            timestamp=datetime.now(timezone.utc),
            expiry_minutes=expiry_minutes,
            rsi=rsi,
            ema8_distance=ema8_distance,
            volatility_ratio=volatility_ratio,
            hour_utc=hour_utc,
            is_rsi_extreme=is_rsi_extreme,
            is_ema_extended=is_ema_extended,
            is_high_volatility=is_high_volatility,
            is_good_hour=is_good_hour,
            reasoning=" | ".join(reasoning_parts)
        )
    
    def _no_signal(
        self, 
        symbol: str, 
        expiry_minutes: int, 
        reasoning: str
    ) -> PredictionSignal:
        """Create a no-signal response."""
        return PredictionSignal(
            symbol=symbol,
            direction=PredictionDirection.NO_SIGNAL,
            confidence=0.0,
            accuracy_estimate=0.50,
            timestamp=datetime.now(timezone.utc),
            expiry_minutes=expiry_minutes,
            rsi=50.0,
            ema8_distance=0.0,
            volatility_ratio=1.0,
            hour_utc=datetime.now(timezone.utc).hour,
            is_rsi_extreme=False,
            is_ema_extended=False,
            is_high_volatility=False,
            is_good_hour=False,
            reasoning=reasoning
        )


# Convenience function for API usage
async def get_live_signal(symbol: str = "BTCUSDT", expiry_minutes: int = 7) -> Dict[str, Any]:
    """Get current live signal for a symbol."""
    predictor = LivePredictor()
    signal = await predictor.generate_signal(symbol, expiry_minutes)
    return signal.to_dict()

"""
Real-Time Price Feed

Connects to Binance WebSocket for sub-second crypto price updates.
Tracks price history and calculates momentum across multiple timeframes.
"""
import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

try:
    import websockets
except ImportError:
    websockets = None

logger = logging.getLogger(__name__)


@dataclass
class PricePoint:
    """Single price observation."""
    timestamp: float
    price: float
    volume: float = 0.0


@dataclass
class MomentumData:
    """Momentum across multiple timeframes."""
    momentum_1s: float = 0.0
    momentum_5s: float = 0.0
    momentum_30s: float = 0.0
    momentum_60s: float = 0.0
    momentum_300s: float = 0.0  # 5 minutes
    
    volume_1s: float = 0.0
    volume_5s: float = 0.0
    volume_60s: float = 0.0
    
    @property
    def short_term(self) -> float:
        """Short-term momentum (1-5s weighted)."""
        return self.momentum_1s * 0.6 + self.momentum_5s * 0.4
    
    @property
    def medium_term(self) -> float:
        """Medium-term momentum (30-60s weighted)."""
        return self.momentum_30s * 0.5 + self.momentum_60s * 0.5
    
    @property
    def trend_strength(self) -> float:
        """Overall trend strength (-1 to 1)."""
        # Weighted average of all timeframes
        weights = [0.15, 0.20, 0.25, 0.25, 0.15]
        momentums = [
            self.momentum_1s, 
            self.momentum_5s, 
            self.momentum_30s, 
            self.momentum_60s,
            self.momentum_300s
        ]
        return sum(w * m for w, m in zip(weights, momentums))


class RealTimePriceFeed:
    """
    Real-time cryptocurrency price feed using Binance WebSocket.
    
    Features:
    - Sub-second price updates
    - Multi-timeframe momentum calculation
    - Volume tracking
    - Automatic reconnection
    """
    
    # Binance WebSocket endpoint
    WS_URL = "wss://stream.binance.com:9443/ws"
    
    # Assets to track (mapped to Binance symbols)
    ASSET_SYMBOLS = {
        "BTC": "btcusdt",
        "ETH": "ethusdt",
        "SOL": "solusdt",
        "XRP": "xrpusdt",
        "DOGE": "dogeusdt",
        "ADA": "adausdt",
        "AVAX": "avaxusdt",
        "DOT": "dotusdt",
        "LINK": "linkusdt",
        "MATIC": "maticusdt",
    }
    
    # Price history length (seconds)
    HISTORY_LENGTH = 600  # 10 minutes
    
    def __init__(self):
        # Price history: asset -> deque of PricePoint
        self.prices: dict[str, deque] = {
            asset: deque(maxlen=self.HISTORY_LENGTH * 10)  # ~10 updates/sec
            for asset in self.ASSET_SYMBOLS
        }
        
        # Latest prices
        self.latest_prices: dict[str, float] = {}
        
        # Momentum cache
        self._momentum_cache: dict[str, MomentumData] = {}
        self._momentum_cache_time: dict[str, float] = {}
        
        # WebSocket connection
        self._ws = None
        self._running = False
        self._reconnect_delay = 1.0
        
        # Callbacks
        self._on_price_update: Optional[Callable] = None
    
    def set_price_callback(self, callback: Callable[[str, float, float], None]):
        """Set callback for price updates: callback(asset, price, volume)."""
        self._on_price_update = callback
    
    async def connect(self):
        """Connect to Binance WebSocket and start receiving prices."""
        if websockets is None:
            logger.error("websockets library not installed")
            return
        
        self._running = True
        
        while self._running:
            try:
                # Build stream URL for all assets
                streams = [f"{sym}@trade" for sym in self.ASSET_SYMBOLS.values()]
                url = f"{self.WS_URL}/{'/'.join(streams)}"
                
                # Use combined stream
                combined_url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
                
                logger.info(f"Connecting to Binance WebSocket...")
                
                async with websockets.connect(combined_url) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1.0
                    logger.info("Connected to Binance WebSocket")
                    
                    async for message in ws:
                        await self._handle_message(message)
                        
            except Exception as e:
                logger.warning(f"Binance WebSocket error: {e}")
                
                if self._running:
                    logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(30, self._reconnect_delay * 2)
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            
            # Combined stream format
            if "stream" in data and "data" in data:
                trade_data = data["data"]
            else:
                trade_data = data
            
            # Parse trade
            symbol = trade_data.get("s", "").lower()
            price = float(trade_data.get("p", 0))
            quantity = float(trade_data.get("q", 0))
            trade_time = trade_data.get("T", time.time() * 1000) / 1000
            
            # Map symbol back to asset
            asset = None
            for a, s in self.ASSET_SYMBOLS.items():
                if s == symbol:
                    asset = a
                    break
            
            if asset and price > 0:
                # Record price
                self.prices[asset].append(PricePoint(
                    timestamp=trade_time,
                    price=price,
                    volume=quantity * price  # Volume in USD
                ))
                self.latest_prices[asset] = price
                
                # Invalidate momentum cache
                if asset in self._momentum_cache_time:
                    del self._momentum_cache_time[asset]
                
                # Call callback
                if self._on_price_update:
                    self._on_price_update(asset, price, quantity * price)
                    
        except Exception as e:
            logger.debug(f"Error parsing message: {e}")
    
    def get_latest_price(self, asset: str) -> Optional[float]:
        """Get latest price for an asset."""
        return self.latest_prices.get(asset)
    
    def get_price_at_time(self, asset: str, target_time: float) -> Optional[float]:
        """Get price closest to target timestamp."""
        if asset not in self.prices:
            return None
        
        history = self.prices[asset]
        if not history:
            return None
        
        # Find closest price
        closest = min(history, key=lambda p: abs(p.timestamp - target_time))
        return closest.price
    
    def calculate_momentum(
        self, 
        asset: str, 
        window_seconds: int,
        reference_time: Optional[float] = None
    ) -> float:
        """
        Calculate price momentum over a time window.
        
        Returns percentage change as decimal (0.01 = 1% up).
        """
        if asset not in self.prices:
            return 0.0
        
        history = self.prices[asset]
        if len(history) < 2:
            return 0.0
        
        now = reference_time or time.time()
        window_start = now - window_seconds
        
        # Get prices in window
        window_prices = [p for p in history if p.timestamp >= window_start]
        
        if len(window_prices) < 2:
            return 0.0
        
        # Calculate momentum: (latest - earliest) / earliest
        earliest = window_prices[0].price
        latest = window_prices[-1].price
        
        if earliest == 0:
            return 0.0
        
        return (latest - earliest) / earliest
    
    def get_momentum(self, asset: str) -> MomentumData:
        """
        Get momentum data across all timeframes.
        
        Caches results for 100ms to avoid recalculating.
        """
        now = time.time()
        
        # Check cache
        if asset in self._momentum_cache_time:
            if now - self._momentum_cache_time[asset] < 0.1:  # 100ms cache
                return self._momentum_cache.get(asset, MomentumData())
        
        # Calculate fresh momentum
        momentum = MomentumData(
            momentum_1s=self.calculate_momentum(asset, 1),
            momentum_5s=self.calculate_momentum(asset, 5),
            momentum_30s=self.calculate_momentum(asset, 30),
            momentum_60s=self.calculate_momentum(asset, 60),
            momentum_300s=self.calculate_momentum(asset, 300),
            volume_1s=self._calculate_volume(asset, 1),
            volume_5s=self._calculate_volume(asset, 5),
            volume_60s=self._calculate_volume(asset, 60),
        )
        
        # Cache it
        self._momentum_cache[asset] = momentum
        self._momentum_cache_time[asset] = now
        
        return momentum
    
    def _calculate_volume(self, asset: str, window_seconds: int) -> float:
        """Calculate total volume in window."""
        if asset not in self.prices:
            return 0.0
        
        now = time.time()
        window_start = now - window_seconds
        
        total_volume = sum(
            p.volume for p in self.prices[asset] 
            if p.timestamp >= window_start
        )
        
        return total_volume
    
    def get_volume_rate(self, asset: str, window_seconds: int = 60) -> float:
        """Get volume per second rate."""
        volume = self._calculate_volume(asset, window_seconds)
        return volume / window_seconds if window_seconds > 0 else 0
    
    async def stop(self):
        """Stop the WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
    
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._ws is not None and self._running
    
    def get_summary(self) -> dict:
        """Get summary of current state."""
        return {
            "connected": self.is_connected(),
            "assets_tracked": len(self.latest_prices),
            "prices": {
                asset: {
                    "price": self.latest_prices.get(asset, 0),
                    "momentum_60s": f"{self.calculate_momentum(asset, 60)*100:+.2f}%",
                    "history_points": len(self.prices.get(asset, [])),
                }
                for asset in self.ASSET_SYMBOLS
            }
        }

"""
Data Fetcher - Isolated External API Calls

Single responsibility: Fetch market data and crypto prices from external APIs.
No business logic, just data retrieval.
"""
import asyncio
import aiohttp
import json
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.market_maker.models import FifteenMinMarket

logger = logging.getLogger(__name__)

# Constants
GAMMA_API = "https://gamma-api.polymarket.com"
ASSETS = {
    "BTC": "btc-updown-15m",
    "ETH": "eth-updown-15m", 
    "SOL": "sol-updown-15m",
    "XRP": "xrp-updown-15m",
}
COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
}


class MarketDataFetcher:
    """
    Fetches market data from external APIs.
    
    Responsibilities:
    - Fetch 15-minute markets from Polymarket
    - Fetch crypto prices from multiple sources (WebSocket, CoinGecko, Binance)
    """
    
    def __init__(self, session: aiohttp.ClientSession, price_feed=None):
        self.session = session
        self.price_feed = price_feed  # Optional RealTimePriceFeed for WebSocket prices
    
    def _get_current_and_future_timestamps(self) -> list[int]:
        """Get timestamps for current and upcoming 15-min windows."""
        now = int(time.time())
        current_window = now - (now % 900) + 900
        return [current_window + (i * 900) for i in range(4)]
    
    async def fetch_crypto_prices(self) -> dict[str, float]:
        """
        Fetch live crypto prices with fallback chain.
        
        Priority:
        1. WebSocket prices (if connected)
        2. CoinGecko API
        3. Binance REST API
        4. Estimated fallback prices
        
        Returns:
            Dict mapping asset symbols to prices in USD
        """
        # Try WebSocket first
        if self.price_feed and self.price_feed.is_connected():
            ws_prices = {
                asset: self.price_feed.get_latest_price(asset)
                for asset in ASSETS
            }
            ws_prices = {k: v for k, v in ws_prices.items() if v and v > 0}
            if len(ws_prices) >= len(ASSETS) - 1:
                logger.debug(f"Using WebSocket prices: {ws_prices}")
                return ws_prices
        
        # Fallback to CoinGecko
        try:
            ids = ",".join(COINGECKO_IDS.values())
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    prices = {
                        asset: data[coin_id]["usd"]
                        for asset, coin_id in COINGECKO_IDS.items()
                        if coin_id in data and "usd" in data[coin_id]
                    }
                    if prices:
                        logger.debug(f"Using CoinGecko prices: {prices}")
                        return prices
        except Exception as e:
            logger.debug(f"CoinGecko failed: {e}")
        
        # Last resort: Binance REST
        try:
            binance_symbols = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
            prices = {}
            for asset, symbol in binance_symbols.items():
                url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        prices[asset] = float(data.get("price", 0))
            if prices:
                logger.debug(f"Using Binance prices: {prices}")
                return prices
        except Exception as e:
            logger.debug(f"Binance failed: {e}")
        
        # Fallback estimates
        logger.warning("Using estimated prices (all APIs unavailable)")
        return {"BTC": 88000, "ETH": 2900, "SOL": 130, "XRP": 2.0}
    
    async def fetch_market_by_slug(self, slug: str) -> Optional[FifteenMinMarket]:
        """Fetch a specific market by slug from Polymarket."""
        try:
            url = f"{GAMMA_API}/events?slug={slug}"
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
            
            if not data or len(data) == 0:
                return None
            
            event = data[0]
            if event.get("closed"):
                return None
            
            markets = event.get("markets", [])
            if not markets:
                return None
            
            market = markets[0]
            if market.get("closed"):
                return None
            
            outcomes = json.loads(market.get("outcomes", "[]"))
            prices = json.loads(market.get("outcomePrices", "[]"))
            
            up_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "up"), 0)
            down_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "down"), 1)
            
            up_price = float(prices[up_idx]) if up_idx < len(prices) else 0.5
            down_price = float(prices[down_idx]) if down_idx < len(prices) else 0.5
            
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            up_token = token_ids[up_idx] if up_idx < len(token_ids) else ""
            down_token = token_ids[down_idx] if down_idx < len(token_ids) else ""
            
            end_date_str = market.get("endDate", "")
            end_time = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            start_time = end_time - timedelta(minutes=15)
            
            asset = "BTC"
            for a, prefix in ASSETS.items():
                if slug.startswith(prefix):
                    asset = a
                    break
            
            return FifteenMinMarket(
                market_id=market.get("id", ""),
                condition_id=market.get("conditionId", ""),
                slug=slug,
                asset=asset,
                title=market.get("question", ""),
                start_time=start_time,
                end_time=end_time,
                up_token_id=up_token,
                down_token_id=down_token,
                up_price=up_price,
                down_price=down_price,
                volume=float(market.get("volumeNum", 0)),
                is_active=not market.get("closed", False)
            )
        except Exception as e:
            logger.debug(f"Error fetching market {slug}: {e}")
            return None
    
    async def fetch_15min_markets(self) -> list[FifteenMinMarket]:
        """Fetch all active 15-minute markets."""
        timestamps = self._get_current_and_future_timestamps()
        slugs_to_check = [
            f"{prefix}-{ts}"
            for ts in timestamps
            for prefix in ASSETS.values()
        ]
        
        tasks = [self.fetch_market_by_slug(slug) for slug in slugs_to_check]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return [
            result for result in results
            if isinstance(result, FifteenMinMarket) and result.is_active
        ]

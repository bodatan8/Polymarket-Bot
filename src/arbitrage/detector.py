"""
Main arbitrage detector that coordinates binary and categorical detection.
"""

from dataclasses import dataclass
from typing import Optional, Union, Callable, Any
import asyncio
import time

from ..clients.websocket_client import OrderBook
from ..clients.gamma_client import Market, GammaClient
from ..utils.cost_calculator import CostCalculator
from ..utils.logger import get_logger
from .binary_arb import BinaryArbitrageDetector, BinaryArbitrageOpportunity
from .categorical_arb import CategoricalArbitrageDetector, CategoricalArbitrageOpportunity

logger = get_logger("detector")

# Type alias for any arbitrage opportunity
ArbitrageOpportunity = Union[BinaryArbitrageOpportunity, CategoricalArbitrageOpportunity]


@dataclass
class DetectorStats:
    """Statistics for the detector."""
    markets_monitored: int = 0
    binary_markets: int = 0
    categorical_markets: int = 0
    opportunities_detected: int = 0
    binary_opportunities: int = 0
    categorical_opportunities: int = 0
    last_scan_time: float = 0.0
    avg_scan_duration_ms: float = 0.0


class ArbitrageDetector:
    """
    Main arbitrage detector coordinating all detection strategies.
    
    Monitors order books and markets, running detection logic
    on each update to find arbitrage opportunities.
    """
    
    def __init__(
        self,
        cost_calculator: CostCalculator,
        gamma_client: GammaClient,
        min_edge_bps: float = 50,
        min_size: float = 1.0,
        max_size: float = 100.0,
        on_opportunity: Optional[Callable[[ArbitrageOpportunity], Any]] = None
    ):
        """
        Initialize arbitrage detector.
        
        Args:
            cost_calculator: Cost calculator for profitability analysis
            gamma_client: Client for market metadata
            min_edge_bps: Minimum edge in basis points
            min_size: Minimum trade size
            max_size: Maximum trade size
            on_opportunity: Callback when opportunity detected
        """
        self.cost_calculator = cost_calculator
        self.gamma_client = gamma_client
        self.min_edge_bps = min_edge_bps
        self.min_size = min_size
        self.max_size = max_size
        self.on_opportunity = on_opportunity
        
        # Initialize sub-detectors
        self.binary_detector = BinaryArbitrageDetector(
            cost_calculator=cost_calculator,
            min_edge_bps=min_edge_bps,
            min_size=min_size,
            max_size=max_size
        )
        
        self.categorical_detector = CategoricalArbitrageDetector(
            cost_calculator=cost_calculator,
            min_edge_bps=min_edge_bps * 2,  # Higher threshold for categorical
            min_size=min_size,
            max_size=max_size / 2  # Lower max size due to higher risk
        )
        
        # Order book cache
        self._order_books: dict[str, OrderBook] = {}
        
        # Market cache
        self._markets: dict[str, Market] = {}
        self._token_to_market: dict[str, str] = {}  # token_id -> condition_id
        
        # Stats
        self._stats = DetectorStats()
        self._scan_durations: list[float] = []
        
        # Active opportunities (avoid duplicate signals)
        self._active_opportunities: dict[str, float] = {}  # market_id -> timestamp
        self._opportunity_cooldown = 5.0  # Seconds before re-signaling same market
    
    async def initialize(self) -> None:
        """Initialize detector with market data."""
        logger.info("Initializing arbitrage detector")
        
        # Fetch markets from Gamma API
        markets = await self.gamma_client.fetch_markets()
        
        for market in markets:
            self._markets[market.condition_id] = market
            for token in market.tokens:
                self._token_to_market[token.token_id] = market.condition_id
        
        self._stats.markets_monitored = len(self._markets)
        self._stats.binary_markets = len([m for m in self._markets.values() if m.is_binary])
        self._stats.categorical_markets = len([m for m in self._markets.values() if m.is_categorical])
        
        logger.info(
            f"Detector initialized",
            extra={
                "total_markets": self._stats.markets_monitored,
                "binary_markets": self._stats.binary_markets,
                "categorical_markets": self._stats.categorical_markets
            }
        )
    
    async def on_order_book_update(self, order_book: OrderBook) -> None:
        """
        Handle order book update from WebSocket.
        
        Args:
            order_book: Updated order book
        """
        # Update cache
        self._order_books[order_book.asset_id] = order_book
        
        # Find market for this token
        market_id = self._token_to_market.get(order_book.asset_id)
        if not market_id:
            return
        
        market = self._markets.get(market_id)
        if not market:
            return
        
        # Check for arbitrage
        await self._check_market(market)
    
    async def _check_market(self, market: Market) -> None:
        """Check a specific market for arbitrage opportunities."""
        start_time = time.time()
        
        # Check cooldown
        last_signal = self._active_opportunities.get(market.condition_id, 0)
        if time.time() - last_signal < self._opportunity_cooldown:
            return
        
        opportunity: Optional[ArbitrageOpportunity] = None
        
        if market.is_binary:
            yes_token = market.get_yes_token()
            no_token = market.get_no_token()
            
            if yes_token and no_token:
                yes_book = self._order_books.get(yes_token.token_id)
                no_book = self._order_books.get(no_token.token_id)
                
                opportunity = self.binary_detector.check_opportunity(
                    market, yes_book, no_book
                )
                
                if opportunity:
                    self._stats.binary_opportunities += 1
        
        elif market.is_categorical:
            opportunity = self.categorical_detector.check_opportunity(
                market, self._order_books
            )
            
            if opportunity:
                self._stats.categorical_opportunities += 1
        
        # Handle detected opportunity
        if opportunity and opportunity.is_executable:
            self._stats.opportunities_detected += 1
            self._active_opportunities[market.condition_id] = time.time()
            
            if self.on_opportunity:
                await self._call_handler(self.on_opportunity, opportunity)
        
        # Track scan duration
        duration_ms = (time.time() - start_time) * 1000
        self._scan_durations.append(duration_ms)
        if len(self._scan_durations) > 100:
            self._scan_durations.pop(0)
        
        self._stats.last_scan_time = time.time()
        self._stats.avg_scan_duration_ms = sum(self._scan_durations) / len(self._scan_durations)
    
    async def scan_all_markets(self) -> list[ArbitrageOpportunity]:
        """
        Scan all markets for arbitrage opportunities.
        
        Returns:
            List of all detected opportunities
        """
        logger.info("Running full market scan")
        start_time = time.time()
        
        opportunities: list[ArbitrageOpportunity] = []
        
        # Binary markets
        binary_markets = [m for m in self._markets.values() if m.is_binary]
        binary_opps = self.binary_detector.check_all_markets(
            binary_markets, self._order_books
        )
        opportunities.extend(binary_opps)
        
        # Categorical markets
        categorical_markets = [m for m in self._markets.values() if m.is_categorical]
        categorical_opps = self.categorical_detector.check_all_markets(
            categorical_markets, self._order_books
        )
        opportunities.extend(categorical_opps)
        
        duration_ms = (time.time() - start_time) * 1000
        
        logger.info(
            f"Full scan complete",
            extra={
                "duration_ms": duration_ms,
                "markets_scanned": len(self._markets),
                "opportunities_found": len(opportunities),
                "binary_opps": len(binary_opps),
                "categorical_opps": len(categorical_opps)
            }
        )
        
        return opportunities
    
    async def _call_handler(self, handler: Callable, *args) -> None:
        """Call handler, supporting both sync and async callbacks."""
        result = handler(*args)
        if asyncio.iscoroutine(result):
            await result
    
    def get_stats(self) -> DetectorStats:
        """Get detector statistics."""
        return self._stats
    
    def get_order_book(self, token_id: str) -> Optional[OrderBook]:
        """Get cached order book for a token."""
        return self._order_books.get(token_id)
    
    def get_market(self, condition_id: str) -> Optional[Market]:
        """Get cached market by condition ID."""
        return self._markets.get(condition_id)
    
    def get_all_token_ids(self) -> list[str]:
        """Get all token IDs for subscription."""
        return list(self._token_to_market.keys())
    
    async def refresh_markets(self) -> None:
        """Refresh market data from Gamma API."""
        logger.info("Refreshing market data")
        
        markets = await self.gamma_client.fetch_markets()
        
        new_tokens = []
        for market in markets:
            if market.condition_id not in self._markets:
                for token in market.tokens:
                    new_tokens.append(token.token_id)
            
            self._markets[market.condition_id] = market
            for token in market.tokens:
                self._token_to_market[token.token_id] = market.condition_id
        
        self._stats.markets_monitored = len(self._markets)
        self._stats.binary_markets = len([m for m in self._markets.values() if m.is_binary])
        self._stats.categorical_markets = len([m for m in self._markets.values() if m.is_categorical])
        
        logger.info(
            f"Market refresh complete",
            extra={
                "total_markets": self._stats.markets_monitored,
                "new_tokens": len(new_tokens)
            }
        )
        
        return new_tokens
    
    def clear_cooldowns(self) -> None:
        """Clear opportunity cooldowns."""
        self._active_opportunities.clear()

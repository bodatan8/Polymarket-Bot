"""
Binary arbitrage detector for YES/NO markets.
Detects when YES + NO prices sum to less than $1.00.
"""

from dataclasses import dataclass
from typing import Optional
import time

from ..clients.websocket_client import OrderBook
from ..clients.gamma_client import Market
from ..utils.cost_calculator import CostCalculator, ArbitrageAnalysis
from ..utils.logger import get_logger

logger = get_logger("binary_arb")


@dataclass
class BinaryArbitrageOpportunity:
    """Detected binary arbitrage opportunity."""
    market: Market
    yes_token_id: str
    no_token_id: str
    yes_ask: float
    no_ask: float
    yes_ask_size: float
    no_ask_size: float
    analysis: ArbitrageAnalysis
    max_size: float  # Maximum executable size
    timestamp: float
    
    @property
    def edge_bps(self) -> float:
        """Get net edge in basis points."""
        return self.analysis.net_edge_bps
    
    @property
    def is_executable(self) -> bool:
        """Check if opportunity can be executed."""
        return (
            self.analysis.is_profitable and
            self.max_size > 0 and
            self.yes_ask_size > 0 and
            self.no_ask_size > 0
        )


class BinaryArbitrageDetector:
    """
    Detector for binary (YES/NO) market arbitrage.
    
    Binary arbitrage works by buying both YES and NO tokens.
    When the market resolves, one token pays $1.00 and the other $0.
    If YES + NO < $1.00 (after fees), there's an arbitrage opportunity.
    
    Profit = $1.00 - YES_price - NO_price - fees
    """
    
    def __init__(
        self,
        cost_calculator: CostCalculator,
        min_edge_bps: float = 50,
        min_size: float = 1.0,
        max_size: float = 100.0
    ):
        """
        Initialize binary arbitrage detector.
        
        Args:
            cost_calculator: Cost calculator for fee analysis
            min_edge_bps: Minimum net edge in basis points to flag
            min_size: Minimum trade size in USDC
            max_size: Maximum trade size in USDC
        """
        self.cost_calculator = cost_calculator
        self.min_edge_bps = min_edge_bps
        self.min_size = min_size
        self.max_size = max_size
        
        # Track detected opportunities
        self._last_opportunities: dict[str, BinaryArbitrageOpportunity] = {}
    
    def check_opportunity(
        self,
        market: Market,
        yes_book: Optional[OrderBook],
        no_book: Optional[OrderBook]
    ) -> Optional[BinaryArbitrageOpportunity]:
        """
        Check if a binary market has an arbitrage opportunity.
        
        Args:
            market: Market metadata
            yes_book: Order book for YES token
            no_book: Order book for NO token
        
        Returns:
            BinaryArbitrageOpportunity if found, None otherwise
        """
        # Validate inputs
        if not market.is_binary:
            return None
        
        if not yes_book or not no_book:
            return None
        
        # Get best asks
        yes_ask = yes_book.best_ask
        no_ask = no_book.best_ask
        
        if yes_ask is None or no_ask is None:
            return None
        
        # Get ask sizes (liquidity at best price)
        yes_ask_size = self._get_size_at_price(yes_book.asks, yes_ask)
        no_ask_size = self._get_size_at_price(no_book.asks, no_ask)
        
        if yes_ask_size <= 0 or no_ask_size <= 0:
            return None
        
        # Calculate maximum executable size
        # Limited by smaller side and max_size setting
        max_size = min(
            yes_ask_size * yes_ask,  # USDC value of YES side
            no_ask_size * no_ask,    # USDC value of NO side
            self.max_size
        )
        
        if max_size < self.min_size:
            return None
        
        # Run cost analysis
        analysis = self.cost_calculator.calculate_binary_arb(
            yes_ask=yes_ask,
            no_ask=no_ask,
            position_size=max_size,
            use_maker=False  # Assume taker for speed
        )
        
        # Check if opportunity meets threshold
        if not analysis.is_profitable or analysis.net_edge_bps < self.min_edge_bps:
            return None
        
        # Get token IDs
        yes_token = market.get_yes_token()
        no_token = market.get_no_token()
        
        if not yes_token or not no_token:
            return None
        
        opportunity = BinaryArbitrageOpportunity(
            market=market,
            yes_token_id=yes_token.token_id,
            no_token_id=no_token.token_id,
            yes_ask=yes_ask,
            no_ask=no_ask,
            yes_ask_size=yes_ask_size,
            no_ask_size=no_ask_size,
            analysis=analysis,
            max_size=max_size,
            timestamp=time.time()
        )
        
        # Log detection
        logger.info(
            f"Binary arb detected",
            extra={
                "market_id": market.condition_id,
                "question": market.question[:50],
                "yes_ask": yes_ask,
                "no_ask": no_ask,
                "combined": yes_ask + no_ask,
                "edge_bps": analysis.net_edge_bps,
                "potential_profit": analysis.potential_profit,
                "max_size": max_size
            }
        )
        
        self._last_opportunities[market.condition_id] = opportunity
        
        return opportunity
    
    def check_all_markets(
        self,
        markets: list[Market],
        order_books: dict[str, OrderBook]
    ) -> list[BinaryArbitrageOpportunity]:
        """
        Check all binary markets for arbitrage.
        
        Args:
            markets: List of markets to check
            order_books: Dict of token_id -> OrderBook
        
        Returns:
            List of detected opportunities, sorted by edge
        """
        opportunities = []
        
        for market in markets:
            if not market.is_binary:
                continue
            
            yes_token = market.get_yes_token()
            no_token = market.get_no_token()
            
            if not yes_token or not no_token:
                continue
            
            yes_book = order_books.get(yes_token.token_id)
            no_book = order_books.get(no_token.token_id)
            
            opp = self.check_opportunity(market, yes_book, no_book)
            if opp:
                opportunities.append(opp)
        
        # Sort by edge (highest first)
        opportunities.sort(key=lambda x: x.edge_bps, reverse=True)
        
        return opportunities
    
    def _get_size_at_price(
        self,
        levels: list,
        target_price: float,
        tolerance: float = 0.0001
    ) -> float:
        """Get total size available at a specific price."""
        total_size = 0.0
        for level in levels:
            if abs(level.price - target_price) < tolerance:
                total_size += level.size
        return total_size
    
    def get_last_opportunity(
        self,
        market_id: str
    ) -> Optional[BinaryArbitrageOpportunity]:
        """Get the last detected opportunity for a market."""
        return self._last_opportunities.get(market_id)
    
    def clear_opportunities(self) -> None:
        """Clear cached opportunities."""
        self._last_opportunities.clear()

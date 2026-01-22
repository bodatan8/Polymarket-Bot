"""
Categorical arbitrage detector for multi-outcome markets.
Detects when sum of all outcome prices is less than $1.00.
"""

from dataclasses import dataclass, field
from typing import Optional
import time

from ..clients.websocket_client import OrderBook
from ..clients.gamma_client import Market, Token
from ..utils.cost_calculator import CostCalculator, ArbitrageAnalysis
from ..utils.logger import get_logger

logger = get_logger("categorical_arb")


@dataclass
class OutcomeData:
    """Data for a single outcome in categorical arb."""
    token: Token
    ask_price: float
    ask_size: float
    order_book: OrderBook


@dataclass
class CategoricalArbitrageOpportunity:
    """Detected categorical arbitrage opportunity."""
    market: Market
    outcomes: list[OutcomeData]
    total_cost: float  # Sum of all ask prices
    analysis: ArbitrageAnalysis
    max_size: float  # Maximum executable size (limited by smallest outcome)
    limiting_outcome: str  # Which outcome limits the size
    timestamp: float
    
    @property
    def edge_bps(self) -> float:
        """Get net edge in basis points."""
        return self.analysis.net_edge_bps
    
    @property
    def num_outcomes(self) -> int:
        """Number of outcomes in this market."""
        return len(self.outcomes)
    
    @property
    def is_executable(self) -> bool:
        """Check if opportunity can be executed."""
        return (
            self.analysis.is_profitable and
            self.max_size > 0 and
            all(o.ask_size > 0 for o in self.outcomes)
        )


class CategoricalArbitrageDetector:
    """
    Detector for categorical (multi-outcome) market arbitrage.
    
    Categorical arbitrage works by buying ALL outcomes in a market.
    Since exactly one outcome will resolve to $1.00, buying all
    guarantees a $1.00 payout.
    
    If sum(all_outcome_prices) < $1.00 (after fees), there's an arb.
    
    Risk: Higher than binary because:
    - More legs to fill (any partial fill breaks the arb)
    - More slippage risk
    - Higher total fees
    """
    
    def __init__(
        self,
        cost_calculator: CostCalculator,
        min_edge_bps: float = 100,  # Higher threshold due to risk
        min_size: float = 5.0,
        max_size: float = 50.0,
        max_outcomes: int = 10  # Skip markets with too many outcomes
    ):
        """
        Initialize categorical arbitrage detector.
        
        Args:
            cost_calculator: Cost calculator for fee analysis
            min_edge_bps: Minimum net edge in basis points to flag
            min_size: Minimum trade size in USDC
            max_size: Maximum trade size in USDC
            max_outcomes: Maximum number of outcomes to consider
        """
        self.cost_calculator = cost_calculator
        self.min_edge_bps = min_edge_bps
        self.min_size = min_size
        self.max_size = max_size
        self.max_outcomes = max_outcomes
        
        self._last_opportunities: dict[str, CategoricalArbitrageOpportunity] = {}
    
    def check_opportunity(
        self,
        market: Market,
        order_books: dict[str, OrderBook]
    ) -> Optional[CategoricalArbitrageOpportunity]:
        """
        Check if a categorical market has an arbitrage opportunity.
        
        Args:
            market: Market metadata
            order_books: Dict of token_id -> OrderBook
        
        Returns:
            CategoricalArbitrageOpportunity if found, None otherwise
        """
        # Validate market
        if not market.is_categorical:
            return None
        
        num_outcomes = len(market.tokens)
        if num_outcomes > self.max_outcomes:
            logger.debug(f"Skipping market with {num_outcomes} outcomes")
            return None
        
        # Gather outcome data
        outcomes: list[OutcomeData] = []
        total_ask_cost = 0.0
        
        for token in market.tokens:
            book = order_books.get(token.token_id)
            if not book:
                return None  # Need all order books
            
            ask = book.best_ask
            if ask is None:
                return None  # Need all asks
            
            ask_size = self._get_size_at_price(book.asks, ask)
            if ask_size <= 0:
                return None
            
            outcomes.append(OutcomeData(
                token=token,
                ask_price=ask,
                ask_size=ask_size,
                order_book=book
            ))
            
            total_ask_cost += ask
        
        # Quick check: is there any edge before fees?
        if total_ask_cost >= 1.0:
            return None  # No gross edge
        
        # Find the limiting outcome (smallest USDC value available)
        min_usdc_available = float('inf')
        limiting_outcome = ""
        
        for outcome in outcomes:
            usdc_value = outcome.ask_size * outcome.ask_price
            if usdc_value < min_usdc_available:
                min_usdc_available = usdc_value
                limiting_outcome = outcome.token.outcome
        
        # Calculate max size
        max_size = min(min_usdc_available, self.max_size)
        
        if max_size < self.min_size:
            return None
        
        # Run cost analysis
        outcome_asks = [o.ask_price for o in outcomes]
        analysis = self.cost_calculator.calculate_categorical_arb(
            outcome_asks=outcome_asks,
            position_size=max_size,
            use_maker=False
        )
        
        # Check threshold
        if not analysis.is_profitable or analysis.net_edge_bps < self.min_edge_bps:
            return None
        
        opportunity = CategoricalArbitrageOpportunity(
            market=market,
            outcomes=outcomes,
            total_cost=total_ask_cost,
            analysis=analysis,
            max_size=max_size,
            limiting_outcome=limiting_outcome,
            timestamp=time.time()
        )
        
        logger.info(
            f"Categorical arb detected",
            extra={
                "market_id": market.condition_id,
                "question": market.question[:50],
                "num_outcomes": num_outcomes,
                "total_cost": total_ask_cost,
                "edge_bps": analysis.net_edge_bps,
                "potential_profit": analysis.potential_profit,
                "max_size": max_size,
                "limiting_outcome": limiting_outcome
            }
        )
        
        self._last_opportunities[market.condition_id] = opportunity
        
        return opportunity
    
    def check_all_markets(
        self,
        markets: list[Market],
        order_books: dict[str, OrderBook]
    ) -> list[CategoricalArbitrageOpportunity]:
        """
        Check all categorical markets for arbitrage.
        
        Args:
            markets: List of markets to check
            order_books: Dict of token_id -> OrderBook
        
        Returns:
            List of detected opportunities, sorted by edge
        """
        opportunities = []
        
        for market in markets:
            if not market.is_categorical:
                continue
            
            opp = self.check_opportunity(market, order_books)
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
    ) -> Optional[CategoricalArbitrageOpportunity]:
        """Get the last detected opportunity for a market."""
        return self._last_opportunities.get(market_id)
    
    def clear_opportunities(self) -> None:
        """Clear cached opportunities."""
        self._last_opportunities.clear()
    
    def estimate_execution_risk(
        self,
        opportunity: CategoricalArbitrageOpportunity
    ) -> float:
        """
        Estimate execution risk for a categorical arb.
        
        More outcomes = higher risk of partial fill.
        
        Returns:
            Risk score 0-1 (higher = riskier)
        """
        num_outcomes = opportunity.num_outcomes
        
        # Base risk increases with number of outcomes
        base_risk = min(0.1 * num_outcomes, 0.5)
        
        # Check liquidity balance
        sizes = [o.ask_size for o in opportunity.outcomes]
        size_ratio = min(sizes) / max(sizes) if max(sizes) > 0 else 0
        
        # Unbalanced liquidity increases risk
        liquidity_risk = 1.0 - size_ratio
        
        # Combined risk
        total_risk = min(base_risk + (liquidity_risk * 0.3), 1.0)
        
        return total_risk

"""
Tests for arbitrage detection logic.
"""

import pytest
from dataclasses import dataclass
from typing import Optional

from src.arbitrage.binary_arb import BinaryArbitrageDetector, BinaryArbitrageOpportunity
from src.arbitrage.categorical_arb import CategoricalArbitrageDetector
from src.clients.websocket_client import OrderBook, OrderBookLevel
from src.clients.gamma_client import Market, Token
from src.utils.cost_calculator import CostCalculator


@pytest.fixture
def cost_calculator():
    """Create cost calculator with known fees."""
    return CostCalculator(
        taker_fee_bps=20,  # 0.2%
        maker_fee_bps=0,
        merge_gas_usd=0.02,
        swap_spread_bps=5,
        safety_buffer_bps=10
    )


@pytest.fixture
def binary_detector(cost_calculator):
    """Create binary arbitrage detector."""
    return BinaryArbitrageDetector(
        cost_calculator=cost_calculator,
        min_edge_bps=50,  # 0.5%
        min_size=1.0,
        max_size=100.0
    )


@pytest.fixture
def categorical_detector(cost_calculator):
    """Create categorical arbitrage detector."""
    return CategoricalArbitrageDetector(
        cost_calculator=cost_calculator,
        min_edge_bps=100,
        min_size=1.0,
        max_size=50.0
    )


def create_binary_market(condition_id: str = "test-market-1") -> Market:
    """Create a test binary market."""
    return Market(
        condition_id=condition_id,
        question_id="q1",
        question="Will it rain tomorrow?",
        tokens=[
            Token(token_id="yes-token-1", outcome="Yes", price=0.45),
            Token(token_id="no-token-1", outcome="No", price=0.50),
        ],
        active=True,
        closed=False
    )


def create_order_book(
    asset_id: str,
    best_bid: float,
    best_ask: float,
    bid_size: float = 100.0,
    ask_size: float = 100.0
) -> OrderBook:
    """Create a test order book."""
    return OrderBook(
        asset_id=asset_id,
        market_id="market-1",
        bids=[OrderBookLevel(price=best_bid, size=bid_size)],
        asks=[OrderBookLevel(price=best_ask, size=ask_size)],
        timestamp=1000.0
    )


class TestBinaryArbitrageDetector:
    """Tests for binary arbitrage detection."""
    
    def test_detects_arbitrage_opportunity(self, binary_detector):
        """Should detect when YES + NO < 1.0 with sufficient edge."""
        market = create_binary_market()
        
        # YES @ 0.40, NO @ 0.50 = 0.90 total = 10% gross edge
        yes_book = create_order_book("yes-token-1", best_bid=0.38, best_ask=0.40)
        no_book = create_order_book("no-token-1", best_bid=0.48, best_ask=0.50)
        
        opportunity = binary_detector.check_opportunity(market, yes_book, no_book)
        
        assert opportunity is not None
        assert opportunity.yes_ask == 0.40
        assert opportunity.no_ask == 0.50
        assert opportunity.analysis.gross_edge == pytest.approx(0.10, rel=0.01)
        assert opportunity.analysis.is_profitable
    
    def test_no_opportunity_when_sum_equals_one(self, binary_detector):
        """Should not detect opportunity when YES + NO = 1.0."""
        market = create_binary_market()
        
        # YES @ 0.50, NO @ 0.50 = 1.0 total = no edge
        yes_book = create_order_book("yes-token-1", best_bid=0.48, best_ask=0.50)
        no_book = create_order_book("no-token-1", best_bid=0.48, best_ask=0.50)
        
        opportunity = binary_detector.check_opportunity(market, yes_book, no_book)
        
        assert opportunity is None
    
    def test_no_opportunity_when_sum_exceeds_one(self, binary_detector):
        """Should not detect opportunity when YES + NO > 1.0."""
        market = create_binary_market()
        
        # YES @ 0.55, NO @ 0.55 = 1.10 total = negative edge
        yes_book = create_order_book("yes-token-1", best_bid=0.53, best_ask=0.55)
        no_book = create_order_book("no-token-1", best_bid=0.53, best_ask=0.55)
        
        opportunity = binary_detector.check_opportunity(market, yes_book, no_book)
        
        assert opportunity is None
    
    def test_edge_below_threshold_rejected(self, binary_detector):
        """Should reject opportunities below minimum edge threshold."""
        market = create_binary_market()
        
        # YES @ 0.495, NO @ 0.50 = 0.995 = 0.5% gross edge
        # After fees (~0.5%), net edge is near zero
        yes_book = create_order_book("yes-token-1", best_bid=0.493, best_ask=0.495)
        no_book = create_order_book("no-token-1", best_bid=0.498, best_ask=0.50)
        
        opportunity = binary_detector.check_opportunity(market, yes_book, no_book)
        
        # Should be rejected because net edge after fees is too low
        assert opportunity is None or opportunity.edge_bps < 50
    
    def test_respects_max_size_limit(self, binary_detector):
        """Should limit max size to configured maximum."""
        market = create_binary_market()
        
        # Large liquidity available
        yes_book = create_order_book("yes-token-1", best_bid=0.38, best_ask=0.40, ask_size=1000)
        no_book = create_order_book("no-token-1", best_bid=0.48, best_ask=0.50, ask_size=1000)
        
        opportunity = binary_detector.check_opportunity(market, yes_book, no_book)
        
        assert opportunity is not None
        assert opportunity.max_size <= 100.0  # detector's max_size
    
    def test_handles_missing_order_book(self, binary_detector):
        """Should return None if order book is missing."""
        market = create_binary_market()
        yes_book = create_order_book("yes-token-1", best_bid=0.38, best_ask=0.40)
        
        opportunity = binary_detector.check_opportunity(market, yes_book, None)
        
        assert opportunity is None
    
    def test_handles_empty_order_book(self, binary_detector):
        """Should return None if order book has no asks."""
        market = create_binary_market()
        
        yes_book = OrderBook(
            asset_id="yes-token-1",
            market_id="market-1",
            bids=[],
            asks=[],
            timestamp=1000.0
        )
        no_book = create_order_book("no-token-1", best_bid=0.48, best_ask=0.50)
        
        opportunity = binary_detector.check_opportunity(market, yes_book, no_book)
        
        assert opportunity is None


class TestCategoricalArbitrageDetector:
    """Tests for categorical arbitrage detection."""
    
    def test_detects_categorical_opportunity(self, categorical_detector):
        """Should detect when sum of all outcomes < 1.0."""
        market = Market(
            condition_id="cat-market-1",
            question_id="q1",
            question="Who will win the election?",
            tokens=[
                Token(token_id="token-a", outcome="Candidate A", price=0.30),
                Token(token_id="token-b", outcome="Candidate B", price=0.30),
                Token(token_id="token-c", outcome="Candidate C", price=0.25),
            ],
            active=True,
            closed=False
        )
        
        order_books = {
            "token-a": create_order_book("token-a", best_bid=0.28, best_ask=0.30),
            "token-b": create_order_book("token-b", best_bid=0.28, best_ask=0.30),
            "token-c": create_order_book("token-c", best_bid=0.23, best_ask=0.25),
        }
        
        opportunity = categorical_detector.check_opportunity(market, order_books)
        
        # Total = 0.30 + 0.30 + 0.25 = 0.85, gross edge = 15%
        assert opportunity is not None
        assert opportunity.total_cost == pytest.approx(0.85, rel=0.01)
        assert opportunity.analysis.is_profitable
    
    def test_no_opportunity_when_sum_equals_one(self, categorical_detector):
        """Should not detect when outcomes sum to 1.0."""
        market = Market(
            condition_id="cat-market-2",
            question_id="q2",
            question="Which team wins?",
            tokens=[
                Token(token_id="token-1", outcome="Team 1", price=0.33),
                Token(token_id="token-2", outcome="Team 2", price=0.33),
                Token(token_id="token-3", outcome="Team 3", price=0.34),
            ],
            active=True,
            closed=False
        )
        
        order_books = {
            "token-1": create_order_book("token-1", best_bid=0.31, best_ask=0.33),
            "token-2": create_order_book("token-2", best_bid=0.31, best_ask=0.33),
            "token-3": create_order_book("token-3", best_bid=0.32, best_ask=0.34),
        }
        
        opportunity = categorical_detector.check_opportunity(market, order_books)
        
        assert opportunity is None
    
    def test_rejects_binary_markets(self, categorical_detector):
        """Should not process binary markets."""
        market = create_binary_market()
        
        order_books = {
            "yes-token-1": create_order_book("yes-token-1", best_bid=0.28, best_ask=0.30),
            "no-token-1": create_order_book("no-token-1", best_bid=0.28, best_ask=0.30),
        }
        
        opportunity = categorical_detector.check_opportunity(market, order_books)
        
        assert opportunity is None


class TestCostCalculator:
    """Tests for cost calculation."""
    
    def test_binary_arb_cost_calculation(self, cost_calculator):
        """Should correctly calculate costs for binary arb."""
        analysis = cost_calculator.calculate_binary_arb(
            yes_ask=0.40,
            no_ask=0.50,
            position_size=100.0,
            use_maker=False
        )
        
        # Gross edge = 1.0 - 0.40 - 0.50 = 0.10 (10%)
        assert analysis.gross_edge == pytest.approx(0.10, rel=0.01)
        
        # Costs should include fees, gas, buffer
        assert analysis.costs.total > 0
        
        # Net edge should be less than gross
        assert analysis.net_edge < analysis.gross_edge
        
        # Should be profitable with 10% edge
        assert analysis.is_profitable
    
    def test_minimum_edge_calculation(self, cost_calculator):
        """Should calculate minimum required edge for profitability."""
        min_edge = cost_calculator.minimum_edge_for_profit(
            position_size=100.0,
            num_outcomes=2,
            use_maker=False
        )
        
        # Should be positive and reasonable
        assert min_edge > 0
        assert min_edge < 0.05  # Less than 5%

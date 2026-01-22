"""
Tests for order execution logic.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from src.execution.executor import (
    OrderExecutor,
    ArbitrageTrade,
    OrderLeg,
    TradeState,
)
from src.execution.merger import TokenMerger, MergeResult
from src.clients.clob_client import CLOBClient, OrderSide, OrderResult, OrderStatus
from src.clients.polygon_client import PolygonClient, TransactionResult
from src.arbitrage.binary_arb import BinaryArbitrageOpportunity
from src.clients.gamma_client import Market, Token
from src.utils.cost_calculator import CostCalculator, ArbitrageAnalysis, TradeCosts


@pytest.fixture
def mock_clob_client():
    """Create a mock CLOB client."""
    client = AsyncMock(spec=CLOBClient)
    
    # Default successful order placement
    client.place_order.return_value = OrderResult(
        order_id="order-123",
        success=True,
        status="LIVE",
        timestamp=1000.0
    )
    
    client.place_orders_parallel.return_value = [
        OrderResult(order_id="order-1", success=True, status="LIVE", timestamp=1000.0),
        OrderResult(order_id="order-2", success=True, status="LIVE", timestamp=1000.0),
    ]
    
    # Default order status (fully filled)
    client.get_order.return_value = OrderStatus(
        order_id="order-123",
        status="MATCHED",
        size_matched=10.0,
        size_remaining=0.0,
        avg_price=0.45
    )
    
    return client


@pytest.fixture
def mock_polygon_client():
    """Create a mock Polygon client."""
    client = AsyncMock(spec=PolygonClient)
    
    client.merge_positions.return_value = TransactionResult(
        success=True,
        tx_hash="0x123abc",
        gas_used=80000,
        gas_cost_wei=1000000000000000,
        gas_cost_usd=0.02
    )
    
    return client


@pytest.fixture
def sample_opportunity():
    """Create a sample arbitrage opportunity."""
    market = Market(
        condition_id="test-condition-1",
        question_id="q1",
        question="Test market?",
        tokens=[
            Token(token_id="yes-token", outcome="Yes", price=0.45),
            Token(token_id="no-token", outcome="No", price=0.50),
        ]
    )
    
    analysis = ArbitrageAnalysis(
        gross_edge=0.05,
        costs=TradeCosts(
            clob_fee=0.004,
            merge_gas=0.0002,
            swap_spread=0.0005,
            buffer=0.001,
            total=0.0057
        ),
        net_edge=0.0443,
        net_edge_bps=44.3,
        is_profitable=True,
        potential_profit=4.43
    )
    
    return BinaryArbitrageOpportunity(
        market=market,
        yes_token_id="yes-token",
        no_token_id="no-token",
        yes_ask=0.45,
        no_ask=0.50,
        yes_ask_size=100.0,
        no_ask_size=100.0,
        analysis=analysis,
        max_size=50.0,
        timestamp=1000.0
    )


class TestOrderExecutor:
    """Tests for order execution."""
    
    @pytest.mark.asyncio
    async def test_execute_creates_trade(self, mock_clob_client, sample_opportunity):
        """Should create a trade with correct legs."""
        executor = OrderExecutor(
            clob_client=mock_clob_client,
            max_concurrent_trades=5,
            fill_timeout_seconds=1.0,
            poll_interval_seconds=0.1
        )
        
        trade = await executor.execute_arbitrage(sample_opportunity)
        
        assert trade is not None
        assert trade.trade_id is not None
        assert len(trade.legs) == 2
        assert trade.opportunity == sample_opportunity
    
    @pytest.mark.asyncio
    async def test_places_orders_in_parallel(self, mock_clob_client, sample_opportunity):
        """Should place both order legs in parallel."""
        executor = OrderExecutor(
            clob_client=mock_clob_client,
            max_concurrent_trades=5,
            fill_timeout_seconds=1.0,
            poll_interval_seconds=0.1
        )
        
        await executor.execute_arbitrage(sample_opportunity)
        
        # Should have called place_orders_parallel once with 2 orders
        mock_clob_client.place_orders_parallel.assert_called_once()
        call_args = mock_clob_client.place_orders_parallel.call_args[0][0]
        assert len(call_args) == 2
    
    @pytest.mark.asyncio
    async def test_fully_filled_trade(self, mock_clob_client, sample_opportunity):
        """Should mark trade as fully filled when all legs match."""
        mock_clob_client.get_order.return_value = OrderStatus(
            order_id="order-123",
            status="MATCHED",
            size_matched=50.0,
            size_remaining=0.0,
            avg_price=0.45
        )
        
        executor = OrderExecutor(
            clob_client=mock_clob_client,
            max_concurrent_trades=5,
            fill_timeout_seconds=1.0,
            poll_interval_seconds=0.1
        )
        
        trade = await executor.execute_arbitrage(sample_opportunity)
        
        assert trade.state == TradeState.FULLY_FILLED
    
    @pytest.mark.asyncio
    async def test_max_concurrent_trades_limit(self, mock_clob_client, sample_opportunity):
        """Should reject trades when at capacity."""
        executor = OrderExecutor(
            clob_client=mock_clob_client,
            max_concurrent_trades=1,
            fill_timeout_seconds=5.0,
            poll_interval_seconds=0.1
        )
        
        # Make the first trade slow
        async def slow_get_order(order_id):
            await asyncio.sleep(10.0)
            return OrderStatus(
                order_id=order_id,
                status="MATCHED",
                size_matched=50.0,
                size_remaining=0.0
            )
        
        mock_clob_client.get_order = slow_get_order
        
        # Start first trade (will be slow)
        task1 = asyncio.create_task(executor.execute_arbitrage(sample_opportunity))
        
        # Give it time to start
        await asyncio.sleep(0.1)
        
        # Second trade should be rejected
        trade2 = await executor.execute_arbitrage(sample_opportunity)
        
        assert trade2.state == TradeState.FAILED
        assert "Max concurrent trades" in trade2.error
        
        # Clean up
        task1.cancel()
        try:
            await task1
        except asyncio.CancelledError:
            pass


class TestTokenMerger:
    """Tests for token merging."""
    
    @pytest.mark.asyncio
    async def test_merge_fully_filled_trade(self, mock_polygon_client):
        """Should merge tokens for a fully filled trade."""
        merger = TokenMerger(
            polygon_client=mock_polygon_client,
            min_merge_amount=1.0
        )
        
        # Create a fully filled trade
        market = Market(
            condition_id="test-condition-1",
            question_id="q1",
            question="Test?",
            tokens=[
                Token(token_id="yes", outcome="Yes", price=0.45),
                Token(token_id="no", outcome="No", price=0.50),
            ]
        )
        
        # Mock opportunity
        analysis = ArbitrageAnalysis(
            gross_edge=0.05,
            costs=TradeCosts(0.004, 0.0002, 0.0005, 0.001, 0.0057),
            net_edge=0.0443,
            net_edge_bps=44.3,
            is_profitable=True,
            potential_profit=4.43
        )
        
        opportunity = MagicMock()
        opportunity.market = market
        opportunity.analysis = analysis
        
        trade = ArbitrageTrade(
            trade_id="trade-1",
            opportunity=opportunity,
            legs=[
                OrderLeg(
                    token_id="yes",
                    side=OrderSide.BUY,
                    size=100.0,
                    price=0.45,
                    order_id="order-1",
                    status="MATCHED",
                    filled_size=100.0,
                    filled_price=0.45
                ),
                OrderLeg(
                    token_id="no",
                    side=OrderSide.BUY,
                    size=100.0,
                    price=0.50,
                    order_id="order-2",
                    status="MATCHED",
                    filled_size=100.0,
                    filled_price=0.50
                )
            ],
            state=TradeState.FULLY_FILLED,
            expected_profit=4.43
        )
        
        result = await merger.merge_trade(trade)
        
        assert result.success
        assert result.tx_hash == "0x123abc"
        assert result.gas_used == 80000
        assert trade.state == TradeState.COMPLETED
    
    @pytest.mark.asyncio
    async def test_rejects_unfilled_trade(self, mock_polygon_client):
        """Should reject trades that aren't fully filled."""
        merger = TokenMerger(
            polygon_client=mock_polygon_client,
            min_merge_amount=1.0
        )
        
        trade = ArbitrageTrade(
            trade_id="trade-1",
            opportunity=MagicMock(),
            legs=[],
            state=TradeState.PARTIALLY_FILLED
        )
        
        result = await merger.merge_trade(trade)
        
        assert not result.success
        assert "not fully filled" in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_merge_retry_on_failure(self, mock_polygon_client):
        """Should retry merge on transient failure."""
        # First two calls fail, third succeeds
        mock_polygon_client.merge_positions.side_effect = [
            TransactionResult(
                success=False,
                tx_hash="",
                gas_used=0,
                gas_cost_wei=0,
                gas_cost_usd=0.0,
                error="Network error"
            ),
            TransactionResult(
                success=False,
                tx_hash="",
                gas_used=0,
                gas_cost_wei=0,
                gas_cost_usd=0.0,
                error="Network error"
            ),
            TransactionResult(
                success=True,
                tx_hash="0x123",
                gas_used=80000,
                gas_cost_wei=1000000000000000,
                gas_cost_usd=0.02
            )
        ]
        
        merger = TokenMerger(
            polygon_client=mock_polygon_client,
            min_merge_amount=1.0,
            max_retries=3
        )
        
        # Create fully filled trade
        trade = ArbitrageTrade(
            trade_id="trade-1",
            opportunity=MagicMock(),
            legs=[
                OrderLeg(
                    token_id="yes",
                    side=OrderSide.BUY,
                    size=100.0,
                    price=0.45,
                    filled_size=100.0,
                    filled_price=0.45
                ),
                OrderLeg(
                    token_id="no",
                    side=OrderSide.BUY,
                    size=100.0,
                    price=0.50,
                    filled_size=100.0,
                    filled_price=0.50
                )
            ],
            state=TradeState.FULLY_FILLED
        )
        trade.opportunity.market.condition_id = "cond-1"
        
        result = await merger.merge_trade(trade)
        
        # Should have called merge 3 times
        assert mock_polygon_client.merge_positions.call_count == 3
        assert result.success

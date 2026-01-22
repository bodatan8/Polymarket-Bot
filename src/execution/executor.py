"""
Order execution engine for arbitrage trades.
Handles parallel order placement and fill monitoring.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Union
from enum import Enum
import time
import uuid

from ..clients.clob_client import CLOBClient, OrderSide, OrderResult, OrderStatus
from ..arbitrage.binary_arb import BinaryArbitrageOpportunity
from ..arbitrage.categorical_arb import CategoricalArbitrageOpportunity
from ..utils.logger import get_logger, TradeLogger

logger = get_logger("executor")
trade_logger = TradeLogger()

ArbitrageOpportunity = Union[BinaryArbitrageOpportunity, CategoricalArbitrageOpportunity]


class TradeState(Enum):
    """State of an arbitrage trade."""
    PENDING = "pending"
    PLACING_ORDERS = "placing_orders"
    MONITORING_FILLS = "monitoring_fills"
    PARTIALLY_FILLED = "partially_filled"
    FULLY_FILLED = "fully_filled"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class OrderLeg:
    """Single leg of an arbitrage trade."""
    token_id: str
    side: OrderSide
    size: float
    price: float
    order_id: Optional[str] = None
    status: str = "pending"
    filled_size: float = 0.0
    filled_price: Optional[float] = None


@dataclass
class ArbitrageTrade:
    """Complete arbitrage trade with all legs."""
    trade_id: str
    opportunity: ArbitrageOpportunity
    legs: list[OrderLeg]
    state: TradeState = TradeState.PENDING
    expected_profit: float = 0.0
    actual_profit: Optional[float] = None
    start_time: float = 0.0
    end_time: Optional[float] = None
    error: Optional[str] = None
    
    @property
    def is_binary(self) -> bool:
        return len(self.legs) == 2
    
    @property
    def all_filled(self) -> bool:
        return all(leg.filled_size >= leg.size * 0.99 for leg in self.legs)
    
    @property
    def any_filled(self) -> bool:
        return any(leg.filled_size > 0 for leg in self.legs)
    
    @property
    def duration_ms(self) -> float:
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time) * 1000
        return 0.0


class OrderExecutor:
    """
    Executes arbitrage trades by placing orders and monitoring fills.
    
    Key responsibilities:
    - Place all legs of an arbitrage trade in parallel
    - Monitor order fills
    - Handle partial fills (cancel remaining orders)
    - Signal when trade is ready for merge
    """
    
    def __init__(
        self,
        clob_client: CLOBClient,
        max_concurrent_trades: int = 5,
        fill_timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.5
    ):
        """
        Initialize order executor.
        
        Args:
            clob_client: CLOB client for order operations
            max_concurrent_trades: Maximum concurrent trades
            fill_timeout_seconds: Timeout for waiting for fills
            poll_interval_seconds: Interval for polling order status
        """
        self.clob_client = clob_client
        self.max_concurrent_trades = max_concurrent_trades
        self.fill_timeout_seconds = fill_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        
        # Active trades
        self._active_trades: dict[str, ArbitrageTrade] = {}
        self._completed_trades: list[ArbitrageTrade] = []
        
        # Lock for thread safety
        self._lock = asyncio.Lock()
    
    async def execute_arbitrage(
        self,
        opportunity: ArbitrageOpportunity
    ) -> ArbitrageTrade:
        """
        Execute an arbitrage opportunity.
        
        Args:
            opportunity: Detected arbitrage opportunity
        
        Returns:
            ArbitrageTrade with execution results
        """
        # Check capacity
        if len(self._active_trades) >= self.max_concurrent_trades:
            logger.warning("Max concurrent trades reached, rejecting opportunity")
            trade = self._create_trade(opportunity)
            trade.state = TradeState.FAILED
            trade.error = "Max concurrent trades reached"
            return trade
        
        # Create trade
        trade = self._create_trade(opportunity)
        trade.start_time = time.time()
        
        async with self._lock:
            self._active_trades[trade.trade_id] = trade
        
        try:
            # Place orders
            trade.state = TradeState.PLACING_ORDERS
            await self._place_orders(trade)
            
            # Monitor fills
            trade.state = TradeState.MONITORING_FILLS
            await self._monitor_fills(trade)
            
            # Check result
            if trade.all_filled:
                trade.state = TradeState.FULLY_FILLED
                logger.info(
                    f"Trade fully filled",
                    extra={
                        "trade_id": trade.trade_id,
                        "duration_ms": trade.duration_ms
                    }
                )
            else:
                # Partial fill - need to handle
                trade.state = TradeState.PARTIALLY_FILLED
                await self._handle_partial_fill(trade)
            
        except Exception as e:
            trade.state = TradeState.FAILED
            trade.error = str(e)
            trade.end_time = time.time()
            
            trade_logger.trade_failed(
                trade_id=trade.trade_id,
                market_id=opportunity.market.condition_id,
                reason="Execution error",
                error=str(e)
            )
            
            # Try to cancel any open orders
            await self._cancel_trade_orders(trade)
        
        finally:
            async with self._lock:
                self._active_trades.pop(trade.trade_id, None)
                self._completed_trades.append(trade)
        
        return trade
    
    def _create_trade(self, opportunity: ArbitrageOpportunity) -> ArbitrageTrade:
        """Create trade from opportunity."""
        trade_id = str(uuid.uuid4())[:8]
        legs = []
        
        if isinstance(opportunity, BinaryArbitrageOpportunity):
            # Binary: buy YES and NO
            legs = [
                OrderLeg(
                    token_id=opportunity.yes_token_id,
                    side=OrderSide.BUY,
                    size=opportunity.max_size / opportunity.yes_ask,
                    price=opportunity.yes_ask
                ),
                OrderLeg(
                    token_id=opportunity.no_token_id,
                    side=OrderSide.BUY,
                    size=opportunity.max_size / opportunity.no_ask,
                    price=opportunity.no_ask
                )
            ]
            expected_profit = opportunity.analysis.potential_profit
            
        elif isinstance(opportunity, CategoricalArbitrageOpportunity):
            # Categorical: buy all outcomes
            for outcome in opportunity.outcomes:
                legs.append(OrderLeg(
                    token_id=outcome.token.token_id,
                    side=OrderSide.BUY,
                    size=opportunity.max_size / outcome.ask_price,
                    price=outcome.ask_price
                ))
            expected_profit = opportunity.analysis.potential_profit
        
        else:
            expected_profit = 0.0
        
        return ArbitrageTrade(
            trade_id=trade_id,
            opportunity=opportunity,
            legs=legs,
            expected_profit=expected_profit
        )
    
    async def _place_orders(self, trade: ArbitrageTrade) -> None:
        """Place all orders for a trade in parallel."""
        logger.info(
            f"Placing {len(trade.legs)} orders",
            extra={"trade_id": trade.trade_id}
        )
        
        # Build order tuples
        orders = [
            (leg.token_id, leg.side, leg.size, leg.price)
            for leg in trade.legs
        ]
        
        # Place in parallel
        results = await self.clob_client.place_orders_parallel(orders)
        
        # Update legs with order IDs
        success_count = 0
        for i, (leg, result) in enumerate(zip(trade.legs, results)):
            leg.order_id = result.order_id
            leg.status = result.status
            
            if result.success:
                success_count += 1
                trade_logger.order_placed(
                    trade_id=trade.trade_id,
                    market_id=trade.opportunity.market.condition_id,
                    side=leg.side.value,
                    size=leg.size,
                    price=leg.price
                )
            else:
                logger.error(
                    f"Failed to place order",
                    extra={
                        "trade_id": trade.trade_id,
                        "leg": i,
                        "error": result.error
                    }
                )
        
        if success_count < len(trade.legs):
            # Some orders failed - cancel the rest
            trade.state = TradeState.FAILED
            trade.error = f"Only {success_count}/{len(trade.legs)} orders placed"
            await self._cancel_trade_orders(trade)
            raise RuntimeError(trade.error)
    
    async def _monitor_fills(self, trade: ArbitrageTrade) -> None:
        """Monitor orders until filled or timeout."""
        start_time = time.time()
        
        while time.time() - start_time < self.fill_timeout_seconds:
            all_done = True
            
            for leg in trade.legs:
                if not leg.order_id:
                    continue
                
                if leg.status in ("MATCHED", "FILLED"):
                    continue
                
                # Query order status
                status = await self.clob_client.get_order(leg.order_id)
                
                if status:
                    leg.status = status.status
                    leg.filled_size = status.size_matched
                    leg.filled_price = status.avg_price
                    
                    if status.status in ("MATCHED", "FILLED"):
                        trade_logger.order_filled(
                            trade_id=trade.trade_id,
                            market_id=trade.opportunity.market.condition_id,
                            fill_price=status.avg_price or leg.price,
                            fill_size=status.size_matched
                        )
                    elif status.status == "LIVE":
                        all_done = False
            
            if all_done:
                break
            
            await asyncio.sleep(self.poll_interval_seconds)
        
        trade.end_time = time.time()
    
    async def _handle_partial_fill(self, trade: ArbitrageTrade) -> None:
        """Handle a trade with partial fills."""
        logger.warning(
            f"Trade partially filled",
            extra={
                "trade_id": trade.trade_id,
                "filled_legs": sum(1 for leg in trade.legs if leg.filled_size > 0),
                "total_legs": len(trade.legs)
            }
        )
        
        # Cancel unfilled orders
        await self._cancel_trade_orders(trade)
        
        # For now, mark as failed
        # In production, you might want to handle this differently
        # (e.g., sell the filled positions)
        trade.state = TradeState.FAILED
        trade.error = "Partial fill - orders cancelled"
        
        trade_logger.trade_failed(
            trade_id=trade.trade_id,
            market_id=trade.opportunity.market.condition_id,
            reason="Partial fill",
            error="Not all legs filled within timeout"
        )
    
    async def _cancel_trade_orders(self, trade: ArbitrageTrade) -> None:
        """Cancel all open orders for a trade."""
        for leg in trade.legs:
            if leg.order_id and leg.status == "LIVE":
                try:
                    await self.clob_client.cancel_order(leg.order_id)
                    leg.status = "CANCELLED"
                except Exception as e:
                    logger.error(f"Failed to cancel order {leg.order_id}: {e}")
    
    def get_active_trades(self) -> list[ArbitrageTrade]:
        """Get list of active trades."""
        return list(self._active_trades.values())
    
    def get_completed_trades(self, limit: int = 100) -> list[ArbitrageTrade]:
        """Get list of completed trades."""
        return self._completed_trades[-limit:]
    
    def get_trade(self, trade_id: str) -> Optional[ArbitrageTrade]:
        """Get trade by ID."""
        if trade_id in self._active_trades:
            return self._active_trades[trade_id]
        
        for trade in self._completed_trades:
            if trade.trade_id == trade_id:
                return trade
        
        return None
    
    async def cancel_all_active(self) -> int:
        """Cancel all active trades."""
        cancelled = 0
        for trade in list(self._active_trades.values()):
            await self._cancel_trade_orders(trade)
            trade.state = TradeState.CANCELLED
            cancelled += 1
        return cancelled

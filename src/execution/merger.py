"""
Token merger for completing arbitrage trades.
Merges YES+NO tokens (or all categorical outcomes) to receive USDC.
"""

import asyncio
from dataclasses import dataclass
from typing import Optional
import time

from ..clients.polygon_client import PolygonClient, TransactionResult
from ..utils.logger import get_logger, TradeLogger
from .executor import ArbitrageTrade, TradeState

logger = get_logger("merger")
trade_logger = TradeLogger()


@dataclass
class MergeResult:
    """Result of a token merge operation."""
    success: bool
    trade_id: str
    tx_hash: str
    gas_used: int
    gas_cost_usd: float
    amount_merged: float
    profit_realized: float
    error: Optional[str] = None


class TokenMerger:
    """
    Merges conditional tokens to receive USDC payout.
    
    After buying YES and NO tokens (or all categorical outcomes),
    they can be merged on-chain via the CTF Exchange contract
    to receive ~$1.00 worth of USDC per token set.
    """
    
    def __init__(
        self,
        polygon_client: PolygonClient,
        min_merge_amount: float = 1.0,
        max_retries: int = 3
    ):
        """
        Initialize token merger.
        
        Args:
            polygon_client: Polygon blockchain client
            min_merge_amount: Minimum amount to merge
            max_retries: Maximum retry attempts for failed merges
        """
        self.polygon_client = polygon_client
        self.min_merge_amount = min_merge_amount
        self.max_retries = max_retries
        
        # Track pending merges
        self._pending_merges: dict[str, ArbitrageTrade] = {}
        self._merge_results: list[MergeResult] = []
    
    async def merge_trade(self, trade: ArbitrageTrade) -> MergeResult:
        """
        Merge tokens for a completed trade.
        
        Args:
            trade: Fully-filled arbitrage trade
        
        Returns:
            MergeResult with transaction details
        """
        if trade.state != TradeState.FULLY_FILLED:
            return MergeResult(
                success=False,
                trade_id=trade.trade_id,
                tx_hash="",
                gas_used=0,
                gas_cost_usd=0.0,
                amount_merged=0.0,
                profit_realized=0.0,
                error=f"Trade not fully filled: {trade.state.value}"
            )
        
        logger.info(
            f"Starting merge for trade {trade.trade_id}",
            extra={"condition_id": trade.opportunity.market.condition_id}
        )
        
        trade.state = TradeState.MERGING
        self._pending_merges[trade.trade_id] = trade
        
        try:
            # Calculate merge amount (minimum across all legs)
            merge_amount = min(leg.filled_size for leg in trade.legs)
            
            if merge_amount < self.min_merge_amount:
                return MergeResult(
                    success=False,
                    trade_id=trade.trade_id,
                    tx_hash="",
                    gas_used=0,
                    gas_cost_usd=0.0,
                    amount_merged=0.0,
                    profit_realized=0.0,
                    error=f"Merge amount {merge_amount} below minimum {self.min_merge_amount}"
                )
            
            # Execute merge with retries
            result = await self._execute_merge_with_retry(
                trade=trade,
                amount=merge_amount
            )
            
            if result.success:
                trade.state = TradeState.COMPLETED
                trade.actual_profit = result.profit_realized
                trade.end_time = time.time()
                
                trade_logger.trade_completed(
                    trade_id=trade.trade_id,
                    market_id=trade.opportunity.market.condition_id,
                    expected_profit=trade.expected_profit,
                    actual_profit=result.profit_realized,
                    latency_ms=trade.duration_ms
                )
                
                trade_logger.merge_completed(
                    trade_id=trade.trade_id,
                    tx_hash=result.tx_hash,
                    gas_used=result.gas_used,
                    gas_cost_usd=result.gas_cost_usd
                )
            else:
                trade.state = TradeState.FAILED
                trade.error = result.error
                
                trade_logger.trade_failed(
                    trade_id=trade.trade_id,
                    market_id=trade.opportunity.market.condition_id,
                    reason="Merge failed",
                    error=result.error
                )
            
            self._merge_results.append(result)
            return result
            
        finally:
            self._pending_merges.pop(trade.trade_id, None)
    
    async def _execute_merge_with_retry(
        self,
        trade: ArbitrageTrade,
        amount: float
    ) -> MergeResult:
        """Execute merge with retries on failure."""
        condition_id = trade.opportunity.market.condition_id
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                logger.info(
                    f"Merge attempt {attempt + 1}/{self.max_retries}",
                    extra={
                        "trade_id": trade.trade_id,
                        "amount": amount
                    }
                )
                
                tx_result = await self.polygon_client.merge_positions(
                    condition_id=condition_id,
                    amount=amount
                )
                
                if tx_result.success:
                    # Calculate profit
                    # Profit = payout ($1 per token) - cost - gas
                    total_cost = sum(
                        leg.filled_size * (leg.filled_price or leg.price)
                        for leg in trade.legs
                    )
                    payout = amount  # $1 per token set
                    profit = payout - total_cost - tx_result.gas_cost_usd
                    
                    return MergeResult(
                        success=True,
                        trade_id=trade.trade_id,
                        tx_hash=tx_result.tx_hash,
                        gas_used=tx_result.gas_used,
                        gas_cost_usd=tx_result.gas_cost_usd,
                        amount_merged=amount,
                        profit_realized=profit
                    )
                else:
                    last_error = tx_result.error
                    
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Merge attempt {attempt + 1} failed: {e}",
                    extra={"trade_id": trade.trade_id}
                )
            
            # Wait before retry
            if attempt < self.max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
        
        return MergeResult(
            success=False,
            trade_id=trade.trade_id,
            tx_hash="",
            gas_used=0,
            gas_cost_usd=0.0,
            amount_merged=0.0,
            profit_realized=0.0,
            error=f"Merge failed after {self.max_retries} attempts: {last_error}"
        )
    
    def get_pending_merges(self) -> list[ArbitrageTrade]:
        """Get list of trades with pending merges."""
        return list(self._pending_merges.values())
    
    def get_merge_results(self, limit: int = 100) -> list[MergeResult]:
        """Get recent merge results."""
        return self._merge_results[-limit:]
    
    def get_stats(self) -> dict:
        """Get merge statistics."""
        if not self._merge_results:
            return {
                "total_merges": 0,
                "successful_merges": 0,
                "failed_merges": 0,
                "total_profit": 0.0,
                "total_gas_cost": 0.0
            }
        
        successful = [r for r in self._merge_results if r.success]
        failed = [r for r in self._merge_results if not r.success]
        
        return {
            "total_merges": len(self._merge_results),
            "successful_merges": len(successful),
            "failed_merges": len(failed),
            "total_profit": sum(r.profit_realized for r in successful),
            "total_gas_cost": sum(r.gas_cost_usd for r in successful),
            "success_rate": len(successful) / len(self._merge_results) if self._merge_results else 0.0
        }

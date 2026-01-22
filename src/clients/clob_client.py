"""
CLOB client wrapper for Polymarket order operations.
Wraps py-clob-client with async support and error handling.
"""

import asyncio
from dataclasses import dataclass
from typing import Optional, Literal
from enum import Enum
import time

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from ..utils.logger import get_logger

logger = get_logger("clob")


class OrderSide(Enum):
    """Order side enum."""
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class OrderResult:
    """Result of an order placement."""
    order_id: str
    success: bool
    status: str
    error: Optional[str] = None
    timestamp: float = 0.0


@dataclass
class OrderStatus:
    """Current status of an order."""
    order_id: str
    status: str  # LIVE, MATCHED, CANCELLED
    size_matched: float
    size_remaining: float
    avg_price: Optional[float] = None


class CLOBClient:
    """
    Async wrapper for Polymarket CLOB client.
    
    Handles order placement, cancellation, and status queries.
    Uses the official py-clob-client under the hood.
    """
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        private_key: str,
        chain_id: int = 137  # Polygon Mainnet
    ):
        """
        Initialize CLOB client.
        
        Args:
            api_key: Polymarket API key
            api_secret: Polymarket API secret
            api_passphrase: Polymarket API passphrase
            private_key: Wallet private key
            chain_id: Blockchain chain ID (137 for Polygon)
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.private_key = private_key
        self.chain_id = chain_id
        
        self._client: Optional[ClobClient] = None
        self._executor = None
    
    async def initialize(self) -> None:
        """Initialize the CLOB client."""
        logger.info("Initializing CLOB client")
        
        # Create client in executor since it may do blocking I/O
        loop = asyncio.get_event_loop()
        self._client = await loop.run_in_executor(
            None,
            self._create_client
        )
        
        logger.info("CLOB client initialized successfully")
    
    def _create_client(self) -> ClobClient:
        """Create the underlying py-clob-client instance."""
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=self.private_key,
            chain_id=self.chain_id,
            creds={
                "apiKey": self.api_key,
                "secret": self.api_secret,
                "passphrase": self.api_passphrase
            }
        )
        return client
    
    async def place_order(
        self,
        token_id: str,
        side: OrderSide,
        size: float,
        price: float,
        order_type: Literal["GTC", "FOK", "GTD"] = "GTC"
    ) -> OrderResult:
        """
        Place an order on the CLOB.
        
        Args:
            token_id: Token ID (asset ID) to trade
            side: BUY or SELL
            size: Order size
            price: Order price (0-1)
            order_type: Order type (GTC, FOK, GTD)
        
        Returns:
            OrderResult with order ID and status
        """
        if not self._client:
            raise RuntimeError("CLOB client not initialized")
        
        logger.debug(
            f"Placing order: {side.value} {size} @ {price} for {token_id}"
        )
        
        try:
            loop = asyncio.get_event_loop()
            
            # Build order args
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY if side == OrderSide.BUY else SELL
            )
            
            # Create and post order
            signed_order = await loop.run_in_executor(
                None,
                lambda: self._client.create_order(order_args)
            )
            
            result = await loop.run_in_executor(
                None,
                lambda: self._client.post_order(signed_order, order_type=OrderType.GTC)
            )
            
            order_id = result.get("orderID", "")
            
            logger.info(
                f"Order placed successfully",
                extra={
                    "order_id": order_id,
                    "token_id": token_id,
                    "side": side.value,
                    "size": size,
                    "price": price
                }
            )
            
            return OrderResult(
                order_id=order_id,
                success=True,
                status="LIVE",
                timestamp=time.time()
            )
            
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return OrderResult(
                order_id="",
                success=False,
                status="FAILED",
                error=str(e),
                timestamp=time.time()
            )
    
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.
        
        Args:
            order_id: Order ID to cancel
        
        Returns:
            True if cancelled successfully
        """
        if not self._client:
            raise RuntimeError("CLOB client not initialized")
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._client.cancel(order_id)
            )
            logger.info(f"Order cancelled: {order_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False
    
    async def cancel_all_orders(self) -> int:
        """
        Cancel all open orders.
        
        Returns:
            Number of orders cancelled
        """
        if not self._client:
            raise RuntimeError("CLOB client not initialized")
        
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._client.cancel_all()
            )
            cancelled = result.get("cancelled", 0)
            logger.info(f"Cancelled {cancelled} orders")
            return cancelled
            
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return 0
    
    async def get_order(self, order_id: str) -> Optional[OrderStatus]:
        """
        Get status of an order.
        
        Args:
            order_id: Order ID to query
        
        Returns:
            OrderStatus or None if not found
        """
        if not self._client:
            raise RuntimeError("CLOB client not initialized")
        
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._client.get_order(order_id)
            )
            
            if not result:
                return None
            
            return OrderStatus(
                order_id=order_id,
                status=result.get("status", "UNKNOWN"),
                size_matched=float(result.get("size_matched", 0)),
                size_remaining=float(result.get("original_size", 0)) - float(result.get("size_matched", 0)),
                avg_price=float(result.get("price", 0)) if result.get("price") else None
            )
            
        except Exception as e:
            logger.error(f"Failed to get order {order_id}: {e}")
            return None
    
    async def get_open_orders(self) -> list[OrderStatus]:
        """Get all open orders."""
        if not self._client:
            raise RuntimeError("CLOB client not initialized")
        
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._client.get_orders()
            )
            
            orders = []
            for order in result:
                orders.append(OrderStatus(
                    order_id=order.get("id", ""),
                    status=order.get("status", "UNKNOWN"),
                    size_matched=float(order.get("size_matched", 0)),
                    size_remaining=float(order.get("original_size", 0)) - float(order.get("size_matched", 0))
                ))
            
            return orders
            
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []
    
    async def place_orders_parallel(
        self,
        orders: list[tuple[str, OrderSide, float, float]]
    ) -> list[OrderResult]:
        """
        Place multiple orders in parallel.
        
        Args:
            orders: List of (token_id, side, size, price) tuples
        
        Returns:
            List of OrderResult for each order
        """
        tasks = [
            self.place_order(token_id, side, size, price)
            for token_id, side, size, price in orders
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Convert exceptions to failed OrderResults
        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed.append(OrderResult(
                    order_id="",
                    success=False,
                    status="FAILED",
                    error=str(result),
                    timestamp=time.time()
                ))
            else:
                processed.append(result)
        
        return processed

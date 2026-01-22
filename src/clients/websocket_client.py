"""
WebSocket client for Polymarket CLOB real-time data.
Handles connection, subscription, and message parsing.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Any
from enum import Enum

import websockets
from websockets.client import WebSocketClientProtocol

from ..utils.logger import get_logger

logger = get_logger("websocket")


class MessageType(Enum):
    """WebSocket message types from Polymarket."""
    BOOK = "book"
    PRICE_CHANGE = "price_change"
    LAST_TRADE_PRICE = "last_trade_price"
    TICK_SIZE_CHANGE = "tick_size_change"


@dataclass
class OrderBookLevel:
    """Single level in the order book."""
    price: float
    size: float


@dataclass
class OrderBook:
    """Order book state for a market."""
    asset_id: str  # Token ID
    market_id: str
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)
    timestamp: float = 0.0
    
    @property
    def best_bid(self) -> Optional[float]:
        """Get best bid price."""
        if self.bids:
            return max(level.price for level in self.bids)
        return None
    
    @property
    def best_ask(self) -> Optional[float]:
        """Get best ask price."""
        if self.asks:
            return min(level.price for level in self.asks)
        return None
    
    @property
    def spread(self) -> Optional[float]:
        """Get bid-ask spread."""
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None


@dataclass
class PriceUpdate:
    """Price update message from WebSocket."""
    asset_id: str
    market_id: str
    price: float
    timestamp: float


class WebSocketClient:
    """
    Async WebSocket client for Polymarket CLOB.
    
    Connects to the CLOB WebSocket and streams real-time order book updates.
    Implements automatic reconnection with exponential backoff.
    """
    
    BASE_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    def __init__(
        self,
        on_book_update: Optional[Callable[[OrderBook], Any]] = None,
        on_price_update: Optional[Callable[[PriceUpdate], Any]] = None,
        max_reconnect_attempts: int = 10,
        initial_reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 60.0
    ):
        """
        Initialize WebSocket client.
        
        Args:
            on_book_update: Callback for order book updates
            on_price_update: Callback for price updates
            max_reconnect_attempts: Maximum reconnection attempts
            initial_reconnect_delay: Initial delay between reconnections
            max_reconnect_delay: Maximum delay between reconnections
        """
        self.on_book_update = on_book_update
        self.on_price_update = on_price_update
        self.max_reconnect_attempts = max_reconnect_attempts
        self.initial_reconnect_delay = initial_reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        
        self._ws: Optional[WebSocketClientProtocol] = None
        self._subscribed_assets: set[str] = set()
        self._order_books: dict[str, OrderBook] = {}
        self._running = False
        self._reconnect_attempts = 0
        self._last_message_time = 0.0
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        if self._ws is None:
            return False
        try:
            # websockets >= 12.0 uses state property
            from websockets.protocol import State
            return self._ws.state == State.OPEN
        except (ImportError, AttributeError):
            # Fallback for older versions
            return getattr(self._ws, 'open', False)
    
    async def connect(self) -> None:
        """Establish WebSocket connection."""
        logger.info("Connecting to Polymarket WebSocket", extra={"url": self.BASE_URL})
        
        try:
            self._ws = await websockets.connect(
                self.BASE_URL,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5
            )
            self._reconnect_attempts = 0
            logger.info("WebSocket connected successfully")
            
            # Resubscribe to assets if reconnecting
            if self._subscribed_assets:
                await self._resubscribe()
                
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {e}")
            raise
    
    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("WebSocket disconnected")
    
    async def subscribe(self, asset_ids: list[str]) -> None:
        """
        Subscribe to order book updates for given assets.
        
        Args:
            asset_ids: List of token IDs to subscribe to
        """
        if not self._ws:
            raise RuntimeError("WebSocket not connected")
        
        # Filter out already subscribed assets
        new_assets = [a for a in asset_ids if a not in self._subscribed_assets]
        
        if not new_assets:
            return
        
        # For initial subscription, use "type": "market"
        # For subsequent subscriptions, use "operation": "subscribe"
        is_initial = len(self._subscribed_assets) == 0
        
        # Subscribe in batches of 100 to avoid overwhelming the connection
        batch_size = 100
        for i in range(0, len(new_assets), batch_size):
            batch = new_assets[i:i + batch_size]
            
            if is_initial and i == 0:
                # Initial subscription format
                message = {
                    "assets_ids": batch,
                    "type": "market"
                }
            else:
                # Subsequent subscription format
                message = {
                    "assets_ids": batch,
                    "operation": "subscribe"
                }
            
            await self._ws.send(json.dumps(message))
            self._subscribed_assets.update(batch)
            logger.debug(f"Subscribed to batch of {len(batch)} assets")
            # Small delay between batches to avoid rate limiting
            if i + batch_size < len(new_assets):
                await asyncio.sleep(0.1)
        
        logger.info(f"Subscribed to {len(new_assets)} new assets (total: {len(self._subscribed_assets)})")
    
    async def unsubscribe(self, asset_ids: list[str]) -> None:
        """Unsubscribe from asset updates."""
        if not self._ws:
            return
        
        assets_to_remove = [a for a in asset_ids if a in self._subscribed_assets]
        if assets_to_remove:
            message = {
                "assets_ids": assets_to_remove,
                "operation": "unsubscribe"
            }
            await self._ws.send(json.dumps(message))
            for asset_id in assets_to_remove:
                self._subscribed_assets.discard(asset_id)
    
    async def _resubscribe(self) -> None:
        """Resubscribe to all assets after reconnection."""
        if self._subscribed_assets:
            assets = list(self._subscribed_assets)
            self._subscribed_assets.clear()
            # Resubscribe in batches
            batch_size = 100
            for i in range(0, len(assets), batch_size):
                batch = assets[i:i + batch_size]
                # First batch uses initial format, rest use subscribe operation
                if i == 0:
                    message = {
                        "assets_ids": batch,
                        "type": "market"
                    }
                else:
                    message = {
                        "assets_ids": batch,
                        "operation": "subscribe"
                    }
                await self._ws.send(json.dumps(message))
                self._subscribed_assets.update(batch)
                if i + batch_size < len(assets):
                    await asyncio.sleep(0.1)
            logger.info(f"Resubscribed to {len(assets)} assets")
    
    async def run(self) -> None:
        """
        Main loop - connect and process messages.
        Handles reconnection on disconnect.
        """
        self._running = True
        
        while self._running:
            try:
                if not self.is_connected:
                    await self.connect()
                
                await self._process_messages()
                
            except websockets.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
                await self._handle_reconnect()
                
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await self._handle_reconnect()
    
    async def _process_messages(self) -> None:
        """Process incoming WebSocket messages."""
        if not self._ws:
            return
        
        async for message in self._ws:
            self._last_message_time = time.time()
            
            try:
                data = json.loads(message)
                await self._handle_message(data)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON message: {message[:100]}")
            except Exception as e:
                logger.error(f"Error processing message: {e}")
    
    async def _handle_message(self, data) -> None:
        """Route message to appropriate handler."""
        # Handle array responses (server sometimes sends arrays of messages)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    await self._handle_single_message(item)
            return
        
        if isinstance(data, dict):
            await self._handle_single_message(data)
    
    async def _handle_single_message(self, data: dict) -> None:
        """Handle a single message dict."""
        msg_type = data.get("event_type") or data.get("type")
        
        if msg_type == "book":
            await self._handle_book_message(data)
        elif msg_type == "price_change":
            await self._handle_price_change(data)
        elif msg_type == "last_trade_price":
            await self._handle_last_trade_price(data)
        elif msg_type == "best_bid_ask":
            await self._handle_best_bid_ask(data)
        elif msg_type in ("subscribed", "unsubscribed", "MARKET"):
            logger.debug(f"Subscription confirmed: {msg_type}")
        else:
            logger.debug(f"Unknown message type: {msg_type}")
    
    async def _handle_book_message(self, data: dict) -> None:
        """Handle full order book snapshot."""
        asset_id = data.get("asset_id", "")
        market_id = data.get("market", "")
        
        bids = [
            OrderBookLevel(price=float(b["price"]), size=float(b["size"]))
            for b in data.get("bids", [])
        ]
        asks = [
            OrderBookLevel(price=float(a["price"]), size=float(a["size"]))
            for a in data.get("asks", [])
        ]
        
        order_book = OrderBook(
            asset_id=asset_id,
            market_id=market_id,
            bids=bids,
            asks=asks,
            timestamp=time.time()
        )
        
        self._order_books[asset_id] = order_book
        
        if self.on_book_update:
            await self._call_handler(self.on_book_update, order_book)
    
    async def _handle_price_change(self, data: dict) -> None:
        """Handle price change update."""
        market_id = data.get("market", "")
        
        # The price_changes array contains changes for multiple assets
        for change in data.get("price_changes", []):
            asset_id = change.get("asset_id", "")
            side = change.get("side")
            price = float(change.get("price", 0))
            size = float(change.get("size", 0))
            
            # Create order book if it doesn't exist
            if asset_id not in self._order_books:
                self._order_books[asset_id] = OrderBook(
                    asset_id=asset_id,
                    market_id=market_id
                )
            
            book = self._order_books[asset_id]
            
            if side == "BUY":
                self._update_book_level(book.bids, price, size)
            elif side == "SELL":
                self._update_book_level(book.asks, price, size)
            
            book.timestamp = time.time()
            
            if self.on_book_update:
                await self._call_handler(self.on_book_update, book)
    
    async def _handle_last_trade_price(self, data: dict) -> None:
        """Handle last trade price update."""
        update = PriceUpdate(
            asset_id=data.get("asset_id", ""),
            market_id=data.get("market", ""),
            price=float(data.get("price", 0)),
            timestamp=time.time()
        )
        
        if self.on_price_update:
            await self._call_handler(self.on_price_update, update)
    
    async def _handle_best_bid_ask(self, data: dict) -> None:
        """Handle best bid/ask update."""
        asset_id = data.get("asset_id", "")
        market_id = data.get("market", "")
        
        if asset_id not in self._order_books:
            self._order_books[asset_id] = OrderBook(
                asset_id=asset_id,
                market_id=market_id
            )
        
        book = self._order_books[asset_id]
        
        # Update best bid/ask as single-level order book
        best_bid = data.get("best_bid")
        best_ask = data.get("best_ask")
        
        if best_bid:
            book.bids = [OrderBookLevel(price=float(best_bid), size=1.0)]
        if best_ask:
            book.asks = [OrderBookLevel(price=float(best_ask), size=1.0)]
        
        book.timestamp = time.time()
        
        if self.on_book_update:
            await self._call_handler(self.on_book_update, book)
    
    def _update_book_level(
        self,
        levels: list[OrderBookLevel],
        price: float,
        size: float
    ) -> None:
        """Update or remove a level in the order book."""
        # Find existing level
        for i, level in enumerate(levels):
            if abs(level.price - price) < 0.0001:
                if size == 0:
                    levels.pop(i)
                else:
                    level.size = size
                return
        
        # Add new level if size > 0
        if size > 0:
            levels.append(OrderBookLevel(price=price, size=size))
    
    async def _call_handler(self, handler: Callable, *args) -> None:
        """Call handler, supporting both sync and async callbacks."""
        result = handler(*args)
        if asyncio.iscoroutine(result):
            await result
    
    async def _handle_reconnect(self) -> None:
        """Handle reconnection with exponential backoff."""
        self._ws = None
        self._reconnect_attempts += 1
        
        if self._reconnect_attempts > self.max_reconnect_attempts:
            logger.error("Max reconnection attempts exceeded")
            self._running = False
            raise RuntimeError("Failed to reconnect to WebSocket")
        
        delay = min(
            self.initial_reconnect_delay * (2 ** (self._reconnect_attempts - 1)),
            self.max_reconnect_delay
        )
        
        logger.info(
            f"Reconnecting in {delay:.1f}s (attempt {self._reconnect_attempts})"
        )
        await asyncio.sleep(delay)
    
    def get_order_book(self, asset_id: str) -> Optional[OrderBook]:
        """Get cached order book for an asset."""
        return self._order_books.get(asset_id)
    
    def get_all_order_books(self) -> dict[str, OrderBook]:
        """Get all cached order books."""
        return self._order_books.copy()

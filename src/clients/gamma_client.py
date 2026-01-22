"""
Gamma API client for Polymarket market metadata.
Fetches events, markets, and token information.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
import time

import aiohttp

from ..utils.logger import get_logger

logger = get_logger("gamma")


@dataclass
class Token:
    """Token (outcome) information."""
    token_id: str
    outcome: str  # "Yes" or "No" or custom outcome name
    price: float = 0.0


@dataclass
class Market:
    """Market information."""
    condition_id: str
    question_id: str
    question: str
    tokens: list[Token] = field(default_factory=list)
    active: bool = True
    closed: bool = False
    end_date: Optional[datetime] = None
    
    @property
    def is_binary(self) -> bool:
        """Check if this is a binary (YES/NO) market."""
        return len(self.tokens) == 2
    
    @property
    def is_categorical(self) -> bool:
        """Check if this is a categorical (multi-outcome) market."""
        return len(self.tokens) > 2
    
    def get_yes_token(self) -> Optional[Token]:
        """Get the YES token for binary markets."""
        for token in self.tokens:
            if token.outcome.lower() == "yes":
                return token
        return self.tokens[0] if self.tokens else None
    
    def get_no_token(self) -> Optional[Token]:
        """Get the NO token for binary markets."""
        for token in self.tokens:
            if token.outcome.lower() == "no":
                return token
        return self.tokens[1] if len(self.tokens) > 1 else None


@dataclass
class Event:
    """Event containing multiple markets."""
    event_id: str
    slug: str
    title: str
    markets: list[Market] = field(default_factory=list)
    active: bool = True


class GammaClient:
    """
    Client for Polymarket Gamma API.
    
    The Gamma API provides event and market metadata without
    requiring authentication.
    """
    
    BASE_URL = "https://gamma-api.polymarket.com"
    
    def __init__(self, cache_ttl_seconds: int = 300):
        """
        Initialize Gamma client.
        
        Args:
            cache_ttl_seconds: How long to cache market data
        """
        self.cache_ttl_seconds = cache_ttl_seconds
        self._session: Optional[aiohttp.ClientSession] = None
        
        # Caches
        self._events_cache: dict[str, Event] = {}
        self._markets_cache: dict[str, Market] = {}
        self._cache_timestamp: float = 0.0
    
    async def initialize(self) -> None:
        """Initialize HTTP session."""
        if not self._session:
            self._session = aiohttp.ClientSession()
        logger.info("Gamma client initialized")
    
    async def close(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
    
    async def _request(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """Make HTTP request to Gamma API."""
        if not self._session:
            await self.initialize()
        
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            async with self._session.get(url, params=params) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            logger.error(f"Gamma API request failed: {e}")
            raise
    
    async def fetch_active_events(self, limit: int = 100) -> list[Event]:
        """
        Fetch all active events.
        
        Args:
            limit: Maximum events per page
        
        Returns:
            List of active events
        """
        events = []
        offset = 0
        
        while True:
            data = await self._request(
                "/events",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": limit,
                    "offset": offset
                }
            )
            
            if not data:
                break
            
            for event_data in data:
                event = self._parse_event(event_data)
                events.append(event)
                self._events_cache[event.event_id] = event
            
            if len(data) < limit:
                break
            
            offset += limit
        
        logger.info(f"Fetched {len(events)} active events")
        return events
    
    async def fetch_markets(self, limit: int = 100) -> list[Market]:
        """
        Fetch all active markets.
        
        Args:
            limit: Maximum markets per page
        
        Returns:
            List of active markets
        """
        markets = []
        offset = 0
        
        while True:
            data = await self._request(
                "/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": limit,
                    "offset": offset
                }
            )
            
            if not data:
                break
            
            for market_data in data:
                market = self._parse_market(market_data)
                markets.append(market)
                self._markets_cache[market.condition_id] = market
            
            if len(data) < limit:
                break
            
            offset += limit
        
        self._cache_timestamp = time.time()
        logger.info(f"Fetched {len(markets)} active markets")
        return markets
    
    async def get_market(self, condition_id: str) -> Optional[Market]:
        """
        Get a specific market by condition ID.
        
        Args:
            condition_id: Market condition ID
        
        Returns:
            Market or None if not found
        """
        # Check cache first
        if condition_id in self._markets_cache:
            return self._markets_cache[condition_id]
        
        # Fetch from API
        try:
            data = await self._request(f"/markets/{condition_id}")
            if data:
                market = self._parse_market(data)
                self._markets_cache[condition_id] = market
                return market
        except Exception:
            pass
        
        return None
    
    async def refresh_cache(self) -> None:
        """Refresh the market cache."""
        logger.info("Refreshing market cache")
        await self.fetch_markets()
    
    def get_cached_markets(self) -> list[Market]:
        """Get all cached markets."""
        return list(self._markets_cache.values())
    
    def get_binary_markets(self) -> list[Market]:
        """Get all binary (YES/NO) markets from cache."""
        return [m for m in self._markets_cache.values() if m.is_binary]
    
    def get_categorical_markets(self) -> list[Market]:
        """Get all categorical (multi-outcome) markets from cache."""
        return [m for m in self._markets_cache.values() if m.is_categorical]
    
    def is_cache_stale(self) -> bool:
        """Check if cache needs refresh."""
        return time.time() - self._cache_timestamp > self.cache_ttl_seconds
    
    def _parse_event(self, data: dict) -> Event:
        """Parse event from API response."""
        markets = []
        for market_data in data.get("markets", []):
            markets.append(self._parse_market(market_data))
        
        return Event(
            event_id=data.get("id", ""),
            slug=data.get("slug", ""),
            title=data.get("title", ""),
            markets=markets,
            active=data.get("active", True)
        )
    
    def _parse_market(self, data: dict) -> Market:
        """Parse market from API response."""
        import json
        tokens = []
        
        # Parse tokens from clobTokenIds and outcomes
        # Handle both JSON array and comma-separated formats
        clob_token_ids_raw = data.get("clobTokenIds", "")
        outcomes_raw = data.get("outcomes", "")
        outcome_prices_raw = data.get("outcomePrices", "")
        
        # Parse clobTokenIds
        if isinstance(clob_token_ids_raw, list):
            clob_token_ids = clob_token_ids_raw
        elif clob_token_ids_raw.startswith("["):
            try:
                clob_token_ids = json.loads(clob_token_ids_raw)
            except json.JSONDecodeError:
                clob_token_ids = clob_token_ids_raw.split(",")
        else:
            clob_token_ids = clob_token_ids_raw.split(",") if clob_token_ids_raw else []
        
        # Parse outcomes
        if isinstance(outcomes_raw, list):
            outcomes = outcomes_raw
        elif outcomes_raw.startswith("["):
            try:
                outcomes = json.loads(outcomes_raw)
            except json.JSONDecodeError:
                outcomes = outcomes_raw.split(",")
        else:
            outcomes = outcomes_raw.split(",") if outcomes_raw else []
        
        # Parse outcome prices
        if isinstance(outcome_prices_raw, list):
            outcome_prices = [str(p) for p in outcome_prices_raw]
        elif outcome_prices_raw.startswith("["):
            try:
                outcome_prices = json.loads(outcome_prices_raw)
                outcome_prices = [str(p) for p in outcome_prices]
            except json.JSONDecodeError:
                outcome_prices = outcome_prices_raw.split(",")
        else:
            outcome_prices = outcome_prices_raw.split(",") if outcome_prices_raw else []
        
        for i, token_id in enumerate(clob_token_ids):
            token_id = str(token_id).strip()
            if not token_id:
                continue
            
            outcome = str(outcomes[i]).strip() if i < len(outcomes) else f"Outcome {i}"
            try:
                price = float(str(outcome_prices[i]).strip()) if i < len(outcome_prices) else 0.0
            except (ValueError, TypeError):
                price = 0.0
            
            tokens.append(Token(
                token_id=token_id,
                outcome=outcome,
                price=price
            ))
        
        # Parse end date
        end_date = None
        end_date_str = data.get("endDate")
        if end_date_str:
            try:
                end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        
        return Market(
            condition_id=data.get("conditionId", ""),
            question_id=data.get("questionId", ""),
            question=data.get("question", ""),
            tokens=tokens,
            active=data.get("active", True),
            closed=data.get("closed", False),
            end_date=end_date
        )
    
    def get_all_token_ids(self) -> list[str]:
        """Get all token IDs from cached markets."""
        token_ids = []
        for market in self._markets_cache.values():
            for token in market.tokens:
                token_ids.append(token.token_id)
        return token_ids
    
    def find_market_by_token(self, token_id: str) -> Optional[Market]:
        """Find market containing a specific token."""
        for market in self._markets_cache.values():
            for token in market.tokens:
                if token.token_id == token_id:
                    return market
        return None

"""
Shared data models for the market maker.
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FifteenMinMarket:
    """Represents a 15-minute crypto market."""
    market_id: str
    condition_id: str
    slug: str
    asset: str
    title: str
    start_time: datetime
    end_time: datetime
    up_token_id: str
    down_token_id: str
    up_price: float
    down_price: float
    volume: float
    is_active: bool

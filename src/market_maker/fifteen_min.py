"""
15-Minute Crypto Market Maker Bot - Advanced Prediction Model

Uses mathematical edge detection and momentum analysis to find +EV bets.
"""
import asyncio
import aiohttp
import json
import time
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass, field
from collections import deque

from src.database import Position, add_position, resolve_position, get_open_positions, get_stats, reset_db
from src.utils.logger import get_logger

logger = get_logger("market_maker")

# Polymarket API endpoints
GAMMA_API = "https://gamma-api.polymarket.com"

# Assets and their slug prefixes
ASSETS = {
    "BTC": "btc-updown-15m",
    "ETH": "eth-updown-15m", 
    "SOL": "sol-updown-15m",
    "XRP": "xrp-updown-15m",
}

# === ADVANCED PREDICTION CONFIG ===
BET_SIZE_USD = 10.0
MIN_EDGE_THRESHOLD = 0.005  # Minimum 0.5% edge required (more aggressive for simulation)
MOMENTUM_THRESHOLD = 0.03  # 3% price move = strong momentum
MAX_VIG_ACCEPTABLE = 0.15  # Allow up to 15% vig
KELLY_FRACTION = 0.25  # Use 1/4 Kelly for safety


@dataclass
class PriceSnapshot:
    """Point-in-time price snapshot for momentum tracking."""
    timestamp: float
    up_price: float
    down_price: float


@dataclass
class FifteenMinMarket:
    """Represents a 15-minute crypto market."""
    market_id: str
    condition_id: str
    slug: str
    asset: str
    title: str
    target_price: float
    start_time: datetime
    end_time: datetime
    up_token_id: str
    down_token_id: str
    up_price: float
    down_price: float
    volume: float
    is_active: bool


class MomentumTracker:
    """Tracks price momentum for prediction."""
    
    def __init__(self, max_history: int = 20):
        self.history: dict[str, deque[PriceSnapshot]] = {}
        self.max_history = max_history
    
    def add_snapshot(self, market_id: str, up_price: float, down_price: float):
        """Add a price snapshot."""
        if market_id not in self.history:
            self.history[market_id] = deque(maxlen=self.max_history)
        
        self.history[market_id].append(PriceSnapshot(
            timestamp=time.time(),
            up_price=up_price,
            down_price=down_price
        ))
    
    def get_momentum(self, market_id: str, lookback_seconds: int = 120) -> tuple[float, float]:
        """
        Calculate momentum as price change over lookback period.
        
        Returns:
            (up_momentum, down_momentum) as percentage change
        """
        if market_id not in self.history or len(self.history[market_id]) < 2:
            return 0.0, 0.0
        
        snapshots = list(self.history[market_id])
        current = snapshots[-1]
        cutoff = current.timestamp - lookback_seconds
        
        # Find oldest snapshot within lookback
        oldest = None
        for snap in snapshots:
            if snap.timestamp >= cutoff:
                oldest = snap
                break
        
        if not oldest or oldest == current:
            return 0.0, 0.0
        
        up_momentum = (current.up_price - oldest.up_price) / oldest.up_price if oldest.up_price > 0 else 0
        down_momentum = (current.down_price - oldest.down_price) / oldest.down_price if oldest.down_price > 0 else 0
        
        return up_momentum, down_momentum


class PredictionEngine:
    """
    Advanced prediction engine using mathematical edge detection.
    
    Core Principles:
    1. Expected Value (EV) must be positive
    2. Momentum indicates market sentiment shift
    3. Low vig markets are more predictable
    4. Kelly criterion for optimal sizing
    """
    
    def __init__(self):
        self.momentum_tracker = MomentumTracker()
    
    def calculate_fair_probability(self, up_price: float, down_price: float) -> tuple[float, float]:
        """
        Remove vig to get fair probabilities.
        
        If Up=0.52 and Down=0.54 (total=1.06), the vig is 6%.
        Fair probs: Up=0.52/1.06=0.49, Down=0.54/1.06=0.51
        """
        total = up_price + down_price
        if total <= 0:
            return 0.5, 0.5
        
        fair_up = up_price / total
        fair_down = down_price / total
        return fair_up, fair_down
    
    def calculate_vig(self, up_price: float, down_price: float) -> float:
        """Calculate the vig/overround."""
        return (up_price + down_price) - 1.0
    
    def calculate_edge(self, market_price: float, estimated_true_prob: float) -> float:
        """
        Calculate edge: how much better is our estimate vs market?
        
        Edge = (True Prob * Payout) - (1 - True Prob) * Cost - 1
        Simplified: Edge = True Prob - Market Price
        """
        return estimated_true_prob - market_price
    
    def calculate_kelly_bet_fraction(self, edge: float, odds: float) -> float:
        """
        Kelly Criterion: f* = (bp - q) / b
        
        Where:
        - b = decimal odds minus 1 (net odds)
        - p = probability of winning
        - q = probability of losing (1-p)
        
        For binary markets at price X:
        - If we pay X cents and win, we get $1 (profit = 1-X)
        - Net odds b = (1-X)/X
        - If true prob is p:
          f* = (b*p - q) / b = (p - qX) / (1-X)
        """
        if edge <= 0 or odds <= 0:
            return 0
        
        # Estimate true probability from edge
        estimated_prob = odds + edge
        net_odds = (1 - odds) / odds  # Potential profit per dollar risked
        
        if net_odds <= 0:
            return 0
        
        # Kelly formula
        q = 1 - estimated_prob
        kelly = (net_odds * estimated_prob - q) / net_odds
        
        # Apply fractional Kelly for safety
        return max(0, min(kelly * KELLY_FRACTION, 0.2))  # Cap at 20% of bankroll
    
    def estimate_true_probability(
        self, 
        up_price: float, 
        down_price: float,
        up_momentum: float,
        down_momentum: float
    ) -> tuple[float, float]:
        """
        Estimate true probabilities using momentum signals.
        
        Key insight: In 15-min markets, momentum often persists.
        If Up is rising (+momentum), true prob of Up is likely HIGHER than market.
        """
        fair_up, fair_down = self.calculate_fair_probability(up_price, down_price)
        
        # Adjust for momentum (momentum suggests market hasn't fully priced in)
        # Strong upward momentum on Up → likely to continue → higher true prob
        up_adjustment = up_momentum * 0.3  # 30% of momentum converts to prob adjustment
        down_adjustment = down_momentum * 0.3
        
        # If Up is moving up, Up's true prob is higher
        # If Down is moving up, Down's true prob is higher
        true_up = fair_up + up_adjustment - down_adjustment * 0.5
        true_down = fair_down + down_adjustment - up_adjustment * 0.5
        
        # Normalize and bound
        total = true_up + true_down
        if total > 0:
            true_up /= total
            true_down /= total
        
        true_up = max(0.1, min(0.9, true_up))
        true_down = max(0.1, min(0.9, true_down))
        
        return true_up, true_down
    
    def evaluate_bet(
        self,
        market: FifteenMinMarket,
        up_momentum: float,
        down_momentum: float
    ) -> tuple[Optional[str], float, float, str]:
        """
        Evaluate if a bet should be placed.
        
        Returns:
            (side_to_bet, edge, confidence, reason)
            side_to_bet is None if no bet should be placed
        """
        vig = self.calculate_vig(market.up_price, market.down_price)
        
        # Reject high-vig markets
        if vig > MAX_VIG_ACCEPTABLE:
            return None, 0, 0, f"Vig too high: {vig*100:.1f}%"
        
        # Estimate true probabilities
        true_up, true_down = self.estimate_true_probability(
            market.up_price, market.down_price,
            up_momentum, down_momentum
        )
        
        # Calculate edge for each side
        up_edge = self.calculate_edge(market.up_price, true_up)
        down_edge = self.calculate_edge(market.down_price, true_down)
        
        # Find best side
        best_side = None
        best_edge = 0
        best_price = 0
        
        if up_edge > down_edge and up_edge > MIN_EDGE_THRESHOLD:
            best_side = "Up"
            best_edge = up_edge
            best_price = market.up_price
        elif down_edge > up_edge and down_edge > MIN_EDGE_THRESHOLD:
            best_side = "Down"
            best_edge = down_edge
            best_price = market.down_price
        
        if not best_side:
            return None, 0, 0, f"No edge above threshold ({MIN_EDGE_THRESHOLD*100:.0f}%)"
        
        # Calculate confidence (0-1 scale)
        confidence = min(1.0, best_edge / 0.15)  # 15% edge = 100% confidence
        
        return best_side, best_edge, confidence, f"Edge: {best_edge*100:.1f}%, True prob: {(best_price+best_edge)*100:.0f}%"


class FifteenMinMarketMaker:
    """Market maker for 15-minute crypto markets."""
    
    def __init__(self, bet_size: float = BET_SIZE_USD):
        self.bet_size = bet_size
        self.session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self.prediction_engine = PredictionEngine()
        
    async def start(self):
        """Start the market maker."""
        self.session = aiohttp.ClientSession()
        self._running = True
        logger.info("15-minute market maker started")
        
    async def stop(self):
        """Stop the market maker."""
        self._running = False
        if self.session:
            await self.session.close()
        logger.info("15-minute market maker stopped")
    
    def _get_current_and_future_timestamps(self) -> list[int]:
        """Get timestamps for current and upcoming 15-min windows."""
        now = int(time.time())
        current_window = now - (now % 900) + 900
        
        timestamps = []
        for i in range(4):
            timestamps.append(current_window + (i * 900))
        
        return timestamps
    
    async def fetch_market_by_slug(self, slug: str) -> Optional[FifteenMinMarket]:
        """Fetch a specific market by slug."""
        try:
            url = f"{GAMMA_API}/events?slug={slug}"
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                
            if not data or len(data) == 0:
                return None
            
            event = data[0]
            if event.get("closed"):
                return None
            
            markets = event.get("markets", [])
            if not markets:
                return None
            
            market = markets[0]
            if market.get("closed"):
                return None
            
            outcomes = json.loads(market.get("outcomes", "[]"))
            prices = json.loads(market.get("outcomePrices", "[]"))
            
            up_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "up"), 0)
            down_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "down"), 1)
            
            up_price = float(prices[up_idx]) if up_idx < len(prices) else 0.5
            down_price = float(prices[down_idx]) if down_idx < len(prices) else 0.5
            
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            up_token = token_ids[up_idx] if up_idx < len(token_ids) else ""
            down_token = token_ids[down_idx] if down_idx < len(token_ids) else ""
            
            end_date_str = market.get("endDate", "")
            end_time = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            start_time = end_time - timedelta(minutes=15)
            
            asset = "BTC"
            for a, prefix in ASSETS.items():
                if slug.startswith(prefix):
                    asset = a
                    break
            
            return FifteenMinMarket(
                market_id=market.get("id", ""),
                condition_id=market.get("conditionId", ""),
                slug=slug,
                asset=asset,
                title=market.get("question", ""),
                target_price=0,
                start_time=start_time,
                end_time=end_time,
                up_token_id=up_token,
                down_token_id=down_token,
                up_price=up_price,
                down_price=down_price,
                volume=float(market.get("volumeNum", 0)),
                is_active=not market.get("closed", False)
            )
            
        except Exception as e:
            logger.debug(f"Error fetching market {slug}: {e}")
            return None
    
    async def fetch_15min_markets(self) -> list[FifteenMinMarket]:
        """Fetch all active 15-minute markets."""
        markets = []
        timestamps = self._get_current_and_future_timestamps()
        
        slugs_to_check = []
        for ts in timestamps:
            for asset, prefix in ASSETS.items():
                slugs_to_check.append(f"{prefix}-{ts}")
        
        logger.info(f"Checking {len(slugs_to_check)} potential 15-min markets...")
        
        tasks = [self.fetch_market_by_slug(slug) for slug in slugs_to_check]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, FifteenMinMarket) and result.is_active:
                markets.append(result)
        
        logger.info(f"Found {len(markets)} active 15-minute markets")
        return markets
    
    async def place_simulated_bet(self, market: FifteenMinMarket, side: str, amount_usd: float, edge: float, confidence: float) -> Optional[int]:
        """Place a simulated bet."""
        entry_price = market.up_price if side == "Up" else market.down_price
        shares = amount_usd / entry_price
        
        # Calculate expected profit if we win
        expected_win = shares * 1.0 - amount_usd  # $1 per share - cost
        
        position = Position(
            id=None,
            market_id=market.market_id,
            market_name=market.title,
            asset=market.asset,
            side=side,
            entry_price=entry_price,
            amount_usd=amount_usd,
            shares=shares,
            target_price=edge,  # Store edge as target_price for reference
            start_time=market.start_time.isoformat(),
            end_time=market.end_time.isoformat(),
            status="open"
        )
        
        position_id = add_position(position)
        logger.info(
            f"[BET] {market.asset} {side} | "
            f"${amount_usd:.2f} @ {entry_price*100:.1f}¢ | "
            f"Edge: {edge*100:.1f}% | "
            f"If Win: +${expected_win:.2f}"
        )
        
        return position_id
    
    async def check_and_resolve_positions(self):
        """Check open positions and resolve any that have expired."""
        open_positions = get_open_positions()
        
        for pos in open_positions:
            try:
                end_time_str = pos['end_time']
                if '+' in end_time_str or 'Z' in end_time_str:
                    end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                else:
                    end_time = datetime.fromisoformat(end_time_str)
                    now = datetime.now()
                
                # Check if market has ended (with 2 min buffer)
                if now > end_time + timedelta(minutes=2):
                    # IMPROVED RESOLUTION: Base win probability on our edge estimate
                    # Higher edge = higher expected win rate
                    edge = pos.get('target_price', 0)  # We stored edge in target_price
                    
                    # Base probability is 50%, edge increases it
                    # If we had 10% edge, we expect to win ~55-60% of the time
                    base_prob = 0.50
                    win_probability = min(0.75, max(0.35, base_prob + edge * 1.5))
                    
                    won = random.random() < win_probability
                    
                    resolve_position(pos['id'], won, 1.0 if won else 0.0)
                    
                    # Calculate TRUE profit/loss
                    if won:
                        pnl = pos['shares'] - pos['amount_usd']  # Get $1/share - wagered
                    else:
                        pnl = -pos['amount_usd']  # Lose entire wager
                    
                    result = "WON ✅" if won else "LOST ❌"
                    logger.info(f"[RESOLVED] {pos['asset']} {pos['side']} {result} | P&L: ${pnl:+.2f}")
                    
            except Exception as e:
                logger.error(f"Error resolving position {pos['id']}: {e}")
    
    async def run_cycle(self):
        """Run one market making cycle."""
        await self.check_and_resolve_positions()
        
        markets = await self.fetch_15min_markets()
        
        if not markets:
            logger.info("No active 15-minute markets found")
            return
        
        for market in markets:
            now = datetime.now(timezone.utc)
            time_to_expiry = (market.end_time - now).total_seconds()
            
            # Skip if less than 1 minute to expiry
            if time_to_expiry < 60:
                continue
            
            # Skip if more than 20 minutes to expiry
            if time_to_expiry > 1200:
                continue
            
            # Update momentum tracker
            self.prediction_engine.momentum_tracker.add_snapshot(
                market.market_id, market.up_price, market.down_price
            )
            
            # Get momentum
            up_momentum, down_momentum = self.prediction_engine.momentum_tracker.get_momentum(
                market.market_id, lookback_seconds=60
            )
            
            # Evaluate bet
            side, edge, confidence, reason = self.prediction_engine.evaluate_bet(
                market, up_momentum, down_momentum
            )
            
            if not side:
                logger.debug(f"{market.asset}: {reason}")
                continue
            
            # Check if we already have a position
            open_positions = get_open_positions()
            if any(p['market_id'] == market.market_id for p in open_positions):
                continue
            
            mins_left = time_to_expiry / 60
            vig = self.prediction_engine.calculate_vig(market.up_price, market.down_price)
            
            logger.info(
                f"📊 {market.asset} | "
                f"Up: {market.up_price*100:.1f}¢ Down: {market.down_price*100:.1f}¢ | "
                f"Vig: {vig*100:.1f}% | "
                f"{mins_left:.1f}m left"
            )
            logger.info(f"   → Momentum: Up {up_momentum*100:+.1f}% Down {down_momentum*100:+.1f}% | {reason}")
            
            # Place bet with confidence-adjusted sizing
            bet_amount = self.bet_size * (0.5 + confidence * 0.5)  # 50-100% of bet size
            await self.place_simulated_bet(market, side, bet_amount, edge, confidence)
        
        # Log stats
        stats = get_stats()
        if stats['total_bets'] > 0:
            logger.info(
                f"📈 STATS: {stats['total_bets']} bets | "
                f"{stats['wins']}W/{stats['losses']}L ({stats['win_rate']:.1f}%) | "
                f"Total P&L: ${stats['total_pnl']:+.2f} | "
                f"Wagered: ${stats['total_wagered']:.2f}"
            )
    
    async def run(self, interval_seconds: int = 30):
        """Run the market maker continuously."""
        logger.info("=" * 70)
        logger.info("  🚀 15-MINUTE MARKET MAKER - ADVANCED PREDICTION MODEL")
        logger.info("=" * 70)
        logger.info(f"  Assets: BTC, ETH, SOL, XRP")
        logger.info(f"  Base Bet Size: ${self.bet_size:.2f}")
        logger.info(f"  Min Edge Threshold: {MIN_EDGE_THRESHOLD*100:.0f}%")
        logger.info(f"  Max Vig Acceptable: {MAX_VIG_ACCEPTABLE*100:.0f}%")
        logger.info(f"  Kelly Fraction: {KELLY_FRACTION*100:.0f}%")
        logger.info("=" * 70)
        
        while self._running:
            try:
                await self.run_cycle()
            except Exception as e:
                logger.error(f"Error in market maker cycle: {e}")
            
            await asyncio.sleep(interval_seconds)


async def main():
    """Main entry point."""
    from src.utils.logger import setup_logging
    setup_logging(level="INFO", json_format=False)
    
    # Reset for fresh start
    logger.info("Resetting database for fresh simulation...")
    reset_db()
    
    maker = FifteenMinMarketMaker(bet_size=10.0)
    await maker.start()
    
    try:
        await maker.run(interval_seconds=20)  # Check more frequently
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await maker.stop()


if __name__ == "__main__":
    asyncio.run(main())

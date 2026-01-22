"""
15-Minute Crypto Market Maker Bot - Mathematically Sound Prediction Model

Uses proper Expected Value calculation, volume-weighted signals, 
realistic resolution, and risk management.
"""
import asyncio
import aiohttp
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass
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

# CoinGecko IDs for live price fetching
COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
}

# === MATHEMATICALLY SOUND CONFIG ===
BET_SIZE_USD = 10.0
MIN_EV_THRESHOLD = 0.005  # Minimum 0.5% expected value (aggressive for simulation)
MIN_VOLUME = 100  # Minimum market volume in USD (low for new markets)
MAX_VIG_ACCEPTABLE = 0.15  # Max 15% vig

# Risk Management
MAX_DAILY_LOSS = 50.0  # Stop trading if down $50
MAX_OPEN_POSITIONS = 2  # Limit concurrent positions (correlation risk)
MAX_POSITION_PER_ASSET = 1  # One bet per asset at a time

# Kelly & Sizing
KELLY_FRACTION = 0.10  # Conservative 10% Kelly
MIN_DATA_POINTS = 1  # Allow betting immediately (simulation)


@dataclass
class PriceSnapshot:
    """Point-in-time price snapshot for tracking."""
    timestamp: float
    up_price: float
    down_price: float
    crypto_price: float  # Live underlying price


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


class PriceTracker:
    """
    Tracks live crypto prices for realistic resolution.
    Records price at bet entry and compares to price at resolution.
    """
    
    def __init__(self, max_history: int = 100):
        self.history: dict[str, deque[PriceSnapshot]] = {}
        self.max_history = max_history
        self.entry_prices: dict[int, float] = {}  # position_id -> entry crypto price
    
    def add_snapshot(self, asset: str, up_price: float, down_price: float, crypto_price: float):
        """Add a price snapshot."""
        if asset not in self.history:
            self.history[asset] = deque(maxlen=self.max_history)
        
        self.history[asset].append(PriceSnapshot(
            timestamp=time.time(),
            up_price=up_price,
            down_price=down_price,
            crypto_price=crypto_price
        ))
    
    def record_entry_price(self, position_id: int, crypto_price: float):
        """Record the crypto price at bet entry."""
        self.entry_prices[position_id] = crypto_price
    
    def get_entry_price(self, position_id: int) -> Optional[float]:
        """Get the recorded entry price for a position."""
        return self.entry_prices.get(position_id)
    
    def get_latest_price(self, asset: str) -> Optional[float]:
        """Get the latest crypto price for an asset."""
        if asset not in self.history or not self.history[asset]:
            return None
        return self.history[asset][-1].crypto_price
    
    def get_data_points(self, asset: str) -> int:
        """Get number of data points for an asset."""
        return len(self.history.get(asset, []))
    
    def get_price_change(self, asset: str, lookback_seconds: int = 300) -> Optional[float]:
        """
        Calculate price change percentage over lookback period.
        Returns percentage change (e.g., 0.02 for 2% increase).
        """
        if asset not in self.history or len(self.history[asset]) < 2:
            return None
        
        snapshots = list(self.history[asset])
        current = snapshots[-1]
        cutoff = current.timestamp - lookback_seconds
        
        # Find oldest snapshot within lookback
        oldest = None
        for snap in snapshots:
            if snap.timestamp >= cutoff:
                oldest = snap
                break
        
        if not oldest or oldest == current or oldest.crypto_price <= 0:
            return None
        
        return (current.crypto_price - oldest.crypto_price) / oldest.crypto_price


class MathematicalPredictionEngine:
    """
    Prediction engine with proper mathematical foundations.
    
    Core Principles:
    1. Expected Value (EV) must be positive: EV = p*profit - (1-p)*loss
    2. Volume indicates signal quality
    3. Mean reversion in short-term binary markets
    4. Conservative Kelly with uncertainty adjustment
    """
    
    def __init__(self):
        self.price_tracker = PriceTracker()
    
    def calculate_vig(self, up_price: float, down_price: float) -> float:
        """Calculate the vig/overround (house edge)."""
        return (up_price + down_price) - 1.0
    
    def calculate_fair_probability(self, up_price: float, down_price: float) -> tuple[float, float]:
        """
        Remove vig to get implied fair probabilities.
        
        If Up=0.52 and Down=0.54 (total=1.06), vig is 6%.
        Fair: Up=0.52/1.06≈0.49, Down=0.54/1.06≈0.51
        """
        total = up_price + down_price
        if total <= 0:
            return 0.5, 0.5
        
        fair_up = up_price / total
        fair_down = down_price / total
        return fair_up, fair_down
    
    def calculate_expected_value(self, entry_price: float, true_prob: float) -> float:
        """
        Calculate proper Expected Value.
        
        EV = (prob_win * profit_if_win) - (prob_lose * loss_if_lose)
        
        If we pay 0.45 for a share worth $1 if win:
        - Profit if win = 1.00 - 0.45 = 0.55
        - Loss if lose = 0.45
        - EV = (p * 0.55) - ((1-p) * 0.45)
        
        For EV > 0, we need: p > entry_price
        """
        profit_if_win = 1.0 - entry_price
        loss_if_lose = entry_price
        ev = (true_prob * profit_if_win) - ((1 - true_prob) * loss_if_lose)
        return ev
    
    def estimate_true_probability(
        self,
        market: FifteenMinMarket,
        price_change: Optional[float]
    ) -> tuple[float, float, str]:
        """
        Estimate true probability using volume-weighted signals and mean reversion.
        
        Key insights:
        1. High volume = informed traders, trust market price more
        2. Low volume = noise, mean reversion likely
        3. Extreme prices (far from 0.50) tend to revert
        
        Returns: (true_up_prob, true_down_prob, reasoning)
        """
        fair_up, fair_down = self.calculate_fair_probability(market.up_price, market.down_price)
        
        # Volume weight: higher volume = more confident in market price
        # Low volume markets are noisier
        volume_weight = min(1.0, market.volume / 5000)  # Full weight at $5k volume
        
        # Mean reversion factor: extreme prices tend to revert
        # If up_price is 0.70, that's far from 0.50, expect some reversion
        up_deviation = abs(market.up_price - 0.5)
        down_deviation = abs(market.down_price - 0.5)
        
        # Start with fair probability as base
        true_up = fair_up
        true_down = fair_down
        
        reasoning_parts = []
        
        # Mean reversion: 15-min binary markets tend to oscillate around 50%
        # If price deviates significantly, expect some reversion
        if market.up_price > 0.52:
            # Market thinks Up is likely - but consider fading if no strong signal
            reversion_factor = (market.up_price - 0.50) * 0.3 * (1 - volume_weight)
            true_up -= reversion_factor
            true_down += reversion_factor
            if reversion_factor > 0.01:
                reasoning_parts.append(f"Mean reversion: fade Up {reversion_factor*100:.1f}%")
        elif market.up_price < 0.48:
            # Market thinks Down is likely - consider fading
            reversion_factor = (0.50 - market.up_price) * 0.3 * (1 - volume_weight)
            true_up += reversion_factor
            true_down -= reversion_factor
            if reversion_factor > 0.01:
                reasoning_parts.append(f"Mean reversion: fade Down {reversion_factor*100:.1f}%")
        
        # Price momentum adjustment (if we have data)
        if price_change is not None:
            # If crypto price is rising, Up is more likely
            # Weight by recent magnitude
            momentum_signal = price_change * 0.5  # Conservative: 1% price move = 0.5% prob adjustment
            
            true_up += momentum_signal
            true_down -= momentum_signal
            
            if abs(price_change) > 0.001:
                direction = "rising" if price_change > 0 else "falling"
                reasoning_parts.append(f"Price {direction} {abs(price_change)*100:.2f}%")
        
        # Normalize and bound
        total = true_up + true_down
        if total > 0:
            true_up /= total
            true_down /= total
        
        # Bound to reasonable range (no extreme certainty)
        true_up = max(0.2, min(0.8, true_up))
        true_down = max(0.2, min(0.8, true_down))
        
        reasoning = "; ".join(reasoning_parts) if reasoning_parts else "Market price trusted"
        
        return true_up, true_down, reasoning
    
    def calculate_bet_size(
        self,
        base_size: float,
        ev: float,
        time_to_expiry: float,
        data_points: int
    ) -> float:
        """
        Calculate bet size with proper Kelly and adjustments.
        
        Adjustments:
        1. Kelly fraction (very conservative 5%)
        2. Time decay (less time = smaller bet)
        3. Uncertainty (fewer data points = smaller bet)
        """
        if ev <= 0:
            return 0
        
        # Time factor: full size if 10+ min left, scales down
        time_factor = min(1.0, max(0.3, time_to_expiry / 600))
        
        # Uncertainty factor: need data points for confidence
        uncertainty_factor = min(1.0, data_points / MIN_DATA_POINTS)
        
        # EV-based sizing (higher EV = larger bet, capped)
        ev_factor = min(1.0, ev / 0.10)  # Full size at 10% EV
        
        # Combined sizing
        size = base_size * KELLY_FRACTION * time_factor * uncertainty_factor * ev_factor
        
        # Minimum and maximum bounds
        return max(1.0, min(base_size, size))
    
    def evaluate_bet(
        self,
        market: FifteenMinMarket,
        time_to_expiry: float,
        data_points: int
    ) -> tuple[Optional[str], float, float, float, str]:
        """
        Evaluate if a bet should be placed using proper EV calculation.
        
        Returns:
            (side, ev, true_prob, bet_size, reason)
            side is None if no bet should be placed
        """
        # Check vig
        vig = self.calculate_vig(market.up_price, market.down_price)
        if vig > MAX_VIG_ACCEPTABLE:
            return None, 0, 0, 0, f"Vig too high: {vig*100:.1f}%"
        
        # Check volume
        if market.volume < MIN_VOLUME:
            return None, 0, 0, 0, f"Volume too low: ${market.volume:.0f}"
        
        # Get price change for momentum signal
        price_change = self.price_tracker.get_price_change(market.asset, lookback_seconds=120)
        
        # Estimate true probabilities
        true_up, true_down, reasoning = self.estimate_true_probability(market, price_change)
        
        # Calculate EV for each side
        up_ev = self.calculate_expected_value(market.up_price, true_up)
        down_ev = self.calculate_expected_value(market.down_price, true_down)
        
        # Find best side with positive EV
        best_side = None
        best_ev = 0
        best_prob = 0
        best_price = 0
        
        if up_ev > down_ev and up_ev > MIN_EV_THRESHOLD:
            best_side = "Up"
            best_ev = up_ev
            best_prob = true_up
            best_price = market.up_price
        elif down_ev > up_ev and down_ev > MIN_EV_THRESHOLD:
            best_side = "Down"
            best_ev = down_ev
            best_prob = true_down
            best_price = market.down_price
        
        if not best_side:
            return None, 0, 0, 0, f"No +EV bet (Up: {up_ev*100:+.1f}%, Down: {down_ev*100:+.1f}%)"
        
        # Calculate bet size
        bet_size = self.calculate_bet_size(BET_SIZE_USD, best_ev, time_to_expiry, data_points)
        
        full_reason = f"EV: {best_ev*100:+.1f}% | P(win): {best_prob*100:.0f}% | {reasoning}"
        
        return best_side, best_ev, best_prob, bet_size, full_reason


class FifteenMinMarketMaker:
    """Market maker for 15-minute crypto markets with proper math."""
    
    def __init__(self, bet_size: float = BET_SIZE_USD):
        self.bet_size = bet_size
        self.session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self.prediction_engine = MathematicalPredictionEngine()
        
    async def start(self):
        """Start the market maker."""
        self.session = aiohttp.ClientSession()
        self._running = True
        logger.info("Market maker started")
        
    async def stop(self):
        """Stop the market maker."""
        self._running = False
        if self.session:
            await self.session.close()
        logger.info("Market maker stopped")
    
    def _get_current_and_future_timestamps(self) -> list[int]:
        """Get timestamps for current and upcoming 15-min windows."""
        now = int(time.time())
        current_window = now - (now % 900) + 900
        
        timestamps = []
        for i in range(4):
            timestamps.append(current_window + (i * 900))
        
        return timestamps
    
    async def fetch_crypto_prices(self) -> dict[str, float]:
        """Fetch live crypto prices from multiple sources."""
        # Try CoinGecko first
        try:
            ids = ",".join(COINGECKO_IDS.values())
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
            
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    prices = {}
                    for asset, coin_id in COINGECKO_IDS.items():
                        if coin_id in data and "usd" in data[coin_id]:
                            prices[asset] = data[coin_id]["usd"]
                    if prices:
                        return prices
        except Exception as e:
            logger.debug(f"CoinGecko failed: {e}")
        
        # Fallback: Use Binance API
        try:
            binance_symbols = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
            prices = {}
            
            for asset, symbol in binance_symbols.items():
                url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        prices[asset] = float(data.get("price", 0))
            
            if prices:
                return prices
        except Exception as e:
            logger.debug(f"Binance failed: {e}")
        
        # Last resort: Use cached/estimated prices
        logger.warning("Using estimated prices (API unavailable)")
        return {"BTC": 88000, "ETH": 2900, "SOL": 130, "XRP": 2.0}
    
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
        
        tasks = [self.fetch_market_by_slug(slug) for slug in slugs_to_check]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, FifteenMinMarket) and result.is_active:
                markets.append(result)
        
        logger.info(f"Found {len(markets)} active 15-minute markets")
        return markets
    
    async def place_simulated_bet(
        self, 
        market: FifteenMinMarket, 
        side: str, 
        amount_usd: float,
        ev: float,
        true_prob: float,
        crypto_price: float
    ) -> Optional[int]:
        """Place a simulated bet and record entry price."""
        entry_price = market.up_price if side == "Up" else market.down_price
        shares = amount_usd / entry_price
        
        # Calculate expected profit if we win
        profit_if_win = shares * 1.0 - amount_usd
        
        position = Position(
            id=None,
            market_id=market.market_id,
            market_name=market.title,
            asset=market.asset,
            side=side,
            entry_price=entry_price,
            amount_usd=amount_usd,
            shares=shares,
            target_price=crypto_price,  # Store entry crypto price for resolution
            start_time=market.start_time.isoformat(),
            end_time=market.end_time.isoformat(),
            status="open"
        )
        
        position_id = add_position(position)
        
        # Record entry crypto price for realistic resolution
        self.prediction_engine.price_tracker.record_entry_price(position_id, crypto_price)
        
        logger.info(
            f"[BET] {market.asset} {side} | "
            f"${amount_usd:.2f} @ {entry_price*100:.1f}¢ | "
            f"EV: {ev*100:+.1f}% | "
            f"P(win): {true_prob*100:.0f}% | "
            f"If Win: +${profit_if_win:.2f}"
        )
        
        return position_id
    
    async def resolve_positions_realistically(self, crypto_prices: dict[str, float]):
        """
        Resolve positions using ACTUAL price comparison.
        
        This is the key fix: instead of random resolution based on our estimate,
        we compare the entry crypto price vs current crypto price to determine
        if Up or Down won.
        """
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
                
                # Check if market has ended (with 1 min buffer)
                if now > end_time + timedelta(minutes=1):
                    asset = pos['asset']
                    side = pos['side']
                    
                    # Get entry price (stored in target_price field)
                    entry_crypto_price = pos.get('target_price', 0)
                    
                    # Get current price
                    current_crypto_price = crypto_prices.get(asset, 0)
                    
                    if entry_crypto_price <= 0 or current_crypto_price <= 0:
                        logger.warning(f"Missing price data for position {pos['id']}")
                        continue
                    
                    # Determine winner based on ACTUAL price movement
                    price_went_up = current_crypto_price > entry_crypto_price
                    
                    # Up wins if price went up, Down wins if price went down or stayed same
                    if side == "Up":
                        won = price_went_up
                    else:  # Down
                        won = not price_went_up
                    
                    # Calculate P&L
                    if won:
                        pnl = pos['shares'] - pos['amount_usd']  # Get $1/share - wagered
                        exit_price = 1.0
                    else:
                        pnl = -pos['amount_usd']  # Lose entire wager
                        exit_price = 0.0
                    
                    resolve_position(pos['id'], won, exit_price)
                    
                    price_change = ((current_crypto_price - entry_crypto_price) / entry_crypto_price) * 100
                    result = "WON ✅" if won else "LOST ❌"
                    
                    logger.info(
                        f"[RESOLVED] {asset} {side} {result} | "
                        f"Price: ${entry_crypto_price:.2f} → ${current_crypto_price:.2f} ({price_change:+.2f}%) | "
                        f"P&L: ${pnl:+.2f}"
                    )
                    
            except Exception as e:
                logger.error(f"Error resolving position {pos['id']}: {e}")
    
    async def run_cycle(self):
        """Run one market making cycle with all risk checks."""
        # Fetch live crypto prices
        crypto_prices = await self.fetch_crypto_prices()
        if not crypto_prices:
            logger.warning("Could not fetch crypto prices")
            return
        
        # Resolve any expired positions using REAL price comparison
        await self.resolve_positions_realistically(crypto_prices)
        
        # Check risk limits
        stats = get_stats()
        if stats['total_pnl'] < -MAX_DAILY_LOSS:
            logger.warning(f"Daily loss limit reached (${stats['total_pnl']:.2f}). Stopping.")
            return
        
        open_positions = get_open_positions()
        if len(open_positions) >= MAX_OPEN_POSITIONS:
            logger.debug(f"Max open positions reached ({len(open_positions)})")
            return
        
        # Track which sides we already have bets on (correlation risk)
        existing_sides = {p['side'] for p in open_positions}
        existing_assets = {p['asset'] for p in open_positions}
        
        # Fetch markets
        markets = await self.fetch_15min_markets()
        
        if not markets:
            logger.info("No active 15-minute markets found")
            return
        
        # Update price tracker with current data
        for asset, price in crypto_prices.items():
            # Find corresponding market for this asset
            for market in markets:
                if market.asset == asset:
                    self.prediction_engine.price_tracker.add_snapshot(
                        asset, market.up_price, market.down_price, price
                    )
                    break
        
        logger.debug(f"Evaluating {len(markets)} markets...")
        
        # Evaluate each market
        for market in markets:
            now = datetime.now(timezone.utc)
            time_to_expiry = (market.end_time - now).total_seconds()
            mins_left = time_to_expiry / 60
            
            # Skip if less than 1 minute to expiry
            if time_to_expiry < 60:
                continue
            
            # Skip if more than 25 minutes to expiry (allow entry at start of 15-min window)
            if time_to_expiry > 1500:
                continue
            
            # Check if we already have a position in this asset
            if market.asset in existing_assets:
                logger.debug(f"{market.asset}: Already have position")
                continue
            
            # Get data points for uncertainty adjustment
            data_points = self.prediction_engine.price_tracker.get_data_points(market.asset)
            
            mins_left = time_to_expiry / 60
            vig = self.prediction_engine.calculate_vig(market.up_price, market.down_price)
            
            logger.info(
                f"📊 {market.asset} | "
                f"Up: {market.up_price*100:.1f}¢ Down: {market.down_price*100:.1f}¢ | "
                f"Vol: ${market.volume:.0f} | Vig: {vig*100:.1f}% | "
                f"{mins_left:.1f}m | Data: {data_points}"
            )
            
            # Evaluate bet
            side, ev, true_prob, bet_size, reason = self.prediction_engine.evaluate_bet(
                market, time_to_expiry, data_points
            )
            
            if not side:
                logger.debug(f"   ❌ {market.asset}: {reason}")
                continue
            
            # Correlation check: don't bet same direction as existing positions
            if side in existing_sides:
                logger.debug(f"{market.asset}: Already have {side} position (correlation)")
                continue
            
            # Get current crypto price for entry
            crypto_price = crypto_prices.get(market.asset, 0)
            if crypto_price <= 0:
                continue
            
            logger.info(f"   ✅ {reason}")
            
            await self.place_simulated_bet(market, side, bet_size, ev, true_prob, crypto_price)
            
            # Update existing sides for correlation check
            existing_sides.add(side)
            existing_assets.add(market.asset)
        
        # Log stats
        stats = get_stats()
        if stats['total_bets'] > 0:
            roi = (stats['total_pnl'] / stats['total_wagered'] * 100) if stats['total_wagered'] > 0 else 0
            logger.info(
                f"📈 STATS: {stats['total_bets']} bets | "
                f"{stats['wins']}W/{stats['losses']}L ({stats['win_rate']:.1f}%) | "
                f"P&L: ${stats['total_pnl']:+.2f} | "
                f"ROI: {roi:+.1f}%"
            )
    
    async def run(self, interval_seconds: int = 20):
        """Run the market maker continuously."""
        logger.info("=" * 70)
        logger.info("  MATHEMATICALLY SOUND 15-MIN MARKET MAKER")
        logger.info("=" * 70)
        logger.info(f"  Assets: BTC, ETH, SOL, XRP")
        logger.info(f"  Base Bet Size: ${self.bet_size:.2f}")
        logger.info(f"  Min EV Threshold: {MIN_EV_THRESHOLD*100:.0f}%")
        logger.info(f"  Min Volume: ${MIN_VOLUME}")
        logger.info(f"  Max Vig: {MAX_VIG_ACCEPTABLE*100:.0f}%")
        logger.info(f"  Kelly Fraction: {KELLY_FRACTION*100:.0f}%")
        logger.info(f"  Max Daily Loss: ${MAX_DAILY_LOSS}")
        logger.info(f"  Max Open Positions: {MAX_OPEN_POSITIONS}")
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
        await maker.run(interval_seconds=20)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await maker.stop()


if __name__ == "__main__":
    asyncio.run(main())

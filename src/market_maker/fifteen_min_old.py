"""
15-Minute Crypto Market Maker Bot - Full Stack Trading System

Integrates:
- Real-time Binance WebSocket for sub-second price data
- Dynamic edge calculation based on time/probability
- Self-learning timing optimizer with Thompson Sampling
- Multi-signal aggregation (momentum, volume, order book, mean reversion)
- Professional risk management with Kelly sizing
"""
import asyncio
import aiohttp
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass
from collections import deque

from src.database import (
    Position, add_position, resolve_position, get_open_positions, get_stats, reset_db,
    record_signal_prediction, resolve_signal_predictions, get_signal_accuracy,
    record_probability_prediction, resolve_probability_prediction, get_probability_calibration
)
from src.utils.logger import get_logger

# Import new full-stack components
from src.signals.price_feed import RealTimePriceFeed, MomentumData
from src.signals.volume_detector import VolumeDetector
from src.signals.aggregator import SignalAggregator, AggregatedSignal
from src.prediction.dynamic_edge import DynamicEdgeCalculator
from src.prediction.calibrator import ProbabilityCalibrator
from src.learning.timing_optimizer import TimingOptimizer
from src.risk.manager import RiskManager, RiskLimits, RiskLevel

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

# === CONFIGURATION ===
BET_SIZE_USD = 10.0
CYCLE_INTERVAL = 15  # seconds between cycles


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


class FullStackMarketMaker:
    """
    Full-stack market maker with professional-grade components.
    
    Architecture:
    ┌─────────────────────────────────────────────────────────┐
    │  Data Layer (Speed)                                     │
    │  - Binance WebSocket: Sub-second price updates          │
    │  - Volume Detector: Anomaly detection                   │
    └─────────────────────────────────────────────────────────┘
                              ↓
    ┌─────────────────────────────────────────────────────────┐
    │  Signal Layer (Intelligence)                            │
    │  - Signal Aggregator: Multi-factor predictions          │
    │  - Momentum, Volume, Order Book, Mean Reversion         │
    └─────────────────────────────────────────────────────────┘
                              ↓
    ┌─────────────────────────────────────────────────────────┐
    │  Prediction Layer (Models)                              │
    │  - Dynamic Edge Calculator: Time/probability-based      │
    │  - Timing Optimizer: Self-learning with Thompson        │
    └─────────────────────────────────────────────────────────┘
                              ↓
    ┌─────────────────────────────────────────────────────────┐
    │  Execution Layer (Risk)                                 │
    │  - Risk Manager: Kelly sizing, limits, correlation      │
    │  - Position tracking and resolution                     │
    └─────────────────────────────────────────────────────────┘
    """
    
    def __init__(self, bet_size: float = BET_SIZE_USD):
        self.bet_size = bet_size
        self.session: Optional[aiohttp.ClientSession] = None
        self._running = False
        
        # Initialize full-stack components
        self.price_feed = RealTimePriceFeed()
        self.volume_detector = VolumeDetector()
        self.signal_aggregator = SignalAggregator(
            price_feed=self.price_feed,
            volume_detector=self.volume_detector
        )
        self.edge_calculator = DynamicEdgeCalculator()
        self.timing_optimizer = TimingOptimizer()
        self.calibrator = ProbabilityCalibrator()  # NEW: Probability calibration
        self.risk_manager = RiskManager(
            limits=RiskLimits(
                max_daily_loss=100.0,
                max_drawdown_percent=25.0,
                max_position_size=50.0,
                min_position_size=1.0,
                max_total_exposure=500.0,
                max_positions_per_asset=2,
                max_open_positions=8,
                max_correlation_exposure=0.7,
                stop_loss_percent=50.0
            ),
            risk_level=RiskLevel.MODERATE
        )
        
        # Entry price tracking for resolution
        self._entry_prices: dict[int, float] = {}  # position_id -> crypto price at entry
        
        # Signal tracking for learning feedback loop
        # Stores: position_id -> (individual_signals, predicted_prob, side)
        self._position_signals: dict[int, tuple] = {}
        
        # Binance WebSocket task
        self._ws_task: Optional[asyncio.Task] = None
        
    async def start(self):
        """Start the market maker with all components."""
        self.session = aiohttp.ClientSession()
        self._running = True
        
        # Start Binance WebSocket in background
        self._ws_task = asyncio.create_task(self._run_price_feed())
        
        logger.info("Full-stack market maker started")
        logger.info("=" * 70)
        logger.info("  FULL-STACK 15-MIN MARKET MAKER")
        logger.info("=" * 70)
        logger.info("  Components:")
        logger.info("    ✓ Binance WebSocket (sub-second prices)")
        logger.info("    ✓ Volume Anomaly Detector")
        logger.info("    ✓ Multi-Signal Aggregator (momentum-focused)")
        logger.info("    ✓ Dynamic Edge Calculator")
        logger.info("    ✓ Probability Calibrator (self-correcting)")
        logger.info("    ✓ Thompson Sampling Timing Optimizer")
        logger.info("    ✓ Professional Risk Manager")
        logger.info("  Learning Loops:")
        logger.info("    ✓ Signal accuracy tracking → weight adjustment")
        logger.info("    ✓ Probability calibration → prediction correction")
        logger.info("    ✓ Timing bucket learning → entry optimization")
        logger.info("=" * 70)
        
    async def stop(self):
        """Stop the market maker."""
        self._running = False
        
        # Stop WebSocket
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        
        await self.price_feed.stop()
        
        if self.session:
            await self.session.close()
        
        logger.info("Market maker stopped")
    
    async def _run_price_feed(self):
        """Run Binance WebSocket in background."""
        try:
            await self.price_feed.connect()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Price feed error: {e}")
    
    def _get_current_and_future_timestamps(self) -> list[int]:
        """Get timestamps for current and upcoming 15-min windows."""
        now = int(time.time())
        current_window = now - (now % 900) + 900
        
        timestamps = []
        for i in range(4):
            timestamps.append(current_window + (i * 900))
        
        return timestamps
    
    async def fetch_crypto_prices(self) -> dict[str, float]:
        """
        Fetch live crypto prices.
        
        Priority:
        1. Real-time from Binance WebSocket (if connected)
        2. CoinGecko API
        3. Binance REST API
        """
        # Try WebSocket prices first (most up-to-date)
        if self.price_feed.is_connected():
            ws_prices = {}
            for asset in ASSETS:
                price = self.price_feed.get_latest_price(asset)
                if price and price > 0:
                    ws_prices[asset] = price
            if len(ws_prices) >= len(ASSETS) - 1:  # Allow 1 missing
                return ws_prices
        
        # Fallback to CoinGecko
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
        
        # Last resort: Binance REST
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
        
        logger.warning("Using estimated prices (all APIs unavailable)")
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
        
        return markets
    
    def calculate_vig(self, up_price: float, down_price: float) -> float:
        """Calculate the vig/overround."""
        return (up_price + down_price) - 1.0
    
    def estimate_edge(
        self, 
        market: FifteenMinMarket,
        aggregated_signal: AggregatedSignal,
        momentum: Optional[MomentumData]
    ) -> tuple[str, float, float, str]:
        """
        Estimate edge using aggregated signals with calibration.
        
        The key insight: For 15-min crypto markets, momentum is THE signal.
        We estimate probability based on:
        1. Market price (what others think) - baseline
        2. Signal strength (our momentum-based adjustment)
        3. Calibration (historical accuracy correction)
        
        Returns: (side, edge, true_probability, reasoning)
        """
        # Get fair probabilities (remove vig)
        total = market.up_price + market.down_price
        fair_up = market.up_price / total if total > 0 else 0.5
        fair_down = market.down_price / total if total > 0 else 0.5
        
        # === MOMENTUM-BASED PROBABILITY ESTIMATION ===
        # The aggregated signal is momentum-focused (70% weight)
        # Stronger momentum = higher probability of continuation
        
        prob_adjustment = aggregated_signal.probability_adjustment
        
        # Scale adjustment by signal confidence
        # High confidence + strong signal = bigger adjustment
        adjusted_prob_shift = prob_adjustment * (0.5 + aggregated_signal.confidence * 0.5)
        
        # Calculate raw predicted probabilities
        raw_up = fair_up + adjusted_prob_shift
        raw_down = fair_down - adjusted_prob_shift
        
        # Bound probabilities
        raw_up = max(0.30, min(0.85, raw_up))
        raw_down = max(0.30, min(0.85, raw_down))
        
        # === APPLY CALIBRATION ===
        # Use historical accuracy to correct our predictions
        # If we're overconfident at 65%, calibration will pull us down
        
        # Calibrate the probability we'll actually use
        if raw_up > raw_down:
            calibration = self.calibrator.calibrate(raw_up)
            true_up = calibration.calibrated_prob
            true_down = 1 - true_up
            calibration_note = f"Cal: {calibration.bucket}"
        else:
            calibration = self.calibrator.calibrate(raw_down)
            true_down = calibration.calibrated_prob
            true_up = 1 - true_down
            calibration_note = f"Cal: {calibration.bucket}"
        
        # If calibrator has low confidence (few samples), blend toward raw
        if calibration.confidence < 0.5:
            blend = calibration.confidence
            true_up = raw_up * (1 - blend) + true_up * blend
            true_down = raw_down * (1 - blend) + true_down * blend
            calibration_note = "Cal: learning"
        
        # === CALCULATE EXPECTED VALUE ===
        # EV = p * profit_if_win - (1-p) * loss_if_lose
        up_ev = true_up * (1 - market.up_price) - (1 - true_up) * market.up_price
        down_ev = true_down * (1 - market.down_price) - (1 - true_down) * market.down_price
        
        # Choose best side
        if up_ev > down_ev:
            side = "Up"
            edge = up_ev
            true_prob = true_up
        else:
            side = "Down"
            edge = down_ev
            true_prob = true_down
        
        # Build reasoning
        signal_dir = aggregated_signal.direction.value
        signal_str = aggregated_signal.strength
        momentum_str = ""
        if momentum:
            momentum_str = f" | Mom: {momentum.trend_strength*100:+.2f}%"
        
        reasoning = (
            f"Signal: {signal_dir} ({signal_str:.0%}){momentum_str} | "
            f"{calibration_note} | "
            f"{aggregated_signal.reasoning}"
        )
        
        return side, edge, true_prob, reasoning
    
    async def place_simulated_bet(
        self, 
        market: FifteenMinMarket, 
        side: str, 
        amount_usd: float,
        edge: float,
        true_prob: float,
        crypto_price: float,
        time_left: float,
        signal_strength: float,
        reasoning: str,
        aggregated_signal: Optional[AggregatedSignal] = None
    ) -> Optional[int]:
        """Place a simulated bet and record entry price with full reasoning."""
        entry_price = market.up_price if side == "Up" else market.down_price
        shares = amount_usd / entry_price
        
        # Calculate expected profit if we win
        profit_if_win = shares * 1.0 - amount_usd
        
        # Get timing bucket
        bucket = self.timing_optimizer.get_bucket(time_left)
        
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
            status="open",
            # Trading decision metadata
            edge=edge,
            true_prob=true_prob,
            signal_strength=signal_strength,
            timing_bucket=bucket,
            reasoning=reasoning
        )
        
        position_id = add_position(position)
        
        # Record entry crypto price for resolution
        self._entry_prices[position_id] = crypto_price
        
        # Store signals for learning feedback loop
        if aggregated_signal:
            self._position_signals[position_id] = (
                aggregated_signal.individual_signals,
                true_prob,
                side
            )
            
            # === RECORD TO DATABASE FOR HISTORICAL ANALYSIS ===
            # Record each individual signal prediction
            for signal in aggregated_signal.individual_signals:
                if abs(signal.value) > 0.05:  # Only record non-neutral signals
                    predicted_dir = "Up" if signal.value > 0 else "Down"
                    record_signal_prediction(
                        position_id=position_id,
                        signal_name=signal.name,
                        signal_value=signal.value,
                        signal_confidence=signal.confidence,
                        predicted_direction=predicted_dir,
                        asset=market.asset,
                        timing_bucket=bucket
                    )
            
            # Record probability prediction for calibration analysis
            prob_bucket = self.calibrator._get_bucket_name(true_prob)
            record_probability_prediction(
                position_id=position_id,
                predicted_prob=true_prob,
                prob_bucket=prob_bucket,
                asset=market.asset
            )
        
        logger.info(
            f"[BET] {market.asset} {side} | "
            f"${amount_usd:.2f} @ {entry_price*100:.1f}¢ | "
            f"Edge: {edge*100:+.1f}% | "
            f"P(win): {true_prob*100:.0f}% | "
            f"Bucket: {bucket} | "
            f"If Win: +${profit_if_win:.2f}"
        )
        logger.info(f"   Reason: {reasoning}")
        
        return position_id
    
    async def resolve_positions(self, crypto_prices: dict[str, float]):
        """Resolve positions using actual price comparison."""
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
                    
                    # Get entry price
                    entry_crypto_price = pos.get('target_price', 0) or self._entry_prices.get(pos['id'], 0)
                    current_crypto_price = crypto_prices.get(asset, 0)
                    
                    if entry_crypto_price <= 0 or current_crypto_price <= 0:
                        logger.warning(f"Missing price data for position {pos['id']}")
                        continue
                    
                    # Determine winner based on actual price movement
                    price_went_up = current_crypto_price > entry_crypto_price
                    
                    if side == "Up":
                        won = price_went_up
                    else:
                        won = not price_went_up
                    
                    # Calculate P&L
                    if won:
                        pnl = pos['shares'] - pos['amount_usd']
                        exit_price = 1.0
                    else:
                        pnl = -pos['amount_usd']
                        exit_price = 0.0
                    
                    resolve_position(pos['id'], won, exit_price)
                    
                    # Update risk manager
                    self.risk_manager.record_pnl(pnl)
                    
                    # === LEARNING FEEDBACK LOOPS ===
                    
                    # 1. Update signal aggregator with trade result
                    # This teaches which signals are predictive
                    if pos['id'] in self._position_signals:
                        signals, predicted_prob, bet_side = self._position_signals[pos['id']]
                        actual_direction = "Up" if price_went_up else "Down"
                        self.signal_aggregator.record_trade_result(
                            individual_signals=signals,
                            won=won,
                            pnl=pnl,
                            actual_direction=actual_direction
                        )
                        del self._position_signals[pos['id']]
                    
                    # 2. Update probability calibrator
                    # This corrects overconfidence/underconfidence
                    predicted_prob = pos.get('true_prob', 0.5)
                    if predicted_prob and predicted_prob > 0:
                        self.calibrator.record_outcome(
                            predicted_probability=predicted_prob,
                            won=won,
                            pnl=pnl
                        )
                    
                    # 3. Update timing optimizer
                    start_time_str = pos.get('start_time', '')
                    try:
                        if '+' in start_time_str or 'Z' in start_time_str:
                            start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                        else:
                            start_time = datetime.fromisoformat(start_time_str)
                        
                        # Use timing bucket from position if available
                        timing_bucket = pos.get('timing_bucket', '')
                        if timing_bucket:
                            # Parse bucket to get approximate time
                            bucket_times = {
                                "1-2min": 90, "2-3min": 150, "3-5min": 240, "5-7min": 360
                            }
                            time_at_entry = bucket_times.get(timing_bucket, 180)
                        else:
                            # Calculate time left when bet was placed (approximate)
                            time_at_entry = (end_time - start_time).total_seconds() / 2
                        
                        # Record for timing optimizer
                        self.timing_optimizer.record_result(
                            time_left_at_entry=time_at_entry,
                            won=won,
                            pnl=pnl,
                            wagered=pos['amount_usd']
                        )
                    except:
                        pass
                    
                    price_change = ((current_crypto_price - entry_crypto_price) / entry_crypto_price) * 100
                    result = "WON ✅" if won else "LOST ❌"
                    
                    logger.info(
                        f"[RESOLVED] {asset} {side} {result} | "
                        f"Price: ${entry_crypto_price:.2f} → ${current_crypto_price:.2f} ({price_change:+.2f}%) | "
                        f"P&L: ${pnl:+.2f}"
                    )
                    
                    # Clean up entry price
                    if pos['id'] in self._entry_prices:
                        del self._entry_prices[pos['id']]
                    
            except Exception as e:
                logger.error(f"Error resolving position {pos['id']}: {e}")
    
    async def run_cycle(self):
        """Run one market making cycle with full-stack analysis."""
        # Fetch live crypto prices
        crypto_prices = await self.fetch_crypto_prices()
        if not crypto_prices:
            logger.warning("Could not fetch crypto prices")
            return
        
        # Update volume detector with latest data
        for asset, price in crypto_prices.items():
            # Simulate volume from price feed if available
            if self.price_feed.is_connected():
                volume_rate = self.price_feed.get_volume_rate(asset, 60)
                self.volume_detector.record_volume(asset, volume_rate)
        
        # Resolve any expired positions
        await self.resolve_positions(crypto_prices)
        
        # Get current positions and stats
        open_positions = get_open_positions()
        stats = get_stats()
        
        # Risk check: daily loss limit
        risk_summary = self.risk_manager.get_risk_summary(open_positions, 1000)
        
        # Fetch markets
        markets = await self.fetch_15min_markets()
        
        if not markets:
            logger.debug("No active 15-minute markets found")
            return
        
        logger.info(f"Found {len(markets)} active markets | {len(open_positions)} open positions")
        
        # Evaluate each market
        for market in markets:
            now = datetime.now(timezone.utc)
            time_to_expiry = (market.end_time - now).total_seconds()
            
            # Skip if less than 1 minute or more than 7 minutes
            # RATIONALE: Betting closer to expiry is more accurate because:
            # - Short-term momentum is more predictive
            # - Less time for random price swings
            # - Market prices have converged to true probability
            if time_to_expiry < 60 or time_to_expiry > 420:  # 1-7 minutes
                continue
            
            # === TIMING OPTIMIZER ===
            timing_decision = self.timing_optimizer.should_bet_now(time_to_expiry)
            if not timing_decision.should_bet:
                logger.debug(f"{market.asset}: {timing_decision.reasoning}")
                continue
            
            # === VOLUME CHECK ===
            can_trade, volume_reason = self.volume_detector.should_trade(market.asset)
            # Don't block on volume in simulation, just log
            if not can_trade:
                logger.debug(f"{market.asset}: Volume warning - {volume_reason}")
            
            # === SIGNAL AGGREGATION ===
            momentum = self.price_feed.get_momentum(market.asset) if self.price_feed.is_connected() else None
            
            aggregated_signal = self.signal_aggregator.aggregate(
                asset=market.asset,
                market_price=market.up_price,
                best_bid=market.up_price - 0.01,
                best_ask=market.up_price + 0.01,
                momentum_data=momentum
            )
            
            # === EDGE CALCULATION ===
            side, estimated_edge, true_prob, reasoning = self.estimate_edge(
                market, aggregated_signal, momentum
            )
            
            # === DYNAMIC EDGE REQUIREMENT ===
            edge_req = self.edge_calculator.calculate_required_edge(
                time_left_seconds=time_to_expiry,
                market_price=market.up_price if side == "Up" else market.down_price,
                volume=market.volume,
                momentum=momentum.trend_strength if momentum else None,
                side=side
            )
            
            vig = self.calculate_vig(market.up_price, market.down_price)
            mins_left = time_to_expiry / 60
            
            # Log market evaluation
            logger.info(
                f"📊 {market.asset} | "
                f"Up: {market.up_price*100:.1f}¢ Down: {market.down_price*100:.1f}¢ | "
                f"Vol: ${market.volume:.0f} | Vig: {vig*100:.1f}% | "
                f"{mins_left:.1f}m | "
                f"Signal: {aggregated_signal.direction.value}"
            )
            
            # Check if edge meets requirement
            if estimated_edge < edge_req.required_edge:
                logger.debug(
                    f"   ❌ Edge {estimated_edge*100:.1f}% < Required {edge_req.required_edge*100:.1f}% | "
                    f"{edge_req.reasoning}"
                )
                continue
            
            # === RISK MANAGEMENT ===
            crypto_price = crypto_prices.get(market.asset, 0)
            
            risk_check = self.risk_manager.can_take_position(
                asset=market.asset,
                side=side,
                proposed_size=self.bet_size,
                edge=estimated_edge,
                confidence=aggregated_signal.confidence,
                current_positions=open_positions,
                bankroll=1000.0
            )
            
            if not risk_check.allowed:
                logger.info(f"   ❌ Risk blocked: {risk_check.reason}")
                continue
            
            # Use risk-adjusted size
            bet_size = risk_check.adjusted_size
            
            if bet_size < 1.0:
                logger.debug(f"   ❌ Bet size too small: ${bet_size:.2f}")
                continue
            
            # === PLACE BET ===
            # Build comprehensive reasoning
            full_reasoning = (
                f"{reasoning} | "
                f"Edge: {estimated_edge*100:+.1f}% > Req: {edge_req.required_edge*100:.1f}% | "
                f"Timing: {timing_decision.bucket} ({timing_decision.sampled_win_rate*100:.0f}% sampled WR) | "
                f"Vig: {vig*100:.1f}% | "
                f"Vol: ${market.volume:.0f}"
            )
            
            logger.info(
                f"   ✅ {reasoning} | "
                f"Edge: {estimated_edge*100:+.1f}% > Req: {edge_req.required_edge*100:.1f}% | "
                f"Size: ${bet_size:.2f}"
            )
            
            await self.place_simulated_bet(
                market=market,
                side=side,
                amount_usd=bet_size,
                edge=estimated_edge,
                true_prob=true_prob,
                crypto_price=crypto_price,
                time_left=time_to_expiry,
                signal_strength=aggregated_signal.strength,
                reasoning=full_reasoning,
                aggregated_signal=aggregated_signal  # For learning feedback
            )
            
            # Refresh positions for correlation check
            open_positions = get_open_positions()
        
        # Log stats
        stats = get_stats()
        if stats['total_bets'] > 0:
            roi = (stats['total_pnl'] / stats['total_wagered'] * 100) if stats['total_wagered'] > 0 else 0
            
            # Get timing summary
            timing_summary = self.timing_optimizer.get_summary()
            best_bucket, best_roi = self.timing_optimizer.get_best_bucket()
            
            logger.info(
                f"📈 STATS: {stats['total_bets']} bets | "
                f"{stats['wins']}W/{stats['losses']}L ({stats['win_rate']:.1f}%) | "
                f"P&L: ${stats['total_pnl']:+.2f} | "
                f"ROI: {roi:+.1f}% | "
                f"Best Bucket: {best_bucket}"
            )
    
    async def run(self, interval_seconds: int = CYCLE_INTERVAL):
        """Run the market maker continuously."""
        while self._running:
            try:
                await self.run_cycle()
            except Exception as e:
                logger.error(f"Error in market maker cycle: {e}")
                import traceback
                traceback.print_exc()
            
            await asyncio.sleep(interval_seconds)


# Keep backward compatibility with old class name
FifteenMinMarketMaker = FullStackMarketMaker


async def main():
    """Main entry point."""
    from src.utils.logger import setup_logging
    setup_logging(level="INFO", json_format=False)
    
    # Reset for fresh start
    logger.info("Resetting database for fresh simulation...")
    reset_db()
    
    maker = FullStackMarketMaker(bet_size=10.0)
    await maker.start()
    
    try:
        await maker.run(interval_seconds=CYCLE_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await maker.stop()


if __name__ == "__main__":
    asyncio.run(main())

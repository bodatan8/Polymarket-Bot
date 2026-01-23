"""
15-Minute Crypto Market Maker - Clean Architecture

Simple, maintainable, and testable design:
- Filter chain for evaluation (each filter is independent)
- Configuration-based strategy (no unnecessary abstractions)
- Clear separation of concerns
- Easy to extend and modify
"""
import asyncio
import aiohttp
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.database import (
    Position, add_position, resolve_position, get_open_positions, get_stats, reset_db,
    record_signal_prediction, record_probability_prediction
)
from src.utils.logger import get_logger
from src.signals.price_feed import RealTimePriceFeed
from src.signals.volume_detector import VolumeDetector
from src.signals.aggregator import SignalAggregator, AggregatedSignal
from src.prediction.dynamic_edge import DynamicEdgeCalculator
from src.prediction.calibrator import ProbabilityCalibrator
from src.learning.timing_optimizer import TimingOptimizer
from src.risk.manager import RiskManager, RiskLimits, RiskLevel
from src.market_maker.config import TradingConfig, config
from src.market_maker.evaluator import MarketEvaluator, EvaluationContext
from src.market_maker.models import FifteenMinMarket

logger = get_logger("market_maker")

# Constants
GAMMA_API = "https://gamma-api.polymarket.com"
ASSETS = {
    "BTC": "btc-updown-15m",
    "ETH": "eth-updown-15m", 
    "SOL": "sol-updown-15m",
    "XRP": "xrp-updown-15m",
}
COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
}


class MarketMaker:
    """
    Clean, simple market maker.
    
    Responsibilities:
    - Fetch market data
    - Evaluate opportunities (via evaluator)
    - Execute trades
    - Manage positions
    """
    
    def __init__(self, cfg: TradingConfig = None):
        self.cfg = cfg or config
        self.session: Optional[aiohttp.ClientSession] = None
        self._running = False
        
        # Initialize components
        self.price_feed = RealTimePriceFeed()
        self.volume_detector = VolumeDetector()
        self.signal_aggregator = SignalAggregator(
            price_feed=self.price_feed,
            volume_detector=self.volume_detector
        )
        self.edge_calculator = DynamicEdgeCalculator()
        self.timing_optimizer = TimingOptimizer()
        self.calibrator = ProbabilityCalibrator()
        
        # Risk manager
        self.risk_manager = RiskManager(
            limits=RiskLimits(
                max_daily_loss=self.cfg.max_daily_loss_usd,
                max_drawdown_percent=self.cfg.max_drawdown_percent,
                max_position_size=self.cfg.max_position_size_usd,
                min_position_size=self.cfg.min_position_size_usd,
                max_total_exposure=self.cfg.max_total_exposure_usd,
                max_positions_per_asset=self.cfg.max_positions_per_asset,
                max_open_positions=self.cfg.max_open_positions,
                max_correlation_exposure=self.cfg.max_correlation_exposure,
                stop_loss_percent=self.cfg.stop_loss_percent
            ),
            risk_level=RiskLevel.MODERATE
        )
        
        # Market evaluator (handles all evaluation logic)
        self.evaluator = MarketEvaluator(
            cfg=self.cfg,
            price_feed=self.price_feed,
            volume_detector=self.volume_detector,
            signal_aggregator=self.signal_aggregator,
            edge_calculator=self.edge_calculator,
            calibrator=self.calibrator,
            timing_optimizer=self.timing_optimizer
        )
        
        # State tracking
        self._entry_prices: dict[int, float] = {}
        self._position_signals: dict[int, tuple] = {}
        self._ws_task: Optional[asyncio.Task] = None
        self._positions_this_cycle = 0
    
    async def start(self):
        """Start the market maker."""
        self.session = aiohttp.ClientSession()
        self._running = True
        self._ws_task = asyncio.create_task(self._run_price_feed())
        
        mode_name = "HIGH FREQUENCY" if self.cfg.high_frequency_mode else "QUALITY"
        logger.info("=" * 70)
        logger.info(f"  MARKET MAKER - {mode_name} MODE")
        logger.info("=" * 70)
        logger.info(f"  Bankroll: ${self.cfg.bankroll_usd:,.0f}")
        logger.info(f"  Cycle: {self._get_cycle_interval()}s")
        logger.info(f"  Base Bet: ${self.cfg.base_bet_size_usd:.2f}")
        logger.info("=" * 70)
    
    async def stop(self):
        """Stop the market maker."""
        self._running = False
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
    
    def _get_cycle_interval(self) -> int:
        """Get cycle interval based on mode."""
        return self.cfg.hf_cycle_interval if self.cfg.high_frequency_mode else self.cfg.quality_cycle_interval
    
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
        return [current_window + (i * 900) for i in range(4)]
    
    async def fetch_crypto_prices(self) -> dict[str, float]:
        """Fetch live crypto prices with fallback chain."""
        # Try WebSocket first
        if self.price_feed.is_connected():
            ws_prices = {
                asset: self.price_feed.get_latest_price(asset)
                for asset in ASSETS
            }
            ws_prices = {k: v for k, v in ws_prices.items() if v and v > 0}
            if len(ws_prices) >= len(ASSETS) - 1:
                return ws_prices
        
        # Fallback to CoinGecko
        try:
            ids = ",".join(COINGECKO_IDS.values())
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    prices = {
                        asset: data[coin_id]["usd"]
                        for asset, coin_id in COINGECKO_IDS.items()
                        if coin_id in data and "usd" in data[coin_id]
                    }
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
        timestamps = self._get_current_and_future_timestamps()
        slugs_to_check = [
            f"{prefix}-{ts}"
            for ts in timestamps
            for prefix in ASSETS.values()
        ]
        
        tasks = [self.fetch_market_by_slug(slug) for slug in slugs_to_check]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return [
            result for result in results
            if isinstance(result, FifteenMinMarket) and result.is_active
        ]
    
    async def place_bet(self, ctx: EvaluationContext) -> Optional[int]:
        """Place a bet based on evaluation context."""
        market = ctx.market
        entry_price = market.up_price if ctx.side == "Up" else market.down_price
        
        # Risk check
        open_positions = get_open_positions()
        risk_check = self.risk_manager.can_take_position(
            asset=market.asset,
            side=ctx.side,
            proposed_size=self.cfg.base_bet_size_usd,
            edge=ctx.edge,
            confidence=ctx.aggregated_signal.confidence,
            current_positions=open_positions,
            bankroll=self.cfg.bankroll_usd
        )
        
        if not risk_check.allowed:
            logger.info(f"   ❌ Risk blocked: {risk_check.reason}")
            return None
        
        # Calculate bet size
        risk_adjusted_size = risk_check.adjusted_size
        
        # Apply mode-specific multiplier
        if self.cfg.high_frequency_mode:
            bet_size = risk_adjusted_size * self.cfg.hf_bet_size_multiplier
        else:
            bet_size = risk_adjusted_size
        
        if bet_size < self.cfg.min_position_size_usd:
            logger.debug(f"   ❌ Bet size too small: ${bet_size:.2f}")
            return None
        
        # Place bet
        shares = bet_size / entry_price
        profit_if_win = shares * 1.0 - bet_size
        bucket = self.timing_optimizer.get_bucket(ctx.time_to_expiry)
        
        position = Position(
            id=None,
            market_id=market.market_id,
            market_name=market.title,
            asset=market.asset,
            side=ctx.side,
            entry_price=entry_price,
            amount_usd=bet_size,
            shares=shares,
            target_price=ctx.crypto_prices.get(market.asset, 0),
            start_time=market.start_time.isoformat(),
            end_time=market.end_time.isoformat(),
            status="open",
            edge=ctx.edge,
            true_prob=ctx.true_probability,
            signal_strength=ctx.aggregated_signal.strength,
            timing_bucket=bucket,
            reasoning=" | ".join(ctx.reasoning)
        )
        
        position_id = add_position(position)
        self._entry_prices[position_id] = ctx.crypto_prices.get(market.asset, 0)
        
        # Store signals for learning
        if ctx.aggregated_signal:
            self._position_signals[position_id] = (
                ctx.aggregated_signal.individual_signals,
                ctx.true_probability,
                ctx.side
            )
            
            # Record predictions
            for signal in ctx.aggregated_signal.individual_signals:
                if abs(signal.value) > 0.05:
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
            
            prob_bucket = self.calibrator._get_bucket_name(ctx.true_probability)
            record_probability_prediction(
                position_id=position_id,
                predicted_prob=ctx.true_probability,
                prob_bucket=prob_bucket,
                asset=market.asset
            )
        
        logger.info(
            f"[BET] {market.asset} {ctx.side} | "
            f"${bet_size:.2f} @ {entry_price*100:.1f}¢ | "
            f"Edge: {ctx.edge*100:+.1f}% | "
            f"P(win): {ctx.true_probability*100:.0f}% | "
            f"Bucket: {bucket} | "
            f"If Win: +${profit_if_win:.2f}"
        )
        logger.info(f"   Reason: {' | '.join(ctx.reasoning)}")
        
        return position_id
    
    async def resolve_positions(self, crypto_prices: dict[str, float]):
        """Resolve expired positions."""
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
                
                if now <= end_time + timedelta(minutes=1):
                    continue
                
                asset = pos['asset']
                side = pos['side']
                entry_crypto_price = pos.get('target_price', 0) or self._entry_prices.get(pos['id'], 0)
                current_crypto_price = crypto_prices.get(asset, 0)
                
                if entry_crypto_price <= 0 or current_crypto_price <= 0:
                    logger.warning(f"Missing price data for position {pos['id']}")
                    continue
                
                price_went_up = current_crypto_price > entry_crypto_price
                won = price_went_up if side == "Up" else not price_went_up
                
                pnl = pos['shares'] - pos['amount_usd'] if won else -pos['amount_usd']
                exit_price = 1.0 if won else 0.0
                
                resolve_position(pos['id'], won, exit_price)
                self.risk_manager.record_pnl(pnl)
                
                # Learning feedback
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
                
                predicted_prob = pos.get('true_prob', 0.5)
                if predicted_prob and predicted_prob > 0:
                    self.calibrator.record_outcome(
                        predicted_probability=predicted_prob,
                        won=won,
                        pnl=pnl
                    )
                
                # Timing optimizer update
                try:
                    start_time_str = pos.get('start_time', '')
                    if '+' in start_time_str or 'Z' in start_time_str:
                        start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                    else:
                        start_time = datetime.fromisoformat(start_time_str)
                    
                    timing_bucket = pos.get('timing_bucket', '')
                    bucket_times = {
                        "1-2min": 90, "2-3min": 150, "3-5min": 240, "5-7min": 360
                    }
                    time_at_entry = bucket_times.get(timing_bucket, 180)
                    
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
                
                if pos['id'] in self._entry_prices:
                    del self._entry_prices[pos['id']]
                    
            except Exception as e:
                logger.error(f"Error resolving position {pos['id']}: {e}")
    
    async def run_cycle(self):
        """Run one market making cycle."""
        # Fetch prices
        crypto_prices = await self.fetch_crypto_prices()
        if not crypto_prices:
            logger.warning("Could not fetch crypto prices")
            return
        
        # Update volume detector
        for asset, price in crypto_prices.items():
            if self.price_feed.is_connected():
                volume_rate = self.price_feed.get_volume_rate(asset, 60)
                self.volume_detector.record_volume(asset, volume_rate)
        
        # Resolve positions
        await self.resolve_positions(crypto_prices)
        
        # Get current state
        open_positions = get_open_positions()
        risk_summary = self.risk_manager.get_risk_summary(open_positions, self.cfg.bankroll_usd)
        
        # Fetch markets
        markets = await self.fetch_15min_markets()
        if not markets:
            logger.debug("No active 15-minute markets found")
            return
        
        logger.info(f"Found {len(markets)} active markets | {len(open_positions)} open positions")
        
        # Reset cycle counter
        self._positions_this_cycle = 0
        
        # Evaluate and trade
        for market in markets:
            # HF mode: check position limit
            if self.cfg.high_frequency_mode:
                if self._positions_this_cycle >= self.cfg.hf_max_positions_per_cycle:
                    logger.debug(f"HF mode: Reached max positions per cycle ({self.cfg.hf_max_positions_per_cycle})")
                    break
            
            # Calculate time to expiry
            now = datetime.now(timezone.utc)
            time_to_expiry = (market.end_time - now).total_seconds()
            
            # Evaluate market
            should_trade, ctx = self.evaluator.evaluate(market, crypto_prices, time_to_expiry)
            
            if not should_trade:
                logger.debug(f"{market.asset}: {ctx.reasoning[-1] if ctx.reasoning else 'Rejected'}")
                continue
            
            # Log evaluation
            mode_label = "🚀 HF" if self.cfg.high_frequency_mode else "📊"
            vig = (market.up_price + market.down_price) - 1.0
            mins_left = time_to_expiry / 60
            
            logger.info(
                f"{mode_label} {market.asset} | "
                f"Up: {market.up_price*100:.1f}¢ Down: {market.down_price*100:.1f}¢ | "
                f"Vol: ${market.volume:.0f} | Vig: {vig*100:.1f}% | "
                f"{mins_left:.1f}m | "
                f"Signal: {ctx.aggregated_signal.direction.value}"
            )
            
            # Place bet
            position_id = await self.place_bet(ctx)
            
            if position_id:
                self._positions_this_cycle += 1
                open_positions = get_open_positions()  # Refresh for correlation check
        
        # Log stats
        stats = get_stats()
        if stats['total_bets'] > 0:
            roi = (stats['total_pnl'] / stats['total_wagered'] * 100) if stats['total_wagered'] > 0 else 0
            best_bucket, best_roi = self.timing_optimizer.get_best_bucket()
            
            logger.info(
                f"📈 STATS: {stats['total_bets']} bets | "
                f"{stats['wins']}W/{stats['losses']}L ({stats['win_rate']:.1f}%) | "
                f"P&L: ${stats['total_pnl']:+.2f} | "
                f"ROI: {roi:+.1f}% | "
                f"Best Bucket: {best_bucket}"
            )
    
    async def run(self):
        """Run the market maker continuously."""
        interval = self._get_cycle_interval()
        while self._running:
            try:
                await self.run_cycle()
            except Exception as e:
                logger.error(f"Error in market maker cycle: {e}")
                import traceback
                traceback.print_exc()
            await asyncio.sleep(interval)


# Backward compatibility
FullStackMarketMaker = MarketMaker
FifteenMinMarketMaker = MarketMaker


async def main():
    """Main entry point."""
    from src.utils.logger import setup_logging
    setup_logging(level="INFO", json_format=False)
    
    logger.info("Resetting database for fresh simulation...")
    reset_db()
    
    maker = MarketMaker()
    await maker.start()
    
    try:
        await maker.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await maker.stop()


if __name__ == "__main__":
    asyncio.run(main())

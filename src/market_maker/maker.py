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
from datetime import datetime, timezone
from typing import Optional

from src.database import get_open_positions, get_stats, reset_db
from src.utils.logger import get_logger
from src.signals.price_feed import RealTimePriceFeed
from src.signals.volume_detector import VolumeDetector
from src.signals.aggregator import SignalAggregator
from src.prediction.dynamic_edge import DynamicEdgeCalculator
from src.prediction.calibrator import ProbabilityCalibrator
from src.learning.timing_optimizer import TimingOptimizer
from src.risk.manager import RiskManager, RiskLimits, RiskLevel
from src.market_maker.config import TradingConfig, config
from src.market_maker.evaluator import MarketEvaluator, EvaluationContext
from src.market_maker.data_fetcher import MarketDataFetcher
from src.market_maker.executor import TradeExecutor

logger = get_logger("market_maker")


class MarketMaker:
    """
    Market Maker Orchestrator
    
    Responsibilities:
    - Initialize components
    - Coordinate data fetching → evaluation → execution
    - Run main loop
    - Handle lifecycle (start/stop)
    
    No business logic - delegates to specialized components.
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
        
        # Market evaluator (handles evaluation logic)
        self.evaluator = MarketEvaluator(
            cfg=self.cfg,
            price_feed=self.price_feed,
            volume_detector=self.volume_detector,
            signal_aggregator=self.signal_aggregator,
            edge_calculator=self.edge_calculator,
            calibrator=self.calibrator,
            timing_optimizer=self.timing_optimizer
        )
        
        # Data fetcher (handles API calls)
        self.data_fetcher: Optional[MarketDataFetcher] = None
        
        # Trade executor (handles execution)
        self.executor = TradeExecutor(
            cfg=self.cfg,
            risk_manager=self.risk_manager,
            timing_optimizer=self.timing_optimizer,
            calibrator=self.calibrator,
            signal_aggregator=self.signal_aggregator
        )
        
        # State tracking
        self._ws_task: Optional[asyncio.Task] = None
        self._positions_this_cycle = 0
    
    async def start(self):
        """Start the market maker."""
        self.session = aiohttp.ClientSession()
        self.data_fetcher = MarketDataFetcher(self.session, self.price_feed)
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
            import traceback
            traceback.print_exc()
    
    
    
    async def run_cycle(self):
        """Run one market making cycle."""
        # Step 1: Fetch data
        crypto_prices = await self.data_fetcher.fetch_crypto_prices()
        if not crypto_prices:
            logger.warning("Could not fetch crypto prices")
            return
        
        # Update volume detector
        price_feed_connected = self.price_feed.is_connected()
        if not price_feed_connected:
            logger.debug("Price feed not connected - using fallback prices")
        
        for asset, price in crypto_prices.items():
            if price_feed_connected:
                try:
                    volume_rate = self.price_feed.get_volume_rate(asset, 60)
                    if volume_rate and volume_rate > 0:
                        self.volume_detector.record_volume(asset, volume_rate)
                except Exception as e:
                    logger.debug(f"Error getting volume rate for {asset}: {e}")
                    self.volume_detector.record_volume(asset, 1000.0)
            else:
                self.volume_detector.record_volume(asset, 1000.0)  # $1000/sec baseline
        
        # Step 2: Resolve positions
        await self.executor.resolve_positions(crypto_prices)
        
        # Step 3: Fetch markets
        markets = await self.data_fetcher.fetch_15min_markets()
        if not markets:
            logger.debug("No active 15-minute markets found")
            return
        
        open_positions = get_open_positions()
        logger.info(f"Found {len(markets)} active markets | {len(open_positions)} open positions")
        
        # Step 4: Evaluate and trade
        self._positions_this_cycle = 0
        
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
                rejection_reason = ctx.reasoning[-1] if ctx.reasoning else 'Rejected'
                logger.info(f"❌ {market.asset}: {rejection_reason}")
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
            position_id = await self.executor.place_bet(ctx)
            
            if position_id:
                self._positions_this_cycle += 1
                open_positions = get_open_positions()  # Refresh for correlation check
        
        # Step 5: Log stats
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
    try:
        await maker.start()
        
        try:
            await maker.run()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception as e:
            logger.error(f"Fatal error in market maker: {e}")
            import traceback
            traceback.print_exc()
            raise
    finally:
        await maker.stop()


if __name__ == "__main__":
    asyncio.run(main())

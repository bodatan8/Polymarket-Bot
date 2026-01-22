"""
Main entry point for Polymarket Arbitrage Bot.
Orchestrates all components and runs the main event loop.
"""

import asyncio
import signal
import sys
from typing import Optional

# Use uvloop for better performance on Linux
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass  # uvloop not available (Windows)

from .config import load_config, Config
from .clients.websocket_client import WebSocketClient, OrderBook
from .clients.clob_client import CLOBClient
from .clients.gamma_client import GammaClient
from .clients.polygon_client import PolygonClient
from .arbitrage.detector import ArbitrageDetector, ArbitrageOpportunity
from .execution.executor import OrderExecutor, ArbitrageTrade, TradeState
from .execution.merger import TokenMerger
from .utils.cost_calculator import CostCalculator
from .utils.logger import setup_logging, get_logger

logger = get_logger("main")


class PolymarketArbBot:
    """
    Main bot orchestrator.
    
    Coordinates:
    - WebSocket connection for real-time data
    - Arbitrage detection
    - Order execution
    - Token merging
    - Risk controls
    """
    
    def __init__(self, config: Config):
        """Initialize bot with configuration."""
        self.config = config
        self._running = False
        self._shutdown_event = asyncio.Event()
        
        # Initialize components
        self.cost_calculator = CostCalculator(
            taker_fee_bps=config.trading.clob_taker_fee_bps,
            maker_fee_bps=config.trading.clob_maker_fee_bps,
            merge_gas_usd=config.trading.merge_gas_cost_usd
        )
        
        self.gamma_client = GammaClient()
        
        self.clob_client = CLOBClient(
            api_key=config.polymarket.api_key,
            api_secret=config.polymarket.api_secret,
            api_passphrase=config.polymarket.api_passphrase,
            private_key=config.wallet.private_key,
            chain_id=config.wallet.chain_id
        )
        
        self.polygon_client = PolygonClient(
            rpc_url=config.wallet.polygon_rpc_url,
            private_key=config.wallet.private_key,
            wallet_address=config.wallet.wallet_address
        )
        
        self.detector: Optional[ArbitrageDetector] = None
        self.executor: Optional[OrderExecutor] = None
        self.merger: Optional[TokenMerger] = None
        self.ws_client: Optional[WebSocketClient] = None
        
        # Stats
        self._opportunities_seen = 0
        self._trades_attempted = 0
        self._trades_successful = 0
        self._total_profit = 0.0
    
    async def initialize(self) -> None:
        """Initialize all components."""
        logger.info("Initializing Polymarket Arbitrage Bot")
        
        # Check kill switch
        if self.config.risk.kill_switch:
            logger.warning("Kill switch is enabled - bot will not trade")
        
        # Initialize clients
        await self.gamma_client.initialize()
        await self.clob_client.initialize()
        
        # Polygon client is only needed for actual trading
        try:
            await self.polygon_client.initialize()
        except Exception as e:
            if self.config.risk.simulation_mode:
                logger.warning(f"Polygon client init failed (simulation mode): {e}")
            else:
                raise
        
        # Check wallet balance (skip in simulation mode if RPC fails)
        try:
            balance = await self.polygon_client.get_balance()
            logger.info(
                f"Wallet balance",
                extra={
                    "usdc": balance.usdc_balance,
                    "matic": balance.matic_balance
                }
            )
            
            if balance.usdc_balance < self.config.risk.min_wallet_balance:
                logger.warning(
                    f"Low USDC balance: {balance.usdc_balance} < {self.config.risk.min_wallet_balance}"
                )
        except Exception as e:
            if self.config.risk.simulation_mode:
                logger.warning(f"Could not check balance (simulation mode): {e}")
            else:
                raise
        
        # Initialize detector
        self.detector = ArbitrageDetector(
            cost_calculator=self.cost_calculator,
            gamma_client=self.gamma_client,
            min_edge_bps=self.config.trading.min_edge_bps,
            min_size=1.0,
            max_size=self.config.trading.max_position_size,
            on_opportunity=self._on_opportunity
        )
        await self.detector.initialize()
        
        # Initialize executor
        self.executor = OrderExecutor(
            clob_client=self.clob_client,
            max_concurrent_trades=self.config.trading.max_concurrent_orders
        )
        
        # Initialize merger
        self.merger = TokenMerger(
            polygon_client=self.polygon_client
        )
        
        # Initialize WebSocket client
        self.ws_client = WebSocketClient(
            on_book_update=self._on_book_update
        )
        
        logger.info("Bot initialized successfully")
    
    async def run(self) -> None:
        """Run the main bot loop."""
        self._running = True
        
        logger.info("Starting Polymarket Arbitrage Bot")
        
        try:
            # Connect WebSocket
            await self.ws_client.connect()
            
            # Subscribe to tokens (limit for simulation to avoid overwhelming)
            token_ids = self.detector.get_all_token_ids()
            
            # In simulation mode, only subscribe to first 1000 tokens for testing
            if self.config.risk.simulation_mode:
                token_ids = token_ids[:1000]
                logger.info(f"[SIMULATION] Subscribing to {len(token_ids)} tokens (limited)")
            else:
                logger.info(f"Subscribing to {len(token_ids)} tokens")
            
            # Subscribe to all tokens (batching handled internally)
            await self.ws_client.subscribe(token_ids)
            
            # Run main tasks
            await asyncio.gather(
                self._run_websocket(),
                self._run_market_refresh(),
                self._run_stats_reporter(),
                self._wait_for_shutdown()
            )
            
        except Exception as e:
            logger.error(f"Bot error: {e}")
            raise
        
        finally:
            await self.shutdown()
    
    async def _run_websocket(self) -> None:
        """Run WebSocket message processing."""
        try:
            await self.ws_client.run()
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            if self._running:
                raise
    
    async def _run_market_refresh(self) -> None:
        """Periodically refresh market data."""
        while self._running:
            try:
                await asyncio.sleep(300)  # Every 5 minutes
                
                if not self._running:
                    break
                
                new_tokens = await self.detector.refresh_markets()
                
                if new_tokens and self.ws_client:
                    await self.ws_client.subscribe(new_tokens)
                    
            except Exception as e:
                logger.error(f"Market refresh error: {e}")
    
    async def _run_stats_reporter(self) -> None:
        """Periodically report statistics."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Every minute
                
                if not self._running:
                    break
                
                self._log_stats()
                
            except Exception as e:
                logger.error(f"Stats reporter error: {e}")
    
    async def _wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        await self._shutdown_event.wait()
    
    async def _on_book_update(self, order_book: OrderBook) -> None:
        """Handle order book update from WebSocket."""
        if not self._running or not self.detector:
            return
        
        # Pass to detector
        await self.detector.on_order_book_update(order_book)
    
    async def _on_opportunity(self, opportunity: ArbitrageOpportunity) -> None:
        """Handle detected arbitrage opportunity."""
        self._opportunities_seen += 1
        
        logger.info(
            f"Opportunity detected",
            extra={
                "market": opportunity.market.question[:50],
                "edge_bps": opportunity.edge_bps,
                "max_size": opportunity.max_size
            }
        )
        
        # Check kill switch
        if self.config.risk.kill_switch:
            logger.debug("Kill switch enabled - not executing")
            return
        
        # Check simulation mode
        if self.config.risk.simulation_mode:
            logger.info(
                "[SIMULATION] Would execute trade",
                extra={
                    "market": opportunity.market.question[:50],
                    "edge_bps": opportunity.edge_bps,
                    "potential_profit": opportunity.analysis.potential_profit,
                    "max_size": opportunity.max_size
                }
            )
            return
        
        # Check if we should execute
        if not opportunity.is_executable:
            logger.debug("Opportunity not executable")
            return
        
        # Execute trade
        try:
            self._trades_attempted += 1
            trade = await self.executor.execute_arbitrage(opportunity)
            
            if trade.state == TradeState.FULLY_FILLED:
                # Merge tokens
                result = await self.merger.merge_trade(trade)
                
                if result.success:
                    self._trades_successful += 1
                    self._total_profit += result.profit_realized
                    
                    logger.info(
                        f"Trade completed successfully",
                        extra={
                            "trade_id": trade.trade_id,
                            "profit": result.profit_realized,
                            "total_profit": self._total_profit
                        }
                    )
            else:
                logger.warning(f"Trade not fully filled: {trade.state.value}")
                
        except Exception as e:
            logger.error(f"Trade execution error: {e}")
    
    def _log_stats(self) -> None:
        """Log current statistics."""
        detector_stats = self.detector.get_stats() if self.detector else None
        merger_stats = self.merger.get_stats() if self.merger else {}
        
        logger.info(
            "Bot statistics",
            extra={
                "opportunities_seen": self._opportunities_seen,
                "trades_attempted": self._trades_attempted,
                "trades_successful": self._trades_successful,
                "total_profit": self._total_profit,
                "markets_monitored": detector_stats.markets_monitored if detector_stats else 0,
                "avg_scan_ms": detector_stats.avg_scan_duration_ms if detector_stats else 0,
                "merger_success_rate": merger_stats.get("success_rate", 0)
            }
        )
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the bot."""
        logger.info("Shutting down bot")
        self._running = False
        
        # Cancel active trades
        if self.executor:
            cancelled = await self.executor.cancel_all_active()
            if cancelled:
                logger.info(f"Cancelled {cancelled} active trades")
        
        # Disconnect WebSocket
        if self.ws_client:
            await self.ws_client.disconnect()
        
        # Close HTTP sessions
        if self.gamma_client:
            await self.gamma_client.close()
        
        # Log final stats
        self._log_stats()
        
        logger.info("Bot shutdown complete")
    
    def request_shutdown(self) -> None:
        """Request graceful shutdown."""
        self._shutdown_event.set()


def setup_signal_handlers(bot: PolymarketArbBot) -> None:
    """Set up signal handlers for graceful shutdown."""
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}")
        bot.request_shutdown()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


async def main() -> None:
    """Main entry point."""
    # Load configuration
    try:
        config = load_config()
    except ValueError as e:
        print(f"Configuration error: {e}")
        sys.exit(1)
    
    # Set up logging
    setup_logging(
        level=config.logging.log_level,
        json_format=config.logging.json_logging
    )
    
    logger.info("Starting Polymarket Arbitrage Bot")
    
    # Create and run bot
    bot = PolymarketArbBot(config)
    setup_signal_handlers(bot)
    
    try:
        await bot.initialize()
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

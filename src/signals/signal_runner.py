"""
Continuous Signal Runner - FAST

Runs every 5-10 seconds to check for trading signals.
Optimized for speed with parallel requests and session reuse.

Run locally: python -m src.signals.signal_runner
Deploy: Azure Container Apps (see deploy/Dockerfile.signalrunner)
"""
import asyncio
import os
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import aiohttp

from .live_predictor import LivePredictor, PredictionDirection, PredictionSignal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration - FAST checking
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL", "5"))  # Default 5 seconds
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
EXPIRY_MINUTES = 7

# Supabase config
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

# Polymarket bot webhook (optional)
POLYMARKET_BOT_URL = os.getenv("POLYMARKET_BOT_URL", "")

# Minimum confidence to act on
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.50"))


class FastSignalRunner:
    """
    Fast signal runner with optimizations:
    - Reusable HTTP session
    - Parallel symbol checking
    - Persistent predictor
    """
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.predictor: Optional[LivePredictor] = None
        self.check_count = 0
        self.signal_count = 0
        self.last_signals: Dict[str, str] = {}  # Avoid duplicate alerts
    
    async def start(self):
        """Start the session."""
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self.session = aiohttp.ClientSession(timeout=timeout)
            # Share session with predictor for speed
            self.predictor = LivePredictor(session=self.session)
    
    async def stop(self):
        """Stop and cleanup."""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def check_symbol(self, symbol: str) -> Optional[PredictionSignal]:
        """Check a single symbol for signals."""
        if not self.predictor:
            logger.error("Predictor not initialized - call start() first")
            return None
        try:
            signal = await self.predictor.generate_signal(symbol, EXPIRY_MINUTES)
            return signal
        except Exception as e:
            logger.error(f"Error checking {symbol}: {e}")
            return None
    
    async def check_all_symbols(self) -> List[PredictionSignal]:
        """Check all symbols in PARALLEL for speed."""
        tasks = [self.check_symbol(symbol) for symbol in SYMBOLS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        signals = []
        for result in results:
            if isinstance(result, PredictionSignal):
                signals.append(result)
            elif isinstance(result, Exception):
                logger.error(f"Signal check error: {result}")
        
        return signals
    
    async def store_signal_supabase(self, signal: PredictionSignal) -> bool:
        """Store signal in Supabase database."""
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not self.session:
            return False
        
        try:
            url = f"{SUPABASE_URL}/rest/v1/strategy_signals"
            headers = {
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal"
            }
            
            # Match existing table structure:
            # id, strategy_id, asset, signal_type, strength, timestamp, indicators, triggered
            payload = {
                "strategy_id": "mean_reversion_7m",
                "asset": signal.symbol,
                "signal_type": "BUY" if signal.direction == PredictionDirection.UP else "SELL",
                "strength": signal.confidence,
                "timestamp": signal.timestamp.isoformat(),
                "indicators": {
                    "rsi": signal.rsi,
                    "ema8_distance": signal.ema8_distance,
                    "volatility_ratio": signal.volatility_ratio,
                    "accuracy_estimate": signal.accuracy_estimate,
                    "hour_utc": signal.hour_utc,
                    "reasoning": signal.reasoning,
                },
                "triggered": False,
            }
            
            async with self.session.post(url, json=payload, headers=headers) as resp:
                if resp.status in [200, 201]:
                    logger.info(f"Stored: {signal.symbol} {signal.direction.value}")
                    return True
                else:
                    text = await resp.text()
                    logger.error(f"Supabase error: {resp.status} - {text[:100]}")
                    return False
        except Exception as e:
            logger.error(f"Store error: {e}")
            return False
    
    async def trigger_polymarket_bot(self, signal: PredictionSignal) -> bool:
        """Call Polymarket bot webhook."""
        if not POLYMARKET_BOT_URL or not self.session:
            return False
        
        try:
            payload = {
                "action": signal.direction.value.lower(),
                "asset": signal.symbol.replace("USDT", ""),
                "strategy": "mean_reversion_7m",
                "strength": signal.confidence,
                "accuracy_estimate": signal.accuracy_estimate,
                "indicators": {
                    "rsi": signal.rsi,
                    "ema8_distance": signal.ema8_distance,
                },
                "reasoning": signal.reasoning,
                "timestamp": signal.timestamp.isoformat(),
                "expiry_minutes": EXPIRY_MINUTES,
            }
            
            async with self.session.post(POLYMARKET_BOT_URL, json=payload) as resp:
                if resp.status == 200:
                    logger.info(f"Triggered bot: {signal.symbol} {signal.direction.value}")
                    return True
                return False
        except Exception as e:
            logger.error(f"Bot trigger error: {e}")
            return False
    
    def is_duplicate_signal(self, signal: PredictionSignal) -> bool:
        """Check if this is a duplicate of the last signal for this symbol."""
        key = signal.symbol
        last = self.last_signals.get(key)
        current = signal.direction.value
        
        if last == current:
            return True
        
        # Update last signal
        if signal.direction != PredictionDirection.NO_SIGNAL:
            self.last_signals[key] = current
        else:
            self.last_signals.pop(key, None)
        
        return False
    
    async def run_once(self) -> int:
        """Run one check cycle. Returns number of actionable signals."""
        self.check_count += 1
        start = datetime.now(timezone.utc)
        
        # Check all symbols in parallel
        signals = await self.check_all_symbols()
        
        actionable = 0
        for signal in signals:
            if signal.direction != PredictionDirection.NO_SIGNAL:
                if signal.confidence >= MIN_CONFIDENCE:
                    # Skip duplicates (same signal repeated)
                    if self.is_duplicate_signal(signal):
                        logger.debug(f"{signal.symbol}: Duplicate signal, skipping")
                        continue
                    
                    actionable += 1
                    self.signal_count += 1
                    
                    logger.info(
                        f">>> SIGNAL: {signal.symbol} {signal.direction.value} "
                        f"[{signal.confidence:.0%} conf, {signal.accuracy_estimate:.0%} acc] "
                        f"- {signal.reasoning}"
                    )
                    
                    # Store and trigger in parallel
                    await asyncio.gather(
                        self.store_signal_supabase(signal),
                        self.trigger_polymarket_bot(signal),
                        return_exceptions=True
                    )
        
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        
        if actionable > 0:
            logger.info(f"Check #{self.check_count}: {actionable} signals ({elapsed:.2f}s)")
        elif self.check_count % 12 == 0:  # Log every minute when quiet
            logger.info(f"Check #{self.check_count}: Watching... (total signals: {self.signal_count})")
        
        return actionable
    
    async def run_continuous(self):
        """Run continuous loop."""
        logger.info("=" * 60)
        logger.info("FAST Signal Runner Starting")
        logger.info(f"Interval: {CHECK_INTERVAL_SECONDS}s | Symbols: {', '.join(SYMBOLS)}")
        logger.info(f"Min confidence: {MIN_CONFIDENCE:.0%} | Expiry: {EXPIRY_MINUTES}m")
        logger.info(f"Supabase: {'OK' if SUPABASE_URL else 'OFF'} | Bot: {'OK' if POLYMARKET_BOT_URL else 'OFF'}")
        logger.info("=" * 60)
        
        await self.start()
        
        try:
            while True:
                start = datetime.now(timezone.utc)
                
                await self.run_once()
                
                # Sleep remaining time
                elapsed = (datetime.now(timezone.utc) - start).total_seconds()
                sleep_time = max(0, CHECK_INTERVAL_SECONDS - elapsed)
                
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                    
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            await self.stop()


async def run_continuous():
    """Entry point for continuous running."""
    runner = FastSignalRunner()
    await runner.run_continuous()


async def check_once() -> List[Dict[str, Any]]:
    """Run one check and return results (for API/testing)."""
    runner = FastSignalRunner()
    await runner.start()
    try:
        signals = await runner.check_all_symbols()
        return [s.to_dict() for s in signals]
    finally:
        await runner.stop()


def main():
    """Entry point."""
    asyncio.run(run_continuous())


if __name__ == "__main__":
    main()

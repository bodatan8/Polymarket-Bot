"""
Continuous Signal Runner

Runs every 10 seconds to check for trading signals.
Stores actionable signals in Supabase and can trigger Polymarket trades.

This is designed to run as a long-running process in Azure Container Apps.
"""
import asyncio
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from .live_predictor import LivePredictor, PredictionDirection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
CHECK_INTERVAL_SECONDS = 10  # Check every 10 seconds
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
EXPIRY_MINUTES = 7

# Supabase config
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

# Polymarket bot webhook (optional)
POLYMARKET_BOT_URL = os.getenv("POLYMARKET_BOT_URL", "")


async def store_signal_supabase(signal: dict) -> bool:
    """Store signal in Supabase database."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.warning("Supabase not configured, skipping storage")
        return False
    
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{SUPABASE_URL}/rest/v1/strategy_signals"
            headers = {
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal"
            }
            
            payload = {
                "strategy_name": "mean_reversion_7m",
                "symbol": signal["symbol"],
                "timeframe": "7m",
                "signal_type": "BUY" if signal["direction"] == "UP" else "SELL",
                "strength": signal["confidence"],
                "price_at_signal": 0,  # We could fetch this
                "indicators": {
                    "rsi": signal["rsi"],
                    "ema8_distance": signal["ema8_distance"],
                    "volatility_ratio": signal["volatility_ratio"],
                },
                "metadata": {
                    "accuracy_estimate": signal["accuracy_estimate"],
                    "hour_utc": signal["hour_utc"],
                    "reasoning": signal["reasoning"],
                },
                "triggered_at": signal["timestamp"],
            }
            
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status in [200, 201]:
                    logger.info(f"Stored signal in Supabase: {signal['symbol']} {signal['direction']}")
                    return True
                else:
                    text = await resp.text()
                    logger.error(f"Failed to store signal: {resp.status} - {text}")
                    return False
    except Exception as e:
        logger.error(f"Error storing signal: {e}")
        return False


async def trigger_polymarket_bot(signal: dict) -> bool:
    """Call Polymarket bot webhook with signal."""
    if not POLYMARKET_BOT_URL:
        logger.debug("Polymarket bot URL not configured")
        return False
    
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "action": signal["direction"].lower(),
                "asset": signal["symbol"].replace("USDT", ""),
                "strategy": "mean_reversion_7m",
                "strength": signal["confidence"],
                "accuracy_estimate": signal["accuracy_estimate"],
                "indicators": {
                    "rsi": signal["rsi"],
                    "ema8_distance": signal["ema8_distance"],
                },
                "reasoning": signal["reasoning"],
                "timestamp": signal["timestamp"],
                "expiry_minutes": EXPIRY_MINUTES,
            }
            
            async with session.post(POLYMARKET_BOT_URL, json=payload) as resp:
                if resp.status == 200:
                    logger.info(f"Triggered Polymarket bot: {signal['symbol']} {signal['direction']}")
                    return True
                else:
                    text = await resp.text()
                    logger.warning(f"Polymarket bot response: {resp.status} - {text}")
                    return False
    except Exception as e:
        logger.error(f"Error triggering Polymarket bot: {e}")
        return False


async def check_signals_once():
    """Check all symbols for signals once."""
    predictor = LivePredictor()
    actionable_count = 0
    
    for symbol in SYMBOLS:
        try:
            signal = await predictor.generate_signal(symbol, EXPIRY_MINUTES)
            signal_dict = signal.to_dict()
            
            if signal.direction != PredictionDirection.NO_SIGNAL:
                actionable_count += 1
                logger.info(
                    f"SIGNAL: {symbol} {signal.direction.value} "
                    f"(conf: {signal.confidence:.0%}, acc: {signal.accuracy_estimate:.0%}) "
                    f"- {signal.reasoning}"
                )
                
                # Store and trigger
                await store_signal_supabase(signal_dict)
                await trigger_polymarket_bot(signal_dict)
            else:
                logger.debug(f"{symbol}: No signal - {signal.reasoning}")
                
        except Exception as e:
            logger.error(f"Error checking {symbol}: {e}")
    
    return actionable_count


async def run_continuous():
    """Run continuous signal checking loop."""
    logger.info("=" * 60)
    logger.info("Starting Continuous Signal Runner")
    logger.info(f"Check interval: {CHECK_INTERVAL_SECONDS}s")
    logger.info(f"Symbols: {', '.join(SYMBOLS)}")
    logger.info(f"Expiry window: {EXPIRY_MINUTES} minutes")
    logger.info(f"Supabase: {'configured' if SUPABASE_URL else 'not configured'}")
    logger.info(f"Polymarket bot: {'configured' if POLYMARKET_BOT_URL else 'not configured'}")
    logger.info("=" * 60)
    
    check_count = 0
    signal_count = 0
    
    while True:
        try:
            check_count += 1
            start_time = datetime.now(timezone.utc)
            
            actionable = await check_signals_once()
            signal_count += actionable
            
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            
            if actionable > 0:
                logger.info(f"Check #{check_count}: {actionable} actionable signals (took {elapsed:.1f}s)")
            else:
                # Only log every 6 checks (1 minute) when no signals
                if check_count % 6 == 0:
                    logger.info(f"Check #{check_count}: No signals. Total signals so far: {signal_count}")
            
            # Wait for next check
            sleep_time = max(0, CHECK_INTERVAL_SECONDS - elapsed)
            await asyncio.sleep(sleep_time)
            
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def main():
    """Entry point."""
    asyncio.run(run_continuous())


if __name__ == "__main__":
    main()

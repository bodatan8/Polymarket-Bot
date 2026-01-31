"""Quick test of the backtesting system."""
import logging
import pandas as pd
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

from src.data.backfill import BinanceBackfill
from src.indicators.supertrend import SupertrendIndicator
from src.strategies.supertrend_cross import SupertrendCrossStrategy
from src.backtesting.engine import BacktestEngine, BacktestConfig

def main():
    print("\n" + "="*60)
    print("BACKTEST: Supertrend Cross Strategy on BTC")
    print("="*60)
    
    # Load data
    backfill = BinanceBackfill()
    
    print("\nLoading 1-minute data...")
    data_1m = backfill.load_from_csv("BTCUSDT", "1m")
    
    if data_1m.empty:
        print("No data found!")
        return
    
    print(f"Loaded {len(data_1m):,} candles")
    print(f"Date range: {data_1m.index[0]} to {data_1m.index[-1]}")
    
    # Resample to 1-hour for the slow timeframe
    print("\nResampling to 1-hour timeframe...")
    data_1h = data_1m.resample('1h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    print(f"Created {len(data_1h):,} hourly candles")
    
    # Use last 6 months of data for faster test
    print("\nUsing last 6 months for backtest...")
    cutoff = data_1m.index[-1] - pd.Timedelta(days=180)
    data_1m_subset = data_1m[data_1m.index >= cutoff]
    data_1h_subset = data_1h[data_1h.index >= cutoff]
    
    print(f"1m candles: {len(data_1m_subset):,}")
    print(f"1h candles: {len(data_1h_subset):,}")
    
    # Configure backtest
    config = BacktestConfig(
        initial_capital=10000.0,
        position_size_pct=10.0,  # 10% per trade
        max_positions=1,
        commission_pct=0.1,
        slippage_pct=0.05,
        stop_loss_pct=2.0,  # 2% stop loss
        take_profit_pct=4.0,  # 4% take profit
    )
    
    # Setup engine
    engine = BacktestEngine(config)
    engine.add_indicator(SupertrendIndicator(period=10, multiplier=3), "1m", "BTCUSDT")
    engine.add_indicator(SupertrendIndicator(period=10, multiplier=3), "1h", "BTCUSDT")
    
    # Set strategy
    strategy = SupertrendCrossStrategy(
        fast_timeframe="1m",
        slow_timeframe="1h",
        require_flip=True  # Require flip on 1m
    )
    engine.set_strategy(strategy)
    
    # Run backtest
    print("\nRunning backtest...")
    print("(This may take a few minutes for 6 months of 1-min data)")
    
    result = engine.run(
        data={"1m": data_1m_subset, "1h": data_1h_subset},
        asset="BTCUSDT"
    )
    
    # Print results
    result.print_summary()
    
    # Show some trades
    if result.trades:
        print("\nSample Trades (last 10):")
        print("-" * 80)
        for trade in result.trades[-10:]:
            status = "WIN" if trade.is_winner else "LOSS"
            print(f"  {trade.entry_time.strftime('%Y-%m-%d %H:%M')} | "
                  f"{trade.direction.upper():5} | "
                  f"Entry: ${trade.entry_price:,.2f} | "
                  f"Exit: ${trade.exit_price:,.2f} | "
                  f"P&L: ${trade.pnl:+,.2f} | "
                  f"{status}")

if __name__ == "__main__":
    main()

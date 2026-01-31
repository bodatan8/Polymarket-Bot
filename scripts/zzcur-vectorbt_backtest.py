"""Fast backtesting with vectorbt - using hourly data."""
import pandas as pd
import numpy as np
import vectorbt as vbt

print("\n" + "="*60)
print("VECTORBT BACKTEST: Supertrend on HOURLY BTC")
print("="*60)

# Load data
print("\nLoading data...")
df = pd.read_csv("data/binance/BTCUSDT_1m_full.csv", parse_dates=['timestamp'], index_col='timestamp')
print(f"Loaded {len(df):,} 1-minute candles")

# Resample to hourly (Supertrend works better on higher TF)
print("\nResampling to hourly...")
df_1h = df.resample('1h').agg({
    'open': 'first',
    'high': 'max', 
    'low': 'min',
    'close': 'last',
    'volume': 'sum'
}).dropna()
print(f"Created {len(df_1h):,} hourly candles")
print(f"Date range: {df_1h.index[0]} to {df_1h.index[-1]}")

# Use 2022-2024 data (volatile period with both bull and bear markets)
df_1h = df_1h['2022-01-01':'2024-12-31']
print(f"\nUsing 2022-2024: {len(df_1h):,} hourly candles")

# Calculate Supertrend (proper implementation)
print("\nCalculating Supertrend (period=10, multiplier=3)...")

def supertrend_indicator(high, low, close, period=10, multiplier=3.0):
    """Vectorized Supertrend calculation."""
    # True Range
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # ATR
    atr = tr.rolling(window=period).mean()
    
    # HL2
    hl2 = (high + low) / 2
    
    # Basic bands
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr
    
    # Initialize arrays
    n = len(close)
    supertrend = np.zeros(n)
    direction = np.zeros(n)  # 1 = bullish, -1 = bearish
    
    # First valid values
    first_valid = period
    supertrend[:first_valid] = np.nan
    direction[:first_valid] = 1
    
    upper = basic_upper.values.copy()
    lower = basic_lower.values.copy()
    close_arr = close.values
    
    for i in range(first_valid, n):
        # Calculate final upper band
        if basic_upper.iloc[i] < upper[i-1] or close_arr[i-1] > upper[i-1]:
            upper[i] = basic_upper.iloc[i]
        else:
            upper[i] = upper[i-1]
        
        # Calculate final lower band  
        if basic_lower.iloc[i] > lower[i-1] or close_arr[i-1] < lower[i-1]:
            lower[i] = basic_lower.iloc[i]
        else:
            lower[i] = lower[i-1]
        
        # Determine trend direction
        if direction[i-1] == 1:  # Was bullish
            if close_arr[i] < lower[i]:
                direction[i] = -1  # Flip to bearish
                supertrend[i] = upper[i]
            else:
                direction[i] = 1
                supertrend[i] = lower[i]
        else:  # Was bearish
            if close_arr[i] > upper[i]:
                direction[i] = 1  # Flip to bullish
                supertrend[i] = lower[i]
            else:
                direction[i] = -1
                supertrend[i] = upper[i]
    
    return pd.Series(direction, index=close.index), pd.Series(supertrend, index=close.index)

direction, st_line = supertrend_indicator(df_1h['high'], df_1h['low'], df_1h['close'])

# Stats
bullish = (direction == 1).sum()
bearish = (direction == -1).sum()
flips_bull = ((direction == 1) & (direction.shift(1) == -1)).sum()
flips_bear = ((direction == -1) & (direction.shift(1) == 1)).sum()

print(f"\nTrend Stats:")
print(f"  Bullish bars: {bullish:,}")
print(f"  Bearish bars: {bearish:,}")
print(f"  Flips to bullish: {flips_bull:,}")
print(f"  Flips to bearish: {flips_bear:,}")

# Generate signals
print("\nGenerating signals...")
entries = (direction == 1) & (direction.shift(1) == -1)  # Flip to bullish
exits = (direction == -1) & (direction.shift(1) == 1)    # Flip to bearish

print(f"Entry signals: {entries.sum()}")
print(f"Exit signals: {exits.sum()}")

# Run backtest
print("\nRunning vectorbt backtest...")

portfolio = vbt.Portfolio.from_signals(
    close=df_1h['close'],
    entries=entries,
    exits=exits,
    init_cash=10000,
    fees=0.001,
    slippage=0.0005,
    freq='1h'
)

# Print results
print("\n" + "="*60)
print("BACKTEST RESULTS (2022-2024)")
print("="*60)

stats = portfolio.stats()
print(f"""
Initial Capital:    $10,000.00
Final Value:        ${stats['End Value']:,.2f}
Total Return:       {stats['Total Return [%]']:.2f}%

Total Trades:       {stats['Total Trades']:.0f}
Win Rate:           {stats['Win Rate [%]']:.1f}%
Best Trade:         {stats['Best Trade [%]']:.2f}%
Worst Trade:        {stats['Worst Trade [%]']:.2f}%

Max Drawdown:       {stats['Max Drawdown [%]']:.2f}%
Sharpe Ratio:       {stats['Sharpe Ratio']:.2f}
Sortino Ratio:      {stats['Sortino Ratio']:.2f}
""")

# Show trades
print("="*60)
print("TRADES")
print("="*60)
trades = portfolio.trades.records_readable
if len(trades) > 0:
    print(f"Total trades: {len(trades)}")
    print("\nFirst 10 trades:")
    print(trades.head(10)[['Entry Timestamp', 'Avg Entry Price', 'Exit Timestamp', 'Avg Exit Price', 'PnL', 'Return', 'Direction']].to_string())
else:
    print("No trades executed")

print("\n" + "="*60)
print("DONE!")
print("="*60)

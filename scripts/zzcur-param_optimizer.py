"""
Parameter Optimizer for Supertrend Strategy
Tests multiple parameter combinations from research sources.
"""
import pandas as pd
import numpy as np
import vectorbt as vbt
from itertools import product
import warnings
warnings.filterwarnings('ignore')

print("\n" + "="*70)
print("SUPERTREND PARAMETER OPTIMIZER")
print("Testing values from research papers, forums, and Bayesian optimization")
print("="*70)

# Load data
print("\nLoading BTCUSDT hourly data...")
df = pd.read_csv("data/binance/BTCUSDT_1m_full.csv", parse_dates=['timestamp'], index_col='timestamp')
df_1h = df.resample('1h').agg({
    'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
}).dropna()

# Test on 2023-2024 (2 years)
df_1h = df_1h['2023-01-01':'2024-12-31']
print(f"Data: {len(df_1h):,} hourly candles")

def supertrend(high, low, close, period=10, multiplier=3.0):
    """Calculate Supertrend indicator."""
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    hl2 = (high + low) / 2
    
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr
    
    n = len(close)
    direction = np.zeros(n)
    upper = basic_upper.values.copy()
    lower = basic_lower.values.copy()
    close_arr = close.values
    
    for i in range(period, n):
        if basic_upper.iloc[i] < upper[i-1] or close_arr[i-1] > upper[i-1]:
            upper[i] = basic_upper.iloc[i]
        else:
            upper[i] = upper[i-1]
        
        if basic_lower.iloc[i] > lower[i-1] or close_arr[i-1] < lower[i-1]:
            lower[i] = basic_lower.iloc[i]
        else:
            lower[i] = lower[i-1]
        
        if direction[i-1] == 1:
            if close_arr[i] < lower[i]:
                direction[i] = -1
            else:
                direction[i] = 1
        else:
            if close_arr[i] > upper[i]:
                direction[i] = 1
            else:
                direction[i] = -1
    
    return pd.Series(direction, index=close.index)

def calc_ema(close, period):
    """Calculate EMA."""
    return close.ewm(span=period, adjust=False).mean()

def backtest_params(period, multiplier, ema_filter=None, use_volatility_filter=False):
    """Backtest a specific parameter combination."""
    direction = supertrend(df_1h['high'], df_1h['low'], df_1h['close'], period, multiplier)
    
    # Basic signals
    entries = (direction == 1) & (direction.shift(1) == -1)
    exits = (direction == -1) & (direction.shift(1) == 1)
    
    # Apply EMA filter if specified
    if ema_filter:
        ema = calc_ema(df_1h['close'], ema_filter)
        entries = entries & (df_1h['close'] > ema)
    
    # Apply volatility filter if specified
    if use_volatility_filter:
        tr = pd.concat([
            df_1h['high'] - df_1h['low'],
            abs(df_1h['high'] - df_1h['close'].shift(1)),
            abs(df_1h['low'] - df_1h['close'].shift(1))
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean()
        atr_avg = atr.rolling(window=50).mean()
        vol_filter = atr > atr_avg
        entries = entries & vol_filter
    
    if entries.sum() == 0:
        return None
    
    portfolio = vbt.Portfolio.from_signals(
        close=df_1h['close'],
        entries=entries,
        exits=exits,
        init_cash=10000,
        fees=0.001,
        slippage=0.0005,
        freq='1h'
    )
    
    stats = portfolio.stats()
    return {
        'period': period,
        'multiplier': multiplier,
        'ema_filter': ema_filter,
        'vol_filter': use_volatility_filter,
        'return_pct': stats['Total Return [%]'],
        'trades': stats['Total Trades'],
        'win_rate': stats['Win Rate [%]'],
        'sharpe': stats['Sharpe Ratio'],
        'sortino': stats['Sortino Ratio'],
        'max_dd': stats['Max Drawdown [%]'],
        'final_value': stats['End Value']
    }

# Parameter combinations to test from research
print("\n" + "-"*70)
print("PARAMETER COMBINATIONS TO TEST (from research sources)")
print("-"*70)

# From academic research & forums
params_to_test = [
    # Default
    (10, 3.0, None, False, "Default (10, 3)"),
    
    # From Bayesian Optimization paper
    (20, 4.0, None, False, "BO: Nifty50 optimal"),
    (14, 4.0, None, False, "BO: Nvidia optimal"),
    (19, 3.0, None, False, "BO: Microsoft optimal"),
    (5, 1.0, None, False, "BO: HUL optimal (aggressive)"),
    
    # From TradingView community
    (9, 2.2, None, False, "TV: Crypto 15m-6H"),
    (10, 2.5, None, False, "Quant: Balanced"),
    (7, 2.0, None, False, "TV: Scalping"),
    (14, 3.0, None, False, "TV: Swing trading"),
    
    # Triple Supertrend values
    (10, 1.0, None, False, "Triple ST: Fast"),
    (11, 2.0, None, False, "Triple ST: Medium"),
    (12, 3.0, None, False, "Triple ST: Slow"),
    
    # With EMA filters
    (10, 3.0, 50, False, "Default + EMA50 filter"),
    (10, 3.0, 200, False, "Default + EMA200 filter"),
    (9, 2.2, 50, False, "TV Crypto + EMA50"),
    
    # With volatility filter
    (10, 3.0, None, True, "Default + Volatility filter"),
    (9, 2.2, None, True, "TV Crypto + Volatility filter"),
    
    # Combined filters
    (10, 3.0, 50, True, "Default + EMA50 + Vol filter"),
]

results = []
print(f"\nTesting {len(params_to_test)} combinations...\n")

for period, mult, ema, vol, name in params_to_test:
    result = backtest_params(period, mult, ema, vol)
    if result:
        result['name'] = name
        results.append(result)
        print(f"  {name}: Return={result['return_pct']:.1f}%, Win={result['win_rate']:.1f}%, Sharpe={result['sharpe']:.2f}")

# Sort by return
results_df = pd.DataFrame(results)
results_df = results_df.sort_values('return_pct', ascending=False)

print("\n" + "="*70)
print("TOP 10 RESULTS (by Total Return)")
print("="*70)
print(results_df[['name', 'return_pct', 'trades', 'win_rate', 'sharpe', 'max_dd']].head(10).to_string(index=False))

print("\n" + "="*70)
print("TOP 5 BY SHARPE RATIO (Risk-Adjusted)")
print("="*70)
by_sharpe = results_df.sort_values('sharpe', ascending=False).head(5)
print(by_sharpe[['name', 'return_pct', 'trades', 'win_rate', 'sharpe', 'max_dd']].to_string(index=False))

print("\n" + "="*70)
print("TOP 5 BY WIN RATE")
print("="*70)
by_winrate = results_df.sort_values('win_rate', ascending=False).head(5)
print(by_winrate[['name', 'return_pct', 'trades', 'win_rate', 'sharpe', 'max_dd']].to_string(index=False))

# Save full results
results_df.to_csv('param_optimization_results.csv', index=False)
print(f"\nFull results saved to param_optimization_results.csv")

print("\n" + "="*70)
print("DONE!")
print("="*70)

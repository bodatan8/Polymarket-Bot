"""Demo: Full backtest with visualization."""
import pandas as pd
import numpy as np
import vectorbt as vbt
from src.backtesting.visualization import BacktestVisualizer

print("\n" + "="*60)
print("FULL BACKTEST DEMO WITH VISUALIZATION")
print("="*60)

# Load data
print("\nLoading BTCUSDT hourly data...")
df = pd.read_csv("data/binance/BTCUSDT_1m_full.csv", parse_dates=['timestamp'], index_col='timestamp')
df_1h = df.resample('1h').agg({
    'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
}).dropna()

# Use 2023-2024 for testing
df_1h = df_1h['2023-01-01':'2024-12-31']
print(f"Data: {len(df_1h):,} hourly candles (2023-2024)")

# Calculate Supertrend
print("\nCalculating Supertrend indicator...")

def supertrend(high, low, close, period=10, multiplier=3.0):
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
    st_line = np.zeros(n)
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
                st_line[i] = upper[i]
            else:
                direction[i] = 1
                st_line[i] = lower[i]
        else:
            if close_arr[i] > upper[i]:
                direction[i] = 1
                st_line[i] = lower[i]
            else:
                direction[i] = -1
                st_line[i] = upper[i]
    
    return pd.Series(direction, index=close.index), pd.Series(st_line, index=close.index)

direction, st_line = supertrend(df_1h['high'], df_1h['low'], df_1h['close'])

# Generate signals
entries = (direction == 1) & (direction.shift(1) == -1)
exits = (direction == -1) & (direction.shift(1) == 1)
print(f"Entry signals: {entries.sum()}, Exit signals: {exits.sum()}")

# Run backtest
print("\nRunning backtest...")
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
print(f"\nResults:")
print(f"  Final Value: ${stats['End Value']:,.2f}")
print(f"  Total Return: {stats['Total Return [%]']:.2f}%")
print(f"  Total Trades: {stats['Total Trades']:.0f}")
print(f"  Win Rate: {stats['Win Rate [%]']:.1f}%")
print(f"  Sharpe Ratio: {stats['Sharpe Ratio']:.2f}")

# Create visualizations
print("\nGenerating visualizations...")
viz = BacktestVisualizer(output_dir="backtest_charts")

# Get data for charts
equity = portfolio.value()
trades = portfolio.trades.records_readable

# Subsample price data for chart (every 4 hours to reduce size)
df_chart = df_1h.iloc[::4].copy()

# Create charts
equity_path = viz.plot_equity_curve(
    equity=equity,
    title="Supertrend Strategy - Equity Curve (2023-2024)"
)
print(f"  Equity chart: {equity_path}")

drawdown_path = viz.plot_drawdown(
    equity=equity,
    title="Supertrend Strategy - Drawdown"
)
print(f"  Drawdown chart: {drawdown_path}")

if len(trades) > 0:
    trade_analysis_path = viz.plot_trade_analysis(
        trades=trades,
        title="Trade Analysis"
    )
    print(f"  Trade analysis: {trade_analysis_path}")
    
    # Price chart with trades (use subset for performance)
    trades_chart_path = viz.plot_trades_on_price(
        df=df_chart,
        trades=trades,
        indicator_data={'Supertrend': st_line.iloc[::4]},
        title="BTCUSDT with Supertrend & Trades"
    )
    print(f"  Price chart: {trades_chart_path}")

# Generate full report
report_path = viz.generate_full_report(
    df=df_chart,
    trades=trades,
    equity=equity,
    stats=stats.to_dict(),
    indicator_data={'Supertrend': st_line.iloc[::4]},
    strategy_name="Supertrend Strategy (2023-2024)"
)
print(f"\n  FULL REPORT: {report_path}")

print("\n" + "="*60)
print("DONE! Open backtest_charts/backtest_report.html in browser")
print("="*60)

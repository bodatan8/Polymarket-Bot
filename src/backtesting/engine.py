"""
Backtesting Engine

Simulates strategy execution on historical data.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable

import pandas as pd
import numpy as np

from src.indicators import IndicatorManager, Indicator
from src.strategies import Strategy, StrategySignal, ActionType
from .result import BacktestResult, Trade

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """Configuration for backtesting."""
    initial_capital: float = 10000.0
    position_size_pct: float = 10.0  # % of capital per trade
    max_positions: int = 1  # Max concurrent positions
    commission_pct: float = 0.1  # Commission as % of trade value
    slippage_pct: float = 0.05  # Slippage as % of price
    
    # Stop loss / take profit (optional)
    stop_loss_pct: Optional[float] = None  # e.g., 2.0 for 2% stop
    take_profit_pct: Optional[float] = None  # e.g., 4.0 for 4% TP
    
    # Trailing stop (optional)
    trailing_stop_pct: Optional[float] = None


@dataclass
class Position:
    """An open position."""
    trade_id: int
    direction: str  # "long" or "short"
    entry_price: float
    entry_time: datetime
    size: float
    reason: str
    indicators: Dict[str, Any]
    highest_price: float = 0.0  # For trailing stop
    lowest_price: float = float('inf')


class BacktestEngine:
    """
    Backtesting engine for testing strategies on historical data.
    
    Usage:
        # Setup
        engine = BacktestEngine(config)
        
        # Add indicators
        engine.add_indicator(SupertrendIndicator(), "1m")
        engine.add_indicator(SupertrendIndicator(), "1h")
        
        # Set strategy
        engine.set_strategy(SupertrendCrossStrategy())
        
        # Run backtest
        result = engine.run(
            data={"1m": df_1m, "1h": df_1h},
            asset="BTCUSDT"
        )
        
        # Analyze
        result.print_summary()
    """
    
    def __init__(self, config: Optional[BacktestConfig] = None):
        """
        Initialize backtest engine.
        
        Args:
            config: Backtest configuration
        """
        self.config = config or BacktestConfig()
        self.indicator_manager = IndicatorManager()
        self._strategy: Optional[Strategy] = None
        self._indicators: List[tuple] = []  # (indicator, timeframe)
    
    def add_indicator(
        self,
        indicator: Indicator,
        timeframe: str,
        asset: str = "default"
    ) -> None:
        """Add an indicator to the backtest."""
        self._indicators.append((indicator, timeframe, asset))
        self.indicator_manager.add_indicator(indicator, timeframe, asset)
    
    def set_strategy(self, strategy: Strategy) -> None:
        """Set the strategy to backtest."""
        self._strategy = strategy
    
    def run(
        self,
        data: Dict[str, pd.DataFrame],  # timeframe -> OHLCV data
        asset: str = "default",
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> BacktestResult:
        """
        Run the backtest.
        
        Args:
            data: Dict of timeframe -> OHLCV DataFrame
            asset: Asset symbol
            progress_callback: Optional callback(current, total) for progress
        
        Returns:
            BacktestResult with all metrics
        """
        if not self._strategy:
            raise ValueError("No strategy set. Call set_strategy() first.")
        
        # Validate data
        if not data:
            raise ValueError("No data provided")
        
        # Get the primary (fastest) timeframe for iteration
        primary_tf = self._get_primary_timeframe(data)
        primary_data = data[primary_tf]
        
        logger.info(f"Running backtest on {len(primary_data)} bars ({primary_tf})")
        
        # Initialize state
        capital = self.config.initial_capital
        positions: List[Position] = []
        trades: List[Trade] = []
        equity_curve = [capital]
        timestamps = [primary_data.index[0]]
        trade_counter = 0
        
        # Pre-calculate indicators for all data
        calculated_data: Dict[str, pd.DataFrame] = {}
        for tf, df in data.items():
            # Calculate all indicators for this timeframe
            calc_df = df.copy()
            for indicator, ind_tf, ind_asset in self._indicators:
                if ind_tf == tf and (ind_asset == asset or ind_asset == "default"):
                    calc_df = indicator.calculate(calc_df)
            calculated_data[tf] = calc_df
        
        # Minimum lookback for indicators
        min_lookback = 50
        
        # Iterate through primary data
        total_bars = len(primary_data)
        
        for i in range(min_lookback, total_bars):
            current_time = primary_data.index[i]
            current_price = float(primary_data['close'].iloc[i])
            
            # Progress callback
            if progress_callback and i % 100 == 0:
                progress_callback(i, total_bars)
            
            # Update indicator manager with data up to current bar
            for tf, calc_df in calculated_data.items():
                # Get data up to current time
                tf_data = calc_df[calc_df.index <= current_time]
                if len(tf_data) > 0:
                    self.indicator_manager.update(asset, tf, tf_data)
            
            # Check for exit conditions on open positions
            for pos in positions[:]:  # Copy list for safe removal
                exit_reason = self._check_exit(pos, current_price, current_time)
                
                if exit_reason:
                    # Close position
                    trade = self._close_position(
                        pos, current_price, current_time, exit_reason
                    )
                    trades.append(trade)
                    capital += trade.pnl
                    positions.remove(pos)
                else:
                    # Update trailing stop tracking
                    pos.highest_price = max(pos.highest_price, current_price)
                    pos.lowest_price = min(pos.lowest_price, current_price)
            
            # Evaluate strategy for new entry
            if len(positions) < self.config.max_positions:
                signal = self._strategy.evaluate(self.indicator_manager, asset)
                
                if signal.is_trade and signal.strength > 0.5:
                    # Calculate position size
                    position_value = capital * (self.config.position_size_pct / 100)
                    size = position_value / current_price
                    
                    # Apply slippage
                    entry_price = current_price * (
                        1 + self.config.slippage_pct / 100
                        if signal.action == ActionType.BUY
                        else 1 - self.config.slippage_pct / 100
                    )
                    
                    # Apply commission
                    commission = position_value * (self.config.commission_pct / 100)
                    capital -= commission
                    
                    # Create position
                    trade_counter += 1
                    pos = Position(
                        trade_id=trade_counter,
                        direction="long" if signal.action == ActionType.BUY else "short",
                        entry_price=entry_price,
                        entry_time=current_time,
                        size=size,
                        reason=signal.reason,
                        indicators=signal.indicators,
                        highest_price=entry_price,
                        lowest_price=entry_price
                    )
                    positions.append(pos)
                    
                    logger.debug(
                        f"Opened {pos.direction} at {entry_price:.2f} "
                        f"({current_time}) - {signal.reason}"
                    )
            
            # Update equity curve
            unrealized_pnl = sum(
                self._calculate_unrealized_pnl(pos, current_price)
                for pos in positions
            )
            equity_curve.append(capital + unrealized_pnl)
            timestamps.append(current_time)
        
        # Close any remaining positions at the end
        final_price = float(primary_data['close'].iloc[-1])
        final_time = primary_data.index[-1]
        
        for pos in positions:
            trade = self._close_position(pos, final_price, final_time, "End of backtest")
            trades.append(trade)
            capital += trade.pnl
        
        # Create result
        result = BacktestResult(
            strategy_name=self._strategy.name,
            asset=asset,
            timeframe=primary_tf,
            start_date=primary_data.index[0].to_pydatetime(),
            end_date=final_time.to_pydatetime(),
            initial_capital=self.config.initial_capital,
            trades=trades,
            equity_curve=equity_curve,
            timestamps=[t.to_pydatetime() if hasattr(t, 'to_pydatetime') else t for t in timestamps],
        )
        
        result.calculate_metrics()
        
        logger.info(f"Backtest complete: {result.total_trades} trades, "
                   f"P&L: ${result.total_pnl:.2f} ({result.total_pnl_pct:.1f}%)")
        
        return result
    
    def _get_primary_timeframe(self, data: Dict[str, pd.DataFrame]) -> str:
        """Get the fastest timeframe for iteration."""
        # Order by expected frequency
        tf_order = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]
        
        for tf in tf_order:
            if tf in data:
                return tf
        
        # Return first available
        return list(data.keys())[0]
    
    def _check_exit(
        self,
        pos: Position,
        current_price: float,
        current_time: datetime
    ) -> Optional[str]:
        """Check if position should be exited."""
        pnl_pct = self._calculate_pnl_pct(pos, current_price)
        
        # Stop loss
        if self.config.stop_loss_pct:
            if pnl_pct <= -self.config.stop_loss_pct:
                return f"Stop loss hit ({pnl_pct:.2f}%)"
        
        # Take profit
        if self.config.take_profit_pct:
            if pnl_pct >= self.config.take_profit_pct:
                return f"Take profit hit ({pnl_pct:.2f}%)"
        
        # Trailing stop
        if self.config.trailing_stop_pct:
            if pos.direction == "long":
                trailing_stop_price = pos.highest_price * (1 - self.config.trailing_stop_pct / 100)
                if current_price <= trailing_stop_price:
                    return f"Trailing stop hit"
            else:
                trailing_stop_price = pos.lowest_price * (1 + self.config.trailing_stop_pct / 100)
                if current_price >= trailing_stop_price:
                    return f"Trailing stop hit"
        
        return None
    
    def _calculate_pnl_pct(self, pos: Position, current_price: float) -> float:
        """Calculate P&L percentage for a position."""
        if pos.direction == "long":
            return (current_price - pos.entry_price) / pos.entry_price * 100
        else:
            return (pos.entry_price - current_price) / pos.entry_price * 100
    
    def _calculate_unrealized_pnl(self, pos: Position, current_price: float) -> float:
        """Calculate unrealized P&L in currency."""
        if pos.direction == "long":
            return (current_price - pos.entry_price) * pos.size
        else:
            return (pos.entry_price - current_price) * pos.size
    
    def _close_position(
        self,
        pos: Position,
        exit_price: float,
        exit_time: datetime,
        reason: str
    ) -> Trade:
        """Close a position and return the trade."""
        # Apply slippage
        actual_exit_price = exit_price * (
            1 - self.config.slippage_pct / 100
            if pos.direction == "long"
            else 1 + self.config.slippage_pct / 100
        )
        
        # Calculate P&L
        if pos.direction == "long":
            pnl = (actual_exit_price - pos.entry_price) * pos.size
            pnl_pct = (actual_exit_price - pos.entry_price) / pos.entry_price * 100
        else:
            pnl = (pos.entry_price - actual_exit_price) * pos.size
            pnl_pct = (pos.entry_price - actual_exit_price) / pos.entry_price * 100
        
        # Apply commission
        commission = abs(pnl) * (self.config.commission_pct / 100)
        pnl -= commission
        
        trade = Trade(
            id=pos.trade_id,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=actual_exit_price,
            size=pos.size,
            pnl=pnl,
            pnl_pct=pnl_pct,
            reason_entry=pos.reason,
            reason_exit=reason,
            indicators_entry=pos.indicators,
        )
        
        logger.debug(
            f"Closed {trade.direction} at {actual_exit_price:.2f} "
            f"P&L: ${pnl:.2f} ({pnl_pct:.2f}%) - {reason}"
        )
        
        return trade


def run_backtest(
    strategy: Strategy,
    indicators: List[tuple],  # [(Indicator, timeframe), ...]
    data: Dict[str, pd.DataFrame],
    asset: str = "BTCUSDT",
    config: Optional[BacktestConfig] = None
) -> BacktestResult:
    """
    Convenience function to run a backtest.
    
    Args:
        strategy: Strategy to test
        indicators: List of (Indicator, timeframe) tuples
        data: Dict of timeframe -> OHLCV data
        asset: Asset symbol
        config: Backtest configuration
    
    Returns:
        BacktestResult
    """
    engine = BacktestEngine(config)
    
    for indicator, timeframe in indicators:
        engine.add_indicator(indicator, timeframe, asset)
    
    engine.set_strategy(strategy)
    
    return engine.run(data, asset)

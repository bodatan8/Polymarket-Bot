"""
Backtest Results

Data structures for storing and analyzing backtest results.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np


@dataclass
class Trade:
    """A single trade in the backtest."""
    id: int
    entry_time: datetime
    exit_time: Optional[datetime]
    direction: str  # "long" or "short"
    entry_price: float
    exit_price: Optional[float]
    size: float  # Position size in units
    pnl: float = 0.0
    pnl_pct: float = 0.0
    reason_entry: str = ""
    reason_exit: str = ""
    indicators_entry: Dict[str, Any] = field(default_factory=dict)
    indicators_exit: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_winner(self) -> bool:
        return self.pnl > 0
    
    @property
    def is_open(self) -> bool:
        return self.exit_time is None
    
    @property
    def duration(self) -> Optional[float]:
        """Trade duration in seconds."""
        if self.exit_time and self.entry_time:
            return (self.exit_time - self.entry_time).total_seconds()
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "size": self.size,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "reason_entry": self.reason_entry,
            "reason_exit": self.reason_exit,
            "is_winner": self.is_winner,
            "duration_seconds": self.duration,
        }


@dataclass
class BacktestResult:
    """
    Complete backtest results with performance metrics.
    """
    # Configuration
    strategy_name: str
    asset: str
    timeframe: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    
    # Results
    final_capital: float = 0.0
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    timestamps: List[datetime] = field(default_factory=list)
    
    # Calculated metrics (computed after backtest)
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    profit_factor: float = 0.0
    avg_trade_pnl: float = 0.0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
    largest_winner: float = 0.0
    largest_loser: float = 0.0
    avg_trade_duration: float = 0.0  # seconds
    
    def calculate_metrics(self) -> None:
        """Calculate all performance metrics from trades."""
        if not self.trades:
            return
        
        closed_trades = [t for t in self.trades if not t.is_open]
        
        self.total_trades = len(closed_trades)
        self.winning_trades = sum(1 for t in closed_trades if t.is_winner)
        self.losing_trades = self.total_trades - self.winning_trades
        
        if self.total_trades > 0:
            self.win_rate = self.winning_trades / self.total_trades
            
            # PnL metrics
            pnls = [t.pnl for t in closed_trades]
            self.total_pnl = sum(pnls)
            self.total_pnl_pct = self.total_pnl / self.initial_capital * 100
            self.avg_trade_pnl = np.mean(pnls)
            
            # Winners and losers
            winners = [t.pnl for t in closed_trades if t.is_winner]
            losers = [t.pnl for t in closed_trades if not t.is_winner]
            
            self.avg_winner = np.mean(winners) if winners else 0
            self.avg_loser = np.mean(losers) if losers else 0
            self.largest_winner = max(winners) if winners else 0
            self.largest_loser = min(losers) if losers else 0
            
            # Profit factor
            gross_profit = sum(winners) if winners else 0
            gross_loss = abs(sum(losers)) if losers else 0
            self.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
            
            # Duration
            durations = [t.duration for t in closed_trades if t.duration]
            self.avg_trade_duration = np.mean(durations) if durations else 0
        
        # Drawdown
        if self.equity_curve:
            self._calculate_drawdown()
        
        # Risk-adjusted returns
        if len(self.equity_curve) > 1:
            self._calculate_risk_metrics()
        
        self.final_capital = self.initial_capital + self.total_pnl
    
    def _calculate_drawdown(self) -> None:
        """Calculate maximum drawdown."""
        equity = np.array(self.equity_curve)
        peak = np.maximum.accumulate(equity)
        drawdown = peak - equity
        
        self.max_drawdown = np.max(drawdown)
        self.max_drawdown_pct = self.max_drawdown / np.max(peak) * 100 if np.max(peak) > 0 else 0
    
    def _calculate_risk_metrics(self) -> None:
        """Calculate Sharpe and Sortino ratios."""
        equity = np.array(self.equity_curve)
        returns = np.diff(equity) / equity[:-1]
        
        if len(returns) < 2:
            return
        
        # Annualize based on timeframe
        periods_per_year = self._get_periods_per_year()
        
        mean_return = np.mean(returns)
        std_return = np.std(returns)
        
        # Sharpe ratio (assuming 0 risk-free rate)
        if std_return > 0:
            self.sharpe_ratio = mean_return / std_return * np.sqrt(periods_per_year)
        
        # Sortino ratio (only downside deviation)
        negative_returns = returns[returns < 0]
        if len(negative_returns) > 0:
            downside_std = np.std(negative_returns)
            if downside_std > 0:
                self.sortino_ratio = mean_return / downside_std * np.sqrt(periods_per_year)
    
    def _get_periods_per_year(self) -> int:
        """Estimate number of periods per year based on timeframe."""
        tf_mapping = {
            "1m": 525600,   # 365 * 24 * 60
            "5m": 105120,
            "15m": 35040,
            "1h": 8760,     # 365 * 24
            "4h": 2190,
            "1d": 365,
            "1w": 52,
        }
        return tf_mapping.get(self.timeframe, 8760)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "strategy_name": self.strategy_name,
            "asset": self.asset,
            "timeframe": self.timeframe,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_capital": self.initial_capital,
            "final_capital": self.final_capital,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate * 100, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_pnl_pct": round(self.total_pnl_pct, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "sortino_ratio": round(self.sortino_ratio, 2),
            "profit_factor": round(self.profit_factor, 2),
            "avg_trade_pnl": round(self.avg_trade_pnl, 2),
            "avg_winner": round(self.avg_winner, 2),
            "avg_loser": round(self.avg_loser, 2),
            "largest_winner": round(self.largest_winner, 2),
            "largest_loser": round(self.largest_loser, 2),
            "avg_trade_duration_min": round(self.avg_trade_duration / 60, 2),
        }
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert trades to DataFrame."""
        return pd.DataFrame([t.to_dict() for t in self.trades])
    
    def print_summary(self) -> None:
        """Print a summary of the backtest results."""
        print("\n" + "=" * 60)
        print(f"BACKTEST RESULTS: {self.strategy_name}")
        print("=" * 60)
        print(f"Asset: {self.asset} | Timeframe: {self.timeframe}")
        print(f"Period: {self.start_date.date()} to {self.end_date.date()}")
        print("-" * 60)
        print(f"Initial Capital: ${self.initial_capital:,.2f}")
        print(f"Final Capital:   ${self.final_capital:,.2f}")
        print(f"Total P&L:       ${self.total_pnl:,.2f} ({self.total_pnl_pct:+.2f}%)")
        print("-" * 60)
        print(f"Total Trades:    {self.total_trades}")
        print(f"Win Rate:        {self.win_rate * 100:.1f}%")
        print(f"Profit Factor:   {self.profit_factor:.2f}")
        print("-" * 60)
        print(f"Avg Trade P&L:   ${self.avg_trade_pnl:,.2f}")
        print(f"Avg Winner:      ${self.avg_winner:,.2f}")
        print(f"Avg Loser:       ${self.avg_loser:,.2f}")
        print(f"Largest Winner:  ${self.largest_winner:,.2f}")
        print(f"Largest Loser:   ${self.largest_loser:,.2f}")
        print("-" * 60)
        print(f"Max Drawdown:    ${self.max_drawdown:,.2f} ({self.max_drawdown_pct:.2f}%)")
        print(f"Sharpe Ratio:    {self.sharpe_ratio:.2f}")
        print(f"Sortino Ratio:   {self.sortino_ratio:.2f}")
        print("=" * 60 + "\n")

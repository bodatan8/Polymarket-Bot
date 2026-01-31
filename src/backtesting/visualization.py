"""Visualization module for backtesting results."""
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False


class BacktestVisualizer:
    """Creates visualizations for backtest results."""
    
    def __init__(self, output_dir: str = "backtest_charts"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        if not PLOTLY_AVAILABLE:
            raise ImportError("plotly is required for visualization. Install with: pip install plotly")
    
    def plot_equity_curve(
        self,
        equity: pd.Series,
        benchmark: Optional[pd.Series] = None,
        title: str = "Equity Curve",
        filename: str = "equity_curve.html"
    ) -> str:
        """Plot equity curve with optional benchmark comparison."""
        fig = go.Figure()
        
        # Equity curve
        fig.add_trace(go.Scatter(
            x=equity.index,
            y=equity.values,
            mode='lines',
            name='Strategy',
            line=dict(color='#2196F3', width=2)
        ))
        
        # Benchmark
        if benchmark is not None:
            fig.add_trace(go.Scatter(
                x=benchmark.index,
                y=benchmark.values,
                mode='lines',
                name='Buy & Hold',
                line=dict(color='#9E9E9E', width=1, dash='dash')
            ))
        
        fig.update_layout(
            title=title,
            xaxis_title="Date",
            yaxis_title="Portfolio Value ($)",
            template="plotly_dark",
            hovermode='x unified'
        )
        
        output_path = self.output_dir / filename
        fig.write_html(str(output_path))
        return str(output_path)
    
    def plot_trades_on_price(
        self,
        df: pd.DataFrame,
        trades: pd.DataFrame,
        indicator_data: Optional[Dict[str, pd.Series]] = None,
        title: str = "Price Chart with Trades",
        filename: str = "trades_chart.html"
    ) -> str:
        """Plot candlestick chart with trade markers and indicators."""
        # Create subplots
        rows = 2 if 'volume' in df.columns else 1
        row_heights = [0.7, 0.3] if rows == 2 else [1]
        
        fig = make_subplots(
            rows=rows, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=row_heights
        )
        
        # Candlestick chart
        fig.add_trace(
            go.Candlestick(
                x=df.index,
                open=df['open'],
                high=df['high'],
                low=df['low'],
                close=df['close'],
                name='Price',
                increasing_line_color='#26A69A',
                decreasing_line_color='#EF5350'
            ),
            row=1, col=1
        )
        
        # Add indicators
        if indicator_data:
            colors = ['#FF9800', '#9C27B0', '#00BCD4', '#FFEB3B']
            for i, (name, data) in enumerate(indicator_data.items()):
                fig.add_trace(
                    go.Scatter(
                        x=data.index,
                        y=data.values,
                        mode='lines',
                        name=name,
                        line=dict(color=colors[i % len(colors)], width=1)
                    ),
                    row=1, col=1
                )
        
        # Trade markers
        if len(trades) > 0:
            # Buy markers
            buys = trades[trades['Direction'] == 'Long']
            if len(buys) > 0:
                fig.add_trace(
                    go.Scatter(
                        x=buys['Entry Timestamp'],
                        y=buys['Avg Entry Price'],
                        mode='markers',
                        name='Buy',
                        marker=dict(
                            symbol='triangle-up',
                            size=12,
                            color='#4CAF50',
                            line=dict(width=1, color='white')
                        )
                    ),
                    row=1, col=1
                )
            
            # Sell markers (exits)
            exits = trades[trades['Status'] == 'Closed']
            if len(exits) > 0:
                fig.add_trace(
                    go.Scatter(
                        x=exits['Exit Timestamp'],
                        y=exits['Avg Exit Price'],
                        mode='markers',
                        name='Sell',
                        marker=dict(
                            symbol='triangle-down',
                            size=12,
                            color='#F44336',
                            line=dict(width=1, color='white')
                        )
                    ),
                    row=1, col=1
                )
        
        # Volume
        if rows == 2 and 'volume' in df.columns:
            colors = ['#26A69A' if c >= o else '#EF5350' 
                     for c, o in zip(df['close'], df['open'])]
            fig.add_trace(
                go.Bar(
                    x=df.index,
                    y=df['volume'],
                    name='Volume',
                    marker_color=colors,
                    opacity=0.5
                ),
                row=2, col=1
            )
        
        fig.update_layout(
            title=title,
            template="plotly_dark",
            xaxis_rangeslider_visible=False,
            hovermode='x unified',
            height=800
        )
        
        output_path = self.output_dir / filename
        fig.write_html(str(output_path))
        return str(output_path)
    
    def plot_drawdown(
        self,
        equity: pd.Series,
        title: str = "Drawdown",
        filename: str = "drawdown.html"
    ) -> str:
        """Plot drawdown chart."""
        # Calculate drawdown
        rolling_max = equity.expanding().max()
        drawdown = (equity - rolling_max) / rolling_max * 100
        
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=drawdown.index,
            y=drawdown.values,
            fill='tozeroy',
            mode='lines',
            name='Drawdown',
            line=dict(color='#F44336', width=1),
            fillcolor='rgba(244, 67, 54, 0.3)'
        ))
        
        fig.update_layout(
            title=title,
            xaxis_title="Date",
            yaxis_title="Drawdown (%)",
            template="plotly_dark",
            hovermode='x unified'
        )
        
        output_path = self.output_dir / filename
        fig.write_html(str(output_path))
        return str(output_path)
    
    def plot_monthly_returns(
        self,
        equity: pd.Series,
        title: str = "Monthly Returns Heatmap",
        filename: str = "monthly_returns.html"
    ) -> str:
        """Plot monthly returns heatmap."""
        # Calculate monthly returns
        monthly = equity.resample('M').last().pct_change() * 100
        
        # Create matrix (years x months)
        monthly_df = pd.DataFrame({
            'year': monthly.index.year,
            'month': monthly.index.month,
            'return': monthly.values
        }).dropna()
        
        pivot = monthly_df.pivot(index='year', columns='month', values='return')
        
        # Month names
        month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        
        fig = go.Figure(data=go.Heatmap(
            z=pivot.values,
            x=month_names[:pivot.shape[1]],
            y=pivot.index.astype(str),
            colorscale='RdYlGn',
            zmid=0,
            text=np.round(pivot.values, 1),
            texttemplate="%{text}%",
            textfont={"size": 10},
            hovertemplate='%{y} %{x}: %{z:.2f}%<extra></extra>'
        ))
        
        fig.update_layout(
            title=title,
            template="plotly_dark",
            height=400
        )
        
        output_path = self.output_dir / filename
        fig.write_html(str(output_path))
        return str(output_path)
    
    def plot_trade_analysis(
        self,
        trades: pd.DataFrame,
        title: str = "Trade Analysis",
        filename: str = "trade_analysis.html"
    ) -> str:
        """Plot trade PnL distribution and analysis."""
        if len(trades) == 0:
            return ""
        
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                'PnL Distribution',
                'Trade Returns (%)',
                'Cumulative PnL',
                'Trade Duration'
            )
        )
        
        # PnL Distribution
        pnl = trades['PnL'].dropna()
        fig.add_trace(
            go.Histogram(
                x=pnl,
                name='PnL',
                marker_color='#2196F3',
                nbinsx=30
            ),
            row=1, col=1
        )
        
        # Trade Returns
        returns = trades['Return'].dropna() * 100
        colors = ['#4CAF50' if r > 0 else '#F44336' for r in returns]
        fig.add_trace(
            go.Bar(
                x=list(range(len(returns))),
                y=returns,
                name='Return %',
                marker_color=colors
            ),
            row=1, col=2
        )
        
        # Cumulative PnL
        cum_pnl = pnl.cumsum()
        fig.add_trace(
            go.Scatter(
                x=list(range(len(cum_pnl))),
                y=cum_pnl,
                mode='lines',
                name='Cumulative PnL',
                line=dict(color='#FF9800', width=2)
            ),
            row=2, col=1
        )
        
        # Trade Duration
        if 'Entry Timestamp' in trades.columns and 'Exit Timestamp' in trades.columns:
            closed = trades[trades['Status'] == 'Closed'].copy()
            if len(closed) > 0:
                closed['duration'] = (
                    pd.to_datetime(closed['Exit Timestamp']) - 
                    pd.to_datetime(closed['Entry Timestamp'])
                ).dt.total_seconds() / 3600  # Hours
                
                fig.add_trace(
                    go.Histogram(
                        x=closed['duration'],
                        name='Duration (hours)',
                        marker_color='#9C27B0',
                        nbinsx=20
                    ),
                    row=2, col=2
                )
        
        fig.update_layout(
            title=title,
            template="plotly_dark",
            showlegend=False,
            height=700
        )
        
        output_path = self.output_dir / filename
        fig.write_html(str(output_path))
        return str(output_path)
    
    def generate_full_report(
        self,
        df: pd.DataFrame,
        trades: pd.DataFrame,
        equity: pd.Series,
        stats: Dict[str, Any],
        indicator_data: Optional[Dict[str, pd.Series]] = None,
        strategy_name: str = "Strategy",
        filename: str = "backtest_report.html"
    ) -> str:
        """Generate a comprehensive backtest report."""
        # Generate individual charts
        equity_chart = self.plot_equity_curve(equity, filename="temp_equity.html")
        trades_chart = self.plot_trades_on_price(df, trades, indicator_data, filename="temp_trades.html")
        drawdown_chart = self.plot_drawdown(equity, filename="temp_drawdown.html")
        
        # Build HTML report
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Backtest Report: {strategy_name}</title>
    <style>
        body {{ font-family: Arial, sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ color: #4CAF50; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin: 20px 0; }}
        .stat-card {{ background: #16213e; padding: 20px; border-radius: 8px; text-align: center; }}
        .stat-value {{ font-size: 24px; font-weight: bold; color: #4CAF50; }}
        .stat-label {{ color: #888; margin-top: 5px; }}
        .negative {{ color: #F44336; }}
        .chart-container {{ margin: 30px 0; }}
        iframe {{ width: 100%; height: 500px; border: none; border-radius: 8px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Backtest Report: {strategy_name}</h1>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">${stats.get('End Value', 0):,.2f}</div>
                <div class="stat-label">Final Value</div>
            </div>
            <div class="stat-card">
                <div class="stat-value {'negative' if stats.get('Total Return [%]', 0) < 0 else ''}">{stats.get('Total Return [%]', 0):.2f}%</div>
                <div class="stat-label">Total Return</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('Total Trades', 0):.0f}</div>
                <div class="stat-label">Total Trades</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('Win Rate [%]', 0):.1f}%</div>
                <div class="stat-label">Win Rate</div>
            </div>
            <div class="stat-card">
                <div class="stat-value {'negative' if stats.get('Max Drawdown [%]', 0) < -20 else ''}">{stats.get('Max Drawdown [%]', 0):.2f}%</div>
                <div class="stat-label">Max Drawdown</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('Sharpe Ratio', 0):.2f}</div>
                <div class="stat-label">Sharpe Ratio</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('Sortino Ratio', 0):.2f}</div>
                <div class="stat-label">Sortino Ratio</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('Avg Winning Trade [%]', 0):.2f}%</div>
                <div class="stat-label">Avg Win</div>
            </div>
        </div>
        
        <div class="chart-container">
            <h2>Equity Curve</h2>
            <iframe src="temp_equity.html"></iframe>
        </div>
        
        <div class="chart-container">
            <h2>Price & Trades</h2>
            <iframe src="temp_trades.html" style="height: 700px;"></iframe>
        </div>
        
        <div class="chart-container">
            <h2>Drawdown</h2>
            <iframe src="temp_drawdown.html"></iframe>
        </div>
    </div>
</body>
</html>
        """
        
        output_path = self.output_dir / filename
        output_path.write_text(html)
        return str(output_path)

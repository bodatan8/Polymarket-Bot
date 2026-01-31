"""
Polymarket Trading Bot

This package contains two distinct trading systems:

1. ARBITRAGE BOT (src.main)
   - Entry point: python -m src.main
   - Detects YES+NO arbitrage opportunities on Polymarket
   - Uses WebSocket for real-time order book data
   - Executes trades and merges tokens on-chain

2. SIGNAL TRADING SYSTEM (src.api.server)
   - Entry point: python -m src.api.server
   - Paper trading dashboard with live crypto signals
   - Mean-reversion strategy for 7-minute predictions
   - React dashboard at dashboard/
   
Key Modules:
- src.arbitrage: Arbitrage detection logic
- src.execution: Order execution and token merging
- src.signals: Signal generation (momentum, volume, live predictor)
- src.api: FastAPI server for dashboard
- src.market_maker: Market maker logic
- src.risk: Risk management
"""

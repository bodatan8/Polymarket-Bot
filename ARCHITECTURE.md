# Architecture Documentation

This document provides detailed technical architecture for both trading systems.

## Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         POLYMARKET TRADING BOT                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────┐         ┌─────────────────────────┐           │
│  │    ARBITRAGE BOT        │         │   SIGNAL TRADING        │           │
│  │    (src/main.py)        │         │   (src/api/server.py)   │           │
│  │                         │         │                         │           │
│  │  - WebSocket listener   │         │  - Live signals         │           │
│  │  - Arbitrage detection  │         │  - Paper trading        │           │
│  │  - Order execution      │         │  - Risk management      │           │
│  │  - Token merging        │         │  - React dashboard      │           │
│  └─────────────────────────┘         └─────────────────────────┘           │
│              │                                   │                          │
│              ▼                                   ▼                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      SHARED INFRASTRUCTURE                          │   │
│  │  - src/clients/ (API clients)                                       │   │
│  │  - src/risk/ (Risk management)                                      │   │
│  │  - src/utils/ (Logging, cost calculation)                           │   │
│  │  - src/database.py (SQLite storage)                                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## System 1: Arbitrage Bot

### Purpose

Detect and execute arbitrage opportunities when:
- Binary markets: YES + NO < $1.00
- Categorical markets: Sum of all outcomes < $1.00

### Data Flow

```
1. DISCOVERY
   ┌─────────────────┐
   │   Gamma API     │──── Fetch active markets ────▶ Market cache
   └─────────────────┘

2. REAL-TIME MONITORING
   ┌─────────────────┐
   │   WebSocket     │──── Order book updates ────▶ Detector
   └─────────────────┘

3. DETECTION
   ┌─────────────────┐
   │   Detector      │──── Check YES+NO < $1 ────▶ Opportunity
   └─────────────────┘

4. EXECUTION
   ┌─────────────────┐     ┌─────────────────┐
   │   Executor      │────▶│   CLOB API      │ Place orders
   └─────────────────┘     └─────────────────┘

5. SETTLEMENT
   ┌─────────────────┐     ┌─────────────────┐
   │   Merger        │────▶│   Polygon RPC   │ Merge tokens
   └─────────────────┘     └─────────────────┘
```

### Key Components

| Component | File | Responsibility |
|-----------|------|----------------|
| Entry Point | `src/main.py` | Orchestrate all components |
| WebSocket | `src/clients/websocket_client.py` | Real-time order book data |
| Detector | `src/arbitrage/detector.py` | Find opportunities |
| Binary Arb | `src/arbitrage/binary_arb.py` | YES/NO arbitrage logic |
| Categorical | `src/arbitrage/categorical_arb.py` | Multi-outcome arbitrage |
| Executor | `src/execution/executor.py` | Place and monitor orders |
| Merger | `src/execution/merger.py` | On-chain token merging |
| CLOB Client | `src/clients/clob_client.py` | Order placement API |
| Gamma Client | `src/clients/gamma_client.py` | Market metadata |
| Polygon Client | `src/clients/polygon_client.py` | Blockchain operations |

### Configuration

```python
# src/config.py
MIN_EDGE_BPS = 50        # Minimum edge (0.5%)
MAX_POSITION_SIZE = 100  # Max USDC per trade
MAX_CONCURRENT = 5       # Max open orders
KILL_SWITCH = False      # Emergency stop
```

## System 2: Signal Trading

### Purpose

Generate crypto price prediction signals and simulate paper trading for:
- Polymarket binary markets (UP/DOWN predictions)
- Leveraged crypto trading (2x)

### Strategy: Mean Reversion

The `LivePredictor` uses mean-reversion signals based on:

```
SIGNAL CONDITIONS:
├── RSI < 35 (oversold) → Predict UP
├── RSI > 65 (overbought) → Predict DOWN
├── Price > 0.4% from EMA8 → Expect reversion
├── High volatility (TR/ATR > 1.3) → Stronger moves
└── Time-of-day filter → Best hours: 4, 9, 11, 20, 21 UTC

EXPECTED ACCURACY: ~62%
WINDOW: 7 minutes
```

### Data Flow

```
1. PRICE DATA
   ┌─────────────────┐
   │   Binance API   │──── OHLCV candles ────▶ Indicator calculation
   └─────────────────┘

2. SIGNAL GENERATION
   ┌─────────────────┐
   │  LivePredictor  │──── Calculate RSI, EMA, ATR ────▶ Signal
   └─────────────────┘
         │
         ▼
   ┌─────────────────┐
   │  Direction: UP/DOWN/NO_SIGNAL
   │  Confidence: 0.5-0.9
   │  Accuracy Est: 0.54-0.70
   │  Reasoning: "RSI oversold..."
   └─────────────────┘

3. RISK VALIDATION
   ┌─────────────────┐
   │  PaperTrader    │──── Check limits ────▶ Position sizing (Kelly)
   └─────────────────┘

4. POSITION TRACKING
   ┌─────────────────┐
   │  Paper Position │──── Wait for expiry ────▶ PnL calculation
   └─────────────────┘

5. API SERVING
   ┌─────────────────┐
   │  FastAPI Server │──── /api/paper/dashboard ────▶ React UI
   └─────────────────┘
```

### Key Components

| Component | File | Responsibility |
|-----------|------|----------------|
| API Server | `src/api/server.py` | REST endpoints |
| Live Predictor | `src/signals/live_predictor.py` | Signal generation |
| Paper Trader | `src/signals/paper_trader.py` | Simulation trading |
| Risk Manager | `src/risk/manager.py` | Position sizing, limits |
| Database | `src/database.py` | SQLite persistence |
| Dashboard | `dashboard/src/App.tsx` | React frontend |
| Paper Trading UI | `dashboard/src/PaperTrading.tsx` | Paper trading view |

### Position Sizing (Kelly Criterion)

```python
# Fractional Kelly (25%) for conservative sizing
kelly_size = bankroll * kelly_fraction * (win_prob - (1 - win_prob) / odds)

# Example:
# Bankroll: $1000
# Win probability: 62%
# Odds: 1:1 (Polymarket binary)
# Kelly fraction: 25%
# Position: $1000 * 0.25 * (0.62 - 0.38/1) = $60
```

### Risk Limits

```python
# src/signals/paper_trader.py - PaperRiskLimits
max_position_size_usd = 50.0     # Per trade
min_position_size_usd = 5.0      # Minimum bet
max_daily_loss_usd = 100.0       # Stop trading
max_concurrent_positions = 6     # Open positions
max_positions_per_symbol = 2     # Per asset
min_confidence = 0.50            # Signal threshold
min_accuracy_estimate = 0.54     # Quality threshold
max_consecutive_losses = 5       # Cooldown trigger
cooldown_after_loss_seconds = 300 # 5 minute pause
```

## External APIs

### Polymarket APIs

| API | Endpoint | Purpose |
|-----|----------|---------|
| CLOB | `clob.polymarket.com` | Order placement |
| Gamma | `gamma-api.polymarket.com` | Market metadata |
| WebSocket | `ws.polymarket.com` | Real-time data |

### Crypto APIs

| API | Endpoint | Purpose |
|-----|----------|---------|
| Binance | `api.binance.com` | OHLCV candles |
| CoinGecko | `api.coingecko.com` | Current prices |

### Blockchain

| Network | RPC | Purpose |
|---------|-----|---------|
| Polygon | Custom RPC | Token operations |

## Database Schema

```sql
-- Positions table
CREATE TABLE positions (
    id INTEGER PRIMARY KEY,
    market_id TEXT,
    side TEXT,           -- 'Up' or 'Down'
    entry_price REAL,
    shares REAL,
    amount_usd REAL,
    asset TEXT,
    is_open INTEGER,
    entry_time TEXT,
    exit_time TEXT,
    exit_price REAL,
    pnl REAL,
    metadata TEXT        -- JSON
);

-- Signal predictions
CREATE TABLE signal_predictions (
    id INTEGER PRIMARY KEY,
    predicted_side TEXT,
    predicted_at TEXT,
    confidence REAL,
    resolved INTEGER,
    was_correct INTEGER
);
```

## Deployment Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AZURE                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────┐    ┌─────────────────────────────┐        │
│  │   Azure Container Apps      │    │   Azure Storage Account     │        │
│  │   (Arbitrage Bot)           │    │   (Static Website)          │        │
│  │                             │    │                             │        │
│  │   - Python 3.12             │    │   - Dashboard HTML/JS       │        │
│  │   - WebSocket connection    │    │   - signals.html            │        │
│  │   - 24/7 running            │    │   - index.html              │        │
│  └─────────────────────────────┘    └─────────────────────────────┘        │
│              │                                   │                          │
│              ▼                                   ▼                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                          EXTERNAL                                   │   │
│  │   - Polymarket APIs (CLOB, Gamma, WebSocket)                        │   │
│  │   - Polygon RPC                                                     │   │
│  │   - Supabase Edge Functions (signal backend)                        │   │
│  │   - Binance API                                                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Module Dependencies

```
src/main.py (Arbitrage)
├── src/config.py
├── src/clients/websocket_client.py
├── src/clients/clob_client.py
├── src/clients/gamma_client.py
├── src/clients/polygon_client.py
├── src/arbitrage/detector.py
│   ├── src/arbitrage/binary_arb.py
│   └── src/arbitrage/categorical_arb.py
└── src/execution/executor.py
    └── src/execution/merger.py

src/api/server.py (Signal Trading)
├── src/database.py
├── src/learning/timing_optimizer.py
├── src/risk/manager.py
├── src/signals/live_predictor.py
└── src/signals/paper_trader.py

dashboard/src/App.tsx
├── dashboard/src/PaperTrading.tsx
└── (API calls to src/api/server.py OR Supabase)
```

## Error Handling

All components follow these patterns:

1. **Logging**: Use Python `logging` module, not `print()`
2. **Exceptions**: Catch specific exceptions, log and re-raise or handle gracefully
3. **API Responses**: Return consistent JSON structure with error details
4. **Retries**: Use exponential backoff for external API calls

## Performance Considerations

- **Signal Generation**: ~200ms per symbol (3 concurrent = ~200ms total)
- **WebSocket Latency**: <100ms for order book updates
- **Database Queries**: SQLite is sufficient for single-instance use
- **Dashboard Polling**: 5-10 second intervals to balance freshness and load

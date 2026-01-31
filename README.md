# Polymarket Trading Bot

Crypto paper trading system with live signals, deployed on Supabase + Azure.

## Live Dashboard

**https://polymktdash097744.z13.web.core.windows.net/**

## Overview

This repository contains **two trading systems**:

| System | Backend | Purpose |
|--------|---------|---------|
| **Paper Trading** | Supabase Edge Functions | Live crypto signals with simulated trading |
| **Arbitrage Bot** | Python (Azure Container) | YES+NO arbitrage on Polymarket |

---

## System 1: Paper Trading (Active)

Fully cloud-based paper trading with two parallel strategies:

### Strategies

| Strategy | Window | Exit Logic | Leverage |
|----------|--------|------------|----------|
| **15-Min Binary** | Fixed 15 min | Binary win/lose at expiry | 1x |
| **2x Leverage** | Dynamic | Trailing stop, stop loss, signal reversal | 2x |

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    PAPER TRADING SYSTEM                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                 SUPABASE (Cloud)                          │  │
│  │                                                           │  │
│  │   ┌─────────────────┐    ┌─────────────────┐             │  │
│  │   │  Edge Function  │    │   Database      │             │  │
│  │   │  generate-live- │───▶│   paper_trades  │             │  │
│  │   │  signal         │    │                 │             │  │
│  │   └─────────────────┘    └─────────────────┘             │  │
│  │          ▲                                                │  │
│  │          │ Every minute (pg_cron)                         │  │
│  │                                                           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                         │                                       │
│                         ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                 AZURE STORAGE                             │  │
│  │                                                           │  │
│  │   ┌─────────────────────────────────────────────────┐    │  │
│  │   │  Static Website (index.html)                     │    │  │
│  │   │  - Real-time P&L display                         │    │  │
│  │   │  - Position tracking                             │    │  │
│  │   │  - Win/loss statistics                           │    │  │
│  │   └─────────────────────────────────────────────────┘    │  │
│  │                                                           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                         │                                       │
│                         ▼                                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                 BINANCE API                               │  │
│  │   BTC, ETH, SOL price data (1-min candles)               │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Signal Generation

Mean-reversion strategy based on:

| Indicator | Buy Signal | Sell Signal |
|-----------|------------|-------------|
| RSI (14) | < 35 (oversold) | > 65 (overbought) |
| EMA Distance | Below EMA8 | Above EMA8 |

### Position Sizing (Kelly-Inspired)

Position size scales with signal confidence:

| Confidence | Position Size | % of Bankroll |
|------------|---------------|---------------|
| 55% | $50 | 5% |
| 70% | $150 | 15% |
| 85% | $250 | 25% (max) |

### Exit Strategies

**15-Min Binary:**
- Closes exactly at 15 minutes
- Binary outcome: win or lose based on price direction

**2x Leverage:**
| Exit Type | Condition |
|-----------|-----------|
| Trailing Stop | 1.5% pullback from peak |
| Stop Loss | -3% (leveraged) |
| Signal Exit | RSI returns to neutral (45-55) |
| Max Hold | 2 hours |

### Performance Tracking

The dashboard shows:
- Real-time P&L for open positions
- Closed trade history with exit reasons
- Win rate and cumulative P&L
- Per-strategy breakdown

---

## System 2: Arbitrage Bot (Optional)

Automated arbitrage for Polymarket binary markets.

### Run Locally

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp env.example .env
# Edit .env with your Polymarket API keys

# Run
python -m src.main
```

---

## Project Structure

```
Polymarket Bot/
├── README.md                 # This file
├── ARCHITECTURE.md           # Technical details
├── requirements.txt          # Python dependencies
├── env.example               # Environment template
│
├── dashboard/
│   └── build/
│       └── index.html        # Live dashboard (deployed)
│
├── src/
│   ├── main.py               # Arbitrage entry point
│   ├── config.py             # Configuration
│   │
│   ├── arbitrage/            # Arbitrage detection
│   │   ├── detector.py
│   │   ├── binary_arb.py
│   │   └── categorical_arb.py
│   │
│   ├── clients/              # External APIs
│   │   ├── clob_client.py
│   │   ├── gamma_client.py
│   │   └── polygon_client.py
│   │
│   ├── execution/            # Order execution
│   │   ├── executor.py
│   │   └── merger.py
│   │
│   └── signals/              # Signal logic (reference)
│       ├── live_predictor.py
│       └── paper_trader.py
│
├── scripts/                  # Utility scripts
├── deploy/                   # Deployment configs
└── tests/                    # Test suite
```

---

## Supabase Edge Functions

| Function | Purpose | Schedule |
|----------|---------|----------|
| `generate-live-signal` | Generate signals, open/close trades | Every minute |
| `polymarket-scanner` | Scan real Polymarket markets | On demand |
| `polymarket-backfill` | Historical data analysis | On demand |

### Deploy Edge Function

```bash
supabase functions deploy generate-live-signal --project-ref oukirnoonygvvctrjmih
```

---

## Database Schema (Supabase)

```sql
CREATE TABLE paper_trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset TEXT NOT NULL,           -- BTCUSDT, ETHUSDT, SOLUSDT
    direction TEXT NOT NULL,       -- UP, DOWN
    entry_price NUMERIC NOT NULL,
    entry_time TIMESTAMPTZ NOT NULL,
    exit_price NUMERIC,
    exit_time TIMESTAMPTZ,
    pnl_percent NUMERIC,
    won BOOLEAN,
    confidence NUMERIC,
    trade_type TEXT DEFAULT 'polymarket',  -- polymarket, leverage
    exit_reason TEXT,              -- trailing_stop, stop_loss, signal_exit, 15min_expiry
    indicators JSONB,              -- rsi, ema_distance, max_pnl, position_size
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Configuration

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Public API key |
| `SUPABASE_SERVICE_ROLE_KEY` | Admin key (Edge Functions) |

### Trading Parameters (in Edge Function)

```typescript
const BANKROLL = 1000;
const BASE_SIZE_PCT = 0.05;    // 5% minimum
const MAX_SIZE_PCT = 0.25;     // 25% maximum
const LEVERAGE = 2;

const RSI_OVERSOLD = 35;
const RSI_OVERBOUGHT = 65;

const LV_STOP_LOSS = -3;       // -3% leveraged
const LV_TRAILING_STOP = 1.5;  // 1.5% pullback
const LV_MAX_HOLD_MINUTES = 120;
```

---

## Deployment

### Dashboard (Azure Static Website)

```bash
az storage blob upload \
  --account-name polymktdash097744 \
  --container-name '$web' \
  --name index.html \
  --file dashboard/build/index.html \
  --content-type "text/html" \
  --overwrite
```

### Edge Functions (Supabase)

Functions are deployed via Supabase CLI or MCP tools.

---

## Live URLs

| Resource | URL |
|----------|-----|
| Dashboard | https://polymktdash097744.z13.web.core.windows.net/ |
| Signal API | https://oukirnoonygvvctrjmih.supabase.co/functions/v1/generate-live-signal |

---

## License

MIT

## Disclaimer

This is for educational purposes only. Paper trading simulates trades without real money. Never invest more than you can afford to lose.

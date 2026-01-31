# Polymarket Trading Bot

Crypto paper trading system with live signals, deployed on Supabase + Azure.

## Live Dashboard

**https://polymktdash097744.z13.web.core.windows.net/**

## Quick Summary

| Component | Location | Status |
|-----------|----------|--------|
| **Dashboard** | Azure Static Website | Live, auto-refreshes every 5s |
| **Trading Logic** | Supabase Edge Function | Runs every minute via pg_cron |
| **Database** | Supabase PostgreSQL | Stores all trades |
| **Price Data** | Binance API | Real-time crypto prices |
| **Polymarket Odds** | Polymarket Gamma API | Real 15-min market odds |

---

## Trading Strategies

### 1. Polymarket Binary Bets (RSI Extreme 15m)

**Based on 90-day backtest: 54% win rate, +4% edge**

| Condition | Action |
|-----------|--------|
| RSI (15-min) < 25 | Bet UP |
| RSI (15-min) > 75 | Bet DOWN |
| RSI 25-75 | No bet |

- Uses **real Polymarket 15-minute odds** from Gamma API
- Holds exactly 15 minutes (binary outcome)
- **Fee**: 2% on winnings
- Payout: `(1/odds - 1) * 0.98` on win, -100% on loss

### 2. Spot Trading (RSI Mean Reversion 1m)

**Backtested with trailing stop for profit protection**

| Condition | Action |
|-----------|--------|
| RSI (1-min) < 35 | Buy (2x leverage) |
| RSI (1-min) > 65 | Sell (2x leverage) |

**Exit Strategies:**
| Exit Type | Condition |
|-----------|-----------|
| Trailing Stop | 1.5% pullback from peak |
| Stop Loss | -3% (leveraged) |
| Signal Exit | RSI returns to neutral (45-55) |
| Max Hold | 2 hours |

- **Fee**: 0.25% per trade (entry + exit + slippage)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    PAPER TRADING SYSTEM                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   SUPABASE CLOUD                                               │
│   ├── pg_cron (every minute)                                   │
│   │   └── Triggers Edge Function                               │
│   │                                                            │
│   ├── Edge Function: generate-live-signal                      │
│   │   ├── Fetch prices from Binance (BTC, ETH, SOL)           │
│   │   ├── Fetch real odds from Polymarket Gamma API            │
│   │   ├── Calculate RSI (15m for PM, 1m for Spot)              │
│   │   ├── Close expired positions                              │
│   │   ├── Open new positions if signal triggered               │
│   │   └── Return stats JSON                                    │
│   │                                                            │
│   └── PostgreSQL                                               │
│       └── paper_trades table                                   │
│                                                                 │
│   AZURE STORAGE                                                │
│   └── Static Website (index.html)                              │
│       ├── Polls Edge Function every 5 seconds                  │
│       └── Displays real-time P&L                               │
│                                                                 │
│   EXTERNAL APIs                                                │
│   ├── Binance: Price data (1m and 15m candles)                │
│   └── Polymarket Gamma: Real 15-min market odds                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Backtest Results (90 days)

Tested on ~26,000 candles per asset (BTC, ETH, SOL):

| Strategy | Win Rate | Trades | Edge |
|----------|----------|--------|------|
| **RSI Extreme (25/75)** | **54.0%** | 2,849 | **+4.0%** |
| RSI Extreme (20/80) | 53.3% | 1,423 | +3.3% |
| Simple Contrarian | 50.8% | 25,914 | +0.8% |
| Momentum | 49.2% | 25,914 | -0.8% |

**BTC specifically**: 56.4% win rate with RSI Extreme strategy.

---

## Position Sizing

Kelly-inspired scaling based on signal confidence:

| Confidence | Position Size | % of Bankroll |
|------------|---------------|---------------|
| 55% | $50 | 5% |
| 70% | $150 | 15% |
| 85%+ | $250 | 25% (max) |

---

## Fees Included

| Platform | Fee Type | Rate |
|----------|----------|------|
| Polymarket | Fee on winnings | 2% |
| Spot (Binance) | Trading fees | 0.1% × 2 |
| Spot (Binance) | Slippage estimate | 0.05% |
| **Spot Total** | Per round-trip | **0.25%** |

---

## Live URLs

| Resource | URL |
|----------|-----|
| Dashboard | https://polymktdash097744.z13.web.core.windows.net/ |
| Signal API | https://oukirnoonygvvctrjmih.supabase.co/functions/v1/generate-live-signal |
| GitHub | https://github.com/bodatan8/Polymarket-Bot |

---

## Database Schema

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
    trade_type TEXT,               -- 'polymarket' or 'spot'
    exit_reason TEXT,
    indicators JSONB,              -- strategy, rsi, bet_odds, position_size, etc.
    created_at TIMESTAMPTZ DEFAULT NOW()
);
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

### Edge Function (Supabase)

Deployed via Supabase MCP tools or CLI:

```bash
supabase functions deploy generate-live-signal --project-ref oukirnoonygvvctrjmih
```

---

## Configuration

### Key Parameters (in Edge Function)

```typescript
// Position sizing
const BANKROLL = 1000;
const BASE_SIZE_PCT = 0.05;    // 5% minimum
const MAX_SIZE_PCT = 0.25;     // 25% maximum

// Polymarket strategy (15-min RSI)
const PM_RSI_OVERSOLD = 25;
const PM_RSI_OVERBOUGHT = 75;
const PM_WIN_FEE = 0.02;       // 2% on winnings

// Spot strategy (1-min RSI)
const SPOT_RSI_OVERSOLD = 35;
const SPOT_RSI_OVERBOUGHT = 65;
const SPOT_STOP_LOSS = -3;
const SPOT_TRAILING_STOP = 1.5;
const LEVERAGE = 2;
```

---

## Project Structure

```
Polymarket Bot/
├── README.md                 # This file
├── ARCHITECTURE.md           # Technical deep-dive
├── env.example               # Environment template
│
├── dashboard/
│   └── build/
│       └── index.html        # Live dashboard (deployed to Azure)
│
├── src/                      # Python arbitrage bot (optional)
│   ├── main.py
│   ├── arbitrage/
│   ├── clients/
│   └── ...
│
├── scripts/                  # Utility/test scripts (zzcur-* prefix)
└── tests/
```

---

## Monitoring

The Edge Function runs automatically every minute. To check status:

```bash
# Test the function
curl https://oukirnoonygvvctrjmih.supabase.co/functions/v1/generate-live-signal | jq

# Check recent trades
# Via Supabase Dashboard > Table Editor > paper_trades
```

---

## License

MIT

## Disclaimer

Paper trading only - no real money involved. For educational purposes.

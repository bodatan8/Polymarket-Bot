# Architecture Documentation

Technical architecture for the crypto paper trading system.

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PAPER TRADING SYSTEM                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                      SUPABASE CLOUD                                 │   │
│   │                                                                     │   │
│   │   ┌───────────────────┐         ┌───────────────────┐              │   │
│   │   │   pg_cron         │         │   Edge Function   │              │   │
│   │   │   (every minute)  │────────▶│   generate-live-  │              │   │
│   │   │                   │         │   signal (v13)    │              │   │
│   │   └───────────────────┘         └───────────────────┘              │   │
│   │                                         │                           │   │
│   │                     ┌───────────────────┼───────────────────┐       │   │
│   │                     ▼                   ▼                   ▼       │   │
│   │              ┌───────────┐       ┌───────────┐       ┌───────────┐ │   │
│   │              │ Binance   │       │ Polymarket│       │ PostgreSQL│ │   │
│   │              │ API       │       │ Gamma API │       │           │ │   │
│   │              │           │       │           │       │ paper_    │ │   │
│   │              │ - 1m data │       │ - 15m odds│       │ trades    │ │   │
│   │              │ - 15m data│       │ - BTC/ETH/│       │           │ │   │
│   │              │ - prices  │       │   SOL     │       │           │ │   │
│   │              └───────────┘       └───────────┘       └───────────┘ │   │
│   │                                                                     │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                              │                                               │
│                              ▼                                               │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                 AZURE STATIC WEBSITE                                │   │
│   │                                                                     │   │
│   │   index.html (Dashboard)                                            │   │
│   │   ├── Polls Edge Function every 5 seconds                          │   │
│   │   ├── Fetches open/closed trades from Supabase REST API            │   │
│   │   └── Displays real-time P&L, win rates, positions                 │   │
│   │                                                                     │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Two Trading Strategies

### Strategy 1: Polymarket Binary (RSI Extreme 15m)

**Backtest**: 54.0% win rate over 90 days (2,849 trades, +4% edge)

```
Trigger Conditions:
├── RSI (15-min timeframe) < 25 → Bet UP
└── RSI (15-min timeframe) > 75 → Bet DOWN

Execution:
├── Fetch real odds from Polymarket Gamma API
├── Calculate bet odds based on direction (up/down price)
├── Open position with confidence-scaled size
└── Close exactly at 15 minutes

P&L Calculation:
├── WIN: pnl = (1/odds - 1) * 0.98 * stake  (2% fee deducted)
└── LOSE: pnl = -stake (100% loss)
```

### Strategy 2: Spot Trading (RSI Mean Reversion 1m)

**Uses 2x leverage with sophisticated exit management**

```
Trigger Conditions:
├── RSI (1-min timeframe) < 35 → BUY (long)
└── RSI (1-min timeframe) > 65 → SELL (short)

Exit Conditions (first triggered wins):
├── Stop Loss: leveraged P&L <= -3%
├── Trailing Stop: P&L drops 1.5% from peak (after reaching +1.5%)
├── Signal Exit: RSI returns to neutral zone (45-55)
└── Max Hold: 2 hours elapsed
```

---

## Edge Function: generate-live-signal

### Execution Flow (Every Minute)

```
1. FETCH DATA (parallel)
   ├── Binance 1-min candles (60 candles) → Spot RSI
   ├── Binance 15-min candles (20 candles) → PM RSI
   ├── Binance current prices
   └── Polymarket Gamma API → Real 15-min odds

2. GENERATE SIGNALS
   ├── Spot: RSI < 35 = UP, RSI > 65 = DOWN
   └── PM: RSI < 25 = UP, RSI > 75 = DOWN

3. CLOSE POSITIONS
   ├── Polymarket: Close if elapsed >= 15 minutes
   │   └── Calculate binary win/loss based on price direction
   └── Spot: Check all exit conditions
       ├── Stop loss?
       ├── Trailing stop?
       ├── Signal reversal?
       └── Max hold time?

4. OPEN NEW POSITIONS
   ├── Only if signal != NEUTRAL
   ├── Only if no existing position for asset+type
   └── Position size = Kelly-scaled by confidence

5. RETURN JSON
   └── Signals, stats, open/closed counts, P&L
```

### Polymarket Odds Fetching

```typescript
// Construct slug for 15-min window
const now = Math.floor(Date.now() / 1000);
const windowTs = Math.floor(now / 900) * 900;  // Round to 15-min
const slug = `btc-updown-15m-${windowTs}`;

// Fetch from Gamma API
const resp = await fetch(`https://gamma-api.polymarket.com/events?slug=${slug}`);
const data = await resp.json();

// Extract odds
const prices = JSON.parse(data[0].markets[0].outcomePrices);
const upOdds = parseFloat(prices[0]);   // e.g., 0.42
const downOdds = parseFloat(prices[1]); // e.g., 0.58
```

### Position Sizing

```typescript
function calcPositionSize(confidence: number): number {
  const BANKROLL = 1000;
  const BASE = 0.05;  // 5% at 50% confidence
  const MAX = 0.25;   // 25% at 85% confidence
  
  const confNorm = (confidence - 0.5) / 0.35;  // Normalize to 0-1
  const sizePct = BASE + (confNorm * (MAX - BASE));
  
  return Math.round(BANKROLL * sizePct);
}

// Examples:
// 55% → $50
// 70% → $150
// 85% → $250
```

### Trailing Stop Implementation

```typescript
// Track max P&L for each position
const maxPnl = Math.max(trade.indicators?.max_pnl || 0, currentPnl);

// Update in database
if (currentPnl > (trade.indicators?.max_pnl || 0)) {
  await supabase.from("paper_trades").update({
    indicators: { ...indicators, max_pnl: currentPnl }
  }).eq("id", trade.id);
}

// Trigger trailing stop
const TRAILING_STOP = 1.5;
if (maxPnl >= TRAILING_STOP && currentPnl <= maxPnl - TRAILING_STOP) {
  shouldClose = true;
  exitReason = 'trailing_stop';
}
```

---

## Database Schema

### paper_trades Table

```sql
CREATE TABLE paper_trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Position Info
    asset TEXT NOT NULL,           -- BTCUSDT, ETHUSDT, SOLUSDT
    direction TEXT NOT NULL,       -- UP, DOWN
    entry_price NUMERIC NOT NULL,
    entry_time TIMESTAMPTZ NOT NULL,
    
    -- Exit Info (NULL while open)
    exit_price NUMERIC,
    exit_time TIMESTAMPTZ,
    pnl_percent NUMERIC,
    won BOOLEAN,
    
    -- Trade Metadata
    confidence NUMERIC,
    trade_type TEXT,               -- 'polymarket' or 'spot'
    exit_reason TEXT,
    
    -- Indicators (JSONB)
    indicators JSONB,
    -- For polymarket: { strategy, rsi_15m, bet_odds, position_size, potential_payout }
    -- For spot: { strategy, rsi, ema_distance, max_pnl, position_size, leverage }
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_open ON paper_trades (exit_time) WHERE exit_time IS NULL;
CREATE INDEX idx_type ON paper_trades (trade_type);
```

### exit_reason Values

| Value | Meaning |
|-------|---------|
| `15min_expiry` | Polymarket bet closed at window end |
| `trailing_stop` | Price dropped from peak |
| `stop_loss` | Hit -3% stop |
| `signal_exit` | RSI returned to neutral |
| `max_hold_2h` | 2 hour limit reached |

---

## Fees Calculation

### Polymarket (Binary Bets)

```typescript
const PM_WIN_FEE = 0.02;  // 2% on winnings

function calcPolymarketPayout(odds: number): number {
  const grossPayout = (1 / odds) - 1;  // e.g., 1/0.4 - 1 = 1.5 = 150%
  return grossPayout * (1 - PM_WIN_FEE);  // 1.5 * 0.98 = 1.47 = 147%
}

// If you bet $100 at 40% odds:
// WIN: profit = $100 * 1.47 = $147
// LOSE: loss = $100 (100%)
```

### Spot Trading

```typescript
const SPOT_TRADING_FEE = 0.001;  // 0.1% per trade
const SPOT_SLIPPAGE = 0.0005;    // 0.05% estimate

const totalFees = (SPOT_TRADING_FEE * 2 + SPOT_SLIPPAGE) * 100;
// = (0.001 * 2 + 0.0005) * 100 = 0.25%

function calcSpotPnlAfterFees(grossPnl: number): number {
  return grossPnl - 0.25;  // Subtract 0.25% from gross P&L
}
```

---

## API Endpoints

### Edge Function

**URL**: `https://oukirnoonygvvctrjmih.supabase.co/functions/v1/generate-live-signal`

**Response**:
```json
{
  "message": "PM: +1/-0 | Spot: +0/-1",
  "signals": {
    "BTC": {
      "spot": { "direction": "NEUTRAL", "rsi": 52.3 },
      "polymarket": { "direction": "UP", "rsi15m": 23.1 },
      "currentPrice": 77500,
      "pmOdds": { "up": 0.42, "down": 0.58 }
    }
  },
  "polymarket": {
    "strategy": "RSI Extreme (15m)",
    "backtest": "54.0% over 90 days",
    "open": 2,
    "total": 15,
    "wins": 9,
    "pnl": 45.50,
    "win_rate": "60.0%"
  },
  "spot": {
    "strategy": "RSI Mean Reversion (1m)",
    "open": 1,
    "total": 20,
    "wins": 17,
    "pnl": 85.25,
    "win_rate": "85.0%"
  },
  "timestamp": "2026-01-31T21:45:00.000Z"
}
```

### Supabase REST API

**Base**: `https://oukirnoonygvvctrjmih.supabase.co/rest/v1`

| Endpoint | Purpose |
|----------|---------|
| `GET /paper_trades?exit_time=is.null` | Open positions |
| `GET /paper_trades?exit_time=not.is.null&order=exit_time.desc` | Closed trades |
| `GET /paper_trades?trade_type=eq.polymarket` | Polymarket only |

---

## Cron Job

```sql
-- Supabase pg_cron (runs every minute)
SELECT cron.schedule(
  'generate-signals',
  '* * * * *',
  $$
  SELECT net.http_post(
    url := 'https://oukirnoonygvvctrjmih.supabase.co/functions/v1/generate-live-signal',
    headers := '{"Content-Type": "application/json"}'::jsonb
  );
  $$
);
```

---

## Dashboard Data Flow

```
Every 5 seconds:

1. Fetch signals from Edge Function
   GET /functions/v1/generate-live-signal
   → Current prices, RSI, PM odds, signal directions

2. Fetch open trades from Supabase
   GET /rest/v1/paper_trades?exit_time=is.null
   → Calculate live P&L using current prices

3. Fetch closed trades
   GET /rest/v1/paper_trades?exit_time=not.is.null&limit=20
   → Display history with win/loss

4. Update UI
   → Prices, RSI badges, P&L, win rates
```

---

## Deployment

### Dashboard

```bash
az storage blob upload \
  --account-name polymktdash097744 \
  --container-name '$web' \
  --name index.html \
  --file dashboard/build/index.html \
  --content-type "text/html" \
  --overwrite
```

### Edge Function

Via Supabase CLI:
```bash
supabase functions deploy generate-live-signal --project-ref oukirnoonygvvctrjmih
```

Or via Supabase MCP tools (used in this project).

---

## Monitoring & Debugging

### Check Function Status

```bash
curl -s https://oukirnoonygvvctrjmih.supabase.co/functions/v1/generate-live-signal | jq '.message'
```

### Check Recent Trades

```bash
curl -s "https://oukirnoonygvvctrjmih.supabase.co/rest/v1/paper_trades?order=created_at.desc&limit=5" \
  -H "apikey: YOUR_ANON_KEY" | jq
```

### Supabase Dashboard

- **Table Editor**: View/edit paper_trades
- **Edge Functions**: View logs, deployments
- **SQL Editor**: Run queries

---

## Security

| Aspect | Implementation |
|--------|----------------|
| API Keys | Supabase anon key (safe for client) |
| Edge Function | verify_jwt=false (public) |
| RLS | Enabled, public read/write for paper_trades |
| No Real Money | Paper trading simulation only |

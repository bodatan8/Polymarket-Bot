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
│   │   │                   │         │   signal          │              │   │
│   │   └───────────────────┘         └───────────────────┘              │   │
│   │                                         │                           │   │
│   │                                         ▼                           │   │
│   │   ┌───────────────────────────────────────────────────────────┐    │   │
│   │   │                    PostgreSQL                              │    │   │
│   │   │                                                            │    │   │
│   │   │   paper_trades                                             │    │   │
│   │   │   ├── Open positions (exit_time IS NULL)                   │    │   │
│   │   │   └── Closed positions (with P&L, exit_reason)             │    │   │
│   │   │                                                            │    │   │
│   │   └───────────────────────────────────────────────────────────┘    │   │
│   │                                                                     │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                              │                                               │
│   ┌──────────────────────────┼──────────────────────────────────────────┐   │
│   │                          ▼                                          │   │
│   │   ┌───────────────────┐         ┌───────────────────┐              │   │
│   │   │   Azure Storage   │         │   Binance API     │              │   │
│   │   │   (Dashboard)     │◀────────│   (Price Data)    │              │   │
│   │   │                   │         │                   │              │   │
│   │   │   - Polls every   │         │   - 1-min candles │              │   │
│   │   │     5 seconds     │         │   - Current price │              │   │
│   │   │   - Real-time P&L │         │                   │              │   │
│   │   └───────────────────┘         └───────────────────┘              │   │
│   │                                                                     │   │
│   │                          EXTERNAL SERVICES                          │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Edge Function: generate-live-signal

The core trading logic runs as a Supabase Edge Function (Deno/TypeScript).

### Execution Flow

```
Every Minute (pg_cron trigger):
│
├── 1. FETCH PRICE DATA
│   └── Binance API → 60 x 1-min candles for BTC, ETH, SOL
│
├── 2. CALCULATE INDICATORS
│   ├── RSI (14-period)
│   └── EMA (8-period)
│
├── 3. GENERATE SIGNALS
│   ├── RSI < 35 → UP signal
│   ├── RSI > 65 → DOWN signal
│   └── Else → NEUTRAL (no action)
│
├── 4. CLOSE POSITIONS
│   │
│   ├── 15-Min Binary (trade_type = 'polymarket')
│   │   └── Close if elapsed >= 15 minutes
│   │
│   └── 2x Leverage (trade_type = 'leverage')
│       ├── Stop Loss: leveraged P&L <= -3%
│       ├── Trailing Stop: P&L drops 1.5% from peak
│       ├── Signal Exit: RSI returns to neutral (45-55)
│       └── Max Hold: elapsed >= 120 minutes
│
├── 5. OPEN NEW POSITIONS
│   ├── Only if signal != NEUTRAL
│   ├── Only if no existing position for asset+type
│   └── Position size = Kelly-scaled by confidence
│
└── 6. RETURN STATS
    └── JSON with signals, open/closed counts, P&L
```

### Position Sizing Algorithm

```typescript
// Kelly-inspired position sizing
function calcPositionSize(confidence: number): number {
  const MIN_CONFIDENCE = 0.55;
  const MAX_CONFIDENCE = 0.85;
  const BASE_SIZE_PCT = 0.05;  // 5% at minimum
  const MAX_SIZE_PCT = 0.25;   // 25% at maximum
  const BANKROLL = 1000;

  // Normalize confidence to 0-1 range
  const confNorm = Math.max(0, Math.min(1, 
    (confidence - MIN_CONFIDENCE) / (MAX_CONFIDENCE - MIN_CONFIDENCE)
  ));
  
  // Linear interpolation
  const sizePct = BASE_SIZE_PCT + (confNorm * (MAX_SIZE_PCT - BASE_SIZE_PCT));
  
  return Math.round(BANKROLL * sizePct);
}

// Examples:
// 55% confidence → $50 (5%)
// 70% confidence → $150 (15%)
// 85% confidence → $250 (25%)
```

### Exit Strategy: Trailing Stop

```typescript
// Track maximum P&L for each position
const indicators = trade.indicators || {};
const maxPnl = Math.max(indicators.max_pnl || 0, currentLeveragedPnl);

// Update max P&L in database
if (currentLeveragedPnl > (indicators.max_pnl || 0)) {
  await supabase.from("paper_trades").update({
    indicators: { ...indicators, max_pnl: currentLeveragedPnl }
  }).eq("id", trade.id);
}

// Trailing stop triggers when:
// 1. We've been profitable (maxPnl >= 1.5%)
// 2. Current P&L drops 1.5% from peak
if (maxPnl >= 1.5 && currentLeveragedPnl <= maxPnl - 1.5) {
  shouldClose = true;
  exitReason = `trailing_stop_peak${maxPnl.toFixed(1)}%`;
}
```

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
    confidence NUMERIC,            -- Signal confidence (0.5-0.85)
    trade_type TEXT DEFAULT 'polymarket',  -- polymarket, leverage
    exit_reason TEXT,              -- Why position closed
    
    -- Indicators (JSONB for flexibility)
    indicators JSONB,              -- { rsi, ema_distance, max_pnl, position_size }
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX idx_paper_trades_open ON paper_trades (exit_time) WHERE exit_time IS NULL;
CREATE INDEX idx_paper_trades_type ON paper_trades (trade_type);
```

### Exit Reason Values

| exit_reason | Meaning |
|-------------|---------|
| `15min_expiry` | Binary trade closed at 15 minutes |
| `trailing_stop_peak{X}%` | Trailing stop triggered after reaching X% peak |
| `stop_loss_{X}%` | Hard stop loss at X% loss |
| `signal_exit_RSI{X}` | Signal reversed, RSI at X |
| `max_hold_2h` | Maximum 2-hour hold time reached |

### Row Level Security

```sql
-- Allow public read access
CREATE POLICY "public_read" ON paper_trades FOR SELECT USING (true);

-- Allow public insert (for Edge Functions with anon key)
CREATE POLICY "public_insert" ON paper_trades FOR INSERT WITH CHECK (true);

-- Allow public update (for updating max_pnl, closing trades)
CREATE POLICY "public_update" ON paper_trades FOR UPDATE USING (true);
```

## Dashboard Architecture

Single-page HTML application with vanilla JavaScript.

### Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     DASHBOARD (index.html)                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐    │
│   │   Every 5 seconds:                                     │    │
│   │                                                        │    │
│   │   1. Fetch signals from Edge Function                  │    │
│   │      GET /functions/v1/generate-live-signal            │    │
│   │      → Current prices, RSI, signal direction           │    │
│   │                                                        │    │
│   │   2. Fetch open trades from Supabase                   │    │
│   │      GET /rest/v1/paper_trades?exit_time=is.null       │    │
│   │      → Calculate live P&L using current prices         │    │
│   │                                                        │    │
│   │   3. Fetch closed trades                               │    │
│   │      GET /rest/v1/paper_trades?exit_time=not.is.null   │    │
│   │      → Display history with exit reasons               │    │
│   │                                                        │    │
│   │   4. Update UI                                         │    │
│   │      → Bankroll, P&L, win rate, positions              │    │
│   └───────────────────────────────────────────────────────┘    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### UI Components

| Component | Purpose |
|-----------|---------|
| Crypto Prices | BTC/ETH/SOL with RSI and signal badges |
| Stats Bar | Bankroll, Total P&L, Unrealized, Win Rate |
| 15-Min Binary | Open bets, countdown timer, recent results |
| 2x Leverage | Open positions with live P&L, trailing stop progress |
| Exit Breakdown | Count of exit types (trail/SL/signal) |

## Cron Job Configuration

```sql
-- Supabase pg_cron job
SELECT cron.schedule(
  'generate-signals',
  '* * * * *',  -- Every minute
  $$
  SELECT net.http_post(
    url := 'https://oukirnoonygvvctrjmih.supabase.co/functions/v1/generate-live-signal',
    headers := '{"Content-Type": "application/json"}'::jsonb
  );
  $$
);
```

## API Endpoints

### Edge Function: generate-live-signal

**URL:** `https://oukirnoonygvvctrjmih.supabase.co/functions/v1/generate-live-signal`

**Method:** GET

**Response:**
```json
{
  "message": "PM: +1/-0 | LV: +1/-0",
  "signals": {
    "BTC": {
      "direction": "UP",
      "confidence": 0.65,
      "rsi": 32,
      "emaDistance": -0.3,
      "currentPrice": 98500,
      "positionSize": 100
    },
    "ETH": { ... },
    "SOL": { ... }
  },
  "polymarket": {
    "open": 2,
    "total": 15,
    "wins": 9,
    "pnl": 12.50,
    "win_rate": "60.0%"
  },
  "leverage": {
    "open": 1,
    "total": 8,
    "wins": 5,
    "pnl": 8.75,
    "win_rate": "62.5%",
    "trailingStops": 3,
    "stopLosses": 1,
    "signalExits": 2
  },
  "settings": {
    "position_sizing": "5%-25% of bankroll",
    "confidence_range": "55%-85%"
  },
  "timestamp": "2026-01-31T20:30:00.000Z"
}
```

### Supabase REST API

**Base URL:** `https://oukirnoonygvvctrjmih.supabase.co/rest/v1`

**Headers:**
```
apikey: <SUPABASE_ANON_KEY>
Authorization: Bearer <SUPABASE_ANON_KEY>
```

**Endpoints:**
| Query | Purpose |
|-------|---------|
| `GET /paper_trades?exit_time=is.null` | Open positions |
| `GET /paper_trades?exit_time=not.is.null&order=exit_time.desc&limit=10` | Recent closed |
| `GET /paper_trades?trade_type=eq.leverage` | Leverage trades only |

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Signal generation | ~500ms (3 symbols parallel) |
| Database queries | ~50ms per query |
| Dashboard refresh | Every 5 seconds |
| Cron execution | Every 60 seconds |
| Price staleness | Max 60 seconds |

## Error Handling

### Edge Function

```typescript
try {
  // Main logic
} catch (error) {
  console.error("Error:", error);
  return new Response(
    JSON.stringify({ error: error.message }),
    { 
      status: 500, 
      headers: { 
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*" 
      } 
    }
  );
}
```

### Dashboard

```javascript
try {
  const resp = await fetch(url);
  const data = await resp.json();
  // Update UI
} catch (err) {
  console.error('Error:', err);
  // UI shows last known state
}
```

## Security Considerations

| Aspect | Implementation |
|--------|----------------|
| API Keys | Supabase anon key (public, read-only) |
| Edge Functions | verify_jwt disabled (public access) |
| RLS Policies | Enabled, allow public read/write for paper trades |
| CORS | `Access-Control-Allow-Origin: *` |
| No Real Money | Paper trading only, no wallet connections |

## Local Development

### Test Edge Function Locally

```bash
# Install Supabase CLI
npm install -g supabase

# Start local Supabase
supabase start

# Serve function locally
supabase functions serve generate-live-signal --env-file .env
```

### Test Dashboard Locally

```bash
# Simple HTTP server
cd dashboard/build
python -m http.server 8080
# Open http://localhost:8080
```

## Deployment Checklist

- [ ] Edge Function deployed to Supabase
- [ ] pg_cron job scheduled
- [ ] RLS policies enabled on paper_trades
- [ ] Dashboard uploaded to Azure Storage
- [ ] CORS headers set on Edge Function
- [ ] Test signal generation working
- [ ] Test position opening/closing
- [ ] Verify dashboard displays data

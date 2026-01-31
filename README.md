# Polymarket Trading Bot

A comprehensive trading system for Polymarket prediction markets, deployed on Azure.

## Overview

This repository contains **two distinct trading systems**:

| System | Entry Point | Purpose |
|--------|-------------|---------|
| **Arbitrage Bot** | `python -m src.main` | Detects YES+NO arbitrage opportunities |
| **Signal Trading** | `python -m src.api.server` | Paper trading with live crypto signals |

## System 1: Arbitrage Bot

Automated arbitrage bot that detects and executes opportunities when YES + NO prices < $1.00.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    ARBITRAGE BOT FLOW                           │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │   WebSocket  │───▶│   Arbitrage  │───▶│    Order     │      │
│  │   Listener   │    │   Detector   │    │   Executor   │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│                                                │                │
│                                                ▼                │
│                                          ┌──────────────┐      │
│                                          │    Token     │      │
│                                          │    Merger    │      │
│                                          └──────────────┘      │
└─────────────────────────────────────────────────────────────────┘
          │                    │                    │
          ▼                    ▼                    ▼
   ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
   │ Polymarket  │      │    Gamma    │      │   Polygon   │
   │  CLOB API   │      │     API     │      │     RPC     │
   └─────────────┘      └─────────────┘      └─────────────┘
```

### Run Arbitrage Bot

```bash
# Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp env.example .env
# Edit .env with your API keys

# Run
python -m src.main
```

## System 2: Signal Trading Dashboard

Live crypto signal trading system with paper trading simulation.

### Features

- **Live Signals**: Mean-reversion signals for BTC, ETH, SOL (7-min window)
- **Paper Trading**: Simulate trades without real money
- **Risk Management**: Kelly Criterion position sizing, daily loss limits
- **Dual View**: Polymarket binary + 2x leverage trading modes
- **Live Dashboard**: https://polymktdash097744.z13.web.core.windows.net/

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    SIGNAL TRADING FLOW                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │   Binance    │───▶│    Live      │───▶│    Paper     │      │
│  │   Price API  │    │  Predictor   │    │   Trader     │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│                             │                   │               │
│                             ▼                   ▼               │
│                      ┌──────────────┐    ┌──────────────┐      │
│                      │   FastAPI    │◀───│    Risk      │      │
│                      │   Server     │    │   Manager    │      │
│                      └──────────────┘    └──────────────┘      │
│                             │                                   │
└─────────────────────────────│───────────────────────────────────┘
                              ▼
                       ┌─────────────┐
                       │   React     │
                       │  Dashboard  │
                       └─────────────┘
```

### Run Signal Trading

```bash
# Start API server
python -m src.api.server

# Dashboard accessible at http://localhost:8000
# Or use the live dashboard (uses Supabase backend)
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/api/signals/live` | GET | Get live signals for all assets |
| `/api/signals/{symbol}` | GET | Get signal for specific symbol |
| `/api/paper/dashboard` | GET | Paper trading dashboard data |
| `/api/paper/trade` | POST | Execute paper trade |
| `/api/paper/stats` | GET | Paper trading statistics |
| `/api/dashboard` | GET | Full dashboard data |

## Project Structure

```
Polymarket Bot/
├── README.md                 # This file
├── ARCHITECTURE.md           # Technical architecture details
├── requirements.txt          # Python dependencies
├── env.example               # Environment variables template
│
├── src/
│   ├── main.py               # Arbitrage bot entry point
│   ├── config.py             # Configuration
│   ├── database.py           # SQLite database
│   │
│   ├── api/                  # FastAPI server
│   │   └── server.py         # Dashboard API
│   │
│   ├── arbitrage/            # Arbitrage detection
│   │   ├── detector.py       # Main detector
│   │   ├── binary_arb.py     # YES/NO detection
│   │   └── categorical_arb.py # Multi-outcome
│   │
│   ├── clients/              # External API clients
│   │   ├── websocket_client.py
│   │   ├── clob_client.py
│   │   ├── gamma_client.py
│   │   └── polygon_client.py
│   │
│   ├── execution/            # Order execution
│   │   ├── executor.py
│   │   └── merger.py
│   │
│   ├── signals/              # Signal generation
│   │   ├── live_predictor.py # Mean-reversion signals
│   │   ├── paper_trader.py   # Paper trading
│   │   ├── aggregator.py     # Signal combination
│   │   └── price_feed.py     # Real-time prices
│   │
│   ├── market_maker/         # Market maker logic
│   ├── risk/                 # Risk management
│   └── utils/                # Utilities
│
├── dashboard/                # React frontend
│   ├── src/
│   │   ├── App.tsx           # Main dashboard
│   │   └── PaperTrading.tsx  # Paper trading UI
│   └── build/                # Deployed static files
│
├── scripts/                  # Utility scripts
├── deploy/                   # Deployment configs
└── tests/                    # Test suite
```

## Configuration

### Environment Variables

Copy `env.example` to `.env` and configure:

**Arbitrage Bot:**
- `POLYMARKET_API_KEY` - Your Polymarket API key
- `POLYMARKET_API_SECRET` - Your Polymarket API secret
- `POLYMARKET_API_PASSPHRASE` - Your Polymarket passphrase
- `PRIVATE_KEY` - Your wallet private key
- `WALLET_ADDRESS` - Your wallet address
- `POLYGON_RPC_URL` - Polygon RPC endpoint

**Signal Trading:**
- `API_HOST` - API server host (default: 0.0.0.0)
- `API_PORT` - API server port (default: 8000)
- `ALLOWED_ORIGINS` - CORS origins (default: *)
- `SUPABASE_URL` - Supabase project URL (optional)
- `SUPABASE_ANON_KEY` - Supabase anon key (optional)

## Deployment

### Azure Deployment

```powershell
# Deploy arbitrage bot
cd deploy
.\deploy.ps1 -ResourceGroupName "rg-polymarket" -AcrName "acrpolymarket"

# Deploy dashboard to Azure Storage
.\deploy-dashboard.ps1
```

### Live URLs

- **Dashboard**: https://polymktdash097744.z13.web.core.windows.net/
- **Signals Page**: https://polymktdash097744.z13.web.core.windows.net/signals.html

## Risk Controls

| Control | Default | Description |
|---------|---------|-------------|
| Max Position Size | $50 | Maximum per trade |
| Max Daily Loss | $100 | Stop trading if exceeded |
| Max Concurrent | 6 | Maximum open positions |
| Kelly Fraction | 25% | Position sizing factor |
| Cooldown | 5 min | After consecutive losses |

## Testing

```bash
pytest tests/ -v
```

## Cost Estimates (Azure)

| Component | Monthly Cost |
|-----------|--------------|
| Container Apps | ~$75 |
| Storage Account | ~$5 |
| Total | ~$80 |

## License

MIT

## Disclaimer

This bot is for educational purposes. Trading involves risk. Never invest more than you can afford to lose.

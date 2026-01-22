# Polymarket Arbitrage Bot

Automated arbitrage bot for Polymarket prediction markets, deployed on Azure Container Apps.

## Overview

This bot detects and executes arbitrage opportunities on Polymarket by:
- **Binary Arbitrage**: When YES + NO prices < $1.00
- **Categorical Arbitrage**: When all outcome prices sum to < $1.00

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    AZURE CONTAINER APPS                         │
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

## Prerequisites

1. **Polymarket Account** with API credentials
2. **Polygon Wallet** with USDC and MATIC for gas
3. **Azure Subscription** for deployment
4. **Python 3.11+** for local development

## Quick Start

### 1. Clone and Setup

```bash
cd "Polymarket Bot"
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `env.example` to `.env` and fill in your credentials:

```bash
cp env.example .env
```

Required variables:
- `POLYMARKET_API_KEY` - Your Polymarket API key
- `POLYMARKET_API_SECRET` - Your Polymarket API secret
- `POLYMARKET_API_PASSPHRASE` - Your Polymarket passphrase
- `PRIVATE_KEY` - Your wallet private key (without 0x)
- `WALLET_ADDRESS` - Your wallet address
- `POLYGON_RPC_URL` - Polygon RPC endpoint (Alchemy, QuickNode, etc.)

### 3. Run Locally

```bash
python -m src.main
```

### 4. Run Tests

```bash
pytest tests/ -v
```

## Deployment to Azure

### Prerequisites

- Azure CLI installed and logged in
- Docker installed
- PowerShell 7+

### Deploy

```powershell
cd deploy
.\deploy.ps1 -ResourceGroupName "rg-polymarket" -AcrName "acrpolymarket" -EnvFile "../.env"
```

This will:
1. Create an Azure Container Registry
2. Build and push the Docker image
3. Deploy Container Apps, Key Vault, and Log Analytics

### Monitor

```bash
# View logs
az containerapp logs show -n polymarket-arb-bot -g rg-polymarket --follow

# Check status
az containerapp show -n polymarket-arb-bot -g rg-polymarket --query "properties.runningStatus"
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MIN_EDGE_BPS` | 50 | Minimum edge in basis points (50 = 0.5%) |
| `MAX_POSITION_SIZE` | 100 | Maximum USDC per trade |
| `MAX_CONCURRENT_ORDERS` | 5 | Maximum open orders |
| `KILL_SWITCH` | false | Set to true to stop trading |
| `MIN_WALLET_BALANCE` | 50 | Minimum USDC to continue trading |

## Risk Controls

- **Kill Switch**: Set `KILL_SWITCH=true` to stop all trading
- **Position Limits**: Configurable max size per trade
- **Concurrent Order Limits**: Prevents over-exposure
- **Wallet Balance Check**: Stops if balance too low

## Cost Estimates

| Component | Monthly Cost |
|-----------|--------------|
| Azure Container Apps | ~$75 |
| Azure Key Vault | ~$5 |
| Log Analytics | ~$10 |
| Container Registry | ~$5 |
| Polygon RPC (Alchemy) | ~$199 |
| **Total** | **~$295** |

## Project Structure

```
polymarket-arb-bot/
├── src/
│   ├── main.py                 # Entry point
│   ├── config.py               # Configuration
│   ├── clients/
│   │   ├── websocket_client.py # Real-time data
│   │   ├── clob_client.py      # Order placement
│   │   ├── gamma_client.py     # Market metadata
│   │   └── polygon_client.py   # Blockchain ops
│   ├── arbitrage/
│   │   ├── detector.py         # Main detector
│   │   ├── binary_arb.py       # YES/NO detection
│   │   └── categorical_arb.py  # Multi-outcome
│   ├── execution/
│   │   ├── executor.py         # Order execution
│   │   └── merger.py           # Token merging
│   └── utils/
│       ├── logger.py           # Logging
│       └── cost_calculator.py  # Fee calculation
├── tests/
├── deploy/
│   ├── Dockerfile
│   ├── container-app.bicep
│   └── deploy.ps1
├── requirements.txt
└── env.example
```

## How It Works

1. **Market Discovery**: Fetches all active markets from Gamma API
2. **WebSocket Connection**: Subscribes to real-time order book updates
3. **Arbitrage Detection**: Monitors for price inefficiencies
4. **Order Execution**: Places both legs simultaneously
5. **Fill Monitoring**: Tracks order fills with timeout
6. **Token Merge**: Merges tokens on-chain for profit

## Arbitrage Types

### Binary Arbitrage
```
If YES ask + NO ask < $1.00 - fees:
  Buy YES and NO tokens
  Merge on-chain for $1.00
  Profit = $1.00 - YES - NO - fees
```

### Categorical Arbitrage
```
If sum(all outcome asks) < $1.00 - fees:
  Buy all outcomes
  One will pay $1.00
  Profit = $1.00 - sum(prices) - fees
```

## Disclaimer

This bot is for educational purposes. Trading involves risk. Never invest more than you can afford to lose. The authors are not responsible for any financial losses.

## License

MIT

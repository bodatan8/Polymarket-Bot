"""
FastAPI server for the 15-minute market maker dashboard.
Includes full-stack component data.
"""
import asyncio
import aiohttp
import json
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

from src.database import get_open_positions, get_closed_positions, get_all_positions, get_stats, reset_db
from src.learning.timing_optimizer import TimingOptimizer
from src.risk.manager import RiskManager, RiskLimits, RiskLevel

app = FastAPI(title="15-Min Market Maker Dashboard API")

# Enable CORS for dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize shared components
timing_optimizer = TimingOptimizer()
risk_manager = RiskManager(
    limits=RiskLimits(
        max_daily_loss=100.0,
        max_drawdown_percent=25.0,
        max_position_size=50.0,
        min_position_size=1.0,
        max_total_exposure=500.0,
        max_positions_per_asset=2,
        max_open_positions=8,
    ),
    risk_level=RiskLevel.MODERATE
)

# Gamma API for live prices
GAMMA_API = "https://gamma-api.polymarket.com"


async def fetch_live_market_data(market_id: str) -> dict:
    """Fetch live market data from Polymarket."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{GAMMA_API}/markets/{market_id}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    outcomes = json.loads(data.get("outcomes", "[]"))
                    prices = json.loads(data.get("outcomePrices", "[]"))
                    
                    up_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "up"), 0)
                    down_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "down"), 1)
                    
                    return {
                        "up_price": float(prices[up_idx]) if up_idx < len(prices) else 0.5,
                        "down_price": float(prices[down_idx]) if down_idx < len(prices) else 0.5,
                    }
    except Exception as e:
        print(f"Error fetching market {market_id}: {e}")
    return {"up_price": 0.5, "down_price": 0.5}


async def fetch_crypto_prices() -> dict:
    """Fetch live crypto prices from CoinGecko."""
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,ripple&vs_currencies=usd"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "BTC": data.get("bitcoin", {}).get("usd", 0),
                        "ETH": data.get("ethereum", {}).get("usd", 0),
                        "SOL": data.get("solana", {}).get("usd", 0),
                        "XRP": data.get("ripple", {}).get("usd", 0),
                    }
    except Exception as e:
        print(f"Error fetching crypto prices: {e}")
    return {"BTC": 0, "ETH": 0, "SOL": 0, "XRP": 0}


@app.get("/")
async def root():
    """Health check."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/stats")
async def api_stats():
    """Get overall statistics."""
    stats = get_stats()
    return JSONResponse(content=stats)


@app.get("/api/positions/open")
async def api_open_positions():
    """Get all open positions."""
    positions = get_open_positions()
    return JSONResponse(content={"positions": positions, "count": len(positions)})


@app.get("/api/positions/closed")
async def api_closed_positions():
    """Get closed positions."""
    positions = get_closed_positions(limit=50)
    return JSONResponse(content={"positions": positions, "count": len(positions)})


@app.get("/api/positions")
async def api_all_positions():
    """Get all positions."""
    positions = get_all_positions(limit=100)
    return JSONResponse(content={"positions": positions, "count": len(positions)})


@app.get("/api/prices")
async def api_prices():
    """Get live crypto prices."""
    prices = await fetch_crypto_prices()
    return JSONResponse(content=prices)


@app.get("/api/timing")
async def api_timing():
    """Get timing optimizer data."""
    summary = timing_optimizer.get_summary()
    best_bucket, best_roi = timing_optimizer.get_best_bucket()
    return JSONResponse(content={
        "buckets": summary,
        "best_bucket": best_bucket,
        "best_roi": f"{best_roi*100:+.1f}%"
    })


@app.get("/api/risk")
async def api_risk():
    """Get risk management status."""
    open_positions = get_open_positions()
    summary = risk_manager.get_risk_summary(open_positions, 1000)
    return JSONResponse(content=summary)


@app.post("/api/reset")
async def api_reset():
    """Reset all data (for testing)."""
    reset_db()
    return JSONResponse(content={"status": "reset", "message": "All data cleared"})


@app.get("/api/dashboard")
async def api_dashboard():
    """Get all dashboard data in one call."""
    stats = get_stats()
    open_positions = get_open_positions()
    closed_positions = get_closed_positions(limit=20)
    
    # Fetch live crypto prices
    crypto_prices = await fetch_crypto_prices()
    
    # Get timing optimizer data
    timing_summary = timing_optimizer.get_summary()
    best_bucket, best_roi = timing_optimizer.get_best_bucket()
    
    # Get risk summary
    risk_summary = risk_manager.get_risk_summary(open_positions, 1000)
    
    # Enrich open positions with live market data
    enriched_positions = []
    for pos in open_positions:
        market_data = await fetch_live_market_data(pos['market_id'])
        
        # Calculate current odds of winning
        current_price = market_data['up_price'] if pos['side'] == 'Up' else market_data['down_price']
        entry_price = pos['entry_price']
        
        # Win probability based on current market price
        win_odds = current_price * 100
        
        # Potential profit if we win
        potential_profit = pos['shares'] * 1.0 - pos['amount_usd']
        
        # Current value (if we could sell now)
        current_value = pos['shares'] * current_price
        unrealized_pnl = current_value - pos['amount_usd']
        
        enriched_pos = {
            **pos,
            "current_price": current_price,
            "current_up_price": market_data['up_price'],
            "current_down_price": market_data['down_price'],
            "live_crypto_price": crypto_prices.get(pos['asset'], 0),
            "win_odds": win_odds,
            "potential_profit": potential_profit,
            "unrealized_pnl": unrealized_pnl,
        }
        enriched_positions.append(enriched_pos)
    
    return JSONResponse(content={
        "stats": stats,
        "open_positions": enriched_positions,
        "closed_positions": closed_positions,
        "crypto_prices": crypto_prices,
        "timing": {
            "buckets": timing_summary,
            "best_bucket": best_bucket,
            "best_roi": f"{best_roi*100:+.1f}%" if best_roi != float('-inf') else "N/A"
        },
        "risk": risk_summary,
        "timestamp": datetime.utcnow().isoformat()
    })


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the API server."""
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()

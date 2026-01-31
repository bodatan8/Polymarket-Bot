"""
FastAPI server for the 15-minute market maker dashboard.
Includes full-stack component data and live prediction signals.
"""
import asyncio
import aiohttp
import json
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

from src.database import get_open_positions, get_closed_positions, get_all_positions, get_stats, reset_db
from src.learning.timing_optimizer import TimingOptimizer
from src.risk.manager import RiskManager, RiskLimits, RiskLevel
from src.signals.live_predictor import LivePredictor, get_live_signal, PredictionDirection
from src.signals.paper_trader import PaperTrader, TradeType, PaperRiskLimits

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
live_predictor = LivePredictor()

# Paper trading instance
paper_trader = PaperTrader(
    starting_bankroll=1000.0,
    risk_limits=PaperRiskLimits(
        max_position_size_usd=50.0,
        min_position_size_usd=5.0,
        max_daily_loss_usd=100.0,
        max_concurrent_positions=6,
        max_positions_per_symbol=2,
        min_confidence=0.50,
        min_accuracy_estimate=0.54,
        max_consecutive_losses=5,
        cooldown_after_loss_seconds=300,
    )
)

# Signal history for tracking
signal_history: list = []
MAX_SIGNAL_HISTORY = 100

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


@app.get("/api/signals/live")
async def api_live_signals():
    """Get live prediction signals for all supported assets."""
    signals = {}
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        try:
            signal = await live_predictor.generate_signal(symbol, expiry_minutes=7)
            signal_dict = signal.to_dict()
            signals[symbol.replace("USDT", "")] = signal_dict
            
            # Store in history if it's an actionable signal
            if signal.direction != PredictionDirection.NO_SIGNAL:
                signal_history.insert(0, {
                    **signal_dict,
                    "recorded_at": datetime.utcnow().isoformat()
                })
                # Trim history
                while len(signal_history) > MAX_SIGNAL_HISTORY:
                    signal_history.pop()
        except Exception as e:
            signals[symbol.replace("USDT", "")] = {
                "error": str(e),
                "direction": "NO_SIGNAL"
            }
    
    return JSONResponse(content={
        "signals": signals,
        "timestamp": datetime.utcnow().isoformat()
    })


@app.get("/api/signals/history")
async def api_signal_history(limit: int = 20):
    """Get recent signal history."""
    return JSONResponse(content={
        "signals": signal_history[:limit],
        "total": len(signal_history)
    })


@app.get("/api/signals/{symbol}")
async def api_signal_for_symbol(symbol: str, expiry: int = 7):
    """Get live prediction signal for a specific symbol."""
    # Normalize symbol
    if not symbol.endswith("USDT"):
        symbol = f"{symbol.upper()}USDT"
    
    try:
        signal = await live_predictor.generate_signal(symbol, expiry_minutes=expiry)
        return JSONResponse(content=signal.to_dict())
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "symbol": symbol}
        )


# =============================================================================
# PAPER TRADING ENDPOINTS
# =============================================================================

@app.get("/api/paper/stats")
async def api_paper_stats():
    """Get paper trading statistics."""
    return JSONResponse(content=paper_trader.get_stats())


@app.get("/api/paper/positions/open")
async def api_paper_open_positions():
    """Get open paper positions."""
    return JSONResponse(content={
        "positions": paper_trader.get_open_positions(),
        "count": len(paper_trader.get_open_positions())
    })


@app.get("/api/paper/positions/closed")
async def api_paper_closed_positions(limit: int = 50):
    """Get closed paper positions."""
    return JSONResponse(content={
        "positions": paper_trader.get_closed_positions(limit),
        "count": len(paper_trader.closed_positions)
    })


@app.post("/api/paper/trade")
async def api_paper_trade(symbol: str = "BTCUSDT", trade_type: str = "polymarket"):
    """
    Execute a paper trade based on current signal.
    trade_type: 'polymarket' or 'leverage'
    """
    # Get current signal
    signal = await live_predictor.generate_signal(symbol, expiry_minutes=7)
    
    if signal.direction == PredictionDirection.NO_SIGNAL:
        return JSONResponse(content={
            "success": False,
            "message": "No signal to trade",
            "signal": signal.to_dict()
        })
    
    # Open position
    t_type = TradeType.POLYMARKET if trade_type == "polymarket" else TradeType.LEVERAGE
    position = await paper_trader.open_position(signal, t_type)
    
    if position:
        return JSONResponse(content={
            "success": True,
            "message": f"Opened {trade_type} position",
            "position": position.to_dict()
        })
    else:
        return JSONResponse(content={
            "success": False,
            "message": "Trade rejected by risk manager",
            "signal": signal.to_dict()
        })


@app.post("/api/paper/check-expired")
async def api_paper_check_expired():
    """Check and close expired positions."""
    closed = await paper_trader.check_and_close_expired()
    return JSONResponse(content={
        "closed_count": len(closed),
        "closed_positions": [p.to_dict() for p in closed],
        "stats": paper_trader.get_stats()
    })


@app.post("/api/paper/reset")
async def api_paper_reset(bankroll: float = 1000.0):
    """Reset paper trading (start fresh)."""
    global paper_trader
    paper_trader = PaperTrader(starting_bankroll=bankroll)
    return JSONResponse(content={
        "message": "Paper trading reset",
        "bankroll": bankroll
    })


@app.get("/api/paper/dashboard")
async def api_paper_dashboard():
    """Get all paper trading data for dashboard."""
    # Check and close any expired positions first
    await paper_trader.check_and_close_expired()
    
    # Get live signals
    signals = {}
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        try:
            signal = await live_predictor.generate_signal(symbol, expiry_minutes=7)
            signals[symbol.replace("USDT", "")] = signal.to_dict()
        except Exception as e:
            signals[symbol.replace("USDT", "")] = {"error": str(e)}
    
    # Get crypto prices
    crypto_prices = await fetch_crypto_prices()
    
    return JSONResponse(content={
        "stats": paper_trader.get_stats(),
        "open_positions": {
            "polymarket": [p for p in paper_trader.get_open_positions() if p["trade_type"] == "polymarket"],
            "leverage": [p for p in paper_trader.get_open_positions() if p["trade_type"] == "leverage"],
        },
        "closed_positions": {
            "polymarket": [p for p in paper_trader.get_closed_positions(20) if p["trade_type"] == "polymarket"],
            "leverage": [p for p in paper_trader.get_closed_positions(20) if p["trade_type"] == "leverage"],
        },
        "signals": signals,
        "crypto_prices": crypto_prices,
        "timestamp": datetime.utcnow().isoformat()
    })


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

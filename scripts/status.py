#!/usr/bin/env python3
"""
Simple status display for Polymarket Arbitrage Bot.
Shows what the bot is doing in real-time.
"""

import asyncio
import json
import time
from datetime import datetime

import websockets
import aiohttp


async def get_market_stats():
    """Fetch basic market stats from Polymarket."""
    async with aiohttp.ClientSession() as session:
        # Get active markets count
        url = "https://gamma-api.polymarket.com/markets?closed=false&limit=1"
        async with session.get(url) as resp:
            # Just check if API is accessible
            return resp.status == 200


async def test_websocket():
    """Test WebSocket connection and show live data."""
    print("\n" + "="*60)
    print("🔌 POLYMARKET WEBSOCKET CONNECTION TEST")
    print("="*60)
    
    # Get a sample token ID from active markets
    async with aiohttp.ClientSession() as session:
        url = "https://gamma-api.polymarket.com/markets?closed=false&limit=5&active=true"
        async with session.get(url) as resp:
            if resp.status != 200:
                print("❌ Failed to fetch markets")
                return
            markets = await resp.json()
    
    if not markets:
        print("❌ No active markets found")
        return
    
    # Get token IDs from first few markets
    token_ids = []
    for market in markets[:3]:
        tokens = market.get("clobTokenIds", "").split(",")
        token_ids.extend([t.strip() for t in tokens if t.strip()])
    
    if not token_ids:
        print("❌ No token IDs found")
        return
    
    print(f"\n📊 Testing with {len(token_ids)} tokens from {len(markets)} markets:")
    for market in markets[:3]:
        print(f"   • {market.get('question', 'Unknown')[:50]}...")
    
    print("\n🔗 Connecting to WebSocket...")
    
    try:
        async with websockets.connect(
            "wss://ws-subscriptions-clob.polymarket.com/ws/market",
            ping_interval=30,
            ping_timeout=10
        ) as ws:
            print("✅ Connected to Polymarket WebSocket!")
            
            # Subscribe to tokens
            subscribe_msg = {
                "assets_ids": token_ids[:10],
                "type": "market"
            }
            await ws.send(json.dumps(subscribe_msg))
            print(f"📡 Subscribed to {len(token_ids[:10])} tokens")
            
            print("\n" + "-"*60)
            print("📈 LIVE MARKET DATA (waiting 30 seconds for updates...)")
            print("-"*60)
            
            book_count = 0
            price_change_count = 0
            trade_count = 0
            start_time = time.time()
            
            while time.time() - start_time < 30:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    
                    if message == "PING" or message == "PONG":
                        continue
                    
                    try:
                        data = json.loads(message)
                        
                        # Handle array of messages
                        if isinstance(data, list):
                            for item in data:
                                await process_message(item)
                        else:
                            event_type = data.get("event_type", "unknown")
                            
                            if event_type == "book":
                                book_count += 1
                                asset = data.get("asset_id", "")[:20]
                                bids = len(data.get("bids", []))
                                asks = len(data.get("asks", []))
                                print(f"📖 Order Book: {bids} bids, {asks} asks | Asset: {asset}...")
                            
                            elif event_type == "price_change":
                                price_change_count += 1
                                changes = data.get("price_changes", [])
                                if changes:
                                    change = changes[0]
                                    price = change.get("price", "?")
                                    side = change.get("side", "?")
                                    print(f"💹 Price Change: {side} @ ${price}")
                            
                            elif event_type == "last_trade_price":
                                trade_count += 1
                                price = data.get("price", "?")
                                size = data.get("size", "?")
                                print(f"🔄 Trade: ${price} x {size} shares")
                            
                            elif event_type == "best_bid_ask":
                                bid = data.get("best_bid", "?")
                                ask = data.get("best_ask", "?")
                                print(f"📊 Best Bid/Ask: ${bid} / ${ask}")
                            
                            else:
                                print(f"📨 Message: {event_type}")
                    
                    except json.JSONDecodeError:
                        if "INVALID" not in message:
                            print(f"⚠️ Non-JSON: {message[:50]}")
                
                except asyncio.TimeoutError:
                    print("⏳ Waiting for data...")
            
            print("\n" + "="*60)
            print("📊 SUMMARY (30 seconds)")
            print("="*60)
            print(f"   📖 Order Books received: {book_count}")
            print(f"   💹 Price Changes: {price_change_count}")
            print(f"   🔄 Trades: {trade_count}")
            print("="*60)
            
    except Exception as e:
        print(f"❌ WebSocket error: {e}")


async def process_message(item):
    """Process a single message item."""
    if isinstance(item, dict):
        event_type = item.get("event_type", "unknown")
        print(f"   📨 {event_type}")


async def show_arbitrage_opportunities():
    """Scan for potential arbitrage opportunities."""
    print("\n" + "="*60)
    print("🔍 SCANNING FOR ARBITRAGE OPPORTUNITIES")
    print("="*60)
    
    async with aiohttp.ClientSession() as session:
        url = "https://gamma-api.polymarket.com/markets?closed=false&limit=100&active=true"
        async with session.get(url) as resp:
            if resp.status != 200:
                print("❌ Failed to fetch markets")
                return
            markets = await resp.json()
    
    opportunities = []
    
    for market in markets:
        # Check if it's a binary market with prices
        outcomes = market.get("outcomes", "")
        outcome_prices = market.get("outcomePrices", "")
        
        if not outcomes or not outcome_prices:
            continue
        
        try:
            # Parse outcomes and prices
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes) if outcomes.startswith("[") else outcomes.split(",")
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices) if outcome_prices.startswith("[") else outcome_prices.split(",")
            
            if len(outcomes) == 2 and len(outcome_prices) >= 2:
                yes_price = float(outcome_prices[0])
                no_price = float(outcome_prices[1])
                total = yes_price + no_price
                
                # If total < 1, there's an arbitrage opportunity
                if total < 0.99:  # Allow 1% for fees
                    edge = (1.0 - total) * 100
                    opportunities.append({
                        "question": market.get("question", "Unknown")[:50],
                        "yes": yes_price,
                        "no": no_price,
                        "total": total,
                        "edge_pct": edge
                    })
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    
    if opportunities:
        # Sort by edge
        opportunities.sort(key=lambda x: x["edge_pct"], reverse=True)
        
        print(f"\n🎯 Found {len(opportunities)} potential opportunities:\n")
        for i, opp in enumerate(opportunities[:10], 1):
            print(f"{i}. {opp['question']}...")
            print(f"   YES: ${opp['yes']:.3f} + NO: ${opp['no']:.3f} = ${opp['total']:.3f}")
            print(f"   📈 Edge: {opp['edge_pct']:.2f}% (before fees)")
            print()
    else:
        print("\n✨ No obvious arbitrage opportunities found.")
        print("   (Markets are efficiently priced - YES + NO ≈ $1.00)")
    
    print("="*60)


async def main():
    """Main status display."""
    print("\n" + "="*60)
    print("  POLYMARKET ARBITRAGE BOT - STATUS DASHBOARD")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    # Check API connectivity
    print("\n🔍 Checking Polymarket API connectivity...")
    api_ok = await get_market_stats()
    if api_ok:
        print("✅ Polymarket API is accessible")
    else:
        print("❌ Polymarket API is not accessible")
        return
    
    # Show arbitrage opportunities
    await show_arbitrage_opportunities()
    
    # Test WebSocket
    await test_websocket()
    
    print("\n" + "="*60)
    print("  STATUS CHECK COMPLETE")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())

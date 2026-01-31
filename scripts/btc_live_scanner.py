#!/usr/bin/env python3
"""
Live BTC Scanner - Compares live BTC price to Polymarket predictions in real-time.
"""

import asyncio
import json
from datetime import datetime, timezone

import aiohttp


async def get_live_btc() -> float:
    """Get live BTC price from CoinGecko."""
    async with aiohttp.ClientSession() as session:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("bitcoin", {}).get("usd", 0)
    return 0


async def get_btc_markets() -> list:
    """Get all BTC price prediction markets."""
    async with aiohttp.ClientSession() as session:
        # Get more markets
        url = "https://gamma-api.polymarket.com/markets"
        params = {
            "closed": "false",
            "limit": 500
        }
        
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                markets = await resp.json()
                # Filter for BTC/crypto price markets
                btc_markets = []
                for m in markets:
                    q = m.get("question", "").lower()
                    # Match Bitcoin price markets
                    if "bitcoin" in q or "btc" in q:
                        btc_markets.append(m)
                    # Also match ETH, SOL, etc
                    elif ("ethereum" in q or "eth" in q) and ("price" in q or "above" in q):
                        btc_markets.append(m)
                    elif "solana" in q and ("price" in q or "above" in q):
                        btc_markets.append(m)
                return btc_markets
    return []


def parse_price_range(outcome: str) -> tuple:
    """Parse price range from outcome string like '<88,000' or '88,000-90,000'."""
    import re
    
    outcome = outcome.replace(",", "").replace("$", "").strip()
    
    # Range pattern: "88000-90000"
    range_match = re.match(r'(\d+)\s*[-–]\s*(\d+)', outcome)
    if range_match:
        return (float(range_match.group(1)), float(range_match.group(2)))
    
    # Below pattern: "<88000"
    below_match = re.match(r'<\s*(\d+)', outcome)
    if below_match:
        return (0, float(below_match.group(1)))
    
    # Above pattern: ">92000" or "92000+"
    above_match = re.match(r'>?\s*(\d+)\+?', outcome)
    if above_match:
        return (float(above_match.group(1)), float('inf'))
    
    return None


async def analyze_btc_market(market: dict, live_btc: float):
    """Analyze a single BTC market for arbitrage opportunities."""
    question = market.get("question", "")
    outcomes = market.get("outcomes", "")
    outcome_prices = market.get("outcomePrices", "")
    end_date = market.get("endDate", "")
    
    # Parse outcomes and prices
    try:
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes) if outcomes.startswith("[") else outcomes.split(",")
        if isinstance(outcome_prices, str):
            outcome_prices = json.loads(outcome_prices) if outcome_prices.startswith("[") else outcome_prices.split(",")
    except:
        return None
    
    # Calculate total probability (should sum to ~100%)
    total_prob = 0
    outcome_data = []
    current_bucket = None
    
    for i, outcome in enumerate(outcomes):
        if i < len(outcome_prices):
            try:
                price = float(outcome_prices[i])
                total_prob += price
                
                price_range = parse_price_range(str(outcome))
                
                # Check if live BTC is in this range
                in_range = False
                if price_range:
                    low, high = price_range
                    in_range = low <= live_btc < high
                    if in_range:
                        current_bucket = {
                            "outcome": outcome,
                            "price": price,
                            "range": price_range
                        }
                
                outcome_data.append({
                    "outcome": outcome,
                    "price": price,
                    "in_range": in_range,
                    "range": price_range
                })
            except (ValueError, TypeError):
                continue
    
    return {
        "question": question,
        "end_date": end_date,
        "outcomes": outcome_data,
        "total_prob": total_prob,
        "current_bucket": current_bucket,
        "live_btc": live_btc
    }


async def main():
    """Main scanner loop."""
    print("\n" + "="*70)
    print("  ₿ LIVE BITCOIN vs POLYMARKET SCANNER")
    print("="*70)
    
    # Get live BTC price
    live_btc = await get_live_btc()
    print(f"\n📈 Live BTC Price: ${live_btc:,.2f}")
    print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Get BTC markets
    markets = await get_btc_markets()
    print(f"\n📊 Found {len(markets)} BTC price prediction markets\n")
    
    if not markets:
        print("No BTC markets found")
        return
    
    print("-"*70)
    
    arbitrage_opportunities = []
    
    for market in markets[:10]:  # Analyze top 10 markets
        analysis = await analyze_btc_market(market, live_btc)
        
        if not analysis:
            continue
        
        print(f"\n🎯 {analysis['question'][:60]}...")
        print(f"   Expires: {analysis['end_date'][:10] if analysis['end_date'] else 'Unknown'}")
        print()
        
        # Show all outcomes
        for outcome in analysis['outcomes']:
            if outcome['in_range']:
                print(f"   ➡️  {outcome['outcome']}: {outcome['price']*100:.1f}% ⬅️ (BTC is HERE)")
            else:
                print(f"      {outcome['outcome']}: {outcome['price']*100:.1f}%")
        
        total = analysis['total_prob']
        print(f"\n   📊 Total Probability: {total*100:.2f}%")
        
        # Check for arbitrage
        if total < 0.99:
            edge = (1.0 - total) * 100
            print(f"   🚨 ARBITRAGE DETECTED: {edge:.2f}% edge!")
            print(f"      Buy ALL outcomes for ${total:.4f}, guaranteed to win $1.00")
            print(f"      Profit: ${1.0 - total:.4f} per $1 wagered")
            arbitrage_opportunities.append({
                "market": analysis['question'][:50],
                "edge": edge,
                "cost": total
            })
        elif total > 1.01:
            print(f"   ⚠️  Overpriced by {(total-1)*100:.2f}% - avoid")
        else:
            print(f"   ✅ Efficiently priced")
        
        # Check if current bucket is mispriced
        if analysis['current_bucket']:
            bucket = analysis['current_bucket']
            if bucket['price'] < 0.7:  # If currently in range but priced < 70%
                print(f"\n   💡 OPPORTUNITY: BTC is in '{bucket['outcome']}' range")
                print(f"      But market only gives {bucket['price']*100:.1f}% probability!")
                print(f"      Consider buying YES at {bucket['price']*100:.1f}¢")
        
        print("-"*70)
    
    # Summary
    print("\n" + "="*70)
    print("📋 SUMMARY")
    print("="*70)
    
    if arbitrage_opportunities:
        print("\n🚨 ARBITRAGE OPPORTUNITIES FOUND:\n")
        for opp in arbitrage_opportunities:
            print(f"   • {opp['market']}...")
            print(f"     Edge: {opp['edge']:.2f}% | Cost: ${opp['cost']:.4f}")
    else:
        print("\n✨ No pure arbitrage opportunities found")
        print("   Markets are efficiently priced (all outcomes sum to ~100%)")
    
    print("\n💡 WHAT THE BOT LOOKS FOR:")
    print("   1. Binary markets where YES + NO < $1.00 (buy both = guaranteed profit)")
    print("   2. Multi-outcome markets where all outcomes < $1.00 total")
    print("   3. Price inefficiencies vs live crypto prices")
    print("="*70)


if __name__ == "__main__":
    asyncio.run(main())

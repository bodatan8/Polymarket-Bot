#!/usr/bin/env python3
"""
Crypto Market Scanner for Polymarket Arbitrage Bot.
Compares live crypto prices to Polymarket prediction markets.
"""

import asyncio
import json
from datetime import datetime
from typing import Optional

import aiohttp


async def get_live_crypto_prices() -> dict:
    """Fetch live crypto prices from CoinGecko."""
    print("\n📊 Fetching live crypto prices from CoinGecko...")
    
    async with aiohttp.ClientSession() as session:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "bitcoin,ethereum,solana,ripple,dogecoin",
            "vs_currencies": "usd",
            "include_24hr_change": "true"
        }
        
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "BTC": data.get("bitcoin", {}).get("usd", 0),
                    "ETH": data.get("ethereum", {}).get("usd", 0),
                    "SOL": data.get("solana", {}).get("usd", 0),
                    "XRP": data.get("ripple", {}).get("usd", 0),
                    "DOGE": data.get("dogecoin", {}).get("usd", 0),
                }
            return {}


async def get_polymarket_crypto_markets() -> list:
    """Fetch crypto markets from Polymarket Gamma API."""
    print("📡 Fetching Polymarket crypto markets...")
    
    async with aiohttp.ClientSession() as session:
        # Get crypto-related markets
        url = "https://gamma-api.polymarket.com/markets"
        params = {
            "closed": "false",
            "active": "true",
            "limit": 200,
            "tag": "crypto"  # Filter for crypto tag
        }
        
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            
        # Fallback: search for bitcoin/ethereum in questions
        url = "https://gamma-api.polymarket.com/markets?closed=false&limit=500"
        async with session.get(url) as resp:
            if resp.status == 200:
                markets = await resp.json()
                crypto_keywords = ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "crypto", "doge"]
                return [m for m in markets if any(kw in m.get("question", "").lower() for kw in crypto_keywords)]
    
    return []


def parse_price_from_question(question: str) -> Optional[tuple]:
    """Extract price threshold and direction from market question."""
    import re
    
    question_lower = question.lower()
    
    # Pattern: "above X" or "below X" or "hit X"
    above_match = re.search(r'above.*?[\$]?([\d,]+(?:\.\d+)?)', question_lower)
    below_match = re.search(r'below.*?[\$]?([\d,]+(?:\.\d+)?)', question_lower)
    hit_match = re.search(r'(?:hit|reach).*?[\$]?([\d,]+(?:\.\d+)?)', question_lower)
    range_match = re.search(r'([\d,]+(?:\.\d+)?)\s*[-–]\s*([\d,]+(?:\.\d+)?)', question_lower)
    
    if above_match:
        price = float(above_match.group(1).replace(",", ""))
        return ("above", price)
    elif below_match:
        price = float(below_match.group(1).replace(",", ""))
        return ("below", price)
    elif hit_match:
        price = float(hit_match.group(1).replace(",", ""))
        return ("hit", price)
    elif range_match:
        low = float(range_match.group(1).replace(",", ""))
        high = float(range_match.group(2).replace(",", ""))
        return ("range", low, high)
    
    return None


def get_crypto_from_question(question: str) -> str:
    """Determine which crypto the market is about."""
    question_lower = question.lower()
    
    if "bitcoin" in question_lower or "btc" in question_lower:
        return "BTC"
    elif "ethereum" in question_lower or "eth" in question_lower:
        return "ETH"
    elif "solana" in question_lower or "sol" in question_lower:
        return "SOL"
    elif "xrp" in question_lower or "ripple" in question_lower:
        return "XRP"
    elif "doge" in question_lower:
        return "DOGE"
    
    return "UNKNOWN"


async def analyze_crypto_inefficiencies(live_prices: dict, markets: list):
    """Analyze crypto markets for pricing inefficiencies."""
    print("\n" + "="*70)
    print("🔍 CRYPTO MARKET INEFFICIENCY ANALYSIS")
    print("="*70)
    
    print(f"\n📈 Live Prices (CoinGecko):")
    for symbol, price in live_prices.items():
        if price > 0:
            print(f"   {symbol}: ${price:,.2f}")
    
    print("\n" + "-"*70)
    print("📊 Polymarket Crypto Markets Analysis:")
    print("-"*70)
    
    inefficiencies = []
    
    for market in markets:
        question = market.get("question", "")
        outcomes = market.get("outcomes", "")
        outcome_prices = market.get("outcomePrices", "")
        
        if not outcomes or not outcome_prices:
            continue
        
        # Parse outcomes and prices
        try:
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes) if outcomes.startswith("[") else outcomes.split(",")
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices) if outcome_prices.startswith("[") else outcome_prices.split(",")
        except:
            continue
        
        crypto = get_crypto_from_question(question)
        live_price = live_prices.get(crypto, 0)
        
        if crypto == "UNKNOWN" or live_price == 0:
            continue
        
        price_info = parse_price_from_question(question)
        
        if not price_info:
            continue
        
        # Calculate theoretical probability based on live price
        direction = price_info[0]
        threshold = price_info[1] if len(price_info) > 1 else 0
        
        # Get market probability (YES price)
        try:
            yes_price = float(outcome_prices[0]) if outcome_prices else 0
            no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 1 - yes_price
        except (ValueError, IndexError):
            continue
        
        total = yes_price + no_price
        
        # Calculate implied probability vs reality
        if direction == "above":
            distance_pct = ((threshold - live_price) / live_price) * 100
            
            # If current price is already above threshold, YES should be ~100%
            if live_price > threshold:
                expected_yes = 0.95  # Already above
                inefficiency = yes_price - expected_yes
            else:
                # Further from threshold = lower probability
                expected_yes = max(0.05, 0.5 - (distance_pct / 20))
                inefficiency = yes_price - expected_yes
            
            if abs(inefficiency) > 0.05:  # 5% or more mispricing
                inefficiencies.append({
                    "question": question[:60],
                    "crypto": crypto,
                    "direction": direction,
                    "threshold": threshold,
                    "live_price": live_price,
                    "market_yes": yes_price,
                    "expected_yes": expected_yes,
                    "inefficiency": inefficiency,
                    "total": total
                })
        
        elif direction == "below":
            distance_pct = ((live_price - threshold) / live_price) * 100
            
            if live_price < threshold:
                expected_yes = 0.95
                inefficiency = yes_price - expected_yes
            else:
                expected_yes = max(0.05, 0.5 - (distance_pct / 20))
                inefficiency = yes_price - expected_yes
            
            if abs(inefficiency) > 0.05:
                inefficiencies.append({
                    "question": question[:60],
                    "crypto": crypto,
                    "direction": direction,
                    "threshold": threshold,
                    "live_price": live_price,
                    "market_yes": yes_price,
                    "expected_yes": expected_yes,
                    "inefficiency": inefficiency,
                    "total": total
                })
    
    # Check for categorical market inefficiencies (all outcomes should sum to ~100%)
    print("\n🎯 Binary Market Analysis (YES + NO should = $1.00):\n")
    
    binary_opps = []
    for market in markets:
        question = market.get("question", "")
        outcomes = market.get("outcomes", "")
        outcome_prices = market.get("outcomePrices", "")
        
        try:
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes) if outcomes.startswith("[") else []
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices) if outcome_prices.startswith("[") else []
            
            if len(outcomes) == 2 and len(outcome_prices) >= 2:
                yes_price = float(outcome_prices[0])
                no_price = float(outcome_prices[1])
                total = yes_price + no_price
                
                # Edge exists if total != 1.0
                edge = (1.0 - total) * 100  # As percentage
                
                if abs(edge) > 0.5:  # More than 0.5% edge
                    binary_opps.append({
                        "question": question[:55],
                        "yes": yes_price,
                        "no": no_price,
                        "total": total,
                        "edge_pct": edge
                    })
        except:
            continue
    
    if binary_opps:
        binary_opps.sort(key=lambda x: abs(x["edge_pct"]), reverse=True)
        
        for opp in binary_opps[:10]:
            edge_symbol = "📈" if opp["edge_pct"] > 0 else "📉"
            print(f"{edge_symbol} {opp['question']}...")
            print(f"   YES: ${opp['yes']:.3f} + NO: ${opp['no']:.3f} = ${opp['total']:.4f}")
            if opp["edge_pct"] > 0:
                print(f"   🎯 Edge: +{opp['edge_pct']:.2f}% (buy both, guaranteed profit)")
            else:
                print(f"   ⚠️  Overpriced by {abs(opp['edge_pct']):.2f}%")
            print()
    else:
        print("   ✨ All binary markets efficiently priced (YES + NO ≈ $1.00)\n")
    
    # Show price-based inefficiencies
    print("-"*70)
    print("📊 Price vs Prediction Analysis:\n")
    
    if inefficiencies:
        inefficiencies.sort(key=lambda x: abs(x["inefficiency"]), reverse=True)
        
        for ineff in inefficiencies[:10]:
            symbol = "🔴" if ineff["inefficiency"] > 0 else "🟢"
            print(f"{symbol} {ineff['question']}...")
            print(f"   Live {ineff['crypto']}: ${ineff['live_price']:,.2f} | Threshold: ${ineff['threshold']:,.2f}")
            print(f"   Market says: {ineff['market_yes']*100:.1f}% YES")
            print(f"   Analysis: Expected ~{ineff['expected_yes']*100:.1f}% based on current price")
            
            if ineff["inefficiency"] > 0:
                print(f"   💡 Market OVERPRICES YES by {ineff['inefficiency']*100:.1f}%")
            else:
                print(f"   💡 Market UNDERPRICES YES by {abs(ineff['inefficiency'])*100:.1f}%")
            print()
    else:
        print("   ✨ No significant price-based inefficiencies detected\n")
    
    print("="*70)
    
    return inefficiencies, binary_opps


async def main():
    """Main scanner."""
    print("\n" + "="*70)
    print("  🚀 POLYMARKET CRYPTO INEFFICIENCY SCANNER")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    # Get live prices
    live_prices = await get_live_crypto_prices()
    
    if not live_prices:
        print("❌ Could not fetch live prices")
        return
    
    # Get Polymarket markets
    markets = await get_polymarket_crypto_markets()
    
    print(f"✅ Found {len(markets)} crypto-related markets")
    
    # Analyze
    inefficiencies, binary_opps = await analyze_crypto_inefficiencies(live_prices, markets)
    
    # Summary
    print("\n📋 SUMMARY")
    print("="*70)
    print(f"   Markets analyzed: {len(markets)}")
    print(f"   Binary arbitrage opportunities: {len([o for o in binary_opps if o['edge_pct'] > 0])}")
    print(f"   Price inefficiencies found: {len(inefficiencies)}")
    
    if binary_opps and any(o["edge_pct"] > 0 for o in binary_opps):
        print("\n   ⚡ ARBITRAGE OPPORTUNITIES DETECTED!")
        print("   Buy both YES and NO for guaranteed profit.")
    elif inefficiencies:
        print("\n   💡 Some price inefficiencies detected - may be worth monitoring")
    else:
        print("\n   ✨ Markets are efficiently priced")
    
    print("="*70)


if __name__ == "__main__":
    asyncio.run(main())

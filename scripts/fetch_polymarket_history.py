#!/usr/bin/env python3
"""
Fetch Historical Polymarket Data for Crypto Markets.

Pulls 1-minute resolution price history from Polymarket CLOB API
for all crypto-related markets (BTC, ETH, SOL).

Output: CSV files with timestamp, price, and market info.
"""

import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiohttp


# Polymarket API endpoints
CLOB_BASE_URL = "https://clob.polymarket.com"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

# Output directory
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "polymarket_history"


async def fetch_crypto_markets(session: aiohttp.ClientSession, include_closed: bool = True) -> list[dict]:
    """
    Fetch crypto-related markets from Polymarket Gamma API.
    
    Args:
        session: aiohttp session
        include_closed: Also fetch closed/resolved markets for historical data
    
    Returns:
        List of market dicts with token info
    """
    import re
    markets = []
    seen_ids = set()
    
    # Strict crypto patterns
    crypto_patterns = [
        r'\bbitcoin\b', r'\bbtc\b', r'\bethereum\b', 
        r'\bsolana\b', r'\bdogecoin\b', r'\bxrp\b', r'megaeth'
    ]
    
    def is_crypto_market(question: str) -> bool:
        q = question.lower()
        return any(re.search(p, q) for p in crypto_patterns)
    
    # Fetch active markets
    for closed in ([False, True] if include_closed else [False]):
        try:
            url = f"{GAMMA_BASE_URL}/markets"
            params = {
                "closed": str(closed).lower(),
                "limit": 500
            }
            if not closed:
                params["active"] = "true"
            
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    for market in data:
                        question = market.get("question", "")
                        condition_id = market.get("conditionId", "")
                        
                        if is_crypto_market(question) and condition_id not in seen_ids:
                            markets.append(market)
                            seen_ids.add(condition_id)
                            
        except Exception as e:
            print(f"Warning: Could not fetch markets (closed={closed}): {e}")
    
    return markets


def parse_tokens(market: dict) -> list[dict]:
    """Parse token IDs and outcomes from market data."""
    tokens = []
    
    clob_token_ids = market.get("clobTokenIds", "")
    outcomes = market.get("outcomes", "")
    outcome_prices = market.get("outcomePrices", "")
    
    # Parse clobTokenIds
    if isinstance(clob_token_ids, str):
        if clob_token_ids.startswith("["):
            try:
                clob_token_ids = json.loads(clob_token_ids)
            except:
                clob_token_ids = clob_token_ids.split(",")
        else:
            clob_token_ids = clob_token_ids.split(",") if clob_token_ids else []
    
    # Parse outcomes
    if isinstance(outcomes, str):
        if outcomes.startswith("["):
            try:
                outcomes = json.loads(outcomes)
            except:
                outcomes = outcomes.split(",")
        else:
            outcomes = outcomes.split(",") if outcomes else []
    
    # Parse prices
    if isinstance(outcome_prices, str):
        if outcome_prices.startswith("["):
            try:
                outcome_prices = json.loads(outcome_prices)
            except:
                outcome_prices = outcome_prices.split(",")
        else:
            outcome_prices = outcome_prices.split(",") if outcome_prices else []
    
    for i, token_id in enumerate(clob_token_ids):
        token_id = str(token_id).strip()
        if not token_id:
            continue
        
        outcome = str(outcomes[i]).strip() if i < len(outcomes) else f"Outcome {i}"
        try:
            price = float(str(outcome_prices[i]).strip()) if i < len(outcome_prices) else 0.0
        except:
            price = 0.0
        
        tokens.append({
            "token_id": token_id,
            "outcome": outcome,
            "price": price
        })
    
    return tokens


async def fetch_price_history(
    session: aiohttp.ClientSession,
    token_id: str,
    interval: str = "max",
    fidelity: int = 1,  # 1 minute resolution
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None
) -> list[dict]:
    """
    Fetch historical price data for a Polymarket token.
    
    Args:
        session: aiohttp session
        token_id: CLOB token ID
        interval: Time interval (1m, 1h, 6h, 1d, 1w, max)
        fidelity: Resolution in minutes (1 = 1 minute)
        start_ts: Start timestamp (Unix)
        end_ts: End timestamp (Unix)
    
    Returns:
        List of {timestamp, price} dicts
    """
    url = f"{CLOB_BASE_URL}/prices-history"
    
    params = {
        "market": token_id,
        "fidelity": fidelity,
    }
    
    if start_ts and end_ts:
        params["startTs"] = start_ts
        params["endTs"] = end_ts
    else:
        params["interval"] = interval
    
    try:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                history = data.get("history", [])
                
                # Convert to list of dicts with readable timestamps
                result = []
                for point in history:
                    ts = point.get("t", 0)
                    price = point.get("p", 0)
                    result.append({
                        "timestamp": ts,
                        "datetime": datetime.fromtimestamp(ts).isoformat() if ts else "",
                        "price": price
                    })
                
                return result
            else:
                print(f"  Error fetching {token_id}: HTTP {resp.status}")
                return []
                
    except Exception as e:
        print(f"  Error fetching {token_id}: {e}")
        return []


async def fetch_order_book(
    session: aiohttp.ClientSession,
    token_id: str
) -> dict:
    """
    Fetch current order book for a token.
    
    Args:
        session: aiohttp session
        token_id: CLOB token ID
    
    Returns:
        Order book data with bids/asks
    """
    url = f"{CLOB_BASE_URL}/book"
    
    try:
        async with session.get(url, params={"token_id": token_id}) as resp:
            if resp.status == 200:
                return await resp.json()
            return {}
    except Exception as e:
        print(f"  Error fetching order book: {e}")
        return {}


def get_crypto_from_question(question: str) -> Optional[str]:
    """
    Determine which crypto the market is about.
    Uses strict matching to avoid false positives.
    Returns None if not a crypto market.
    """
    import re
    q = question.lower()
    
    # Strict patterns - must be standalone words
    patterns = [
        (r'\bbitcoin\b', 'BTC'),
        (r'\bbtc\b', 'BTC'),
        (r'\bethereum\b', 'ETH'),
        (r'\bsolana\b', 'SOL'),
        (r'\bdogecoin\b', 'DOGE'),
        (r'\bxrp\b', 'XRP'),
        (r'megaeth', 'MEGAETH'),  # L2 chain
    ]
    
    for pattern, label in patterns:
        if re.search(pattern, q):
            return label
    
    return None  # Not a crypto market


async def main():
    """Main function to fetch and save historical data."""
    print("\n" + "=" * 70)
    print("  POLYMARKET CRYPTO HISTORICAL DATA FETCHER")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    async with aiohttp.ClientSession() as session:
        print("\n📡 Fetching crypto markets from Polymarket...")
        raw_markets = await fetch_crypto_markets(session)
        
        print(f"✅ Found {len(raw_markets)} crypto markets\n")
        
        # Filter to only crypto markets
        crypto_markets = []
        by_crypto = {"BTC": [], "ETH": [], "SOL": [], "DOGE": [], "XRP": [], "MEGAETH": []}
        
        for market in raw_markets:
            question = market.get("question", "")
            crypto = get_crypto_from_question(question)
            if crypto:  # Only include actual crypto markets
                by_crypto[crypto].append(market)
                crypto_markets.append((market, crypto))
        
        print("📊 Markets by crypto:")
        for crypto, mkts in by_crypto.items():
            if mkts:
                print(f"   {crypto}: {len(mkts)} markets")
        
        print(f"\n📈 Crypto markets to process: {len(crypto_markets)}")
        
        print("\n" + "-" * 70)
        print("📥 Fetching historical price data (1-min resolution)...")
        print("-" * 70)
        
        all_data = []
        
        for market, crypto in crypto_markets:
            question = market.get("question", "")
            condition_id = market.get("conditionId", "")
            tokens = parse_tokens(market)
            
            question_short = question[:50] + "..." if len(question) > 50 else question
            
            print(f"\n🔄 {crypto}: {question_short}")
            
            for token in tokens:
                token_id = token["token_id"]
                outcome = token["outcome"]
                
                print(f"   Token: {outcome} ({token_id[:16]}...)")
                
                # Fetch price history
                history = await fetch_price_history(
                    session,
                    token_id,
                    interval="max",
                    fidelity=1  # 1 minute
                )
                
                if history:
                    print(f"   ✅ Got {len(history)} data points")
                    
                    # Add metadata to each row
                    for point in history:
                        point["crypto"] = crypto
                        point["market_question"] = question
                        point["condition_id"] = condition_id
                        point["token_id"] = token_id
                        point["outcome"] = outcome
                    
                    all_data.extend(history)
                else:
                    print(f"   ⚠️ No history available")
                
                # Fetch current order book
                order_book = await fetch_order_book(session, token_id)
                if order_book:
                    bids = order_book.get("bids", [])
                    asks = order_book.get("asks", [])
                    
                    if bids or asks:
                        best_bid = float(bids[0]["price"]) if bids else 0
                        best_ask = float(asks[0]["price"]) if asks else 0
                        spread = best_ask - best_bid if best_bid and best_ask else 0
                        
                        print(f"   📈 Order Book: Bid ${best_bid:.3f} | Ask ${best_ask:.3f} | Spread ${spread:.3f}")
                
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.2)
        
        # Save to CSV
        if all_data:
            # Sort by timestamp
            all_data.sort(key=lambda x: (x.get("timestamp", 0), x.get("token_id", "")))
            
            # Save combined file
            combined_file = OUTPUT_DIR / f"polymarket_crypto_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            
            fieldnames = ["timestamp", "datetime", "crypto", "outcome", "price", 
                          "market_question", "condition_id", "token_id"]
            
            with open(combined_file, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(all_data)
            
            print(f"\n✅ Saved {len(all_data)} rows to: {combined_file}")
            
            # Save per-crypto files
            for crypto in ["BTC", "ETH", "SOL"]:
                crypto_data = [d for d in all_data if d.get("crypto") == crypto]
                if crypto_data:
                    crypto_file = OUTPUT_DIR / f"polymarket_{crypto.lower()}_history.csv"
                    with open(crypto_file, "w", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                        writer.writeheader()
                        writer.writerows(crypto_data)
                    print(f"   {crypto}: {len(crypto_data)} rows -> {crypto_file.name}")
            
            # Summary
            print("\n" + "=" * 70)
            print("📋 SUMMARY")
            print("=" * 70)
            print(f"   Total crypto markets processed: {len(crypto_markets)}")
            print(f"   Total data points: {len(all_data)}")
            
            if all_data:
                min_ts = min(d["timestamp"] for d in all_data if d.get("timestamp"))
                max_ts = max(d["timestamp"] for d in all_data if d.get("timestamp"))
                print(f"   Date range: {datetime.fromtimestamp(min_ts)} to {datetime.fromtimestamp(max_ts)}")
            
            print(f"   Output directory: {OUTPUT_DIR}")
            print("=" * 70)
        else:
            print("\n⚠️ No historical data available")
    
    return all_data


if __name__ == "__main__":
    asyncio.run(main())

"""
Adaptive Timing Optimizer

Uses Thompson Sampling to learn optimal entry timing for 15-minute markets.
Tracks performance by time bucket and adapts betting strategy over time.
"""
import json
import random
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
import numpy as np


@dataclass
class BucketStats:
    """Statistics for a time bucket."""
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_wagered: float = 0.0
    
    @property
    def total_bets(self) -> int:
        return self.wins + self.losses
    
    @property
    def win_rate(self) -> float:
        if self.total_bets == 0:
            return 0.5  # Prior assumption
        return self.wins / self.total_bets
    
    @property
    def roi(self) -> float:
        if self.total_wagered == 0:
            return 0.0
        return self.total_pnl / self.total_wagered


@dataclass
class TimingDecision:
    """Result of timing optimization."""
    should_bet: bool
    bucket: str
    confidence: float
    sampled_win_rate: float
    reasoning: str


class TimingOptimizer:
    """
    Self-learning timing optimizer using Thompson Sampling.
    
    Tracks performance by time bucket:
    - 15-25 min: Very early (start of window)
    - 10-15 min: Early
    - 5-10 min: Middle
    - 2-5 min: Late (usually best)
    
    Uses Thompson Sampling (Beta distribution) to balance exploration
    and exploitation. Initially explores all buckets, then converges
    on the best-performing ones.
    """
    
    # Time buckets (in seconds from expiry)
    # Focus on the optimal window: 1-7 minutes before expiry
    # Betting closer to expiry = more accurate predictions
    BUCKETS = {
        "1-2min": (60, 120),    # Very late - highest accuracy but risky
        "2-3min": (120, 180),   # Sweet spot
        "3-5min": (180, 300),   # Good accuracy
        "5-7min": (300, 420),   # Early but acceptable
    }
    
    # Prior parameters (Beta distribution)
    # Start with weak prior: alpha=1, beta=1 (uniform)
    PRIOR_ALPHA = 1.0
    PRIOR_BETA = 1.0
    
    # Minimum samples before we trust a bucket's performance
    MIN_SAMPLES = 5
    
    # Persistence file
    DATA_FILE = Path(__file__).parent.parent.parent / "data" / "timing_stats.json"
    
    def __init__(self):
        self.buckets: dict[str, BucketStats] = {
            name: BucketStats() for name in self.BUCKETS
        }
        self._load_stats()
    
    def _load_stats(self):
        """Load saved stats from file."""
        try:
            if self.DATA_FILE.exists():
                with open(self.DATA_FILE, "r") as f:
                    data = json.load(f)
                    for name, stats in data.items():
                        if name in self.buckets:
                            self.buckets[name] = BucketStats(**stats)
        except Exception:
            pass  # Start fresh if file is corrupted
    
    def _save_stats(self):
        """Save stats to file."""
        try:
            self.DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.DATA_FILE, "w") as f:
                data = {name: asdict(stats) for name, stats in self.buckets.items()}
                json.dump(data, f, indent=2)
        except Exception:
            pass  # Non-critical
    
    def get_bucket(self, time_left_seconds: float) -> Optional[str]:
        """Get the bucket name for a given time to expiry."""
        for name, (min_time, max_time) in self.BUCKETS.items():
            if min_time <= time_left_seconds < max_time:
                return name
        return None
    
    def sample_win_rate(self, bucket: str) -> float:
        """
        Sample from Beta posterior distribution.
        
        Thompson Sampling: Instead of using point estimate of win rate,
        we sample from the posterior distribution. This naturally balances
        exploration (trying uncertain buckets) and exploitation (using
        known good buckets).
        
        Beta(alpha + wins, beta + losses) is the posterior after observing
        'wins' successes and 'losses' failures with Beta(alpha, beta) prior.
        """
        stats = self.buckets[bucket]
        
        # Posterior parameters
        alpha = self.PRIOR_ALPHA + stats.wins
        beta = self.PRIOR_BETA + stats.losses
        
        # Sample from Beta distribution
        # Use numpy for proper Beta sampling
        try:
            sampled = np.random.beta(alpha, beta)
        except:
            # Fallback if numpy not available
            sampled = random.betavariate(alpha, beta)
        
        return sampled
    
    def get_bucket_confidence(self, bucket: str) -> float:
        """
        Calculate confidence in bucket's performance estimate.
        
        More samples = higher confidence.
        Returns 0-1 scale.
        """
        stats = self.buckets[bucket]
        # Confidence grows with sqrt of sample size
        return min(1.0, math.sqrt(stats.total_bets / 20))
    
    def should_bet_now(self, time_left_seconds: float) -> TimingDecision:
        """
        Decide if we should bet at current time using Thompson Sampling.
        
        Strategy:
        1. Identify current bucket
        2. Sample win rates for all buckets
        3. If current bucket has highest sampled rate, bet now
        4. Otherwise, wait for better timing
        
        Note: In exploration phase (few samples), we're more permissive.
        """
        current_bucket = self.get_bucket(time_left_seconds)
        
        if current_bucket is None:
            return TimingDecision(
                should_bet=False,
                bucket="none",
                confidence=0,
                sampled_win_rate=0,
                reasoning=f"Time {time_left_seconds/60:.1f}m not in any bucket"
            )
        
        # Sample win rates for all buckets
        sampled_rates = {name: self.sample_win_rate(name) for name in self.BUCKETS}
        
        # Find best bucket
        best_bucket = max(sampled_rates, key=sampled_rates.get)
        current_sampled = sampled_rates[current_bucket]
        best_sampled = sampled_rates[best_bucket]
        
        # Get confidence in current bucket
        confidence = self.get_bucket_confidence(current_bucket)
        
        # Count total samples across all buckets
        total_samples = sum(s.total_bets for s in self.buckets.values())
        
        # In exploration phase (< 20 total samples), be more permissive
        # This ensures we collect data across all buckets
        exploration_mode = total_samples < 20
        
        # Should we bet now or wait?
        # Bet if current bucket is best OR if difference is small OR in exploration
        margin = best_sampled - current_sampled
        should_bet = current_bucket == best_bucket or margin < 0.10 or exploration_mode
        
        # Build reasoning
        stats = self.buckets[current_bucket]
        if stats.total_bets >= self.MIN_SAMPLES:
            actual_wr = f"Actual WR: {stats.win_rate*100:.0f}%"
        else:
            actual_wr = f"Only {stats.total_bets} samples"
        
        if exploration_mode:
            reasoning = f"Exploring {current_bucket} (sampled {current_sampled*100:.0f}%) | {actual_wr}"
        elif should_bet:
            reasoning = f"Bucket {current_bucket} looks good (sampled {current_sampled*100:.0f}%) | {actual_wr}"
        else:
            reasoning = f"Wait for {best_bucket} (sampled {best_sampled*100:.0f}% vs {current_sampled*100:.0f}%) | {actual_wr}"
        
        return TimingDecision(
            should_bet=should_bet,
            bucket=current_bucket,
            confidence=confidence,
            sampled_win_rate=current_sampled,
            reasoning=reasoning
        )
    
    def record_result(
        self, 
        time_left_at_entry: float, 
        won: bool, 
        pnl: float,
        wagered: float
    ):
        """Record a bet result to update bucket statistics."""
        bucket = self.get_bucket(time_left_at_entry)
        if bucket is None:
            return
        
        stats = self.buckets[bucket]
        if won:
            stats.wins += 1
        else:
            stats.losses += 1
        stats.total_pnl += pnl
        stats.total_wagered += wagered
        
        self._save_stats()
    
    def get_summary(self) -> dict:
        """Get summary of all bucket performance."""
        summary = {}
        for name, stats in self.buckets.items():
            summary[name] = {
                "bets": stats.total_bets,
                "wins": stats.wins,
                "losses": stats.losses,
                "win_rate": f"{stats.win_rate*100:.1f}%",
                "roi": f"{stats.roi*100:+.1f}%",
                "pnl": f"${stats.total_pnl:+.2f}",
            }
        return summary
    
    def get_best_bucket(self) -> tuple[str, float]:
        """Get the empirically best bucket based on ROI."""
        best_bucket = None
        best_roi = float('-inf')
        
        for name, stats in self.buckets.items():
            if stats.total_bets >= self.MIN_SAMPLES:
                if stats.roi > best_roi:
                    best_roi = stats.roi
                    best_bucket = name
        
        return best_bucket or "2-5min", best_roi

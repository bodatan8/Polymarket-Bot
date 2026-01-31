"""
Probability Calibrator

Tracks predicted probabilities vs actual outcomes to calibrate future predictions.
The key insight: If we predict 65% and win only 55% of the time, we're overconfident.

Uses bucketed calibration:
- Group predictions by probability range (e.g., 60-65%)
- Track actual win rate for each bucket
- Apply correction factor to future predictions in that bucket
"""
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CalibrationBucket:
    """Statistics for a probability bucket."""
    predictions: int = 0
    correct: int = 0
    total_pnl: float = 0.0
    
    @property
    def actual_win_rate(self) -> float:
        """Actual win rate for this bucket."""
        if self.predictions == 0:
            return 0.5  # No data, assume neutral
        return self.correct / self.predictions
    
    @property
    def has_enough_data(self) -> bool:
        """Do we have enough data to trust this bucket?"""
        return self.predictions >= 10


@dataclass
class CalibrationResult:
    """Result of calibration lookup."""
    original_prob: float
    calibrated_prob: float
    bucket: str
    adjustment: float
    confidence: float  # How confident in calibration (based on sample size)
    reasoning: str


class ProbabilityCalibrator:
    """
    Calibrates predicted probabilities based on historical accuracy.
    
    The Problem:
    If we predict "60% chance of winning" but historically we only
    win 52% when predicting 60%, we're overconfident by 8%.
    
    The Solution:
    Track predictions by bucket, measure actual win rates, and adjust
    future predictions to match historical accuracy.
    
    Buckets:
    - 50-55%: Near coin flip
    - 55-60%: Slight edge
    - 60-65%: Moderate edge
    - 65-70%: Good edge
    - 70-75%: Strong edge
    - 75%+: Very strong edge
    """
    
    # Probability buckets (lower bound, upper bound, name)
    BUCKETS = [
        (0.50, 0.55, "50-55%"),
        (0.55, 0.60, "55-60%"),
        (0.60, 0.65, "60-65%"),
        (0.65, 0.70, "65-70%"),
        (0.70, 0.75, "70-75%"),
        (0.75, 1.00, "75%+"),
    ]
    
    # Minimum samples before we trust calibration
    MIN_SAMPLES = 10
    
    # Maximum adjustment (don't over-correct)
    MAX_ADJUSTMENT = 0.15  # 15% max shift
    
    # Persistence file
    DATA_FILE = Path(__file__).parent.parent.parent / "data" / "calibration_stats.json"
    
    def __init__(self):
        # Calibration data by bucket
        self.buckets: Dict[str, CalibrationBucket] = {
            name: CalibrationBucket() 
            for _, _, name in self.BUCKETS
        }
        self._load_stats()
    
    def _load_stats(self):
        """Load saved calibration data."""
        try:
            if self.DATA_FILE.exists():
                with open(self.DATA_FILE, "r") as f:
                    data = json.load(f)
                    for name, stats in data.items():
                        if name in self.buckets:
                            self.buckets[name] = CalibrationBucket(**stats)
        except Exception as e:
            logger.debug(f"Could not load calibration stats: {e}")
    
    def _save_stats(self):
        """Save calibration data to file."""
        try:
            self.DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self.DATA_FILE, "w") as f:
                data = {name: asdict(bucket) for name, bucket in self.buckets.items()}
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.debug(f"Could not save calibration stats: {e}")
    
    def _get_bucket_name(self, probability: float) -> str:
        """Get the bucket name for a probability."""
        prob = abs(probability)  # Handle negative (down) probabilities
        prob = max(0.50, min(1.0, prob))  # Clamp to valid range
        
        for lower, upper, name in self.BUCKETS:
            if lower <= prob < upper:
                return name
        
        return "75%+"  # Default to highest bucket
    
    def calibrate(self, predicted_probability: float) -> CalibrationResult:
        """
        Calibrate a predicted probability based on historical accuracy.
        
        Args:
            predicted_probability: Our predicted win probability (0.5 to 1.0)
        
        Returns:
            CalibrationResult with adjusted probability
        """
        bucket_name = self._get_bucket_name(predicted_probability)
        bucket = self.buckets[bucket_name]
        
        if not bucket.has_enough_data:
            # Not enough data, return original with low confidence
            return CalibrationResult(
                original_prob=predicted_probability,
                calibrated_prob=predicted_probability,
                bucket=bucket_name,
                adjustment=0.0,
                confidence=0.3,
                reasoning=f"Bucket {bucket_name}: only {bucket.predictions} samples (need {self.MIN_SAMPLES})"
            )
        
        # Calculate expected midpoint of bucket
        bucket_midpoint = predicted_probability
        for lower, upper, name in self.BUCKETS:
            if name == bucket_name:
                bucket_midpoint = (lower + upper) / 2
                break
        
        # How far off are we?
        # If we predict 62.5% (midpoint of 60-65%) but actually win 55%,
        # adjustment = 55% - 62.5% = -7.5%
        actual_rate = bucket.actual_win_rate
        adjustment = actual_rate - bucket_midpoint
        
        # Clamp adjustment
        adjustment = max(-self.MAX_ADJUSTMENT, min(self.MAX_ADJUSTMENT, adjustment))
        
        # Apply adjustment
        calibrated = predicted_probability + adjustment
        calibrated = max(0.50, min(0.95, calibrated))  # Keep in valid range
        
        # Confidence based on sample size
        confidence = min(1.0, bucket.predictions / 50)  # Full confidence at 50 samples
        
        # Blend toward calibrated value based on confidence
        # Low confidence = stay closer to original
        final_prob = predicted_probability + (adjustment * confidence)
        final_prob = max(0.50, min(0.95, final_prob))
        
        direction = "down" if adjustment < 0 else "up"
        reasoning = (
            f"Bucket {bucket_name}: predicted {predicted_probability:.0%}, "
            f"actual {actual_rate:.0%} ({bucket.predictions} samples) → "
            f"adjust {direction} {abs(adjustment)*100:.1f}%"
        )
        
        return CalibrationResult(
            original_prob=predicted_probability,
            calibrated_prob=final_prob,
            bucket=bucket_name,
            adjustment=adjustment,
            confidence=confidence,
            reasoning=reasoning
        )
    
    def record_outcome(
        self, 
        predicted_probability: float,
        won: bool,
        pnl: float = 0.0
    ):
        """
        Record the outcome of a prediction for calibration.
        
        Args:
            predicted_probability: What we predicted (0.5 to 1.0)
            won: Did we win the bet?
            pnl: Profit/loss from this trade
        """
        bucket_name = self._get_bucket_name(predicted_probability)
        bucket = self.buckets[bucket_name]
        
        bucket.predictions += 1
        if won:
            bucket.correct += 1
        bucket.total_pnl += pnl
        
        self._save_stats()
        
        logger.debug(
            f"Calibration: {bucket_name} now {bucket.correct}/{bucket.predictions} "
            f"({bucket.actual_win_rate:.1%})"
        )
    
    def get_summary(self) -> Dict[str, Dict]:
        """Get summary of calibration state."""
        summary = {}
        for name, bucket in self.buckets.items():
            if bucket.predictions > 0:
                # Calculate expected midpoint
                expected = 0.5
                for lower, upper, n in self.BUCKETS:
                    if n == name:
                        expected = (lower + upper) / 2
                        break
                
                deviation = bucket.actual_win_rate - expected
                summary[name] = {
                    "predictions": bucket.predictions,
                    "correct": bucket.correct,
                    "actual_rate": f"{bucket.actual_win_rate:.1%}",
                    "expected_rate": f"{expected:.1%}",
                    "deviation": f"{deviation:+.1%}",
                    "pnl": f"${bucket.total_pnl:+.2f}",
                    "calibrated": bucket.has_enough_data,
                }
        return summary
    
    def get_overall_accuracy(self) -> Tuple[float, int]:
        """Get overall prediction accuracy across all buckets."""
        total_predictions = sum(b.predictions for b in self.buckets.values())
        total_correct = sum(b.correct for b in self.buckets.values())
        
        if total_predictions == 0:
            return 0.5, 0
        
        return total_correct / total_predictions, total_predictions
    
    def is_calibrated(self) -> bool:
        """Do we have enough data for meaningful calibration?"""
        total_predictions = sum(b.predictions for b in self.buckets.values())
        return total_predictions >= 30

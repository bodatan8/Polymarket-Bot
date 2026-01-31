"""
Probability Engine - Centralized Probability and Edge Calculations

All mathematical logic for probability estimation, calibration, and expected value
calculation in one place. Easy to test, debug, and maintain.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from src.market_maker.models import FifteenMinMarket
from src.signals.aggregator import AggregatedSignal
from src.signals.price_feed import MomentumData
from src.prediction.calibrator import ProbabilityCalibrator

logger = logging.getLogger(__name__)


@dataclass
class ProbabilityResult:
    """Result of probability calculation."""
    prob_up: float
    prob_down: float
    side: str  # "Up" or "Down"
    edge: float
    true_probability: float  # Probability of chosen side
    reasoning: str


class ProbabilityEngine:
    """
    Centralized probability and edge calculation engine.
    
    Responsibilities:
    - Convert signals to probabilities
    - Apply calibration
    - Calculate expected value (CORRECT formula)
    - Calculate edge
    """
    
    def __init__(self, calibrator: ProbabilityCalibrator):
        self.calibrator = calibrator
    
    def calculate(
        self,
        market: FifteenMinMarket,
        aggregated_signal: AggregatedSignal,
        momentum: Optional[MomentumData] = None
    ) -> ProbabilityResult:
        """
        Calculate probability and edge for a market.
        
        Flow:
        1. Get fair market probabilities (remove vig)
        2. Adjust based on signals
        3. Apply calibration
        4. Calculate expected value (CORRECT formula)
        5. Choose best side and calculate edge
        
        Returns:
            ProbabilityResult with probabilities, side, edge, and reasoning
        """
        # Step 1: Get fair probabilities (remove vig)
        total = market.up_price + market.down_price
        fair_up = market.up_price / total if total > 0 else 0.5
        fair_down = market.down_price / total if total > 0 else 0.5
        
        # Step 2: Adjust probabilities based on signals
        prob_adjustment = aggregated_signal.probability_adjustment
        adjusted_prob_shift = prob_adjustment * (0.5 + aggregated_signal.confidence * 0.5)
        
        # Apply signal adjustment
        raw_up = fair_up + adjusted_prob_shift
        raw_down = fair_down - adjusted_prob_shift
        
        # Clamp to reasonable range (but less restrictive)
        raw_up = max(0.20, min(0.90, raw_up))
        raw_down = max(0.20, min(0.90, raw_down))
        
        # Ensure probabilities sum to 1.0
        prob_sum = raw_up + raw_down
        if prob_sum > 0:
            raw_up = raw_up / prob_sum
            raw_down = raw_down / prob_sum
        else:
            # Fallback if both are zero
            raw_up = 0.5
            raw_down = 0.5
        
        # Step 3: Apply calibration
        if raw_up > raw_down:
            calibration = self.calibrator.calibrate(raw_up)
            true_up = calibration.calibrated_prob
            true_down = 1 - true_up
        else:
            calibration = self.calibrator.calibrate(raw_down)
            true_down = calibration.calibrated_prob
            true_up = 1 - true_down
        
        # Blend if low confidence
        if calibration.confidence < 0.5:
            blend = calibration.confidence
            true_up = raw_up * (1 - blend) + true_up * blend
            true_down = raw_down * (1 - blend) + true_down * blend
        
        # Ensure probabilities still sum to 1.0 after calibration
        prob_sum = true_up + true_down
        if prob_sum > 0:
            true_up = true_up / prob_sum
            true_down = true_down / prob_sum
        
        # Step 4: Calculate expected value (CORRECT FORMULA)
        # For binary market: bet $1 at price p, get 1/p shares
        # If win: profit = (1/p) * 1 - 1 = (1-p)/p
        # If lose: loss = -1
        # EV = prob_win * (1-p)/p - (1-prob_win) * 1
        # EV = prob_win/p - 1
        up_ev = self._calculate_expected_value(true_up, market.up_price)
        down_ev = self._calculate_expected_value(true_down, market.down_price)
        
        # Step 5: Choose best side
        if up_ev > down_ev:
            side = "Up"
            edge = up_ev
            true_prob = true_up
        else:
            side = "Down"
            edge = down_ev
            true_prob = true_down
        
        # Build reasoning
        signal_dir = aggregated_signal.direction.value
        momentum_str = f" | Mom: {momentum.trend_strength*100:+.2f}%" if momentum else ""
        reasoning = (
            f"Signal: {signal_dir} ({aggregated_signal.strength:.0%}){momentum_str} | "
            f"Cal: {calibration.bucket} | "
            f"{aggregated_signal.reasoning}"
        )
        
        # Log for debugging (DEBUG level - can be enabled for troubleshooting)
        logger.debug(
            f"PROB_ENGINE: market=({market.up_price:.1%}/{market.down_price:.1%}) "
            f"fair=({fair_up:.1%}/{fair_down:.1%}) "
            f"signal_adj={adjusted_prob_shift:+.2%} "
            f"raw=({raw_up:.1%}/{raw_down:.1%}) "
            f"calibrated=({true_up:.1%}/{true_down:.1%}) "
            f"EV=(up:{up_ev:.2%}/down:{down_ev:.2%}) "
            f"→ {side} edge={edge:.2%}"
        )
        
        return ProbabilityResult(
            prob_up=true_up,
            prob_down=true_down,
            side=side,
            edge=edge,
            true_probability=true_prob,
            reasoning=reasoning
        )
    
    @staticmethod
    def _calculate_expected_value(prob_win: float, market_price: float) -> float:
        """
        Calculate expected value for binary market.
        
        CORRECT FORMULA:
        - Bet $1 at price p, receive 1/p shares
        - If win: profit = (1/p) * 1 - 1 = (1-p)/p
        - If lose: loss = -1
        - EV = prob_win * (1-p)/p - (1-prob_win) * 1
        - EV = prob_win/p - 1
        
        Args:
            prob_win: Probability of winning (0-1)
            market_price: Market price for this side (0-1)
        
        Returns:
            Expected value as decimal (e.g., 0.02 = 2% edge)
        """
        if market_price <= 0 or market_price >= 1:
            return -1.0  # Invalid price, negative EV
        
        if prob_win <= 0:
            return -1.0  # Zero win probability, guaranteed loss
        
        # CORRECT FORMULA: EV = prob_win / price - 1
        ev = prob_win / market_price - 1.0
        
        return ev

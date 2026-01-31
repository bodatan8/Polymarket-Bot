"""
Unified Probability Calculator

Provides a clean interface for probability calculation that combines:
- Market prices (efficient market hypothesis)
- Quant models (stochastic processes)
- Signals (momentum, volume, etc.)
- Calibration (historical accuracy)
"""
from dataclasses import dataclass
from typing import Optional

from typing import Optional
from src.signals.aggregator import AggregatedSignal
from src.signals.price_feed import MomentumData
from src.prediction.quant_models import QuantProbabilityCalculator, ProbabilityDistribution
from src.prediction.calibrator import ProbabilityCalibrator


@dataclass
class ProbabilityResult:
    """Final probability calculation result."""
    prob_up: float
    prob_down: float
    side: str  # "Up" or "Down"
    edge: float
    confidence: float
    reasoning: str


class UnifiedProbabilityCalculator:
    """
    Unified probability calculator that combines all methods.
    
    Flow:
    1. Get quant probability (stochastic models + Bayesian)
    2. Blend with market prices based on confidence
    3. Apply calibration
    4. Calculate expected value
    """
    
    def __init__(
        self,
        quant_calculator: QuantProbabilityCalculator,
        calibrator: ProbabilityCalibrator
    ):
        self.quant_calculator = quant_calculator
        self.calibrator = calibrator
    
    def calculate(
        self,
        market_up_price: float,
        market_down_price: float,
        market_asset: str,
        aggregated_signal: AggregatedSignal,
        momentum: Optional[MomentumData],
        crypto_prices: dict[str, float],
        time_to_expiry: float
    ) -> ProbabilityResult:
        """
        Calculate final probability and edge.
        
        Returns:
            ProbabilityResult with probabilities, side, edge, and reasoning
        """
        # Get current crypto price
        current_crypto_price = crypto_prices.get(market_asset, 0)
        
        # Get fair market probabilities (remove vig)
        total = market_up_price + market_down_price
        fair_up = market_up_price / total if total > 0 else 0.5
        fair_down = market_down_price / total if total > 0 else 0.5
        
        # Calculate quant probability if we have price data
        if current_crypto_price > 0:
            # Convert signal to probability
            signal_value = aggregated_signal.strength * (
                1.0 if aggregated_signal.direction.value == "up" else -1.0
            )
            signal_prob_up = 0.5 + signal_value * 0.3  # Map to 0.2-0.8 range
            
            # Get quant probability
            quant_dist = self.quant_calculator.calculate_probability(
                asset=market_asset,
                current_price=current_crypto_price,
                time_left_seconds=time_to_expiry,
                momentum_data=momentum,
                signal_probability=signal_prob_up,
                signal_strength=aggregated_signal.strength,
                signal_confidence=aggregated_signal.confidence
            )
            
            quant_prob_up = quant_dist.mean
            quant_prob_down = 1 - quant_prob_up
            
            # Blend quant with market price based on confidence
            blend_factor = quant_dist.confidence
            blended_up = blend_factor * quant_prob_up + (1 - blend_factor) * fair_up
            blended_down = blend_factor * quant_prob_down + (1 - blend_factor) * fair_down
            
            quant_info = f"Quant: {quant_prob_up:.0%} (conf: {quant_dist.confidence:.0%})"
        else:
            # No price data - use market price only
            blended_up = fair_up
            blended_down = fair_down
            quant_info = "No price data"
        
        # Apply calibration
        if blended_up > blended_down:
            calibration = self.calibrator.calibrate(blended_up)
            calibrated_up = calibration.calibrated_prob
            calibrated_down = 1 - calibrated_up
        else:
            calibration = self.calibrator.calibrate(blended_down)
            calibrated_down = calibration.calibrated_prob
            calibrated_up = 1 - calibrated_down
        
        # Final blend with calibration
        cal_blend = calibration.confidence
        final_up = cal_blend * calibrated_up + (1 - cal_blend) * blended_up
        final_down = cal_blend * calibrated_down + (1 - cal_blend) * blended_down
        
        # Normalize probabilities
        total_prob = final_up + final_down
        if total_prob > 0:
            final_up = final_up / total_prob
            final_down = final_down / total_prob
        
        # Calculate expected value
        up_ev = self._calculate_ev(final_up, market_up_price)
        down_ev = self._calculate_ev(final_down, market_down_price)
        
        # Choose best side
        if up_ev > down_ev:
            side = "Up"
            edge = up_ev
            true_prob = final_up
        else:
            side = "Down"
            edge = down_ev
            true_prob = final_down
        
        # Build reasoning
        signal_dir = aggregated_signal.direction.value
        momentum_str = (
            f" | Mom: {momentum.trend_strength*100:+.2f}%" 
            if momentum else ""
        )
        reasoning = (
            f"Signal: {signal_dir} ({aggregated_signal.strength:.0%}){momentum_str} | "
            f"{quant_info} | "
            f"Cal: {calibration.bucket} | "
            f"{aggregated_signal.reasoning}"
        )
        
        confidence = (
            quant_dist.confidence if current_crypto_price > 0 
            else 0.3
        ) * calibration.confidence
        
        return ProbabilityResult(
            prob_up=final_up,
            prob_down=final_down,
            side=side,
            edge=edge,
            confidence=confidence,
            reasoning=reasoning
        )
    
    @staticmethod
    def _calculate_ev(prob: float, market_price: float) -> float:
        """
        Calculate expected value.
        
        EV = P(win) × payout - P(lose) × cost
        payout = 1 - market_price (if we win)
        cost = market_price (if we lose)
        """
        return prob * (1 - market_price) - (1 - prob) * market_price

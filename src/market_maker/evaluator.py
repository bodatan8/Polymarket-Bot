"""
Market Evaluation System - Filter Chain Pattern

Clean, testable, and maintainable evaluation pipeline.
Each filter checks one condition independently.
"""
from dataclasses import dataclass
from typing import Optional, List, Callable
from abc import ABC, abstractmethod

from src.market_maker.config import TradingConfig
from src.signals.price_feed import RealTimePriceFeed, MomentumData
from src.signals.volume_detector import VolumeDetector
from src.signals.aggregator import SignalAggregator, AggregatedSignal
from src.prediction.dynamic_edge import DynamicEdgeCalculator
from src.prediction.calibrator import ProbabilityCalibrator
from src.learning.timing_optimizer import TimingOptimizer, TimingDecision
from src.market_maker.models import FifteenMinMarket
from src.market_maker.probability_engine import ProbabilityEngine


@dataclass
class EvaluationContext:
    """Context passed through the evaluation pipeline."""
    market: FifteenMinMarket
    time_to_expiry: float
    crypto_prices: dict[str, float]
    momentum: Optional[MomentumData] = None
    aggregated_signal: Optional[AggregatedSignal] = None
    side: str = ""
    edge: float = 0.0
    true_probability: float = 0.0
    reasoning: List[str] = None
    
    def __post_init__(self):
        if self.reasoning is None:
            self.reasoning = []


@dataclass
class FilterResult:
    """Result of a filter check."""
    passed: bool
    reason: str = ""


class MarketFilter(ABC):
    """Base class for market evaluation filters."""
    
    @abstractmethod
    def check(self, ctx: EvaluationContext) -> FilterResult:
        """Check if market passes this filter."""
        pass


class TimingWindowFilter(MarketFilter):
    """Filter: Check if market is in valid timing window."""
    
    def __init__(self, cfg: TradingConfig):
        self.cfg = cfg
    
    def check(self, ctx: EvaluationContext) -> FilterResult:
        if self.cfg.high_frequency_mode:
            min_time, max_time = self.cfg.hf_timing_window
        else:
            min_time, max_time = self.cfg.quality_timing_window
        
        if not (min_time <= ctx.time_to_expiry <= max_time):
            return FilterResult(
                passed=False,
                reason=f"Time {ctx.time_to_expiry:.0f}s outside window [{min_time}, {max_time}]"
            )
        return FilterResult(passed=True)


class TimingOptimizerFilter(MarketFilter):
    """Filter: Check timing optimizer (skipped in HF mode)."""
    
    def __init__(self, cfg: TradingConfig, timing_optimizer: TimingOptimizer):
        self.cfg = cfg
        self.timing_optimizer = timing_optimizer
    
    def check(self, ctx: EvaluationContext) -> FilterResult:
        if self.cfg.high_frequency_mode:
            # HF mode bypasses timing optimizer
            return FilterResult(passed=True)
        
        decision = self.timing_optimizer.should_bet_now(ctx.time_to_expiry)
        if not decision.should_bet:
            return FilterResult(passed=False, reason=f"Timing: {decision.reasoning}")
        return FilterResult(passed=True)


class VolumeFilter(MarketFilter):
    """Filter: Check volume requirements."""
    
    def __init__(self, cfg: TradingConfig, volume_detector: VolumeDetector):
        self.cfg = cfg
        self.volume_detector = volume_detector
    
    def check(self, ctx: EvaluationContext) -> FilterResult:
        # Binance volume check
        can_trade, reason = self.volume_detector.should_trade(ctx.market.asset)
        if not can_trade:
            return FilterResult(passed=False, reason=f"Volume: {reason}")
        
        # Polymarket volume check (HF mode only)
        if self.cfg.high_frequency_mode:
            if ctx.market.volume < self.cfg.hf_min_polymarket_volume:
                return FilterResult(
                    passed=False,
                    reason=f"Polymarket volume ${ctx.market.volume:.0f} < ${self.cfg.hf_min_polymarket_volume:.0f}"
                )
        
        return FilterResult(passed=True)


class SignalFilter(MarketFilter):
    """Filter: Check signal quality thresholds."""
    
    def __init__(self, cfg: TradingConfig):
        self.cfg = cfg
    
    def check(self, ctx: EvaluationContext) -> FilterResult:
        if not ctx.aggregated_signal:
            return FilterResult(passed=False, reason="No signal available")
        
        if self.cfg.high_frequency_mode:
            # HF mode has lower thresholds
            if ctx.edge < self.cfg.hf_min_edge:
                return FilterResult(
                    passed=False,
                    reason=f"Edge {ctx.edge*100:.1f}% < HF min {self.cfg.hf_min_edge*100:.1f}%"
                )
            
            if ctx.aggregated_signal.confidence < self.cfg.hf_min_confidence:
                return FilterResult(
                    passed=False,
                    reason=f"Confidence {ctx.aggregated_signal.confidence*100:.1f}% < HF min {self.cfg.hf_min_confidence*100:.1f}%"
                )
            
            if ctx.aggregated_signal.strength < self.cfg.hf_min_signal_strength:
                return FilterResult(
                    passed=False,
                    reason=f"Signal strength {ctx.aggregated_signal.strength*100:.1f}% < HF min {self.cfg.hf_min_signal_strength*100:.1f}%"
                )
            
            # Market uncertainty check: reject markets that are too far from 50/50
            # (too certain = one side heavily favored, less opportunity)
            # NOTE: Disabled in HF mode for maximum opportunities
            if not self.cfg.high_frequency_mode:
                market_uncertainty = abs(ctx.market.up_price - 0.5)
                if market_uncertainty > self.cfg.hf_min_market_uncertainty:
                    return FilterResult(
                        passed=False,
                        reason=f"Market too skewed ({ctx.market.up_price*100:.1f}¢/{ctx.market.down_price*100:.1f}¢) - need closer to 50/50"
                    )
        
        return FilterResult(passed=True)


class EdgeRequirementFilter(MarketFilter):
    """Filter: Check if edge meets requirements."""
    
    def __init__(self, cfg: TradingConfig, edge_calculator: DynamicEdgeCalculator):
        self.cfg = cfg
        self.edge_calculator = edge_calculator
    
    def check(self, ctx: EvaluationContext) -> FilterResult:
        if not ctx.aggregated_signal:
            return FilterResult(passed=False, reason="No signal for edge calculation")
        
        # Calculate required edge
        edge_req = self.edge_calculator.calculate_required_edge(
            time_left_seconds=ctx.time_to_expiry,
            market_price=ctx.market.up_price if ctx.side == "Up" else ctx.market.down_price,
            volume=ctx.market.volume,
            momentum=ctx.momentum.trend_strength if ctx.momentum else None,
            side=ctx.side
        )
        
        vig = (ctx.market.up_price + ctx.market.down_price) - 1.0
        
        # Check requirement based on mode
        if self.cfg.high_frequency_mode:
            # HF: simple check - edge must beat vig + minimum
            required = vig + self.cfg.hf_min_edge
            if ctx.edge < required:
                return FilterResult(
                    passed=False,
                    reason=f"Edge {ctx.edge*100:.1f}% < Required {required*100:.1f}%"
                )
        else:
            # Quality: use dynamic edge requirement
            if ctx.edge < edge_req.required_edge:
                return FilterResult(
                    passed=False,
                    reason=f"Edge {ctx.edge*100:.1f}% < Required {edge_req.required_edge*100:.1f}%"
                )
        
        return FilterResult(passed=True)


class MarketEvaluator:
    """
    Clean evaluation pipeline using filter chain pattern.
    
    Each filter checks one condition independently.
    Easy to add/remove/modify filters.
    """
    
    def __init__(
        self,
        cfg: TradingConfig,
        price_feed: RealTimePriceFeed,
        volume_detector: VolumeDetector,
        signal_aggregator: SignalAggregator,
        edge_calculator: DynamicEdgeCalculator,
        calibrator: ProbabilityCalibrator,
        timing_optimizer: TimingOptimizer
    ):
        self.cfg = cfg
        
        # Build filter chain
        self.filters: List[MarketFilter] = [
            TimingWindowFilter(cfg),
            TimingOptimizerFilter(cfg, timing_optimizer),
            VolumeFilter(cfg, volume_detector),
            SignalFilter(cfg),
            EdgeRequirementFilter(cfg, edge_calculator),
        ]
        
        # Components for signal/edge calculation
        self.price_feed = price_feed
        self.signal_aggregator = signal_aggregator
        self.edge_calculator = edge_calculator
        self.calibrator = calibrator
        
        # Probability engine (centralized math)
        self.probability_engine = ProbabilityEngine(calibrator)
    
    def evaluate(
        self,
        market: FifteenMinMarket,
        crypto_prices: dict[str, float],
        time_to_expiry: float
    ) -> tuple[bool, EvaluationContext]:
        """
        Evaluate a market opportunity.
        
        Returns: (should_trade, context)
        """
        # Create evaluation context
        ctx = EvaluationContext(
            market=market,
            time_to_expiry=time_to_expiry,
            crypto_prices=crypto_prices
        )
        
        # Get momentum
        ctx.momentum = self.price_feed.get_momentum(market.asset) if self.price_feed.is_connected() else None
        
        # Get aggregated signal
        ctx.aggregated_signal = self.signal_aggregator.aggregate(
            asset=market.asset,
            market_price=market.up_price,
            best_bid=market.up_price - 0.01,
            best_ask=market.up_price + 0.01,
            momentum_data=ctx.momentum
        )
        
        # Calculate probability and edge using probability engine
        prob_result = self.probability_engine.calculate(
            market=market,
            aggregated_signal=ctx.aggregated_signal,
            momentum=ctx.momentum
        )
        
        ctx.side = prob_result.side
        ctx.edge = prob_result.edge
        ctx.true_probability = prob_result.true_probability
        ctx.reasoning.append(prob_result.reasoning)
        
        # Run filters
        for filter_obj in self.filters:
            result = filter_obj.check(ctx)
            if not result.passed:
                ctx.reasoning.append(f"Rejected: {result.reason}")
                return False, ctx
        
        # All filters passed
        mode_label = "HF" if self.cfg.high_frequency_mode else "Quality"
        vig = (market.up_price + market.down_price) - 1.0
        ctx.reasoning.append(
            f"{mode_label} Mode: Edge {ctx.edge*100:+.1f}% | "
            f"Signal: {ctx.aggregated_signal.strength*100:.0f}% | "
            f"Confidence: {ctx.aggregated_signal.confidence*100:.0f}% | "
            f"Vig: {vig*100:.1f}% | "
            f"Vol: ${market.volume:.0f}"
        )
        
        return True, ctx
    

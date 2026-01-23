"""
Signal Aggregator

Combines multiple signals into a unified trading signal.
Uses weighted combination with adaptive weights based on historical performance.
"""
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum

from .price_feed import RealTimePriceFeed, MomentumData
from .volume_detector import VolumeDetector

logger = logging.getLogger(__name__)


class SignalType(Enum):
    STRONG_UP = "strong_up"
    UP = "up"
    NEUTRAL = "neutral"
    DOWN = "down"
    STRONG_DOWN = "strong_down"


@dataclass
class IndividualSignal:
    """Single signal from one source."""
    name: str
    value: float  # -1 to 1 (negative = down, positive = up)
    confidence: float  # 0 to 1
    weight: float
    reasoning: str


@dataclass
class AggregatedSignal:
    """Combined signal from all sources."""
    direction: SignalType
    strength: float  # 0 to 1
    confidence: float  # 0 to 1
    recommended_side: str  # "Up" or "Down"
    probability_adjustment: float  # How much to adjust market probability
    individual_signals: list
    reasoning: str
    
    @property
    def is_tradeable(self) -> bool:
        """Is the signal strong enough to trade?"""
        # With momentum-focused signals, we can be more aggressive
        return self.strength >= 0.2 and self.confidence >= 0.35


class SignalAggregator:
    """
    Aggregates multiple signals into a unified trading signal.
    
    Signal Sources (with default weights):
    1. Price Momentum (70%) - PRIMARY signal for 15-min crypto markets
       The only thing that matters is whether crypto price goes up or down.
    2. Volatility/Volume (20%) - Confidence modifier based on market activity
    3. Order Book (10%) - Minor input from Polymarket book pressure
    
    NOTE: Mean reversion and cross-market signals were REMOVED because:
    - Mean reversion fights momentum in short timeframes (counterproductive)
    - Cross-market for same asset is just redundant/circular
    
    Weights are adaptive and can be updated based on performance.
    """
    
    DEFAULT_WEIGHTS = {
        "momentum": 0.70,  # PRIMARY: crypto price direction
        "volume": 0.20,    # Confidence modifier from volatility
        "order_book": 0.10,  # Minor: Polymarket book pressure
    }
    
    # Thresholds
    MOMENTUM_THRESHOLD = 0.003  # 0.3% momentum considered significant (lowered for sensitivity)
    
    def __init__(
        self,
        price_feed: Optional[RealTimePriceFeed] = None,
        volume_detector: Optional[VolumeDetector] = None
    ):
        self.price_feed = price_feed
        self.volume_detector = volume_detector
        self.weights = self.DEFAULT_WEIGHTS.copy()
        
        # Performance tracking for weight adaptation
        self._signal_performance: Dict[str, Dict[str, float]] = {
            name: {"correct": 0, "total": 0, "pnl": 0.0}
            for name in self.DEFAULT_WEIGHTS
        }
    
    def _momentum_signal(
        self, 
        asset: str, 
        momentum_data: Optional[MomentumData] = None
    ) -> IndividualSignal:
        """
        Generate signal from price momentum - PRIMARY SIGNAL.
        
        For 15-minute crypto markets, momentum is THE predictive factor.
        Uses multiple timeframes with HEAVY weight on short-term (most predictive).
        
        Strategy: In short timeframes, momentum tends to CONTINUE.
        """
        if self.price_feed is None:
            momentum_data = momentum_data or MomentumData()
        else:
            momentum_data = self.price_feed.get_momentum(asset)
        
        # Short-term dominates for 15-min markets (momentum continuation)
        # Rationale: Recent price action is most predictive for short windows
        short_weight = 0.50   # 1-5s momentum (immediate direction)
        medium_weight = 0.35  # 30-60s momentum (trend confirmation)
        long_weight = 0.15    # 5min momentum (background trend)
        
        combined = (
            momentum_data.short_term * short_weight +
            momentum_data.medium_term * medium_weight +
            momentum_data.momentum_300s * long_weight
        )
        
        # Normalize to -1 to 1 with higher sensitivity
        # 0.5% move = 0.5 signal, 1% move = 1.0 (capped)
        value = max(-1, min(1, combined * 100))  # 1% = full signal
        
        # Confidence based on alignment of ALL timeframes
        # If all timeframes agree, confidence is high
        timeframe_signs = [
            1 if momentum_data.momentum_1s > 0 else (-1 if momentum_data.momentum_1s < 0 else 0),
            1 if momentum_data.momentum_5s > 0 else (-1 if momentum_data.momentum_5s < 0 else 0),
            1 if momentum_data.momentum_30s > 0 else (-1 if momentum_data.momentum_30s < 0 else 0),
            1 if momentum_data.momentum_60s > 0 else (-1 if momentum_data.momentum_60s < 0 else 0),
        ]
        
        # Count how many timeframes agree on direction
        positive_count = sum(1 for s in timeframe_signs if s > 0)
        negative_count = sum(1 for s in timeframe_signs if s < 0)
        max_agreement = max(positive_count, negative_count)
        alignment = max_agreement / len(timeframe_signs)  # 0.25 to 1.0
        
        # Confidence scales with alignment AND strength
        base_confidence = alignment
        strength_bonus = min(0.3, abs(combined) * 10)  # Stronger moves = more confident
        confidence = min(1.0, base_confidence + strength_bonus)
        
        if abs(combined) < self.MOMENTUM_THRESHOLD:
            reasoning = f"Weak momentum ({combined*100:+.3f}%)"
            value = 0
            confidence = 0.2
        else:
            direction = "UP" if combined > 0 else "DOWN"
            agreement = f"{max_agreement}/4 agree"
            reasoning = f"Momentum {direction} ({combined*100:+.2f}%) [{agreement}]"
        
        return IndividualSignal(
            name="momentum",
            value=value,
            confidence=confidence,
            weight=self.weights["momentum"],
            reasoning=reasoning
        )
    
    def _volume_signal(
        self, 
        asset: str,
        momentum_data: Optional[MomentumData] = None
    ) -> IndividualSignal:
        """
        Generate signal from volume/volatility analysis.
        
        This is a CONFIDENCE MODIFIER that reinforces momentum:
        - High volume + momentum = STRONGER confidence in momentum direction
        - Low volume = WEAKER confidence, signals less reliable
        
        The key insight: Volume confirms conviction. High volume moves
        are more likely to continue than low volume moves.
        """
        if self.volume_detector is None:
            return IndividualSignal(
                name="volume",
                value=0,
                confidence=0.5,
                weight=self.weights["volume"],
                reasoning="No volume data"
            )
        
        stats = self.volume_detector.get_volume_stats(asset)
        
        # Volume REINFORCES momentum direction (not independent)
        # Get momentum direction to align volume signal
        if momentum_data is None and self.price_feed is not None:
            momentum_data = self.price_feed.get_momentum(asset)
        
        momentum_direction = 0
        if momentum_data:
            momentum_direction = 1 if momentum_data.trend_strength > 0 else -1
        
        # High volume = momentum is more reliable, amplify it
        # Low volume = momentum is noise, dampen it
        if stats.z_score > 2.0:
            # High volume - strong confirmation of momentum
            value = momentum_direction * 0.8  # Amplify momentum direction
            confidence = 0.9
            reasoning = f"High vol ({stats.z_score:.1f}σ) confirms momentum"
        elif stats.z_score > 1.0:
            # Above average volume - moderate confirmation
            value = momentum_direction * 0.5
            confidence = 0.7
            reasoning = f"Good vol ({stats.z_score:.1f}σ) supports move"
        elif stats.z_score < -1.0:
            # Low volume - momentum is less reliable
            value = 0  # Don't add directional bias
            confidence = 0.3
            reasoning = f"Low vol ({stats.z_score:.1f}σ) - weak signal"
        else:
            # Normal volume
            value = momentum_direction * 0.3
            confidence = 0.5
            reasoning = f"Normal vol ({stats.z_score:.1f}σ)"
        
        return IndividualSignal(
            name="volume",
            value=value,
            confidence=confidence,
            weight=self.weights["volume"],
            reasoning=reasoning
        )
    
    def _order_book_signal(
        self, 
        best_bid: float, 
        best_ask: float,
        bid_size: float = 0,
        ask_size: float = 0
    ) -> IndividualSignal:
        """
        Generate signal from order book imbalance.
        
        More bids than asks = bullish
        More asks than bids = bearish
        """
        if bid_size == 0 and ask_size == 0:
            # Use bid/ask spread as proxy
            spread = best_ask - best_bid if best_ask > best_bid else 0
            mid = (best_bid + best_ask) / 2 if best_ask > 0 else best_bid
            
            # Tight spread = efficient market
            if spread < 0.02 and mid > 0:
                confidence = 0.5
                reasoning = f"Tight spread ({spread*100:.1f}%)"
            else:
                confidence = 0.3
                reasoning = f"Wide spread ({spread*100:.1f}%)"
            
            return IndividualSignal(
                name="order_book",
                value=0,
                confidence=confidence,
                weight=self.weights["order_book"],
                reasoning=reasoning
            )
        
        # Calculate imbalance
        total_size = bid_size + ask_size
        if total_size == 0:
            imbalance = 0
        else:
            imbalance = (bid_size - ask_size) / total_size  # -1 to 1
        
        value = imbalance
        confidence = min(0.8, abs(imbalance) + 0.3)
        
        if imbalance > 0.2:
            reasoning = f"Bid pressure ({imbalance:.1%})"
        elif imbalance < -0.2:
            reasoning = f"Ask pressure ({imbalance:.1%})"
        else:
            reasoning = f"Balanced book ({imbalance:.1%})"
        
        return IndividualSignal(
            name="order_book",
            value=value,
            confidence=confidence,
            weight=self.weights["order_book"],
            reasoning=reasoning
        )
    
    def _mean_reversion_signal(
        self, 
        market_price: float,
        side: str
    ) -> IndividualSignal:
        """
        Generate mean reversion signal.
        
        Extreme prices tend to revert toward 50%.
        """
        distance_from_50 = market_price - 0.50
        
        # Signal strength increases with distance from 50%
        if abs(distance_from_50) < 0.25:
            # Near middle - no reversion signal
            value = 0
            confidence = 0.3
            reasoning = f"Price near center ({market_price:.0%})"
        else:
            # Far from middle - expect reversion
            # If price is high (>75%), expect down (reversion)
            # If price is low (<25%), expect up (reversion)
            reversion_direction = -1 if market_price > 0.50 else 1
            strength = (abs(distance_from_50) - 0.25) / 0.25  # 0 at 25%, 1 at 50%
            value = reversion_direction * strength
            confidence = 0.5 + strength * 0.3
            
            if value > 0:
                reasoning = f"Oversold ({market_price:.0%}) - expect up"
            else:
                reasoning = f"Overbought ({market_price:.0%}) - expect down"
        
        return IndividualSignal(
            name="mean_reversion",
            value=value,
            confidence=confidence,
            weight=self.weights["mean_reversion"],
            reasoning=reasoning
        )
    
    def _cross_market_signal(
        self, 
        asset: str,
        related_assets: Optional[Dict[str, float]] = None
    ) -> IndividualSignal:
        """
        Generate signal from correlated markets.
        
        If correlated assets are moving, our asset may follow.
        """
        if self.price_feed is None or related_assets is None:
            return IndividualSignal(
                name="cross_market",
                value=0,
                confidence=0.3,
                weight=self.weights["cross_market"],
                reasoning="No cross-market data"
            )
        
        # For crypto, BTC often leads
        btc_momentum = self.price_feed.get_momentum("BTC")
        eth_momentum = self.price_feed.get_momentum("ETH")
        
        # Weighted average of major assets
        cross_momentum = btc_momentum.trend_strength * 0.6 + eth_momentum.trend_strength * 0.4
        
        value = max(-1, min(1, cross_momentum * 20))
        confidence = 0.4  # Cross-market signals are weaker
        
        if abs(cross_momentum) > 0.005:
            direction = "bullish" if cross_momentum > 0 else "bearish"
            reasoning = f"Crypto market {direction} ({cross_momentum*100:+.2f}%)"
        else:
            reasoning = "Crypto market neutral"
            value = 0
        
        return IndividualSignal(
            name="cross_market",
            value=value,
            confidence=confidence,
            weight=self.weights["cross_market"],
            reasoning=reasoning
        )
    
    def aggregate(
        self,
        asset: str,
        market_price: float,
        best_bid: float = 0,
        best_ask: float = 0,
        bid_size: float = 0,
        ask_size: float = 0,
        momentum_data: Optional[MomentumData] = None,
    ) -> AggregatedSignal:
        """
        Aggregate all signals into unified trading signal.
        
        Signal Priority (for 15-min crypto markets):
        1. Momentum (70%) - THE primary signal, crypto price direction
        2. Volume (20%) - Confidence modifier, reinforces momentum
        3. Order Book (10%) - Minor input from Polymarket pressure
        
        NOTE: Mean reversion and cross-market signals were REMOVED.
        
        Args:
            asset: Asset symbol (BTC, ETH, etc.)
            market_price: Current market price (0-1)
            best_bid: Best bid price
            best_ask: Best ask price
            bid_size: Total bid size
            ask_size: Total ask size
            momentum_data: Optional pre-calculated momentum
        
        Returns:
            AggregatedSignal with direction, strength, and recommendations
        """
        # Get momentum first (needed by volume signal)
        momentum_signal = self._momentum_signal(asset, momentum_data)
        
        # Collect signals - ONLY the productive ones
        signals = [
            momentum_signal,
            self._volume_signal(asset, momentum_data),
            self._order_book_signal(best_bid, best_ask, bid_size, ask_size),
        ]
        
        # Calculate weighted average with confidence scaling
        total_weight = 0
        weighted_value = 0
        weighted_confidence = 0
        
        for signal in signals:
            # Effective weight = base weight * confidence
            # High confidence signals count more
            effective_weight = signal.weight * signal.confidence
            total_weight += effective_weight
            weighted_value += signal.value * effective_weight
            weighted_confidence += signal.confidence * signal.weight
        
        if total_weight > 0:
            combined_value = weighted_value / total_weight
        else:
            combined_value = 0
        
        total_base_weight = sum(s.weight for s in signals)
        combined_confidence = weighted_confidence / total_base_weight if total_base_weight > 0 else 0
        
        # Determine direction with tighter thresholds (momentum-focused)
        if combined_value > 0.4:
            direction = SignalType.STRONG_UP
        elif combined_value > 0.15:
            direction = SignalType.UP
        elif combined_value < -0.4:
            direction = SignalType.STRONG_DOWN
        elif combined_value < -0.15:
            direction = SignalType.DOWN
        else:
            direction = SignalType.NEUTRAL
        
        # Recommended side
        if combined_value > 0:
            recommended_side = "Up"
        else:
            recommended_side = "Down"
        
        # Probability adjustment - MORE AGGRESSIVE now that momentum dominates
        # combined_value is -1 to 1, scale to meaningful probability shift
        # Max adjustment of ~15% for very strong signals
        probability_adjustment = combined_value * 0.15 * combined_confidence
        
        # Build reasoning - prioritize momentum signal
        reasoning_parts = []
        for s in signals:
            if s.name == "momentum" or abs(s.value) > 0.2 or s.confidence > 0.7:
                reasoning_parts.append(s.reasoning)
        reasoning = " | ".join(reasoning_parts[:3])
        
        return AggregatedSignal(
            direction=direction,
            strength=abs(combined_value),
            confidence=combined_confidence,
            recommended_side=recommended_side,
            probability_adjustment=probability_adjustment,
            individual_signals=signals,
            reasoning=reasoning or "No strong signals"
        )
    
    def update_weights(self, signal_name: str, was_correct: bool, pnl: float = 0.0):
        """Update weights based on signal performance."""
        if signal_name not in self._signal_performance:
            return
        
        self._signal_performance[signal_name]["total"] += 1
        self._signal_performance[signal_name]["pnl"] += pnl
        if was_correct:
            self._signal_performance[signal_name]["correct"] += 1
        
        # Recalculate weights based on accuracy
        # Only after enough data
        min_samples = 15  # Lowered from 20 for faster adaptation
        for name, perf in self._signal_performance.items():
            if perf["total"] >= min_samples:
                accuracy = perf["correct"] / perf["total"]
                # Adjust weight: better accuracy = higher weight
                # But keep within reasonable bounds
                base_weight = self.DEFAULT_WEIGHTS[name]
                adjustment = (accuracy - 0.5) * 0.4  # -0.2 to +0.2
                self.weights[name] = max(0.05, min(0.80, base_weight + adjustment))
        
        # Normalize weights to sum to 1
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: v/total for k, v in self.weights.items()}
    
    def record_trade_result(
        self, 
        individual_signals: list,
        won: bool,
        pnl: float,
        actual_direction: str  # "Up" or "Down"
    ):
        """
        Record trade result and update all signal weights.
        
        This is the key learning feedback loop:
        - For each signal, check if it predicted correctly
        - Update that signal's accuracy tracking
        - Adjust weights based on historical accuracy
        """
        for signal in individual_signals:
            if signal.name not in self._signal_performance:
                continue
            
            # Did this signal predict correctly?
            signal_predicted_up = signal.value > 0
            actual_was_up = actual_direction == "Up"
            
            # Signal was correct if:
            # - It predicted up and actual was up, OR
            # - It predicted down and actual was down, OR
            # - It was neutral (didn't make a prediction)
            if abs(signal.value) < 0.1:
                # Neutral signal - don't count
                continue
            
            signal_correct = signal_predicted_up == actual_was_up
            
            # Update this signal's performance
            self.update_weights(signal.name, signal_correct, pnl if signal_correct else -abs(pnl))
            
            logger.debug(
                f"Signal '{signal.name}' predicted {'up' if signal_predicted_up else 'down'}, "
                f"actual was {actual_direction}, correct={signal_correct}"
            )
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get summary of signal performance."""
        summary = {}
        for name, perf in self._signal_performance.items():
            if perf["total"] > 0:
                accuracy = perf["correct"] / perf["total"]
                summary[name] = {
                    "accuracy": f"{accuracy:.1%}",
                    "samples": perf["total"],
                    "weight": f"{self.weights[name]:.1%}",
                    "pnl": f"${perf['pnl']:+.2f}",
                }
        return summary

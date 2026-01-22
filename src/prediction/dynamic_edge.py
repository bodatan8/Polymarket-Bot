"""
Dynamic Edge Calculator

Calculates required edge based on:
- Time remaining (more time = more uncertainty = need bigger edge)
- Market price (closer to 50% = harder to predict)
- Volume (low volume = noisy = need bigger edge)
- Momentum strength (stronger momentum = more confident)
"""
import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class EdgeRequirement:
    """Result of edge calculation."""
    required_edge: float
    time_factor: float
    uncertainty_factor: float
    volume_factor: float
    momentum_factor: float
    reasoning: str


class DynamicEdgeCalculator:
    """
    Calculates dynamic edge requirements based on market conditions.
    
    The core insight: A 2% edge with 15 minutes left is worth less than
    a 2% edge with 2 minutes left, because there's more time for things
    to go wrong.
    """
    
    # Base edge requirement (0.5%)
    BASE_EDGE = 0.005
    
    # Time decay parameters
    TIME_REFERENCE = 120  # 2 minutes as baseline
    TIME_EXPONENT = 0.5  # Square root decay
    
    # Volume parameters
    VOLUME_REFERENCE = 5000  # $5000 as "high volume"
    
    # Momentum boost parameters
    MOMENTUM_THRESHOLD = 0.02  # 2% momentum considered significant
    
    def calculate_time_factor(self, time_left_seconds: float) -> float:
        """
        More time = higher factor = need more edge.
        
        At 2 min: factor = 1.0
        At 5 min: factor = 1.58
        At 10 min: factor = 2.24
        At 15 min: factor = 2.74
        """
        if time_left_seconds <= 0:
            return 10.0  # Very high if expired
        
        ratio = time_left_seconds / self.TIME_REFERENCE
        return max(1.0, ratio ** self.TIME_EXPONENT)
    
    def calculate_uncertainty_factor(self, market_price: float) -> float:
        """
        Closer to 50% = more uncertain = higher factor.
        
        At 50%: factor = 1.5 (maximum uncertainty)
        At 60%: factor = 1.4
        At 70%: factor = 1.3
        At 80%: factor = 1.2
        """
        distance_from_50 = abs(market_price - 0.50)
        # Factor ranges from 1.0 (at extremes) to 1.5 (at 50%)
        return 1.0 + (0.50 - distance_from_50)
    
    def calculate_volume_factor(self, volume: float) -> float:
        """
        Low volume = noisy = higher factor.
        
        At $5000+: factor = 1.0
        At $2500: factor = 1.5
        At $1000: factor = 1.8
        At $500: factor = 1.9
        """
        if volume >= self.VOLUME_REFERENCE:
            return 1.0
        
        if volume <= 0:
            return 3.0  # Very high for no volume
        
        # Logarithmic scaling
        ratio = volume / self.VOLUME_REFERENCE
        return max(1.0, 2.0 - ratio)
    
    def calculate_momentum_factor(
        self, 
        momentum: Optional[float], 
        side: str,
        market_price: float
    ) -> float:
        """
        Strong momentum in our direction = lower factor (more confident).
        Momentum against us = higher factor (need more edge).
        
        Returns factor between 0.7 (strong confirmation) and 1.5 (against us).
        """
        if momentum is None:
            return 1.0  # Neutral if no momentum data
        
        # Determine if momentum confirms our side
        # For "Up" side: positive momentum is good
        # For "Down" side: negative momentum is good
        if side == "Up":
            momentum_confirms = momentum > 0
            momentum_strength = abs(momentum)
        else:
            momentum_confirms = momentum < 0
            momentum_strength = abs(momentum)
        
        # Scale factor based on momentum strength
        if momentum_strength < self.MOMENTUM_THRESHOLD:
            return 1.0  # Weak momentum, neutral
        
        strength_ratio = min(1.0, momentum_strength / 0.05)  # Cap at 5% momentum
        
        if momentum_confirms:
            # Momentum confirms our bet - reduce required edge
            return max(0.7, 1.0 - strength_ratio * 0.3)
        else:
            # Momentum against our bet - increase required edge
            return min(1.5, 1.0 + strength_ratio * 0.5)
    
    def calculate_required_edge(
        self,
        time_left_seconds: float,
        market_price: float,
        volume: float,
        momentum: Optional[float] = None,
        side: str = "Up"
    ) -> EdgeRequirement:
        """
        Calculate the required edge to place a bet.
        
        Args:
            time_left_seconds: Seconds until market resolution
            market_price: Current price of the side we're betting on (0-1)
            volume: Market volume in USD
            momentum: Price momentum (-1 to 1, optional)
            side: "Up" or "Down"
        
        Returns:
            EdgeRequirement with required edge and component factors
        """
        time_factor = self.calculate_time_factor(time_left_seconds)
        uncertainty_factor = self.calculate_uncertainty_factor(market_price)
        volume_factor = self.calculate_volume_factor(volume)
        momentum_factor = self.calculate_momentum_factor(momentum, side, market_price)
        
        required_edge = (
            self.BASE_EDGE 
            * time_factor 
            * uncertainty_factor 
            * volume_factor 
            * momentum_factor
        )
        
        # Build reasoning string
        reasons = []
        if time_factor > 1.5:
            reasons.append(f"Time: {time_left_seconds/60:.0f}m left ({time_factor:.1f}x)")
        if uncertainty_factor > 1.3:
            reasons.append(f"Near 50% ({uncertainty_factor:.1f}x)")
        if volume_factor > 1.3:
            reasons.append(f"Low vol ${volume:.0f} ({volume_factor:.1f}x)")
        if momentum_factor < 0.9:
            reasons.append(f"Momentum confirms ({momentum_factor:.1f}x)")
        elif momentum_factor > 1.1:
            reasons.append(f"Momentum against ({momentum_factor:.1f}x)")
        
        reasoning = " | ".join(reasons) if reasons else "Standard conditions"
        
        return EdgeRequirement(
            required_edge=required_edge,
            time_factor=time_factor,
            uncertainty_factor=uncertainty_factor,
            volume_factor=volume_factor,
            momentum_factor=momentum_factor,
            reasoning=reasoning
        )
    
    def should_bet(
        self,
        estimated_edge: float,
        time_left_seconds: float,
        market_price: float,
        volume: float,
        momentum: Optional[float] = None,
        side: str = "Up"
    ) -> tuple[bool, float, str]:
        """
        Determine if we should place a bet.
        
        Returns:
            (should_bet, edge_margin, reasoning)
            edge_margin = estimated_edge - required_edge
        """
        req = self.calculate_required_edge(
            time_left_seconds, market_price, volume, momentum, side
        )
        
        edge_margin = estimated_edge - req.required_edge
        should_bet = edge_margin > 0
        
        if should_bet:
            reason = f"Edge {estimated_edge*100:.1f}% > Required {req.required_edge*100:.1f}% | {req.reasoning}"
        else:
            reason = f"Edge {estimated_edge*100:.1f}% < Required {req.required_edge*100:.1f}% | {req.reasoning}"
        
        return should_bet, edge_margin, reason

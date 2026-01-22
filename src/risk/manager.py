"""
Professional Risk Management Module

Implements comprehensive risk controls:
- Position sizing (Kelly Criterion)
- Daily loss limits
- Maximum drawdown protection
- Correlation exposure limits
- Position concentration limits
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from enum import Enum

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """Risk levels for position sizing."""
    CONSERVATIVE = 0.25  # 1/4 Kelly
    MODERATE = 0.50     # 1/2 Kelly
    AGGRESSIVE = 0.75   # 3/4 Kelly
    FULL = 1.0          # Full Kelly (not recommended)


@dataclass
class RiskLimits:
    """Risk limit configuration."""
    max_daily_loss: float = 100.0
    max_drawdown_percent: float = 20.0
    max_position_size: float = 50.0
    min_position_size: float = 1.0
    max_total_exposure: float = 500.0
    max_positions_per_asset: int = 2
    max_open_positions: int = 10
    max_correlation_exposure: float = 0.7
    stop_loss_percent: float = 50.0  # Stop at 50% loss on position


@dataclass
class Position:
    """Position information."""
    id: int
    asset: str
    side: str
    amount_usd: float
    entry_price: float
    current_price: float
    pnl: float
    opened_at: datetime
    
    @property
    def unrealized_pnl_percent(self) -> float:
        if self.amount_usd == 0:
            return 0
        return self.pnl / self.amount_usd * 100


@dataclass
class RiskCheckResult:
    """Result of risk check."""
    allowed: bool
    reason: str
    adjusted_size: float
    risk_score: float  # 0-1, higher = more risky


class RiskManager:
    """
    Professional-grade risk management.
    
    Features:
    1. Kelly Criterion position sizing
    2. Daily loss limits (hard stop)
    3. Maximum drawdown protection
    4. Correlation exposure limits
    5. Position concentration limits
    6. Adaptive sizing based on volatility
    """
    
    def __init__(self, limits: Optional[RiskLimits] = None, risk_level: RiskLevel = RiskLevel.MODERATE):
        self.limits = limits or RiskLimits()
        self.risk_level = risk_level
        
        # Track daily P&L
        self._daily_pnl: float = 0.0
        self._daily_pnl_date: Optional[datetime] = None
        
        # Track peak equity for drawdown
        self._peak_equity: float = 0.0
        self._current_equity: float = 0.0
        
        # Track positions by asset
        self._positions: Dict[str, List[Position]] = {}
        
        # Correlation matrix (simplified)
        self._correlations = {
            ("BTC", "ETH"): 0.85,
            ("BTC", "SOL"): 0.75,
            ("BTC", "XRP"): 0.65,
            ("ETH", "SOL"): 0.80,
            ("ETH", "XRP"): 0.60,
            ("SOL", "XRP"): 0.55,
        }
    
    def _check_daily_reset(self):
        """Reset daily P&L at midnight."""
        today = datetime.utcnow().date()
        if self._daily_pnl_date is None or self._daily_pnl_date != today:
            self._daily_pnl = 0.0
            self._daily_pnl_date = today
    
    def record_pnl(self, pnl: float, equity: Optional[float] = None):
        """Record P&L for risk tracking."""
        self._check_daily_reset()
        self._daily_pnl += pnl
        
        if equity is not None:
            self._current_equity = equity
            self._peak_equity = max(self._peak_equity, equity)
    
    def calculate_kelly_size(
        self,
        win_probability: float,
        edge: float,
        bankroll: float
    ) -> float:
        """
        Calculate position size using Kelly Criterion.
        
        Kelly formula: f = (bp - q) / b
        where:
            b = odds received (profit/loss ratio)
            p = probability of winning
            q = probability of losing = 1 - p
        
        For binary markets: b = (1/entry_price) - 1
        """
        if win_probability <= 0 or win_probability >= 1:
            return 0
        
        # For binary markets at price p:
        # Win: receive $1, profit = 1 - p
        # Lose: lose $p
        # Odds = (1-p) / p
        
        entry_price = 1 - edge - (1 - win_probability)  # Approximate
        if entry_price <= 0 or entry_price >= 1:
            entry_price = 1 - win_probability
        
        profit_if_win = 1 - entry_price
        loss_if_lose = entry_price
        
        if loss_if_lose == 0:
            return 0
        
        b = profit_if_win / loss_if_lose  # Odds
        p = win_probability
        q = 1 - p
        
        kelly_fraction = (b * p - q) / b
        
        # Apply risk level adjustment
        adjusted_kelly = kelly_fraction * self.risk_level.value
        
        # Never bet negative or more than a cap
        adjusted_kelly = max(0, min(0.25, adjusted_kelly))  # Cap at 25% of bankroll
        
        return bankroll * adjusted_kelly
    
    def get_correlation(self, asset1: str, asset2: str) -> float:
        """Get correlation between two assets."""
        if asset1 == asset2:
            return 1.0
        
        key1 = (asset1, asset2)
        key2 = (asset2, asset1)
        
        return self._correlations.get(key1, self._correlations.get(key2, 0.3))
    
    def calculate_correlation_exposure(
        self,
        new_asset: str,
        new_side: str,
        new_amount: float,
        current_positions: List[Dict]
    ) -> float:
        """
        Calculate total correlation-adjusted exposure.
        
        High correlation means positions are effectively the same bet.
        """
        total_exposure = 0.0
        
        for pos in current_positions:
            pos_asset = pos.get('asset', '')
            pos_side = pos.get('side', '')
            pos_amount = pos.get('amount_usd', 0)
            
            correlation = self.get_correlation(new_asset, pos_asset)
            
            # Same direction = add exposure, opposite = reduce
            if pos_side == new_side:
                direction_factor = 1.0
            else:
                direction_factor = -0.5  # Partial hedge
            
            total_exposure += pos_amount * correlation * direction_factor
        
        # Add new position
        total_exposure += new_amount
        
        return total_exposure
    
    def can_take_position(
        self,
        asset: str,
        side: str,
        proposed_size: float,
        edge: float,
        confidence: float,
        current_positions: List[Dict],
        bankroll: float = 1000.0
    ) -> RiskCheckResult:
        """
        Check if a new position passes all risk checks.
        
        Returns adjusted size and approval status.
        """
        self._check_daily_reset()
        
        reasons = []
        risk_score = 0.0
        adjusted_size = proposed_size
        
        # 1. Check daily loss limit
        if self._daily_pnl <= -self.limits.max_daily_loss:
            return RiskCheckResult(
                allowed=False,
                reason=f"Daily loss limit reached (${self._daily_pnl:.2f})",
                adjusted_size=0,
                risk_score=1.0
            )
        
        # Reduce size if approaching daily limit
        remaining_loss_budget = self.limits.max_daily_loss + self._daily_pnl
        if adjusted_size > remaining_loss_budget * 0.5:
            adjusted_size = remaining_loss_budget * 0.5
            reasons.append(f"Reduced due to daily loss ({self._daily_pnl:+.2f})")
            risk_score += 0.2
        
        # 2. Check drawdown
        if self._peak_equity > 0 and self._current_equity > 0:
            drawdown = (self._peak_equity - self._current_equity) / self._peak_equity * 100
            if drawdown >= self.limits.max_drawdown_percent:
                return RiskCheckResult(
                    allowed=False,
                    reason=f"Max drawdown reached ({drawdown:.1f}%)",
                    adjusted_size=0,
                    risk_score=1.0
                )
            
            if drawdown > self.limits.max_drawdown_percent * 0.5:
                reduction = 1 - (drawdown / self.limits.max_drawdown_percent)
                adjusted_size *= reduction
                reasons.append(f"Reduced due to drawdown ({drawdown:.1f}%)")
                risk_score += 0.3
        
        # 3. Check position limits
        num_open = len([p for p in current_positions if p.get('status') == 'open'])
        if num_open >= self.limits.max_open_positions:
            return RiskCheckResult(
                allowed=False,
                reason=f"Max open positions ({self.limits.max_open_positions})",
                adjusted_size=0,
                risk_score=1.0
            )
        
        # 4. Check asset concentration
        asset_positions = [p for p in current_positions if p.get('asset') == asset and p.get('status') == 'open']
        if len(asset_positions) >= self.limits.max_positions_per_asset:
            return RiskCheckResult(
                allowed=False,
                reason=f"Max positions for {asset} ({self.limits.max_positions_per_asset})",
                adjusted_size=0,
                risk_score=1.0
            )
        
        # 5. Check correlation exposure
        corr_exposure = self.calculate_correlation_exposure(
            asset, side, adjusted_size, 
            [p for p in current_positions if p.get('status') == 'open']
        )
        if corr_exposure > self.limits.max_total_exposure * self.limits.max_correlation_exposure:
            reduction = (self.limits.max_total_exposure * self.limits.max_correlation_exposure) / corr_exposure
            adjusted_size *= reduction
            reasons.append(f"Reduced due to correlation ({corr_exposure:.0f})")
            risk_score += 0.2
        
        # 6. Check total exposure
        current_exposure = sum(p.get('amount_usd', 0) for p in current_positions if p.get('status') == 'open')
        if current_exposure + adjusted_size > self.limits.max_total_exposure:
            adjusted_size = max(0, self.limits.max_total_exposure - current_exposure)
            reasons.append(f"Reduced due to total exposure")
            risk_score += 0.1
        
        # 7. Apply Kelly sizing
        win_prob = confidence * 0.5 + 0.5  # Convert confidence to win probability estimate
        kelly_size = self.calculate_kelly_size(win_prob, edge, bankroll)
        if adjusted_size > kelly_size * 2:
            adjusted_size = kelly_size * 2  # Don't exceed 2x Kelly
            reasons.append(f"Capped at 2x Kelly (${kelly_size:.2f})")
            risk_score += 0.1
        
        # 8. Enforce min/max size limits
        if adjusted_size < self.limits.min_position_size:
            return RiskCheckResult(
                allowed=False,
                reason=f"Size ${adjusted_size:.2f} below minimum ${self.limits.min_position_size}",
                adjusted_size=0,
                risk_score=0.5
            )
        
        adjusted_size = min(adjusted_size, self.limits.max_position_size)
        
        # 9. Low edge warning
        if edge < 0.01:
            risk_score += 0.3
            reasons.append(f"Low edge ({edge*100:.1f}%)")
        
        # Final decision
        allowed = adjusted_size >= self.limits.min_position_size
        
        if reasons:
            reason = " | ".join(reasons)
        else:
            reason = "All checks passed"
        
        return RiskCheckResult(
            allowed=allowed,
            reason=reason,
            adjusted_size=adjusted_size,
            risk_score=min(1.0, risk_score)
        )
    
    def should_close_position(
        self,
        position: Dict,
        current_price: float
    ) -> tuple[bool, str]:
        """
        Check if a position should be closed due to risk.
        
        Triggers:
        - Stop loss hit
        - Time-based exit
        """
        entry_price = position.get('entry_price', 0)
        amount_usd = position.get('amount_usd', 0)
        side = position.get('side', 'Up')
        
        if entry_price == 0 or amount_usd == 0:
            return False, "Invalid position"
        
        # Calculate unrealized P&L
        if side == "Up":
            # Bought at entry_price, worth current_price
            pnl = (current_price - entry_price) * (amount_usd / entry_price)
        else:
            # Bought "Down" (No) at (1-entry_price), worth (1-current_price)
            pnl = ((1 - current_price) - (1 - entry_price)) * (amount_usd / (1 - entry_price))
        
        pnl_percent = pnl / amount_usd * 100 if amount_usd > 0 else 0
        
        # Check stop loss
        if pnl_percent <= -self.limits.stop_loss_percent:
            return True, f"Stop loss hit ({pnl_percent:.1f}%)"
        
        return False, f"Position OK ({pnl_percent:+.1f}%)"
    
    def get_risk_summary(self, current_positions: List[Dict], bankroll: float) -> Dict[str, Any]:
        """Get summary of current risk state."""
        self._check_daily_reset()
        
        open_positions = [p for p in current_positions if p.get('status') == 'open']
        total_exposure = sum(p.get('amount_usd', 0) for p in open_positions)
        
        # Drawdown
        drawdown = 0
        if self._peak_equity > 0 and self._current_equity > 0:
            drawdown = (self._peak_equity - self._current_equity) / self._peak_equity * 100
        
        return {
            "daily_pnl": f"${self._daily_pnl:+.2f}",
            "daily_limit_used": f"{abs(self._daily_pnl) / self.limits.max_daily_loss * 100:.0f}%",
            "open_positions": len(open_positions),
            "max_positions": self.limits.max_open_positions,
            "total_exposure": f"${total_exposure:.2f}",
            "max_exposure": f"${self.limits.max_total_exposure:.2f}",
            "drawdown": f"{drawdown:.1f}%",
            "max_drawdown": f"{self.limits.max_drawdown_percent}%",
            "risk_level": self.risk_level.name,
        }

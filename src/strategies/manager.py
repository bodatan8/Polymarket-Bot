"""
Strategy Manager

Manages multiple trading strategies and evaluates them together.
"""
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from src.indicators import IndicatorManager
from .base import Strategy, StrategySignal, ActionType

logger = logging.getLogger(__name__)


@dataclass
class StrategyResult:
    """Result from evaluating a strategy."""
    strategy_name: str
    signal: StrategySignal
    enabled: bool = True


class StrategyManager:
    """
    Manages multiple trading strategies.
    
    Usage:
        manager = StrategyManager(indicator_manager)
        
        # Add strategies
        manager.add_strategy(SupertrendCrossStrategy())
        manager.add_strategy(SupertrendWithSRStrategy())
        
        # Evaluate all strategies
        results = manager.evaluate_all("BTCUSDT")
        
        # Get combined recommendation
        action = manager.get_recommendation("BTCUSDT")
    """
    
    def __init__(self, indicator_manager: IndicatorManager):
        """
        Initialize strategy manager.
        
        Args:
            indicator_manager: IndicatorManager for indicator access
        """
        self.indicator_manager = indicator_manager
        self._strategies: Dict[str, Strategy] = {}
        self._enabled: Dict[str, bool] = {}
    
    def add_strategy(self, strategy: Strategy, enabled: bool = True) -> None:
        """
        Add a strategy.
        
        Args:
            strategy: Strategy instance
            enabled: Whether strategy is enabled
        """
        self._strategies[strategy.name] = strategy
        self._enabled[strategy.name] = enabled
        logger.info(f"Added strategy: {strategy.name} (enabled={enabled})")
    
    def remove_strategy(self, name: str) -> bool:
        """Remove a strategy by name."""
        if name in self._strategies:
            del self._strategies[name]
            del self._enabled[name]
            return True
        return False
    
    def enable_strategy(self, name: str) -> None:
        """Enable a strategy."""
        if name in self._enabled:
            self._enabled[name] = True
    
    def disable_strategy(self, name: str) -> None:
        """Disable a strategy."""
        if name in self._enabled:
            self._enabled[name] = False
    
    def evaluate(
        self,
        strategy_name: str,
        asset: str
    ) -> Optional[StrategySignal]:
        """
        Evaluate a single strategy.
        
        Args:
            strategy_name: Strategy name
            asset: Asset symbol
        
        Returns:
            StrategySignal or None if strategy not found
        """
        strategy = self._strategies.get(strategy_name)
        if not strategy:
            logger.warning(f"Strategy not found: {strategy_name}")
            return None
        
        if not self._enabled.get(strategy_name, False):
            logger.debug(f"Strategy disabled: {strategy_name}")
            return None
        
        return strategy.evaluate(self.indicator_manager, asset)
    
    def evaluate_all(
        self,
        asset: str
    ) -> List[StrategyResult]:
        """
        Evaluate all enabled strategies.
        
        Args:
            asset: Asset symbol
        
        Returns:
            List of StrategyResult
        """
        results = []
        
        for name, strategy in self._strategies.items():
            enabled = self._enabled.get(name, False)
            
            if enabled:
                try:
                    signal = strategy.evaluate(self.indicator_manager, asset)
                    results.append(StrategyResult(
                        strategy_name=name,
                        signal=signal,
                        enabled=True
                    ))
                except Exception as e:
                    logger.error(f"Error evaluating {name}: {e}")
            else:
                results.append(StrategyResult(
                    strategy_name=name,
                    signal=StrategySignal(
                        action=ActionType.HOLD,
                        strength=0.0,
                        asset=asset,
                        reason="Strategy disabled"
                    ),
                    enabled=False
                ))
        
        return results
    
    def get_recommendation(
        self,
        asset: str,
        min_strength: float = 0.5
    ) -> Dict[str, Any]:
        """
        Get combined recommendation from all strategies.
        
        Args:
            asset: Asset symbol
            min_strength: Minimum signal strength to consider
        
        Returns:
            Dict with recommendation and details
        """
        results = self.evaluate_all(asset)
        
        # Filter enabled strategies with signals
        active_results = [r for r in results if r.enabled and r.signal.is_trade]
        
        if not active_results:
            return {
                "action": "hold",
                "strength": 0.0,
                "reason": "No active trade signals",
                "strategies": []
            }
        
        # Count buy/sell votes weighted by strength
        buy_score = sum(
            r.signal.strength for r in active_results 
            if r.signal.action == ActionType.BUY and r.signal.strength >= min_strength
        )
        sell_score = sum(
            r.signal.strength for r in active_results 
            if r.signal.action == ActionType.SELL and r.signal.strength >= min_strength
        )
        
        # Determine action
        if buy_score > sell_score and buy_score > 0:
            action = "buy"
            strength = buy_score / len(active_results)
        elif sell_score > buy_score and sell_score > 0:
            action = "sell"
            strength = sell_score / len(active_results)
        else:
            action = "hold"
            strength = 0.0
        
        return {
            "action": action,
            "strength": strength,
            "buy_score": buy_score,
            "sell_score": sell_score,
            "reason": f"{len(active_results)} strategies evaluated",
            "strategies": [
                {
                    "name": r.strategy_name,
                    "action": r.signal.action.value,
                    "strength": r.signal.strength,
                    "reason": r.signal.reason,
                }
                for r in active_results
            ]
        }
    
    def list_strategies(self) -> List[Dict[str, Any]]:
        """List all registered strategies."""
        return [
            {
                "name": name,
                "type": strategy.__class__.__name__,
                "enabled": self._enabled.get(name, False),
                "required_indicators": strategy.required_indicators,
                "required_timeframes": strategy.required_timeframes,
            }
            for name, strategy in self._strategies.items()
        ]

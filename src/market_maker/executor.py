"""
Trade Executor - Handle Trade Execution and Position Management

Single responsibility: Execute trades and resolve positions.
No evaluation logic, just execution.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

from src.database import (
    Position, add_position, resolve_position, get_open_positions,
    record_signal_prediction, record_probability_prediction
)
from src.market_maker.config import TradingConfig
from src.market_maker.evaluator import EvaluationContext
from src.market_maker.models import FifteenMinMarket
from src.risk.manager import RiskManager
from src.learning.timing_optimizer import TimingOptimizer
from src.prediction.calibrator import ProbabilityCalibrator
from src.signals.aggregator import SignalAggregator

logger = logging.getLogger(__name__)


class TradeExecutor:
    """
    Handles trade execution and position resolution.
    
    Responsibilities:
    - Place bets (with risk checks)
    - Resolve expired positions
    - Record outcomes for learning
    """
    
    def __init__(
        self,
        cfg: TradingConfig,
        risk_manager: RiskManager,
        timing_optimizer: TimingOptimizer,
        calibrator: ProbabilityCalibrator,
        signal_aggregator: SignalAggregator
    ):
        self.cfg = cfg
        self.risk_manager = risk_manager
        self.timing_optimizer = timing_optimizer
        self.calibrator = calibrator
        self.signal_aggregator = signal_aggregator
        
        # State tracking
        self._entry_prices: Dict[int, float] = {}
        self._position_signals: Dict[int, tuple] = {}
    
    async def place_bet(self, ctx: EvaluationContext) -> Optional[int]:
        """
        Place a bet based on evaluation context.
        
        Returns:
            Position ID if bet was placed, None otherwise
        """
        market = ctx.market
        entry_price = market.up_price if ctx.side == "Up" else market.down_price
        
        # Risk check
        open_positions = get_open_positions()
        risk_check = self.risk_manager.can_take_position(
            asset=market.asset,
            side=ctx.side,
            proposed_size=self.cfg.base_bet_size_usd,
            edge=ctx.edge,
            confidence=ctx.aggregated_signal.confidence,
            current_positions=open_positions,
            bankroll=self.cfg.bankroll_usd
        )
        
        if not risk_check.allowed:
            logger.info(f"   ❌ Risk blocked: {risk_check.reason}")
            return None
        
        # Calculate bet size
        risk_adjusted_size = risk_check.adjusted_size
        
        # Apply mode-specific multiplier
        if self.cfg.high_frequency_mode:
            bet_size = risk_adjusted_size * self.cfg.hf_bet_size_multiplier
        else:
            bet_size = risk_adjusted_size
        
        if bet_size < self.cfg.min_position_size_usd:
            logger.debug(f"   ❌ Bet size too small: ${bet_size:.2f}")
            return None
        
        # Place bet
        shares = bet_size / entry_price
        profit_if_win = shares * 1.0 - bet_size
        bucket = self.timing_optimizer.get_bucket(ctx.time_to_expiry)
        
        position = Position(
            id=None,
            market_id=market.market_id,
            market_name=market.title,
            asset=market.asset,
            side=ctx.side,
            entry_price=entry_price,
            amount_usd=bet_size,
            shares=shares,
            target_price=ctx.crypto_prices.get(market.asset, 0),
            start_time=market.start_time.isoformat(),
            end_time=market.end_time.isoformat(),
            status="open",
            edge=ctx.edge,
            true_prob=ctx.true_probability,
            signal_strength=ctx.aggregated_signal.strength,
            timing_bucket=bucket,
            reasoning=" | ".join(ctx.reasoning)
        )
        
        position_id = add_position(position)
        self._entry_prices[position_id] = ctx.crypto_prices.get(market.asset, 0)
        
        # Store signals for learning
        if ctx.aggregated_signal:
            self._position_signals[position_id] = (
                ctx.aggregated_signal.individual_signals,
                ctx.true_probability,
                ctx.side
            )
            
            # Record predictions
            for signal in ctx.aggregated_signal.individual_signals:
                if abs(signal.value) > 0.05:
                    predicted_dir = "Up" if signal.value > 0 else "Down"
                    record_signal_prediction(
                        position_id=position_id,
                        signal_name=signal.name,
                        signal_value=signal.value,
                        signal_confidence=signal.confidence,
                        predicted_direction=predicted_dir,
                        asset=market.asset,
                        timing_bucket=bucket
                    )
            
            prob_bucket = self.calibrator._get_bucket_name(ctx.true_probability)
            record_probability_prediction(
                position_id=position_id,
                predicted_prob=ctx.true_probability,
                prob_bucket=prob_bucket,
                asset=market.asset
            )
        
        logger.info(
            f"[BET] {market.asset} {ctx.side} | "
            f"${bet_size:.2f} @ {entry_price*100:.1f}¢ | "
            f"Edge: {ctx.edge*100:+.1f}% | "
            f"P(win): {ctx.true_probability*100:.0f}% | "
            f"Bucket: {bucket} | "
            f"If Win: +${profit_if_win:.2f}"
        )
        logger.info(f"   Reason: {' | '.join(ctx.reasoning)}")
        
        return position_id
    
    async def resolve_positions(self, crypto_prices: Dict[str, float]):
        """
        Resolve expired positions and record outcomes for learning.
        
        Args:
            crypto_prices: Current crypto prices for resolution
        """
        open_positions = get_open_positions()
        
        for pos in open_positions:
            try:
                end_time_str = pos['end_time']
                if '+' in end_time_str or 'Z' in end_time_str:
                    end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                else:
                    end_time = datetime.fromisoformat(end_time_str)
                    now = datetime.now()
                
                if now <= end_time + timedelta(minutes=1):
                    continue
                
                asset = pos['asset']
                side = pos['side']
                entry_crypto_price = pos.get('target_price', 0) or self._entry_prices.get(pos['id'], 0)
                current_crypto_price = crypto_prices.get(asset, 0)
                
                if entry_crypto_price <= 0 or current_crypto_price <= 0:
                    logger.warning(f"Missing price data for position {pos['id']}")
                    continue
                
                price_went_up = current_crypto_price > entry_crypto_price
                won = price_went_up if side == "Up" else not price_went_up
                
                pnl = pos['shares'] - pos['amount_usd'] if won else -pos['amount_usd']
                exit_price = 1.0 if won else 0.0
                
                resolve_position(pos['id'], won, exit_price)
                self.risk_manager.record_pnl(pnl)
                
                # Learning feedback
                if pos['id'] in self._position_signals:
                    signals, predicted_prob, bet_side = self._position_signals[pos['id']]
                    actual_direction = "Up" if price_went_up else "Down"
                    self.signal_aggregator.record_trade_result(
                        individual_signals=signals,
                        won=won,
                        pnl=pnl,
                        actual_direction=actual_direction
                    )
                    del self._position_signals[pos['id']]
                
                predicted_prob = pos.get('true_prob', 0.5)
                if predicted_prob and predicted_prob > 0:
                    self.calibrator.record_outcome(
                        predicted_probability=predicted_prob,
                        won=won,
                        pnl=pnl
                    )
                
                # Timing optimizer update
                try:
                    start_time_str = pos.get('start_time', '')
                    if '+' in start_time_str or 'Z' in start_time_str:
                        start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                    else:
                        start_time = datetime.fromisoformat(start_time_str)
                    
                    timing_bucket = pos.get('timing_bucket', '')
                    bucket_times = {
                        "1-2min": 90, "2-3min": 150, "3-5min": 240, "5-7min": 360
                    }
                    time_at_entry = bucket_times.get(timing_bucket, 180)
                    
                    self.timing_optimizer.record_result(
                        time_left_at_entry=time_at_entry,
                        won=won,
                        pnl=pnl,
                        wagered=pos['amount_usd']
                    )
                except Exception as e:
                    logger.debug(f"Error recording timing result: {e}")
                
                price_change = ((current_crypto_price - entry_crypto_price) / entry_crypto_price) * 100
                result = "WON ✅" if won else "LOST ❌"
                
                logger.info(
                    f"[RESOLVED] {asset} {side} {result} | "
                    f"Price: ${entry_crypto_price:.2f} → ${current_crypto_price:.2f} ({price_change:+.2f}%) | "
                    f"P&L: ${pnl:+.2f} | "
                    f"Predicted: {predicted_prob:.1%} | "
                    f"Actual: {'WON' if won else 'LOST'}"
                )
                
                if pos['id'] in self._entry_prices:
                    del self._entry_prices[pos['id']]
                    
            except Exception as e:
                logger.error(f"Error resolving position {pos['id']}: {e}")

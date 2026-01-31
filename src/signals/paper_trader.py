"""
Paper Trading System

Tracks signals without real money to validate strategy performance.
Calculates theoretical PnL for both Polymarket and leverage trading.

Features:
- Position sizing based on Kelly Criterion
- Pre-trade validation (recent performance check)
- Risk limits enforcement
- Dual tracking: Polymarket binary + Leverage trading
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
from enum import Enum
import aiohttp
import json

from .live_predictor import LivePredictor, PredictionDirection, PredictionSignal

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class TradeType(Enum):
    POLYMARKET = "polymarket"  # Binary prediction: win/lose
    LEVERAGE = "leverage"      # 2x leveraged crypto trading


@dataclass
class PaperPosition:
    """A paper trading position."""
    id: str
    symbol: str
    direction: str  # UP or DOWN
    trade_type: TradeType
    
    # Entry
    entry_time: datetime
    entry_price: float  # Crypto price at entry
    position_size_usd: float
    
    # For Polymarket
    polymarket_odds: float = 0.50  # What we'd pay for the bet
    
    # For Leverage
    leverage: float = 2.0
    
    # Signal info
    confidence: float = 0.0
    accuracy_estimate: float = 0.50
    reasoning: str = ""
    
    # Exit (filled when closed)
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    won: Optional[bool] = None
    
    # Status
    is_open: bool = True
    expiry_time: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "direction": self.direction,
            "trade_type": self.trade_type.value,
            "entry_time": self.entry_time.isoformat(),
            "entry_price": self.entry_price,
            "position_size_usd": self.position_size_usd,
            "polymarket_odds": self.polymarket_odds,
            "leverage": self.leverage,
            "confidence": self.confidence,
            "accuracy_estimate": self.accuracy_estimate,
            "reasoning": self.reasoning,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "won": self.won,
            "is_open": self.is_open,
            "expiry_time": self.expiry_time.isoformat() if self.expiry_time else None,
        }


@dataclass
class PaperRiskLimits:
    """Risk management limits for paper trading."""
    max_position_size_usd: float = 100.0
    min_position_size_usd: float = 5.0
    max_daily_loss_usd: float = 200.0
    max_concurrent_positions: int = 5
    max_positions_per_symbol: int = 2
    min_confidence: float = 0.50
    min_accuracy_estimate: float = 0.54  # Need edge over 50%
    max_consecutive_losses: int = 5
    cooldown_after_loss_seconds: int = 300  # 5 min cooldown after loss


@dataclass
class TradingStats:
    """Aggregate trading statistics."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    daily_pnl: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    consecutive_losses: int = 0
    last_trade_time: Optional[datetime] = None
    last_loss_time: Optional[datetime] = None
    
    # By type
    polymarket_trades: int = 0
    polymarket_pnl: float = 0.0
    leverage_trades: int = 0
    leverage_pnl: float = 0.0
    
    @property
    def win_rate(self) -> float:
        return self.wins / self.total_trades if self.total_trades > 0 else 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": f"{self.win_rate:.1%}",
            "total_pnl": self.total_pnl,
            "daily_pnl": self.daily_pnl,
            "best_trade": self.best_trade,
            "worst_trade": self.worst_trade,
            "consecutive_losses": self.consecutive_losses,
            "polymarket_trades": self.polymarket_trades,
            "polymarket_pnl": self.polymarket_pnl,
            "leverage_trades": self.leverage_trades,
            "leverage_pnl": self.leverage_pnl,
        }


class PositionSizer:
    """Calculate optimal position size using Kelly Criterion."""
    
    @staticmethod
    def kelly_size(
        bankroll: float,
        win_probability: float,
        win_payout: float,  # How much we win per $1 bet (e.g., 1.0 = double)
        loss_amount: float = 1.0,  # How much we lose per $1 bet
        fraction: float = 0.25  # Use quarter-Kelly for safety
    ) -> float:
        """
        Kelly Criterion: f* = (bp - q) / b
        where:
        - b = odds received on the wager (win_payout)
        - p = probability of winning
        - q = probability of losing (1 - p)
        """
        if win_probability <= 0.5:
            return 0  # No edge, don't bet
        
        p = win_probability
        q = 1 - p
        b = win_payout / loss_amount
        
        kelly = (b * p - q) / b
        
        # Use fractional Kelly for safety
        safe_kelly = kelly * fraction
        
        # Calculate position size
        position = bankroll * max(0, safe_kelly)
        
        return position
    
    @staticmethod
    def polymarket_size(
        bankroll: float,
        accuracy_estimate: float,
        confidence: float,
        max_size: float = 100.0,
        min_size: float = 5.0
    ) -> float:
        """
        Calculate Polymarket bet size.
        
        Polymarket pays out ~2x if you win at 50% odds.
        Adjust for actual odds.
        """
        # Win payout at 50% odds is roughly 1:1 (bet $50, win $50 profit)
        win_payout = 1.0  # Simplified
        
        kelly = PositionSizer.kelly_size(
            bankroll=bankroll,
            win_probability=accuracy_estimate,
            win_payout=win_payout,
            fraction=0.25  # Quarter Kelly
        )
        
        # Scale by confidence
        size = kelly * confidence
        
        # Clamp to limits
        return max(min_size, min(max_size, size))
    
    @staticmethod
    def leverage_size(
        bankroll: float,
        accuracy_estimate: float,
        confidence: float,
        leverage: float = 2.0,
        expected_move_pct: float = 0.5,  # Expected price move in %
        max_size: float = 100.0,
        min_size: float = 5.0
    ) -> float:
        """
        Calculate leveraged position size.
        
        For 2x leverage on a 0.5% expected move:
        - Win: +1% on position
        - Lose: -1% on position
        """
        # Expected return with leverage
        win_return = expected_move_pct * leverage / 100  # e.g., 0.01 (1%)
        
        # Adjust Kelly for smaller payouts
        kelly = PositionSizer.kelly_size(
            bankroll=bankroll,
            win_probability=accuracy_estimate,
            win_payout=win_return,
            loss_amount=win_return,  # Symmetric
            fraction=0.10  # More conservative for leverage
        )
        
        size = kelly * confidence
        
        return max(min_size, min(max_size, size))


class PaperTrader:
    """
    Paper trading system that tracks signals without real money.
    """
    
    def __init__(
        self,
        starting_bankroll: float = 1000.0,
        risk_limits: Optional[PaperRiskLimits] = None,
        supabase_url: str = "",
        supabase_key: str = ""
    ):
        self.bankroll = starting_bankroll
        self.starting_bankroll = starting_bankroll
        self.limits = risk_limits or PaperRiskLimits()
        self.predictor = LivePredictor()
        
        self.positions: List[PaperPosition] = []
        self.closed_positions: List[PaperPosition] = []
        self.stats = TradingStats()
        
        self.supabase_url = supabase_url
        self.supabase_key = supabase_key
        
        self._position_counter = 0
    
    def _generate_position_id(self) -> str:
        self._position_counter += 1
        return f"paper_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{self._position_counter}"
    
    async def fetch_current_price(self, symbol: str) -> float:
        """Fetch current price from Binance."""
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                return float(data["price"])
    
    def check_pre_trade_validation(self, signal: PredictionSignal) -> tuple[bool, str]:
        """
        Validate if we should take this trade.
        Returns (allowed, reason).
        """
        # Check minimum confidence
        if signal.confidence < self.limits.min_confidence:
            return False, f"Confidence {signal.confidence:.0%} < min {self.limits.min_confidence:.0%}"
        
        # Check minimum accuracy estimate
        if signal.accuracy_estimate < self.limits.min_accuracy_estimate:
            return False, f"Accuracy {signal.accuracy_estimate:.0%} < min {self.limits.min_accuracy_estimate:.0%}"
        
        # Check concurrent positions
        open_count = len([p for p in self.positions if p.is_open])
        if open_count >= self.limits.max_concurrent_positions:
            return False, f"Max concurrent positions ({self.limits.max_concurrent_positions}) reached"
        
        # Check positions per symbol
        symbol_count = len([p for p in self.positions if p.is_open and p.symbol == signal.symbol])
        if symbol_count >= self.limits.max_positions_per_symbol:
            return False, f"Max positions for {signal.symbol} reached"
        
        # Check daily loss limit
        if self.stats.daily_pnl <= -self.limits.max_daily_loss_usd:
            return False, f"Daily loss limit (${self.limits.max_daily_loss_usd}) reached"
        
        # Check consecutive losses
        if self.stats.consecutive_losses >= self.limits.max_consecutive_losses:
            # Check cooldown
            if self.stats.last_loss_time:
                cooldown_end = self.stats.last_loss_time + timedelta(seconds=self.limits.cooldown_after_loss_seconds)
                if datetime.now(timezone.utc) < cooldown_end:
                    remaining = (cooldown_end - datetime.now(timezone.utc)).seconds
                    return False, f"Cooldown active ({remaining}s remaining after {self.stats.consecutive_losses} losses)"
        
        # Check bankroll
        if self.bankroll < self.limits.min_position_size_usd:
            return False, f"Insufficient bankroll (${self.bankroll:.2f})"
        
        return True, "Passed all checks"
    
    async def open_position(
        self,
        signal: PredictionSignal,
        trade_type: TradeType = TradeType.POLYMARKET
    ) -> Optional[PaperPosition]:
        """
        Open a paper position based on signal.
        """
        # Validate
        allowed, reason = self.check_pre_trade_validation(signal)
        if not allowed:
            logger.info(f"Trade rejected: {reason}")
            return None
        
        # Get current price
        try:
            current_price = await self.fetch_current_price(signal.symbol)
        except Exception as e:
            logger.error(f"Failed to fetch price: {e}")
            return None
        
        # Calculate position size
        if trade_type == TradeType.POLYMARKET:
            size = PositionSizer.polymarket_size(
                bankroll=self.bankroll,
                accuracy_estimate=signal.accuracy_estimate,
                confidence=signal.confidence,
                max_size=self.limits.max_position_size_usd,
                min_size=self.limits.min_position_size_usd
            )
        else:
            size = PositionSizer.leverage_size(
                bankroll=self.bankroll,
                accuracy_estimate=signal.accuracy_estimate,
                confidence=signal.confidence,
                leverage=2.0,
                max_size=self.limits.max_position_size_usd,
                min_size=self.limits.min_position_size_usd
            )
        
        if size < self.limits.min_position_size_usd:
            logger.info(f"Position size ${size:.2f} below minimum")
            return None
        
        # Create position
        position = PaperPosition(
            id=self._generate_position_id(),
            symbol=signal.symbol,
            direction=signal.direction.value,
            trade_type=trade_type,
            entry_time=datetime.now(timezone.utc),
            entry_price=current_price,
            position_size_usd=size,
            polymarket_odds=0.50,  # Assume 50/50 odds
            leverage=2.0 if trade_type == TradeType.LEVERAGE else 1.0,
            confidence=signal.confidence,
            accuracy_estimate=signal.accuracy_estimate,
            reasoning=signal.reasoning,
            expiry_time=datetime.now(timezone.utc) + timedelta(minutes=signal.expiry_minutes)
        )
        
        self.positions.append(position)
        self.bankroll -= size  # Reserve the capital
        
        logger.info(
            f"OPENED {trade_type.value} position: {signal.symbol} {signal.direction.value} "
            f"@ ${current_price:,.2f}, size ${size:.2f}, expires {signal.expiry_minutes}min"
        )
        
        return position
    
    async def check_and_close_expired(self) -> List[PaperPosition]:
        """Check all open positions and close expired ones."""
        closed = []
        now = datetime.now(timezone.utc)
        
        for position in self.positions:
            if not position.is_open:
                continue
            
            if position.expiry_time and now >= position.expiry_time:
                # Close the position
                result = await self.close_position(position)
                if result:
                    closed.append(result)
        
        return closed
    
    async def close_position(self, position: PaperPosition) -> Optional[PaperPosition]:
        """Close a position and calculate PnL."""
        try:
            exit_price = await self.fetch_current_price(position.symbol)
        except Exception as e:
            logger.error(f"Failed to fetch exit price: {e}")
            return None
        
        position.exit_time = datetime.now(timezone.utc)
        position.exit_price = exit_price
        position.is_open = False
        
        # Calculate if we won
        price_went_up = exit_price > position.entry_price
        predicted_up = position.direction == "UP"
        position.won = price_went_up == predicted_up
        
        # Calculate PnL based on trade type
        if position.trade_type == TradeType.POLYMARKET:
            # Binary outcome: win or lose
            if position.won:
                # Win: get back bet + profit (at 50% odds, roughly 2x)
                payout = position.position_size_usd * (1 / position.polymarket_odds)
                position.pnl = payout - position.position_size_usd
            else:
                # Lose: lose the bet
                position.pnl = -position.position_size_usd
        else:
            # Leverage trading: profit/loss based on price move
            price_change_pct = (exit_price - position.entry_price) / position.entry_price
            
            # Adjust for direction
            if position.direction == "DOWN":
                price_change_pct = -price_change_pct
            
            # Apply leverage
            leveraged_return = price_change_pct * position.leverage
            position.pnl = position.position_size_usd * leveraged_return
        
        # Update bankroll
        self.bankroll += position.position_size_usd + position.pnl
        
        # Update stats
        self._update_stats(position)
        
        # Move to closed
        self.closed_positions.append(position)
        
        logger.info(
            f"CLOSED {position.trade_type.value}: {position.symbol} {position.direction} "
            f"{'WON' if position.won else 'LOST'} ${position.pnl:+.2f} "
            f"(entry ${position.entry_price:,.2f} -> exit ${exit_price:,.2f})"
        )
        
        return position
    
    def _update_stats(self, position: PaperPosition):
        """Update trading statistics after closing a position."""
        self.stats.total_trades += 1
        self.stats.total_pnl += position.pnl
        self.stats.daily_pnl += position.pnl
        self.stats.last_trade_time = position.exit_time
        
        if position.won:
            self.stats.wins += 1
            self.stats.consecutive_losses = 0
            if position.pnl > self.stats.best_trade:
                self.stats.best_trade = position.pnl
        else:
            self.stats.losses += 1
            self.stats.consecutive_losses += 1
            self.stats.last_loss_time = position.exit_time
            if position.pnl < self.stats.worst_trade:
                self.stats.worst_trade = position.pnl
        
        if position.trade_type == TradeType.POLYMARKET:
            self.stats.polymarket_trades += 1
            self.stats.polymarket_pnl += position.pnl
        else:
            self.stats.leverage_trades += 1
            self.stats.leverage_pnl += position.pnl
    
    def get_open_positions(self) -> List[Dict]:
        return [p.to_dict() for p in self.positions if p.is_open]
    
    def get_closed_positions(self, limit: int = 50) -> List[Dict]:
        return [p.to_dict() for p in self.closed_positions[-limit:]]
    
    def get_stats(self) -> Dict:
        return {
            **self.stats.to_dict(),
            "bankroll": self.bankroll,
            "starting_bankroll": self.starting_bankroll,
            "total_return": f"{((self.bankroll / self.starting_bankroll) - 1) * 100:+.1f}%",
        }
    
    def reset_daily_stats(self):
        """Reset daily stats (call at midnight)."""
        self.stats.daily_pnl = 0.0
        self.stats.consecutive_losses = 0


# Convenience function for running paper trading
async def run_paper_trading(
    check_interval: int = 10,
    starting_bankroll: float = 1000.0
):
    """Run paper trading simulation."""
    trader = PaperTrader(starting_bankroll=starting_bankroll)
    
    logger.info("=" * 60)
    logger.info("PAPER TRADING MODE - No real money")
    logger.info(f"Starting bankroll: ${starting_bankroll:,.2f}")
    logger.info(f"Check interval: {check_interval}s")
    logger.info("=" * 60)
    
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    
    while True:
        try:
            # Check for new signals
            for symbol in symbols:
                signal = await trader.predictor.generate_signal(symbol, expiry_minutes=7)
                
                if signal.direction != PredictionDirection.NO_SIGNAL:
                    # Open both Polymarket and Leverage positions
                    await trader.open_position(signal, TradeType.POLYMARKET)
                    await trader.open_position(signal, TradeType.LEVERAGE)
            
            # Check and close expired positions
            closed = await trader.check_and_close_expired()
            
            # Log status periodically
            stats = trader.get_stats()
            open_count = len(trader.get_open_positions())
            
            if closed or open_count > 0:
                logger.info(
                    f"Bankroll: ${stats['bankroll']:,.2f} | "
                    f"Open: {open_count} | "
                    f"Trades: {stats['total_trades']} | "
                    f"Win Rate: {stats['win_rate']} | "
                    f"PnL: ${stats['total_pnl']:+.2f}"
                )
            
            await asyncio.sleep(check_interval)
            
        except KeyboardInterrupt:
            logger.info("Shutting down paper trading...")
            break
        except Exception as e:
            logger.error(f"Error: {e}")
            await asyncio.sleep(check_interval)


if __name__ == "__main__":
    asyncio.run(run_paper_trading())

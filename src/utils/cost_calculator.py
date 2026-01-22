"""
Cost calculator for arbitrage profitability analysis.
Accounts for all fees, gas costs, and slippage.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeCosts:
    """Breakdown of all costs for a trade."""
    clob_fee: float  # CLOB trading fee
    merge_gas: float  # On-chain merge gas cost
    swap_spread: float  # USDC swap spread if applicable
    buffer: float  # Safety buffer for slippage
    total: float  # Total costs


@dataclass
class ArbitrageAnalysis:
    """Complete analysis of an arbitrage opportunity."""
    gross_edge: float  # Raw price difference
    costs: TradeCosts
    net_edge: float  # Edge after costs
    net_edge_bps: float  # Net edge in basis points
    is_profitable: bool
    potential_profit: float  # In USDC for given position size


class CostCalculator:
    """
    Calculator for arbitrage costs and profitability.
    
    Polymarket fee structure:
    - Taker fee: 0.2% (20 bps)
    - Maker fee: 0% (currently)
    - Merge gas: ~$0.01-0.03 on Polygon
    """
    
    def __init__(
        self,
        taker_fee_bps: int = 20,
        maker_fee_bps: int = 0,
        merge_gas_usd: float = 0.02,
        swap_spread_bps: int = 5,
        safety_buffer_bps: int = 10
    ):
        """
        Initialize cost calculator.
        
        Args:
            taker_fee_bps: Taker fee in basis points (20 = 0.2%)
            maker_fee_bps: Maker fee in basis points
            merge_gas_usd: Estimated gas cost for merge in USD
            swap_spread_bps: USDC/USDC.e swap spread in bps
            safety_buffer_bps: Additional buffer for slippage
        """
        self.taker_fee_bps = taker_fee_bps
        self.maker_fee_bps = maker_fee_bps
        self.merge_gas_usd = merge_gas_usd
        self.swap_spread_bps = swap_spread_bps
        self.safety_buffer_bps = safety_buffer_bps
    
    def calculate_binary_arb(
        self,
        yes_ask: float,
        no_ask: float,
        position_size: float,
        use_maker: bool = False
    ) -> ArbitrageAnalysis:
        """
        Calculate profitability for binary (YES/NO) arbitrage.
        
        Binary arbitrage: Buy YES + NO tokens, merge for $1.00
        Profit if: yes_ask + no_ask + fees < $1.00
        
        Args:
            yes_ask: Best ask price for YES token
            no_ask: Best ask price for NO token
            position_size: Size in USDC to trade
            use_maker: Whether using maker orders (lower fees)
        
        Returns:
            Complete arbitrage analysis
        """
        # Calculate gross edge (before fees)
        combined_cost = yes_ask + no_ask
        gross_edge = 1.0 - combined_cost
        
        # Calculate fees
        fee_bps = self.maker_fee_bps if use_maker else self.taker_fee_bps
        # Pay fee on both legs
        clob_fee = (fee_bps / 10000) * 2  # Fee on both YES and NO purchase
        
        # Fixed costs per trade
        merge_gas = self.merge_gas_usd / position_size  # Normalize to per-dollar
        
        # Swap spread (if converting USDC)
        swap_spread = self.swap_spread_bps / 10000
        
        # Safety buffer
        buffer = self.safety_buffer_bps / 10000
        
        # Total costs
        total_costs = clob_fee + merge_gas + swap_spread + buffer
        
        costs = TradeCosts(
            clob_fee=clob_fee,
            merge_gas=merge_gas,
            swap_spread=swap_spread,
            buffer=buffer,
            total=total_costs
        )
        
        # Net edge
        net_edge = gross_edge - total_costs
        net_edge_bps = net_edge * 10000
        
        # Potential profit
        potential_profit = net_edge * position_size
        
        return ArbitrageAnalysis(
            gross_edge=gross_edge,
            costs=costs,
            net_edge=net_edge,
            net_edge_bps=net_edge_bps,
            is_profitable=net_edge > 0,
            potential_profit=potential_profit
        )
    
    def calculate_categorical_arb(
        self,
        outcome_asks: list[float],
        position_size: float,
        use_maker: bool = False
    ) -> ArbitrageAnalysis:
        """
        Calculate profitability for categorical (multi-outcome) arbitrage.
        
        Categorical arbitrage: Buy all outcomes, guaranteed $1.00 payout
        Profit if: sum(all_asks) + fees < $1.00
        
        Args:
            outcome_asks: List of best ask prices for each outcome
            position_size: Size in USDC to trade
            use_maker: Whether using maker orders
        
        Returns:
            Complete arbitrage analysis
        """
        num_outcomes = len(outcome_asks)
        combined_cost = sum(outcome_asks)
        gross_edge = 1.0 - combined_cost
        
        # Fee on each leg
        fee_bps = self.maker_fee_bps if use_maker else self.taker_fee_bps
        clob_fee = (fee_bps / 10000) * num_outcomes
        
        # Merge gas (one merge covers all outcomes)
        merge_gas = self.merge_gas_usd / position_size
        
        swap_spread = self.swap_spread_bps / 10000
        buffer = self.safety_buffer_bps / 10000
        
        # Higher buffer for categorical due to partial fill risk
        categorical_buffer = buffer * num_outcomes
        
        total_costs = clob_fee + merge_gas + swap_spread + categorical_buffer
        
        costs = TradeCosts(
            clob_fee=clob_fee,
            merge_gas=merge_gas,
            swap_spread=swap_spread,
            buffer=categorical_buffer,
            total=total_costs
        )
        
        net_edge = gross_edge - total_costs
        net_edge_bps = net_edge * 10000
        potential_profit = net_edge * position_size
        
        return ArbitrageAnalysis(
            gross_edge=gross_edge,
            costs=costs,
            net_edge=net_edge,
            net_edge_bps=net_edge_bps,
            is_profitable=net_edge > 0,
            potential_profit=potential_profit
        )
    
    def minimum_edge_for_profit(
        self,
        position_size: float,
        num_outcomes: int = 2,
        use_maker: bool = False
    ) -> float:
        """
        Calculate minimum gross edge needed for profitability.
        
        Args:
            position_size: Trade size in USDC
            num_outcomes: Number of outcomes (2 for binary)
            use_maker: Whether using maker orders
        
        Returns:
            Minimum edge in decimal (e.g., 0.005 = 0.5%)
        """
        fee_bps = self.maker_fee_bps if use_maker else self.taker_fee_bps
        clob_fee = (fee_bps / 10000) * num_outcomes
        merge_gas = self.merge_gas_usd / position_size
        swap_spread = self.swap_spread_bps / 10000
        buffer = (self.safety_buffer_bps / 10000) * (num_outcomes / 2)
        
        return clob_fee + merge_gas + swap_spread + buffer

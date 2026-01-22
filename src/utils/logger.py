"""
Structured logging for Polymarket Arbitrage Bot.
Supports JSON logging for Azure Log Analytics integration.
"""

import logging
import sys
from typing import Optional
from pythonjsonlogger import jsonlogger


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter with additional fields."""
    
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record['level'] = record.levelname
        log_record['logger'] = record.name
        log_record['timestamp'] = self.formatTime(record, self.datefmt)


def setup_logging(
    level: str = "INFO",
    json_format: bool = True,
    logger_name: Optional[str] = None
) -> logging.Logger:
    """
    Set up logging configuration.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        json_format: Whether to use JSON format (for Azure Log Analytics)
        logger_name: Optional specific logger name
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(logger_name or "polymarket_arb")
    logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    logger.handlers = []
    
    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper()))
    
    if json_format:
        formatter = CustomJsonFormatter(
            '%(timestamp)s %(level)s %(name)s %(message)s'
        )
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger with the given name."""
    return logging.getLogger(f"polymarket_arb.{name}")


class TradeLogger:
    """Specialized logger for trade-related events."""
    
    def __init__(self):
        self.logger = get_logger("trades")
    
    def opportunity_detected(
        self,
        market_id: str,
        arb_type: str,
        edge_bps: float,
        potential_profit: float
    ):
        """Log when an arbitrage opportunity is detected."""
        self.logger.info(
            "Arbitrage opportunity detected",
            extra={
                "event": "opportunity_detected",
                "market_id": market_id,
                "arb_type": arb_type,
                "edge_bps": edge_bps,
                "potential_profit_usd": potential_profit
            }
        )
    
    def order_placed(
        self,
        trade_id: str,
        market_id: str,
        side: str,
        size: float,
        price: float
    ):
        """Log when an order is placed."""
        self.logger.info(
            "Order placed",
            extra={
                "event": "order_placed",
                "trade_id": trade_id,
                "market_id": market_id,
                "side": side,
                "size": size,
                "price": price
            }
        )
    
    def order_filled(
        self,
        trade_id: str,
        market_id: str,
        fill_price: float,
        fill_size: float
    ):
        """Log when an order is filled."""
        self.logger.info(
            "Order filled",
            extra={
                "event": "order_filled",
                "trade_id": trade_id,
                "market_id": market_id,
                "fill_price": fill_price,
                "fill_size": fill_size
            }
        )
    
    def trade_completed(
        self,
        trade_id: str,
        market_id: str,
        expected_profit: float,
        actual_profit: float,
        latency_ms: float
    ):
        """Log when a complete arbitrage trade is done."""
        self.logger.info(
            "Trade completed",
            extra={
                "event": "trade_completed",
                "trade_id": trade_id,
                "market_id": market_id,
                "expected_profit_usd": expected_profit,
                "actual_profit_usd": actual_profit,
                "latency_ms": latency_ms,
                "slippage_usd": expected_profit - actual_profit
            }
        )
    
    def trade_failed(
        self,
        trade_id: str,
        market_id: str,
        reason: str,
        error: Optional[str] = None
    ):
        """Log when a trade fails."""
        self.logger.error(
            "Trade failed",
            extra={
                "event": "trade_failed",
                "trade_id": trade_id,
                "market_id": market_id,
                "reason": reason,
                "error": error
            }
        )
    
    def merge_completed(
        self,
        trade_id: str,
        tx_hash: str,
        gas_used: int,
        gas_cost_usd: float
    ):
        """Log when tokens are merged on-chain."""
        self.logger.info(
            "Token merge completed",
            extra={
                "event": "merge_completed",
                "trade_id": trade_id,
                "tx_hash": tx_hash,
                "gas_used": gas_used,
                "gas_cost_usd": gas_cost_usd
            }
        )

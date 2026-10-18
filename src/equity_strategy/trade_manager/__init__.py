"""
Trade Management module for take-profit and stop-loss price calculation.
"""
from equity_strategy.trade_manager.atr import ATRTradeManager
from equity_strategy.trade_manager.base import BaseTradeManager
from equity_strategy.trade_manager.fixed import FixedTPSLTradeManager

__all__ = [
    "BaseTradeManager",
    "FixedTPSLTradeManager",
    "ATRTradeManager",
]

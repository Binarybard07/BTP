"""
Risk management module for equity quantitative strategies.
"""
from equity_strategy.risk_management.base import BaseRiskManager
from equity_strategy.risk_management.fixed_percentage import FixedPercentageRiskManager

__all__ = [
    "BaseRiskManager",
    "FixedPercentageRiskManager",
]

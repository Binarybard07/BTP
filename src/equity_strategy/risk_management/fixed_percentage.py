"""
Fixed-percentage risk management implementation.
"""
from equity_strategy.risk_management.base import BaseRiskManager


class FixedPercentageRiskManager(BaseRiskManager):
    """
    Fixed-percentage capital deployment risk manager.

    Allocates a fixed fraction of total portfolio equity as the capital
    deployment ceiling for rebalancing.
    """

    def __init__(self, max_deployment_pct: float = 1.0) -> None:
        val = float(max_deployment_pct)
        if not (0.0 <= val <= 1.0):
            raise ValueError(f"max_deployment_pct must be between 0.0 and 1.0, got {max_deployment_pct}")
        self.max_deployment_pct = val

    def calculate_deployment_budget(self, portfolio_equity: float) -> float:
        equity = float(portfolio_equity)
        if equity < 0.0:
            raise ValueError(f"portfolio_equity cannot be negative, got {portfolio_equity}")
        return equity * self.max_deployment_pct

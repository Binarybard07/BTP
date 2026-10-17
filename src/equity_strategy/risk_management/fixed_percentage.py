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
        self.max_deployment_pct = float(max_deployment_pct)

    def calculate_deployment_budget(self, portfolio_equity: float) -> float:
        return float(portfolio_equity * self.max_deployment_pct)

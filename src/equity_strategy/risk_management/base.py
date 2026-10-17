"""
Risk management base interfaces for equity quantitative strategies.
"""
from abc import ABC, abstractmethod


class BaseRiskManager(ABC):
    """
    Abstract base class for portfolio risk management and capital allocation.

    Responsible for determining the total portfolio capital permitted for deployment,
    taking authoritative portfolio equity as input and returning the deployable budget.

    Separation of concerns:
    - Risk Management: determines how much total portfolio capital may be deployed.
    - Portfolio Construction: determines individual stock target weights.
    - Rebalancer: executes orders to align current holdings with target weights.
    """

    @abstractmethod
    def calculate_deployment_budget(self, portfolio_equity: float) -> float:
        """
        Calculates the amount of portfolio capital permitted for deployment.

        Parameters:
            portfolio_equity: Current total portfolio equity from NautilusTrader.

        Returns:
            float: Permitted deployment budget in account base currency.
        """
        pass

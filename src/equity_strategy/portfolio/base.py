from abc import ABC, abstractmethod
import pandas as pd

class PortfolioConstructor(ABC):
    """
    Abstract interface for portfolio construction algorithms.
    Future implementations (e.g. RiskBasedPortfolio, GSAOptimizer) will inherit from this base class.
    """

    @abstractmethod
    def construct_portfolio(self, df_signals: pd.DataFrame) -> pd.DataFrame:
        """
        Takes signal-scored DataFrame for a given rebalance date and returns portfolio allocation DataFrame.
        """
        pass

"""
V1 Indian Equity Quantitative Trading Research & Backtesting System
"""
from equity_strategy.logging_config import configure_logging
from equity_strategy.rebalancing import (
    BaseRebalanceStrategy,
    MonthlyRebalanceStrategy,
    Rebalancer,
)

__version__ = "0.1.0"

# Configure centralized default logging for the package
configure_logging()

__all__ = [
    "BaseRebalanceStrategy",
    "MonthlyRebalanceStrategy",
    "Rebalancer",
    "configure_logging",
    "__version__",
]

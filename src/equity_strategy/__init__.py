"""
V1 Indian Equity Quantitative Trading Research & Backtesting System
"""
from equity_strategy.logging_config import configure_logging
from equity_strategy.rebalancing import (
    BaseRebalanceStrategy,
    MonthlyRebalanceStrategy,
    Rebalancer,
)
from equity_strategy.position_manager import PositionManager
from equity_strategy.trade_manager import (
    ATRTradeManager,
    BaseTradeManager,
    FixedTPSLTradeManager,
)

__version__ = "0.1.0"

# Configure centralized default logging for the package
configure_logging()

__all__ = [
    "BaseRebalanceStrategy",
    "MonthlyRebalanceStrategy",
    "Rebalancer",
    "BaseTradeManager",
    "FixedTPSLTradeManager",
    "ATRTradeManager",
    "PositionManager",
    "configure_logging",
    "__version__",
]

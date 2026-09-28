"""
V1 Indian Equity Quantitative Trading Research & Backtesting System
"""
from equity_strategy.logging_config import configure_logging

__version__ = "0.1.0"

# Configure centralized default logging for the package
configure_logging()

__all__ = [
    "configure_logging",
    "__version__",
]


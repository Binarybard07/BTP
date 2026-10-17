"""
Minimal reusable base strategy and configuration for equity trading on NautilusTrader.
"""
from typing import Any, List
import pandas as pd

from nautilus_trader.model import BarType
from nautilus_trader.trading import Strategy, StrategyConfig

class BaseExecutionConfig(StrategyConfig):
    """
    Minimal shared configuration for equity strategies.

    Parameters:
        bar_types: List of Nautilus BarType subscriptions for the active universe.
        venue: Exchange venue code (default: 'NSE').
        currency: Settlement currency (default: 'INR').
        exchange_tz: Exchange timezone identifier (default: 'Asia/Kolkata').
    """

    def __init__(
        self,
        bar_types: List[BarType],
        venue: str = "NSE",
        currency: str = "INR",
        exchange_tz: str = "Asia/Kolkata",
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.bar_types = bar_types
        self.venue = venue
        self.currency = currency
        self.exchange_tz = exchange_tz
        for key, value in kwargs.items():
            setattr(self, key, value)


class BaseExecutionStrategy(Strategy):
    """
    Minimal shared NautilusTrader strategy for equity strategies.

    Responsibilities:
    - Provides shared universe configuration (bar_types, venue, currency, exchange_tz).
    - Subscribes to bar streams for all configured bar types on start.
    - Normalizes event timestamps to exchange session dates.
    """

    def __init__(self, config: BaseExecutionConfig) -> None:
        super().__init__(config=config)
        self.bar_types: List[BarType] = getattr(config, "bar_types", [])
        self.venue: str = getattr(config, "venue", "NSE")
        self.currency: str = getattr(config, "currency", "INR")
        self.exchange_tz: str = getattr(config, "exchange_tz", "Asia/Kolkata")

    def on_start(self) -> None:
        """Nautilus lifecycle callback: subscribes to all configured bar types."""
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def extract_session_date(self, item: Any) -> pd.Timestamp:
        """Normalizes a Nautilus Bar or QuoteTick timestamp to calendar session Timestamp."""
        if hasattr(item, "ts_init") or hasattr(item, "ts_event"):
            ts = getattr(item, "ts_init", None) or getattr(item, "ts_event")
            dt = pd.to_datetime(ts, unit="ns", utc=True).tz_convert(self.exchange_tz)
            return pd.Timestamp(dt.date())
        return pd.Timestamp(pd.to_datetime(item).date())


# Reusable alias for strategy authoring
BaseStrategy = BaseExecutionStrategy
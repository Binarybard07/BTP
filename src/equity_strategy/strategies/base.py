"""
Minimal reusable base strategy and configuration for equity trading on NautilusTrader.
"""
from typing import Any, List
import pandas as pd

from nautilus_trader.model import BarType
from nautilus_trader.trading import Strategy, StrategyConfig


class BaseStrategyConfig(StrategyConfig):
    """
    Minimal shared execution configuration for equity strategies on NautilusTrader.

    Parameters:
        bar_types: Optional list of Nautilus BarType subscriptions for the active universe.
        venue: Exchange venue code (default: 'NSE').
        currency: Settlement currency (default: 'INR').
        exchange_tz: Exchange timezone identifier (default: 'Asia/Kolkata').
    """

    def __init__(
        self,
        venue: str = "NSE",
        currency: str = "INR",
        exchange_tz: str = "Asia/Kolkata",
    ) -> None:
        super().__init__()
        self.venue: str = venue
        self.currency: str = currency
        self.exchange_tz: str = exchange_tz


class BaseStrategy(Strategy):
    """
    Minimal shared NautilusTrader strategy for equity strategies.

    Responsibilities:
    - Provides shared universe configuration (bar_types, venue, currency, exchange_tz).
    - Subscribes to bar streams for all configured bar types on start.
    - Normalizes event timestamps to exchange session dates.
    """

    def __init__(self, config: BaseStrategyConfig, bar_types: List[BarType]) -> None:
        super().__init__(config=config)
        self.bar_types = bar_types
        self.venue: str = config.venue
        self.currency: str = config.currency
        self.exchange_tz: str = config.exchange_tz

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


__all__ = [
    "BaseStrategy",
    "BaseStrategyConfig",
]

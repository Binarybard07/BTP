import logging
from typing import Any, Optional
import pandas as pd

from equity_strategy.rebalancing.base import BaseRebalanceStrategy

logger = logging.getLogger(__name__)


class MonthlyRebalanceStrategy(BaseRebalanceStrategy):
    """
    Causal monthly rebalance scheduler.

    Detects when a new calendar month begins and returns the previous session date
    as the completed month-end rebalance decision date without scanning future data.
    """

    def __init__(self, timezone: str = "Asia/Kolkata"):
        self.timezone = timezone
        self._current_date: Optional[pd.Timestamp] = None

    def reset(self) -> None:
        """Resets scheduler state."""
        self._current_date = None

    def _extract_date(self, item: Any) -> pd.Timestamp:
        """Normalizes Nautilus Bar or date-like object to a timezone-appropriate Timestamp."""
        if hasattr(item, "ts_init") or hasattr(item, "ts_event"):
            ts = getattr(item, "ts_init", None) or getattr(item, "ts_event")
            dt = pd.to_datetime(ts, unit="ns", utc=True).tz_convert(self.timezone)
            return pd.Timestamp(dt.date())
        return pd.Timestamp(pd.to_datetime(item).date())

    def step(self, item: Any) -> Optional[pd.Timestamp]:
        """
        Processes a session observation.

        Returns:
            Optional[pd.Timestamp]: The prior month-end session date if a new calendar
                                    month has started, else None.
        """
        date = self._extract_date(item)

        if self._current_date is None:
            self._current_date = date
            return None

        # Same session (absorbs multi-instrument bars on the same day)
        if date == self._current_date:
            return None

        # Ignore out-of-order observations
        if date < self._current_date:
            return None

        prev_date = self._current_date
        self._current_date = date

        # Detect transition to a new calendar month
        if (date.year, date.month) != (prev_date.year, prev_date.month):
            logger.debug(
                "New month started (%s -> %s). Rebalance decision date: %s",
                prev_date.strftime("%Y-%m"),
                date.strftime("%Y-%m"),
                prev_date.strftime("%Y-%m-%d"),
            )
            return prev_date

        return None

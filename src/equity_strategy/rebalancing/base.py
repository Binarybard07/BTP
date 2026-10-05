from abc import ABC, abstractmethod
from typing import Any, Iterable, List, Optional
import pandas as pd


class BaseRebalanceStrategy(ABC):
    """
    Minimal interface for rebalance scheduling.
    Determines whether a rebalance should occur from incoming market data.
    """

    @abstractmethod
    def step(self, item: Any) -> Optional[pd.Timestamp]:
        """
        Processes a market observation or session date.
        Returns the rebalance decision date if a rebalance should occur, else None.
        """
        pass

    @abstractmethod
    def reset(self) -> None:
        """Resets all internal scheduler state."""
        pass

    def generate_rebalance_dates(self, stream: Iterable[Any]) -> List[pd.Timestamp]:
        """
        Causally processes a stream of dates or bars and returns rebalance decision dates.
        """
        self.reset()
        rebalance_dates: List[pd.Timestamp] = []
        for item in stream:
            reb_date = self.step(item)
            if reb_date is not None:
                rebalance_dates.append(reb_date)
        return rebalance_dates

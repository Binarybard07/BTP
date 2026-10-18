"""
Abstract base class for take-profit and stop-loss calculation trade managers.
"""
from abc import ABC, abstractmethod
from typing import Tuple, Union

from nautilus_trader.model import InstrumentId


class BaseTradeManager(ABC):
    """
    Abstract base interface for trade managers calculating take-profit (TP)
    and stop-loss (SL) price levels.
    """

    @abstractmethod
    def calculate_tp_sl(
        self,
        reference_price: float,
        instrument_id: Union[InstrumentId, str],
    ) -> Tuple[float, float]:
        """
        Calculates (take_profit_price, stop_loss_price) for a long position.

        Parameters
        ----------
        reference_price : float
            The entry or execution reference price (must be positive).
        instrument_id : Union[InstrumentId, str]
            The instrument identifier.

        Returns
        -------
        Tuple[float, float]
            A tuple of (take_profit_price, stop_loss_price).
        """
        ...

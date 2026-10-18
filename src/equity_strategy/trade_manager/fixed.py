"""
Fixed-percentage take-profit and stop-loss trade manager.
"""
from typing import Tuple, Union

from nautilus_trader.model import InstrumentId

from equity_strategy.trade_manager.base import BaseTradeManager


class FixedTPSLTradeManager(BaseTradeManager):
    """
    Calculates take-profit and stop-loss prices using fixed percentage offsets.
    """

    def __init__(
        self,
        take_profit_pct: float = 0.10,
        stop_loss_pct: float = 0.05,
    ) -> None:
        if take_profit_pct <= 0.0:
            raise ValueError(f"take_profit_pct must be > 0, got {take_profit_pct}")
        if not (0.0 < stop_loss_pct < 1.0):
            raise ValueError(f"stop_loss_pct must be between 0 and 1, got {stop_loss_pct}")

        self.take_profit_pct = float(take_profit_pct)
        self.stop_loss_pct = float(stop_loss_pct)

    def calculate_tp_sl(
        self,
        reference_price: float,
        instrument_id: Union[InstrumentId, str],
    ) -> Tuple[float, float]:
        price = float(reference_price)
        if price <= 0.0:
            raise ValueError(f"reference_price must be > 0, got {price}")

        tp = price * (1.0 + self.take_profit_pct)
        sl = price * (1.0 - self.stop_loss_pct)

        if sl <= 0.0:
            raise ValueError(f"Calculated stop-loss price ({sl:.4f}) is non-positive.")

        return (tp, sl)

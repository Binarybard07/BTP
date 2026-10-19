"""
Fixed-percentage take-profit and stop-loss trade manager.
"""
from typing import Optional, Tuple, Union

from nautilus_trader.model import InstrumentId

from equity_strategy.trade_manager.base import BaseTradeManager


class FixedTPSLTradeManager(BaseTradeManager):
    """
    Calculates take-profit and stop-loss prices using fixed percentage offsets.
    """

    def __init__(
        self,
        take_profit_pct: Optional[float] = None,
        stop_loss_pct: Optional[float] = None,
    ) -> None:
        if take_profit_pct is not None:
            if take_profit_pct <= 0.0:
                raise ValueError(f"take_profit_pct must be > 0, got {take_profit_pct}")
            self.take_profit_pct: Optional[float] = float(take_profit_pct)
        else:
            self.take_profit_pct = None

        if stop_loss_pct is not None:
            if not (0.0 < stop_loss_pct < 1.0):
                raise ValueError(f"stop_loss_pct must be between 0 and 1, got {stop_loss_pct}")
            self.stop_loss_pct: Optional[float] = float(stop_loss_pct)
        else:
            self.stop_loss_pct = None

    def calculate_tp_sl(
        self,
        reference_price: float,
        instrument_id: Union[InstrumentId, str],
    ) -> Tuple[Optional[float], Optional[float]]:
        price = float(reference_price)
        if price <= 0.0:
            raise ValueError(f"reference_price must be > 0, got {price}")

        tp = price * (1.0 + self.take_profit_pct) if self.take_profit_pct is not None else None

        sl = None
        if self.stop_loss_pct is not None:
            sl = price * (1.0 - self.stop_loss_pct)
            if sl <= 0.0:
                raise ValueError(f"Calculated stop-loss price ({sl:.4f}) is non-positive.")

        return (tp, sl)

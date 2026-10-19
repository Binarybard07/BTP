"""
ATR-based take-profit and stop-loss trade manager using native NautilusTrader indicators.
"""
import math
from typing import Dict, Optional, Sequence, Tuple, Union

from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.model import BarType, InstrumentId
from nautilus_trader.trading import Strategy

from equity_strategy.trade_manager.base import BaseTradeManager


class ATRTradeManager(BaseTradeManager):
    """
    Calculates take-profit and stop-loss prices using NautilusTrader's native
    AverageTrueRange indicator for instrument-specific volatility.
    """

    def __init__(
        self,
        lookback_period: int = 14,
        take_profit_multiplier: Optional[float] = None,
        stop_loss_multiplier: Optional[float] = None,
        strategy: Optional[Strategy] = None,
        bar_types: Optional[Sequence[BarType]] = None,
    ) -> None:
        if lookback_period < 1:
            raise ValueError(f"lookback_period must be >= 1, got {lookback_period}")
        if take_profit_multiplier is not None:
            if take_profit_multiplier <= 0.0:
                raise ValueError(f"take_profit_multiplier must be > 0, got {take_profit_multiplier}")
            self.take_profit_multiplier: Optional[float] = float(take_profit_multiplier)
        else:
            self.take_profit_multiplier = None

        if stop_loss_multiplier is not None:
            if stop_loss_multiplier <= 0.0:
                raise ValueError(f"stop_loss_multiplier must be > 0, got {stop_loss_multiplier}")
            self.stop_loss_multiplier: Optional[float] = float(stop_loss_multiplier)
        else:
            self.stop_loss_multiplier = None

        self.lookback_period = int(lookback_period)
        self._indicators: Dict[InstrumentId, AverageTrueRange] = {}

        if strategy is not None and bar_types is not None:
            self.register(strategy, bar_types)

    def register(
        self,
        strategy: Strategy,
        bar_types: Sequence[BarType],
    ) -> None:
        """
        Registers an AverageTrueRange indicator for each BarType with the
        Nautilus Strategy runtime. Nautilus automatically updates the indicator
        as bars arrive.
        """
        for bar_type in bar_types:
            indicator = AverageTrueRange(self.lookback_period)
            strategy.register_indicator_for_bars(bar_type, indicator)
            self._indicators[bar_type.instrument_id] = indicator

    def calculate_tp_sl(
        self,
        reference_price: float,
        instrument_id: Union[InstrumentId, str],
    ) -> Tuple[Optional[float], Optional[float]]:
        price = float(reference_price)
        if price <= 0.0:
            raise ValueError(f"reference_price must be > 0, got {price}")

        if self.take_profit_multiplier is None and self.stop_loss_multiplier is None:
            return (None, None)

        inst_id = (
            instrument_id
            if isinstance(instrument_id, InstrumentId)
            else InstrumentId.from_str(str(instrument_id))
        )

        indicator = self._indicators.get(inst_id)
        if indicator is None:
            raise KeyError(f"No ATR indicator registered for instrument '{inst_id}'")

        if not indicator.initialized:
            raise ValueError(
                f"ATR indicator for '{inst_id}' is not yet initialized "
                f"({indicator.count}/{self.lookback_period} bars processed)."
            )

        atr = float(indicator.value)
        if atr <= 0.0 or math.isnan(atr):
            raise ValueError(f"ATR value for '{inst_id}' is non-positive or invalid: {atr}")

        tp = None
        if self.take_profit_multiplier is not None:
            tp = price + atr * self.take_profit_multiplier
            if tp <= 0.0:
                raise ValueError(
                    f"Calculated take-profit price ({tp:.4f}) is non-positive "
                    f"for reference_price {price:.4f} and ATR {atr:.4f}."
                )

        sl = None
        if self.stop_loss_multiplier is not None:
            sl = price - atr * self.stop_loss_multiplier
            if sl <= 0.0:
                raise ValueError(
                    f"Calculated stop-loss price ({sl:.4f}) is non-positive "
                    f"for reference_price {price:.4f} and ATR {atr:.4f}."
                )

        return (tp, sl)

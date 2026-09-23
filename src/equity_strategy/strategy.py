from __future__ import annotations

from decimal import Decimal
from typing import Dict, Tuple

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId, OrderSide
from nautilus_trader.trading import Strategy


class MACrossoverStrategyConfig(StrategyConfig):
    def __init__(
        self,
        *,
        instrument_id: InstrumentId,
        bar_type: BarType,
        trade_size: Decimal,
        short_ma_period: int = 50,
        long_ma_period: int = 200,
        **_kwargs: object,
    ) -> None:
        super().__init__()

        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.trade_size = trade_size
        self.short_ma_period = short_ma_period
        self.long_ma_period = long_ma_period


class MACrossoverStrategy(Strategy):
    """
    Long-only moving-average crossover strategy.

    Entry:
        previous MA50 <= previous MA200
        AND
        current MA50 > current MA200

    Exit:
        previous MA50 >= previous MA200
        AND
        current MA50 < current MA200
    """

    def __init__(
        self,
        config: MACrossoverStrategyConfig,
        feature_values: Dict[int, Tuple[float, float]],
    ) -> None:
        super().__init__(config)

        # Timestamp -> (MA50, MA200)
        self.feature_values = feature_values

        self.previous_ma50: float | None = None
        self.previous_ma200: float | None = None

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        """
        Called by Nautilus once for every incoming bar.
        """

        features = self.feature_values.get(bar.ts_event)

        if features is None:
            return

        current_ma50, current_ma200 = features

        # Feature warm-up period.
        if current_ma50 is None or current_ma200 is None:
            self.previous_ma50 = current_ma50
            self.previous_ma200 = current_ma200
            return

        # Need a previous valid observation to detect a crossover.
        if (
            self.previous_ma50 is None
            or self.previous_ma200 is None
        ):
            self.previous_ma50 = current_ma50
            self.previous_ma200 = current_ma200
            return

        bullish_cross = (
            self.previous_ma50 <= self.previous_ma200
            and current_ma50 > current_ma200
        )

        bearish_cross = (
            self.previous_ma50 >= self.previous_ma200
            and current_ma50 < current_ma200
        )

        if bullish_cross:
            self._enter_long()

        elif bearish_cross:
            self._exit_long()

        self.previous_ma50 = current_ma50
        self.previous_ma200 = current_ma200

    def _enter_long(self) -> None:
        if self.portfolio.is_net_long(self.config.instrument_id):
            return

        if not self.portfolio.is_net_flat(self.config.instrument_id):
            self.close_all_positions(self.config.instrument_id)

        instrument = self.cache.instrument(self.config.instrument_id)

        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=instrument.make_qty(self.config.trade_size),
        )

        self.submit_order(order)

    def _exit_long(self) -> None:
        if not self.portfolio.is_net_long(self.config.instrument_id):
            return

        self.close_all_positions(self.config.instrument_id)
import logging
from typing import Any, Dict, List, Optional, Sequence, Union
import pandas as pd

from nautilus_trader.execution import FeeModel, FillModel
from nautilus_trader.model import (
    Bar,
    BarType,
    Currency,
    InstrumentId,
    MarketOrder,
    OrderFilled,
    QuoteTick,
    Venue,
)
from nautilus_trader.trading import Strategy, StrategyConfig

from equity_strategy.rebalancing.rebalancer import Rebalancer

logger = logging.getLogger(__name__)


class BaseExecutionConfig(StrategyConfig):
    """
    Configuration for strategies running on NautilusTrader with direct execution.

    Parameters:
        bar_types: List of Nautilus BarType subscriptions for the active universe.
        all_trading_dates: Chronologically sorted unique trading dates across the universe.
        venue: Exchange venue code (default: 'NSE').
        currency: Settlement currency (default: 'INR').
        rebalancer: Optional execution Rebalancer instance.
        fee_model: Optional Nautilus FeeModel.
        fill_model: Optional Nautilus FillModel.
        exchange_tz: Exchange timezone identifier (default: 'Asia/Kolkata').
    """

    def __init__(
        self,
        bar_types: List[BarType],
        all_trading_dates: Optional[Sequence[pd.Timestamp]] = None,
        venue: str = "NSE",
        currency: str = "INR",
        rebalancer: Optional[Rebalancer] = None,
        fee_model: Optional[FeeModel] = None,
        fill_model: Optional[FillModel] = None,
        exchange_tz: str = "Asia/Kolkata",
        recorder: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.bar_types = bar_types
        self.all_trading_dates = list(all_trading_dates) if all_trading_dates is not None else []
        self.venue = venue
        self.currency = currency
        self.rebalancer = rebalancer
        self.fee_model = fee_model
        self.fill_model = fill_model
        self.exchange_tz = exchange_tz


class BaseExecutionStrategy(Strategy):
    """
    Reusable base NautilusTrader strategy for equity portfolio rebalancing
    with native Nautilus order execution, cost modeling, and fill reconciliation.

    Responsibilities:
    - Subscribes to universe daily bars (15:30 IST) and opening QuoteTicks (09:15 IST).
    - Initializes and wires the Rebalancer for direct native market order submission.
    - Attaches engine clock to fee and fill models.
    - Coordinates event lifecycle:
        * on_bar: tracks latest close prices per instrument.
        * on_quote: handles market quotes for native matching.
        * on_order_filled: links fill events with slippage and fee models.
    - Exposes direct rebalance execution for concrete strategies.
    """

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)

        self.bar_types: List[BarType] = getattr(config, "bar_types", [])
        self.all_trading_dates: List[pd.Timestamp] = getattr(config, "all_trading_dates", [])
        self.venue: str = getattr(config, "venue", "NSE")
        self.currency: str = getattr(config, "currency", "INR")
        self.rebalancer: Optional[Rebalancer] = getattr(config, "rebalancer", None)
        self.fee_model: Optional[FeeModel] = getattr(config, "fee_model", None)
        self.fill_model: Optional[FillModel] = getattr(config, "fill_model", None)
        self.exchange_tz: str = getattr(config, "exchange_tz", "Asia/Kolkata")

        # Fast date-to-index lookup for calendar calculations
        self.date_to_index: Dict[pd.Timestamp, int] = {
            d: i for i, d in enumerate(self.all_trading_dates)
        }

        # Symbol string to InstrumentId lookup
        self._symbol_to_instrument_id: Dict[str, InstrumentId] = {
            str(bt.instrument_id.symbol).upper(): bt.instrument_id for bt in self.bar_types
        }

        # Market state tracking: latest close price per instrument
        self._latest_prices: Dict[InstrumentId, float] = {}

    def on_start(self) -> None:
        """
        Nautilus lifecycle callback:
        - Subscribes to bars and opening quotes for all configured bar types.
        - Initializes and wires the Rebalancer to this strategy's execution environment.
        - Wires engine clock to fee and fill models.
        """
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)
            self.subscribe_quotes(bar_type.instrument_id)

        if self.rebalancer is None:
            self.rebalancer = Rebalancer(
                strategy=self,
                default_venue=self.venue,
                base_currency=self.currency,
            )
        else:
            self.rebalancer.strategy = self
            self.rebalancer.portfolio = self.portfolio
            self.rebalancer.cache = self.cache
            self.rebalancer.order_factory = self.order_factory
            self.rebalancer._submit_order_fn = self.submit_order
            if self.rebalancer.default_venue is None:
                self.rebalancer.default_venue = Venue(self.venue)
            if self.rebalancer.base_currency is None:
                self.rebalancer.base_currency = Currency.from_str(self.currency)

        # Attach engine simulation clock to fee and fill models
        if self.fee_model and hasattr(self.fee_model, "set_clock"):
            self.fee_model.set_clock(self.clock)
        if self.fill_model and hasattr(self.fill_model, "set_clock"):
            self.fill_model.set_clock(self.clock)

        logger.info("BaseExecutionStrategy initialized and subscribed to universe.")

    def extract_session_date(self, item: Any) -> pd.Timestamp:
        """Normalizes a Nautilus Bar or QuoteTick timestamp to calendar session Timestamp."""
        if hasattr(item, "ts_init") or hasattr(item, "ts_event"):
            ts = getattr(item, "ts_init", None) or getattr(item, "ts_event")
            dt = pd.to_datetime(ts, unit="ns", utc=True).tz_convert(self.exchange_tz)
            return pd.Timestamp(dt.date())
        return pd.Timestamp(pd.to_datetime(item).date())

    def _extract_date(self, item: Any) -> pd.Timestamp:
        """Compatibility alias for extract_session_date."""
        return self.extract_session_date(item)

    def on_bar(self, bar: Bar) -> None:
        """Default on_bar callback tracks latest close price for the incoming instrument."""
        inst_id = bar.bar_type.instrument_id
        self._latest_prices[inst_id] = float(bar.close.as_double())

    def on_quote(self, quote: QuoteTick) -> None:
        """Nautilus lifecycle callback for opening QuoteTicks (09:15 IST)."""
        pass

    def on_order_filled(self, event: OrderFilled) -> None:
        """
        Nautilus lifecycle callback when an order fill event is received.
        Delegates fill reconciliation to fee and slippage models.
        """
        if self.fill_model and hasattr(self.fill_model, "record_fill"):
            self.fill_model.record_fill(event)
        if self.fee_model and hasattr(self.fee_model, "on_order_filled"):
            self.fee_model.on_order_filled(event)

    def on_stop(self) -> None:
        """Nautilus lifecycle callback when engine stops."""
        pass

    def rebalance(
        self,
        target_weights: Dict[Union[InstrumentId, str], float],
        current_prices: Optional[Dict[Union[InstrumentId, str], float]] = None,
        *,
        instruments: Optional[Dict[Union[InstrumentId, str], Any]] = None,
    ) -> List[MarketOrder]:
        """
        Executes a portfolio rebalance directly through the Rebalancer.
        Calculates required quantities and submits native Nautilus market orders directly.
        """
        if self.rebalancer is None:
            raise RuntimeError("Strategy requires an attached Rebalancer to execute rebalance.")

        prices = dict(self._latest_prices)
        if current_prices:
            prices.update(current_prices)

        return self.rebalancer.rebalance(
            target_weights=target_weights,
            current_prices=prices,
            instruments=instruments,
        )


# Reusable alias for strategy authoring
BaseStrategy = BaseExecutionStrategy

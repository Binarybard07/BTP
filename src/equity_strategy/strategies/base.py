import logging
from typing import Any, Dict, List, Optional, Sequence, Union
import pandas as pd

from nautilus_trader.model import (
    Bar,
    BarType,
    Currency,
    InstrumentId,
    OrderFilled,
    QuoteTick,
    Venue,
)
from nautilus_trader.execution import FeeModel, FillModel
from nautilus_trader.trading import Strategy, StrategyConfig

from equity_strategy.execution import (
    AdverseSlippageFillModel,
    ExecutionCostReport,
    ExecutionEventRecorder,
    IndianEquityDeliveryFeeModel,
    T1OpenExecution,
)
from equity_strategy.execution.t1_open import QueuedOrder
from equity_strategy.rebalancing.rebalancer import RebalancePlan, Rebalancer

logger = logging.getLogger(__name__)


class BaseExecutionConfig(StrategyConfig):
    """
    Configuration for strategies running on NautilusTrader with T+1 Open execution.

    Parameters:
        bar_types: List of Nautilus BarType subscriptions for the active universe.
        all_trading_dates: Chronologically sorted unique trading dates across the universe.
        venue: Exchange venue code (default: 'NSE').
        currency: Settlement currency (default: 'INR').
        rebalancer: Optional execution Rebalancer instance.
        fee_model: Optional Nautilus FeeModel.
        fill_model: Optional Nautilus FillModel.
        recorder: Optional ExecutionEventRecorder.
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
        recorder: Optional[ExecutionEventRecorder] = None,
        exchange_tz: str = "Asia/Kolkata",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.bar_types = bar_types
        self.all_trading_dates = list(all_trading_dates) if all_trading_dates is not None else []
        self.venue = venue
        self.currency = currency
        self.rebalancer = rebalancer
        self.fee_model = fee_model
        self.fill_model = fill_model
        self.recorder = recorder
        self.exchange_tz = exchange_tz


class BaseExecutionStrategy(Strategy):
    """
    Reusable base NautilusTrader strategy for equity portfolio rebalancing
    with T+1 opening execution and execution cost model integration.

    Responsibilities:
    - Initializes and manages the T1OpenExecution coordinator and Rebalancer.
    - Manages subscriptions to daily bars and opening QuoteTicks for active instruments.
    - Attaches engine clock and execution recorder to fee and slippage models.
    - Coordinates event lifecycle:
        * on_quote: releases queued T+1 orders on opening quotes.
        * on_order_filled: reconciles fill events with fee and slippage models.
        * on_stop: cleans up remaining pending orders and logs cost reports.
    - Exposes simple rebalance queueing methods for concrete strategies:
        * queue_rebalance_plan(plan): queues an existing RebalancePlan.
        * plan_and_queue_rebalance(target_weights, ...): generates a plan via Rebalancer and queues it.
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
        self.recorder: Optional[ExecutionEventRecorder] = (
            getattr(config, "recorder", None)
            or getattr(self.fee_model, "recorder", None)
            or getattr(self.fill_model, "recorder", None)
        )
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

        # Reusable T+1 opening execution coordinator
        self.t1_execution: T1OpenExecution = T1OpenExecution(
            strategy=self,
            rebalancer=self.rebalancer,
            trading_dates=self.all_trading_dates,
            recorder=self.recorder,
            fee_model=self.fee_model,
            fill_model=self.fill_model,
            exchange_tz=self.exchange_tz,
        )

    def on_start(self) -> None:
        """
        Nautilus lifecycle callback:
        - Subscribes to bars and opening quotes for all configured bar types.
        - Initializes and wires the Rebalancer to this strategy's execution environment.
        - Wires engine clock and shared recorder to fee and fill models.
        - Synchronizes T1OpenExecution coordinator with Rebalancer and models.
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

        # Attach engine clock and shared recorder to execution cost models
        if self.fee_model and hasattr(self.fee_model, "set_clock"):
            self.fee_model.set_clock(self.clock)
        if self.fill_model and hasattr(self.fill_model, "set_clock"):
            self.fill_model.set_clock(self.clock)
        if self.recorder is not None:
            if self.fee_model and hasattr(self.fee_model, "set_recorder"):
                self.fee_model.set_recorder(self.recorder)
            if self.fill_model and hasattr(self.fill_model, "set_recorder"):
                self.fill_model.set_recorder(self.recorder)

        # Wire T1OpenExecution coordinator
        self.t1_execution.rebalancer = self.rebalancer
        self.t1_execution.recorder = self.recorder
        self.t1_execution.fee_model = self.fee_model
        self.t1_execution.fill_model = self.fill_model
        if not self.t1_execution.trading_dates and self.all_trading_dates:
            self.t1_execution.set_trading_dates(self.all_trading_dates)

        logger.info("BaseExecutionStrategy initialized and subscribed to universe.")

    def extract_session_date(self, item: Any) -> pd.Timestamp:
        """Normalizes a Nautilus Bar or QuoteTick timestamp to calendar session Timestamp."""
        return self.t1_execution.extract_session_date(item)

    def _extract_date(self, item: Any) -> pd.Timestamp:
        """Compatibility alias for extract_session_date."""
        return self.extract_session_date(item)

    def on_bar(self, bar: Bar) -> None:
        """Default on_bar callback tracks latest close price for the incoming instrument."""
        inst_id = bar.bar_type.instrument_id
        self._latest_prices[inst_id] = float(bar.close.as_double())

    def on_quote(self, quote: QuoteTick) -> None:
        """
        Nautilus lifecycle callback for opening QuoteTicks (09:15 IST):
        Delegates T+1 opening execution release to the T1OpenExecution coordinator.
        """
        self.t1_execution.on_quote(quote)

    def on_order_filled(self, event: OrderFilled) -> None:
        """
        Nautilus lifecycle callback when an order fill event is received.
        Delegates fill reconciliation and fee tracking to T1OpenExecution.
        """
        self.t1_execution.on_order_filled(event)

    def on_stop(self) -> None:
        """
        Nautilus lifecycle callback when engine stops.
        Cleans up unreleased pending orders and logs the execution cost report.
        """
        self.t1_execution.on_stop()
        if self.fee_model is not None or self.fill_model is not None:
            cost_report = self.get_cost_report()
            logger.info("\n%s", cost_report.to_text())

    def get_cost_report(self) -> ExecutionCostReport:
        """Returns the consolidated ExecutionCostReport for this strategy."""
        return ExecutionCostReport(
            fee_model=self.fee_model if isinstance(self.fee_model, IndianEquityDeliveryFeeModel) else None,
            fill_model=self.fill_model if isinstance(self.fill_model, AdverseSlippageFillModel) else None,
        )

    def has_pending_orders(self) -> bool:
        """Returns True if there are queued orders awaiting opening quotes."""
        return self.t1_execution.has_pending_orders()

    def queue_rebalance_plan(
        self,
        plan: RebalancePlan,
        execution_session: Optional[pd.Timestamp] = None,
    ) -> List[QueuedOrder]:
        """
        Queues all planned order instructions in a RebalancePlan for T+1 opening execution.
        """
        return self.t1_execution.queue_plan(plan, execution_session=execution_session)

    def plan_and_queue_rebalance(
        self,
        target_weights: Dict[Union[InstrumentId, str], float],
        reference_prices: Optional[Dict[Union[InstrumentId, str], float]] = None,
        decision_date: Optional[pd.Timestamp] = None,
        execution_session: Optional[pd.Timestamp] = None,
    ) -> RebalancePlan:
        """
        Plans a portfolio rebalance using the Rebalancer and queues it for T+1 opening execution.

        Parameters:
            target_weights: Target portfolio weights keyed by InstrumentId or symbol string.
            reference_prices: Session T close prices for order sizing (defaults to latest observed prices).
            decision_date: Session date T when the rebalance decision is made.
            execution_session: Optional explicit target session T+1 (defaults to next trading calendar date).
        """
        if self.rebalancer is None:
            raise RuntimeError("Strategy requires an attached Rebalancer to plan rebalances.")

        prices = dict(self._latest_prices)
        if reference_prices:
            prices.update(reference_prices)

        plan = self.rebalancer.plan_rebalance(
            target_weights=target_weights,
            reference_prices=prices,
            decision_date=decision_date,
        )
        self.queue_rebalance_plan(plan, execution_session=execution_session)
        return plan


# Reusable alias for strategy authoring
BaseStrategy = BaseExecutionStrategy

from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Sequence
import pandas as pd

from nautilus_trader.model import (
    InstrumentId,
    MarketOrder,
    OrderFilled,
    OrderSide,
    QuoteTick,
)
from nautilus_trader.trading import Strategy

from equity_strategy.execution.recorder import ExecutionEventRecorder
from equity_strategy.rebalancing.rebalancer import (
    PlannedOrderInstruction,
    RebalancePlan,
    Rebalancer,
)

logger = logging.getLogger(__name__)


def extract_session_date(item: Any, tz: str = "Asia/Kolkata") -> pd.Timestamp:
    """Normalizes a Nautilus Bar, QuoteTick, timestamp, or datetime to calendar session Timestamp."""
    if hasattr(item, "ts_init") or hasattr(item, "ts_event"):
        ts = getattr(item, "ts_init", None) or getattr(item, "ts_event")
        dt = pd.to_datetime(ts, unit="ns", utc=True).tz_convert(tz)
        return pd.Timestamp(dt.date())
    return pd.Timestamp(pd.to_datetime(item).date())


class T1OrderStatus(str, Enum):
    """Lifecycle status for a planned order managed by T1OpenExecution."""

    PENDING = "PENDING"
    RELEASED = "RELEASED"
    MISSED = "MISSED"


@dataclass
class QueuedOrder:
    """Represents a planned order queued for next-quote opening execution."""

    instruction: PlannedOrderInstruction
    decision_date: pd.Timestamp
    execution_session: Optional[pd.Timestamp] = None
    status: T1OrderStatus = T1OrderStatus.PENDING

    @property
    def instrument_id(self) -> InstrumentId:
        return self.instruction.instrument_id

    @property
    def order_side(self) -> OrderSide:
        return self.instruction.order_side


class T1OpenExecution:
    """
    Reusable coordinator for next eligible quote execution in NautilusTrader strategies.

    Execution Lifecycle & Design:
    - Stages planned order instructions prepared at session T close without submitting them on T.
    - Eliminates strict calendar date-to-index matching in favor of next eligible quote execution.
    - Sells before Buys: Ensures all planned SELL instructions are submitted as their respective
      opening quotes arrive before any BUY instructions are released, preserving cash availability
      in CASH netting accounts.
    - Orders are submitted to the Nautilus simulated exchange, where the venue's configured
      StaticLatencyModel (e.g. 1 ms insert latency) models realistic order arrival.
    - Pre-registers reference prices and session timestamps with the execution recorder,
      fee model, and adverse slippage fill model.
    - Dispatches fill events to fee and slippage models for native accounting reconciliation.
    """

    def __init__(
        self,
        strategy: Optional[Strategy] = None,
        rebalancer: Optional[Rebalancer] = None,
        trading_dates: Optional[Sequence[pd.Timestamp]] = None,
        recorder: Optional[ExecutionEventRecorder] = None,
        fee_model: Optional[Any] = None,
        fill_model: Optional[Any] = None,
        exchange_tz: str = "Asia/Kolkata",
    ) -> None:
        self.strategy = strategy
        self._rebalancer = rebalancer or getattr(strategy, "rebalancer", None)
        self.exchange_tz = exchange_tz

        self._trading_dates: List[pd.Timestamp] = []
        if trading_dates is not None:
            self.set_trading_dates(trading_dates)
        elif strategy is not None and hasattr(strategy, "all_trading_dates"):
            self.set_trading_dates(getattr(strategy, "all_trading_dates"))

        self.recorder: Optional[ExecutionEventRecorder] = (
            recorder or getattr(strategy, "recorder", None)
        )
        self.fee_model: Optional[Any] = fee_model or getattr(strategy, "fee_model", None)
        self.fill_model: Optional[Any] = fill_model or getattr(strategy, "fill_model", None)

        # Pending instructions partitioned by side to guarantee sell-before-buy execution
        self._pending_sells: Dict[InstrumentId, PlannedOrderInstruction] = {}
        self._pending_buys: Dict[InstrumentId, PlannedOrderInstruction] = {}
        self._decision_date: Optional[pd.Timestamp] = None
        self._staged_quotes: Dict[InstrumentId, QuoteTick] = {}

        # Backward compatibility map
        self._pending_orders: Dict[InstrumentId, QueuedOrder] = {}

    @property
    def rebalancer(self) -> Optional[Rebalancer]:
        return self._rebalancer

    @rebalancer.setter
    def rebalancer(self, value: Optional[Rebalancer]) -> None:
        self._rebalancer = value

    @property
    def trading_dates(self) -> List[pd.Timestamp]:
        return self._trading_dates

    @property
    def pending_orders(self) -> Dict[InstrumentId, QueuedOrder]:
        return self._pending_orders

    def set_trading_dates(self, trading_dates: Sequence[pd.Timestamp]) -> None:
        """Stores unique trading calendar dates (retained for backward compatibility)."""
        self._trading_dates = [
            pd.Timestamp(d).normalize() for d in sorted(set(trading_dates))
        ]

    def extract_session_date(self, item: Any) -> pd.Timestamp:
        """Normalizes a Nautilus Bar or QuoteTick to a calendar session Timestamp."""
        return extract_session_date(item, self.exchange_tz)

    def get_next_trading_session(self, decision_date: pd.Timestamp) -> Optional[pd.Timestamp]:
        """Compatibility helper returning next calendar date if available."""
        norm_date = pd.Timestamp(decision_date).normalize()
        for d in self._trading_dates:
            if d > norm_date:
                return d
        return None

    def has_pending_orders(self) -> bool:
        """Returns True if there are staged orders awaiting eligible quotes."""
        return bool(self._pending_sells or self._pending_buys)

    def queue_orders(
        self,
        instructions: Sequence[PlannedOrderInstruction],
        decision_date: pd.Timestamp,
        execution_session: Optional[pd.Timestamp] = None,
    ) -> List[QueuedOrder]:
        """Stages planned orders for next eligible quote execution."""
        if not instructions:
            return []

        norm_decision = pd.Timestamp(decision_date).normalize()
        self._decision_date = norm_decision
        self._staged_quotes.clear()

        # Clear any prior unreleased orders
        if self._pending_sells or self._pending_buys:
            logger.debug(
                "Replacing %d prior unreleased orders with new rebalance plan for %s",
                len(self._pending_sells) + len(self._pending_buys),
                norm_decision.strftime("%Y-%m-%d"),
            )
            self._pending_sells.clear()
            self._pending_buys.clear()
            self._pending_orders.clear()

        queued_list: List[QueuedOrder] = []
        for instr in instructions:
            q = QueuedOrder(
                instruction=instr,
                decision_date=norm_decision,
                execution_session=execution_session,
            )
            self._pending_orders[instr.instrument_id] = q
            if instr.order_side == OrderSide.SELL:
                self._pending_sells[instr.instrument_id] = instr
            else:
                self._pending_buys[instr.instrument_id] = instr
            queued_list.append(q)

        logger.info(
            "Staged %d order(s) (%d SELLs, %d BUYs) for next eligible quote execution (decision date: %s)",
            len(queued_list),
            len(self._pending_sells),
            len(self._pending_buys),
            norm_decision.strftime("%Y-%m-%d"),
        )
        return queued_list

    def queue_plan(
        self,
        plan: RebalancePlan,
        execution_session: Optional[pd.Timestamp] = None,
    ) -> List[QueuedOrder]:
        """Convenience helper to stage instructions from a RebalancePlan."""
        return self.queue_orders(
            instructions=plan.all_instructions,
            decision_date=plan.decision_date,
            execution_session=execution_session,
        )

    def _submit_instruction(
        self, instr: PlannedOrderInstruction, quote: QuoteTick
    ) -> MarketOrder:
        """Submits a single planned instruction against its eligible opening quote."""
        if self._rebalancer is None:
            raise RuntimeError("Execution coordinator requires an attached Rebalancer to submit orders.")

        inst_id = instr.instrument_id
        quote_date = self.extract_session_date(quote)
        quote_ts = int(quote.ts_event)
        cid = instr.order.client_order_id

        intended_px = (
            float(quote.ask_price.as_double())
            if instr.order_side == OrderSide.BUY
            else float(quote.bid_price.as_double())
        )

        # Pre-register reference price and session before matching engine submission
        if self.recorder is not None:
            self.recorder.register_order_release(
                client_order_id=cid,
                timestamp_ns=quote_ts,
                intended_price=intended_px,
            )
        if self.fee_model and hasattr(self.fee_model, "register_execution_session"):
            self.fee_model.register_execution_session(
                client_order_id=cid,
                timestamp_ns=quote_ts,
                session_date=quote_date,
            )
        if self.fill_model and hasattr(self.fill_model, "register_reference_price"):
            self.fill_model.register_reference_price(
                client_order_id=cid,
                reference_price=intended_px,
            )

        order = self._rebalancer.submit_planned_order(instr)
        logger.info(
            "Submitted next-quote order: %s %s %s @ %.2f",
            instr.order_side.name,
            instr.quantity,
            inst_id,
            intended_px,
        )
        return order

    def on_quote(self, quote: QuoteTick) -> Optional[MarketOrder]:
        """
        Evaluates an incoming opening QuoteTick against pending orders:
        1. Ignores quotes from session <= decision_date (quotes from decision session T or earlier).
        2. Stashes the fresh opening quote for the instrument.
        3. If the instrument has a pending SELL order, releases and submits it immediately.
        4. If all pending SELLs have been released, releases any pending BUY orders whose opening
           quote has arrived, guaranteeing all sells execute before buys.
        """
        inst_id = quote.instrument_id
        quote_date = self.extract_session_date(quote)

        # Ignore quotes from session <= decision_date
        if self._decision_date is not None and quote_date <= self._decision_date:
            return None

        # Stash opening quote
        self._staged_quotes[inst_id] = quote
        last_submitted: Optional[MarketOrder] = None

        # 1. Release eligible SELL order for this instrument
        if inst_id in self._pending_sells:
            instr = self._pending_sells.pop(inst_id)
            self._pending_orders.pop(inst_id, None)
            last_submitted = self._submit_instruction(instr, quote)

        # 2. If all pending sells are submitted, release pending buys whose quotes have arrived
        if not self._pending_sells:
            for buy_inst_id in list(self._pending_buys.keys()):
                if buy_inst_id in self._staged_quotes:
                    b_quote = self._staged_quotes[buy_inst_id]
                    b_instr = self._pending_buys.pop(buy_inst_id)
                    self._pending_orders.pop(buy_inst_id, None)
                    last_submitted = self._submit_instruction(b_instr, b_quote)

        return last_submitted

    def on_order_filled(self, event: OrderFilled) -> None:
        """Dispatches fill events to fill and fee models for accounting reconciliation."""
        if self.fill_model and hasattr(self.fill_model, "record_fill"):
            self.fill_model.record_fill(event)
        if self.fee_model and hasattr(self.fee_model, "on_order_filled"):
            self.fee_model.on_order_filled(event)

    def on_stop(self) -> None:
        """Clears any remaining pending orders at strategy shutdown."""
        unreleased_count = len(self._pending_sells) + len(self._pending_buys)
        if unreleased_count > 0:
            logger.warning(
                "T1OpenExecution stopped with %d unreleased pending order(s) (%d SELLs, %d BUYs).",
                unreleased_count,
                len(self._pending_sells),
                len(self._pending_buys),
            )
            self._pending_sells.clear()
            self._pending_buys.clear()
            self._pending_orders.clear()


# Reusable alias
NextQuoteExecution = T1OpenExecution

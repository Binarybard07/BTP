from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Sequence, Union
import pandas as pd

from nautilus_trader.model import (
    ClientOrderId,
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


class T1OrderStatus(str, Enum):
    """Lifecycle status for a planned order managed by T1OpenExecution."""

    PENDING = "PENDING"
    RELEASED = "RELEASED"
    UNEXECUTED_MISSED_QUOTE = "UNEXECUTED_MISSED_QUOTE"
    UNEXECUTED_SHUTDOWN = "UNEXECUTED_SHUTDOWN"
    UNEXECUTED_NO_NEXT_SESSION = "UNEXECUTED_NO_NEXT_SESSION"


@dataclass
class QueuedOrder:
    """
    Structured record tracking an order instruction through the T+1 opening execution lifecycle.
    """

    instruction: PlannedOrderInstruction
    decision_date: Optional[pd.Timestamp]
    execution_session: Optional[pd.Timestamp]
    status: T1OrderStatus = T1OrderStatus.PENDING
    intended_price: Optional[float] = None
    release_timestamp_ns: Optional[int] = None
    released_order: Optional[MarketOrder] = None
    unexecuted_reason: Optional[str] = None

    @property
    def instrument_id(self) -> InstrumentId:
        return self.instruction.instrument_id

    @property
    def client_order_id(self) -> ClientOrderId:
        return self.instruction.order.client_order_id

    @property
    def order_side(self) -> OrderSide:
        return self.instruction.order_side

    @property
    def is_pending(self) -> bool:
        return self.status == T1OrderStatus.PENDING

    @property
    def is_released(self) -> bool:
        return self.status == T1OrderStatus.RELEASED

    @property
    def is_unexecuted(self) -> bool:
        return not (self.is_pending or self.is_released)


class T1OpenExecution:
    """
    Reusable coordinator for T+1 opening price execution in NautilusTrader strategies.

    Responsibilities:
    - Queues planned order instructions prepared at session T close without submitting them on T.
    - Resolves and enforces the explicit intended execution session (the next valid trading session T+1).
    - Releases an eligible order only when the matching instrument's opening quote arrives on the exact
      intended execution session.
    - Prevents early releases (quotes from session <= T) and late releases (quotes from session > T+1).
    - Preserves unexecuted status and logs diagnostics if an expected opening quote is missed,
      never silently substituting a later price.
    - Handles the end of available historical calendar data explicitly without inventing next sessions.
    - Registers intended reference prices and execution timestamps with ExecutionEventRecorder,
      FeeModel, and FillModel before order submission.
    - Delegates final order submission to the strategy's Rebalancer.
    - Dispatches fill events to fee and slippage models for accounting reconciliation.
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

        # Trading calendar for next-session lookup
        self._trading_dates: List[pd.Timestamp] = []
        self._date_to_index: Dict[pd.Timestamp, int] = {}
        if trading_dates is not None:
            self.set_trading_dates(trading_dates)
        elif strategy is not None and hasattr(strategy, "all_trading_dates"):
            self.set_trading_dates(getattr(strategy, "all_trading_dates"))

        # Execution cost and attribution models
        self.recorder: Optional[ExecutionEventRecorder] = (
            recorder or getattr(strategy, "recorder", None)
        )
        self.fee_model: Optional[Any] = fee_model or getattr(strategy, "fee_model", None)
        self.fill_model: Optional[Any] = fill_model or getattr(strategy, "fill_model", None)

        # Queued order state
        self._pending_orders: Dict[InstrumentId, QueuedOrder] = {}
        self._all_queued_orders: List[QueuedOrder] = []
        self._released_orders_by_date: Dict[pd.Timestamp, List[MarketOrder]] = {}
        self._order_execution_metadata: Dict[ClientOrderId, Dict[str, Any]] = {}

    @property
    def rebalancer(self) -> Optional[Rebalancer]:
        """The Rebalancer responsible for planned order submission."""
        return self._rebalancer

    @rebalancer.setter
    def rebalancer(self, value: Optional[Rebalancer]) -> None:
        self._rebalancer = value

    @property
    def trading_dates(self) -> List[pd.Timestamp]:
        """Chronologically sorted valid trading session dates."""
        return list(self._trading_dates)

    @property
    def pending_orders(self) -> Dict[InstrumentId, QueuedOrder]:
        """Mapping of InstrumentId to currently queued pending orders awaiting release."""
        return dict(self._pending_orders)

    @property
    def all_queued_orders(self) -> List[QueuedOrder]:
        """List of all orders ever managed by this coordinator across all lifecycle states."""
        return list(self._all_queued_orders)

    @property
    def released_orders(self) -> List[QueuedOrder]:
        """List of orders successfully released upon opening quote arrival."""
        return [q for q in self._all_queued_orders if q.is_released]

    @property
    def unexecuted_orders(self) -> List[QueuedOrder]:
        """List of orders that were never released (missed quote, shutdown, or no next session)."""
        return [q for q in self._all_queued_orders if q.is_unexecuted]

    @property
    def released_orders_by_date(self) -> Dict[pd.Timestamp, List[MarketOrder]]:
        """Mapping of calendar date to list of released MarketOrders."""
        return dict(self._released_orders_by_date)

    @property
    def order_execution_metadata(self) -> Dict[ClientOrderId, Dict[str, Any]]:
        """Mapping of ClientOrderId to execution release metadata for fill reconciliation."""
        return dict(self._order_execution_metadata)

    def set_trading_dates(self, trading_dates: Sequence[pd.Timestamp]) -> None:
        """Sets and indexes the chronologically sorted trading calendar."""
        self._trading_dates = [
            pd.Timestamp(d).normalize() for d in sorted(set(trading_dates))
        ]
        self._date_to_index = {d: i for i, d in enumerate(self._trading_dates)}

    def extract_session_date(self, item: Any) -> pd.Timestamp:
        """
        Normalizes a Nautilus Bar, QuoteTick, timestamp, or datetime to calendar session Timestamp
        in the configured exchange timezone.
        """
        if hasattr(item, "ts_init") or hasattr(item, "ts_event"):
            ts = getattr(item, "ts_init", None) or getattr(item, "ts_event")
            dt = pd.to_datetime(ts, unit="ns", utc=True).tz_convert(self.exchange_tz)
            return pd.Timestamp(dt.date())
        return pd.Timestamp(pd.to_datetime(item).date())

    def get_next_trading_session(self, decision_date: pd.Timestamp) -> Optional[pd.Timestamp]:
        """
        Determines the next valid trading session after decision_date using the calendar.
        Returns None if decision_date is at or beyond the end of available historical dates.
        """
        norm_date = pd.Timestamp(decision_date).normalize()

        date_idx = self._date_to_index.get(norm_date)
        if date_idx is not None:
            if date_idx + 1 < len(self._trading_dates):
                return self._trading_dates[date_idx + 1]
            return None

        # Fallback binary/linear search for arbitrary timestamp not explicitly in index
        for d in self._trading_dates:
            if d > norm_date:
                return d
        return None

    def has_pending_orders(self) -> bool:
        """Returns True if there are queued orders awaiting opening quotes."""
        return bool(self._pending_orders)

    def queue_plan(
        self,
        plan: RebalancePlan,
        execution_session: Optional[pd.Timestamp] = None,
    ) -> List[QueuedOrder]:
        """
        Queues all instructions in a RebalancePlan for strict next-session opening execution.
        """
        return self.queue_orders(
            instructions=plan.all_instructions,
            decision_date=plan.decision_date,
            execution_session=execution_session,
        )

    def queue_orders(
        self,
        instructions: Sequence[PlannedOrderInstruction],
        decision_date: Optional[pd.Timestamp] = None,
        execution_session: Optional[pd.Timestamp] = None,
    ) -> List[QueuedOrder]:
        """
        Queues planned order instructions for a specified next trading session.
        If execution_session is not provided, it is resolved from the trading calendar using decision_date.
        """
        norm_decision = pd.Timestamp(decision_date).normalize() if decision_date is not None else None
        target_session = (
            pd.Timestamp(execution_session).normalize()
            if execution_session is not None
            else (self.get_next_trading_session(norm_decision) if norm_decision is not None else None)
        )

        queued_list: List[QueuedOrder] = []

        if target_session is None:
            logger.warning(
                "Cannot queue orders for decision date %s: No next trading session available in calendar (end of historical data). "
                "%d order instructions marked UNEXECUTED.",
                norm_decision.strftime("%Y-%m-%d") if norm_decision else "N/A",
                len(instructions),
            )
            for instr in instructions:
                q = QueuedOrder(
                    instruction=instr,
                    decision_date=norm_decision,
                    execution_session=None,
                    status=T1OrderStatus.UNEXECUTED_NO_NEXT_SESSION,
                    unexecuted_reason="End of historical trading calendar reached (no T+1 session available).",
                )
                self._all_queued_orders.append(q)
                queued_list.append(q)
            return queued_list

        logger.info(
            "T+1 EXECUTION QUEUE: Queueing %d instructions from decision date %s for strict execution session %s",
            len(instructions),
            norm_decision.strftime("%Y-%m-%d") if norm_decision else "N/A",
            target_session.strftime("%Y-%m-%d"),
        )

        for instr in instructions:
            inst_id = instr.instrument_id
            if inst_id in self._pending_orders:
                prev = self._pending_orders.pop(inst_id)
                prev.status = T1OrderStatus.UNEXECUTED_MISSED_QUOTE
                prev.unexecuted_reason = f"Replaced by newer rebalance plan for session {target_session.strftime('%Y-%m-%d')}"
                logger.warning(
                    "Replacing existing pending order for %s from earlier decision date %s",
                    inst_id,
                    prev.decision_date.strftime("%Y-%m-%d") if prev.decision_date else "N/A",
                )

            q = QueuedOrder(
                instruction=instr,
                decision_date=norm_decision,
                execution_session=target_session,
                status=T1OrderStatus.PENDING,
            )
            self._pending_orders[inst_id] = q
            self._all_queued_orders.append(q)
            queued_list.append(q)

            logger.info(
                "  QUEUED INSTRUCTION: %s | side=%s | qty=%s | target_qty=%.2f | ref_price=%.2f | target_weight=%.4f",
                inst_id,
                instr.order_side.name,
                instr.quantity,
                instr.target_qty,
                instr.reference_price,
                instr.target_weight,
            )

        return queued_list

    def on_quote(self, quote: QuoteTick) -> Optional[MarketOrder]:
        """
        Evaluates an incoming opening QuoteTick against pending queued orders:
        - If quote session < intended execution session: ignores (too early, decision session T or before).
        - If quote session > intended execution session: marks order unexecuted/missed and does NOT release.
        - If quote session == intended execution session: records intended opening reference price,
          notifies execution models, and submits the order via Rebalancer.
        """
        inst_id = quote.instrument_id
        if inst_id not in self._pending_orders:
            return None

        queued = self._pending_orders[inst_id]
        quote_date = self.extract_session_date(quote)
        target_session = queued.execution_session

        if target_session is not None:
            if quote_date < target_session:
                # Quote arrived earlier than intended execution session (e.g. quote on session T)
                logger.debug(
                    "Quote for %s on %s is earlier than intended execution session %s; not releasing.",
                    inst_id,
                    quote_date.strftime("%Y-%m-%d"),
                    target_session.strftime("%Y-%m-%d"),
                )
                return None

            if quote_date > target_session:
                # Opening quote for intended session was missed; quote is from a later session
                self._pending_orders.pop(inst_id)
                reason = (
                    f"Missed opening quote for intended session {target_session.strftime('%Y-%m-%d')}; "
                    f"received quote from later session {quote_date.strftime('%Y-%m-%d')}."
                )
                queued.status = T1OrderStatus.UNEXECUTED_MISSED_QUOTE
                queued.unexecuted_reason = reason
                logger.warning(
                    "UNEXECUTED ORDER [MISSED OPENING QUOTE]: %s intended for session %s was not released. Reason: %s",
                    inst_id,
                    target_session.strftime("%Y-%m-%d"),
                    reason,
                )
                return None

        # Exact match: quote_date == target_session
        self._pending_orders.pop(inst_id)
        instr = queued.instruction

        # Determine intended opening reference price under synthetic quote model:
        # BUY orders execute against Ask; SELL orders execute against Bid
        intended_px = (
            float(quote.ask_price.as_double())
            if instr.order_side == OrderSide.BUY
            else float(quote.bid_price.as_double())
        )
        quote_ts = int(quote.ts_event)
        cid = instr.order.client_order_id

        # Register execution session and reference price before submitting to matching engine
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

        logger.info(
            "RELEASING QUEUED ORDER on opening quote: %s | quote_px=%.2f | quote_ts=%s | side=%s | qty=%s",
            inst_id,
            intended_px,
            quote_ts,
            instr.order_side.name,
            instr.quantity,
        )

        if self._rebalancer is None:
            raise RuntimeError("T1OpenExecution requires an attached Rebalancer to submit orders.")

        order = self._rebalancer.submit_planned_order(instr)

        queued.status = T1OrderStatus.RELEASED
        queued.intended_price = intended_px
        queued.release_timestamp_ns = quote_ts
        queued.released_order = order

        self._order_execution_metadata[cid] = {
            "intended_px": intended_px,
            "quote_ts": quote_ts,
            "instrument_id": inst_id,
            "decision_date": queued.decision_date,
            "execution_session": quote_date,
        }

        if quote_date not in self._released_orders_by_date:
            self._released_orders_by_date[quote_date] = []
        self._released_orders_by_date[quote_date].append(order)

        return order

    def on_order_filled(self, event: OrderFilled) -> Optional[Dict[str, Any]]:
        """
        Coordinates authoritative fill events with fill and fee models,
        and logs execution slippage relative to the intended opening reference price.
        """
        if self.fill_model and hasattr(self.fill_model, "record_fill"):
            self.fill_model.record_fill(event)

        if self.fee_model and hasattr(self.fee_model, "on_order_filled"):
            self.fee_model.on_order_filled(event)

        meta = self._order_execution_metadata.get(event.client_order_id)
        fill_px = float(event.last_px.as_double())
        fill_qty = float(event.last_qty.as_double())

        if meta is not None:
            intended_px = meta["intended_px"]
            diff = fill_px - intended_px
            logger.info(
                "ORDER FILLED: %s | client_order_id=%s | trade_id=%s | side=%s | qty=%.2f | fill_px=%.2f (intended_open_px=%.2f, diff=%.4f) | fill_ts=%s | decision_date=%s | execution_session=%s",
                event.instrument_id,
                event.client_order_id,
                event.trade_id,
                event.order_side.name,
                fill_qty,
                fill_px,
                intended_px,
                diff,
                event.ts_event,
                meta.get("decision_date"),
                meta.get("execution_session"),
            )
        else:
            logger.info(
                "ORDER FILLED: %s | client_order_id=%s | trade_id=%s | side=%s | qty=%.2f | fill_px=%.2f | fill_ts=%s",
                event.instrument_id,
                event.client_order_id,
                event.trade_id,
                event.order_side.name,
                fill_qty,
                fill_px,
                event.ts_event,
            )

        return meta

    def sweep_expired_orders(self, current_session: pd.Timestamp) -> List[QueuedOrder]:
        """
        Explicitly expires pending orders whose intended execution session has elapsed.
        Useful when an instrument did not receive any quotes during its intended session.
        """
        norm_current = pd.Timestamp(current_session).normalize()
        expired: List[QueuedOrder] = []

        for inst_id, queued in list(self._pending_orders.items()):
            if queued.execution_session is not None and queued.execution_session < norm_current:
                reason = (
                    f"Opening quote never arrived for session {queued.execution_session.strftime('%Y-%m-%d')}; "
                    f"simulation advanced to session {norm_current.strftime('%Y-%m-%d')}."
                )
                queued.status = T1OrderStatus.UNEXECUTED_MISSED_QUOTE
                queued.unexecuted_reason = reason
                del self._pending_orders[inst_id]
                expired.append(queued)
                logger.warning(
                    "UNEXECUTED ORDER [MISSED OPENING QUOTE]: %s intended for session %s expired on %s",
                    inst_id,
                    queued.execution_session.strftime("%Y-%m-%d"),
                    norm_current.strftime("%Y-%m-%d"),
                )

        return expired

    def on_stop(self) -> List[QueuedOrder]:
        """
        Lifecycle shutdown callback: handles any orders still pending release.
        Marks them unexecuted with reason and logs diagnostics.
        """
        unreleased: List[QueuedOrder] = []
        if self._pending_orders:
            logger.info(
                "T+1 execution coordinator stopping with %d unreleased queued orders.",
                len(self._pending_orders),
            )
            for inst_id, queued in list(self._pending_orders.items()):
                reason = (
                    f"Engine stopped before opening QuoteTick arrived for session "
                    f"{queued.execution_session.strftime('%Y-%m-%d') if queued.execution_session else 'N/A'}."
                )
                queued.status = T1OrderStatus.UNEXECUTED_SHUTDOWN
                queued.unexecuted_reason = reason
                logger.info(
                    "  UNRELEASED AT SHUTDOWN: %s | intended_session=%s | decision_date=%s | side=%s | qty=%s",
                    inst_id,
                    queued.execution_session.strftime("%Y-%m-%d") if queued.execution_session else "N/A",
                    queued.decision_date.strftime("%Y-%m-%d") if queued.decision_date else "N/A",
                    queued.order_side.name,
                    queued.instruction.quantity,
                )
                unreleased.append(queued)

            self._pending_orders.clear()

        return unreleased

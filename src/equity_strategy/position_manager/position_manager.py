"""
PositionManager module for managing take-profit and stop-loss protective orders
for open positions using NautilusTrader 2.0.0rc5 APIs.
"""
import logging
from typing import Any, Dict, Optional, Tuple

from nautilus_trader.model import (
    ClientOrderId,
    InstrumentId,
    OrderCanceled,
    OrderCancelRejected,
    OrderFilled,
    OrderSide,
    PositionChanged,
    PositionClosed,
    PositionOpened,
    PositionSide,
    Price,
    Quantity,
)
from nautilus_trader.trading import Strategy

from equity_strategy.trade_manager.base import BaseTradeManager

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Manages take-profit (TP) and stop-loss (SL) protective orders for open positions
    using NautilusTrader 2.0.0rc5 APIs and a delegated TradeManager.

    Responsibilities:
    - Observes Nautilus position lifecycle events: on_position_opened,
      on_position_changed, and on_position_closed.
    - Manages protection on the aggregate net position per instrument (NETTING OMS).
    - Delegates TP/SL price calculations to the configured BaseTradeManager.
    - Submits native reduce-only Limit (TP) and Stop-Market (SL) protective orders.
    - Reconciles existing protection on position changes, handling asynchronous
      order cancellation before submitting replacement protection.
    - Automatically cancels remaining protective orders upon position closure or
      when one protective order fills.
    """

    def __init__(
        self,
        trade_manager: BaseTradeManager,
        strategy: Optional[Strategy] = None,
    ) -> None:
        self.trade_manager: BaseTradeManager = trade_manager
        self.strategy: Optional[Strategy] = strategy

        # Per-instrument active protective order IDs
        self._tp_orders: Dict[InstrumentId, ClientOrderId] = {}
        self._sl_orders: Dict[InstrumentId, ClientOrderId] = {}

        # Reverse lookup from ClientOrderId to InstrumentId for fast callback routing
        self._order_to_instrument: Dict[ClientOrderId, InstrumentId] = {}

        # Pending protection replacements awaiting cancellation of previous orders:
        # InstrumentId -> (tp_price, sl_price, quantity)
        self._pending_protection: Dict[InstrumentId, Tuple[Price, Price, Quantity]] = {}

    def initialize(
        self,
        strategy: Strategy,
    ) -> None:
        """
        Initializes the strategy reference and registers indicators with bar types.
        """
        self.strategy = strategy
        self.trade_manager.register(strategy, strategy.bar_types)

    # --------------------------------------------------------------------------
    # Position Lifecycle Callbacks
    # --------------------------------------------------------------------------

    def on_position_opened(self, event: PositionOpened) -> None:
        """
        Handles position opened event: calculates TP/SL and submits protective orders.
        """
        self._ensure_initialized()
        inst_id = event.instrument_id

        # Re-check direction: Indian cash equity supports LONG positions
        if event.side != PositionSide.LONG:
            logger.warning(
                "Position for %s is not LONG (side: %s). Skipping protective orders.",
                inst_id,
                event.side,
            )
            return

        pos = self._get_position(event.position_id)
        pos_or_event = pos if pos is not None else event

        qty = self._extract_quantity(pos_or_event)
        if qty.as_double() <= 0:
            logger.warning("Opened position for %s has non-positive quantity: %s", inst_id, qty)
            return

        ref_price = self._extract_reference_price(pos_or_event)
        try:
            tp_val, sl_val = self.trade_manager.calculate_tp_sl(ref_price, inst_id)
        except Exception as exc:
            logger.warning(
                "Failed to calculate TP/SL for %s at reference price %.4f: %s",
                inst_id,
                ref_price,
                exc,
            )
            return

        instrument = self._get_instrument(inst_id)
        tp_price = instrument.make_price(tp_val)
        sl_price = instrument.make_price(sl_val)

        logger.info(
            "Position OPENED for %s (qty=%s, ref_px=%.4f). Submitting TP=%s, SL=%s",
            inst_id,
            qty,
            ref_price,
            tp_price,
            sl_price,
        )
        self._submit_protective_orders(inst_id, tp_price, sl_price, qty)

    def on_position_changed(self, event: PositionChanged) -> None:
        """
        Handles position changed event: reconciles existing protection with new quantity
        and reference price.
        """
        self._ensure_initialized()
        inst_id = event.instrument_id

        pos = self._get_position(event.position_id)
        pos_or_event = pos if pos is not None else event

        qty = self._extract_quantity(pos_or_event)
        if qty.as_double() <= 0 or (hasattr(pos_or_event, "is_closed") and pos_or_event.is_closed):
            logger.info("Position for %s changed to FLAT/closed. Cancelling protection.", inst_id)
            self._cancel_all_for_instrument(inst_id)
            return

        if not self._is_long(pos_or_event):
            logger.warning(
                "Position for %s is not LONG (side: %s). Cancelling existing protection.",
                inst_id,
                getattr(pos_or_event, "side", None),
            )
            self._cancel_all_for_instrument(inst_id)
            return

        ref_price = self._extract_reference_price(pos_or_event)
        try:
            tp_val, sl_val = self.trade_manager.calculate_tp_sl(ref_price, inst_id)
        except Exception as exc:
            logger.warning(
                "Failed to calculate TP/SL on position change for %s: %s",
                inst_id,
                exc,
            )
            return

        instrument = self._get_instrument(inst_id)
        new_tp_price = instrument.make_price(tp_val)
        new_sl_price = instrument.make_price(sl_val)

        # Check if existing active protection already matches exactly
        if self._protection_matches(inst_id, qty, new_tp_price, new_sl_price):
            logger.debug(
                "Existing protection for %s already matches new position state.",
                inst_id,
            )
            return

        # Protection needs updating. If existing orders are active, cancel them first
        # and store pending replacement to be submitted when cancellations complete.
        if not self._all_protective_orders_closed(inst_id):
            self._pending_protection[inst_id] = (new_tp_price, new_sl_price, qty)
            logger.info(
                "Position CHANGED for %s (qty=%s, ref_px=%.4f). Cancelling old orders before replacement...",
                inst_id,
                qty,
                ref_price,
            )
            self._cancel_active_protective_orders(inst_id)
        else:
            self._pending_protection.pop(inst_id, None)
            logger.info(
                "Position CHANGED for %s (qty=%s, ref_px=%.4f). Submitting new TP=%s, SL=%s",
                inst_id,
                qty,
                ref_price,
                new_tp_price,
                new_sl_price,
            )
            self._submit_protective_orders(inst_id, new_tp_price, new_sl_price, qty)

    def on_position_closed(self, event: PositionClosed) -> None:
        """
        Handles position closed event: cancels all outstanding protective orders.
        """
        inst_id = event.instrument_id
        logger.info("Position CLOSED for %s. Cancelling all protective orders...", inst_id)
        self._cancel_all_for_instrument(inst_id)

    # --------------------------------------------------------------------------
    # Order Event Callbacks
    # --------------------------------------------------------------------------

    def on_order_canceled(self, event: OrderCanceled) -> None:
        """
        Handles order canceled event: checks whether pending replacement orders can be submitted.
        """
        inst_id = self._order_to_instrument.get(event.client_order_id)
        if inst_id is None:
            return

        logger.debug(
            "Protective order %s for %s confirmed canceled.",
            event.client_order_id,
            inst_id,
        )

        # If all protective orders for this instrument are closed, submit pending replacement if any
        if self._all_protective_orders_closed(inst_id) and inst_id in self._pending_protection:
            tp_price, sl_price, qty = self._pending_protection.pop(inst_id)

            # Re-verify the position is still open before submitting replacement
            if self._is_position_open_for_instrument(inst_id):
                logger.info(
                    "All previous protective orders for %s canceled. Submitting replacement TP=%s, SL=%s (qty=%s)...",
                    inst_id,
                    tp_price,
                    sl_price,
                    qty,
                )
                self._submit_protective_orders(inst_id, tp_price, sl_price, qty)
            else:
                logger.info(
                    "Position for %s is no longer open. Discarding replacement protection.",
                    inst_id,
                )

    def on_order_cancel_rejected(self, event: OrderCancelRejected) -> None:
        """
        Handles order cancel rejected event: logs warning and clears pending replacement.
        """
        inst_id = self._order_to_instrument.get(event.client_order_id)
        if inst_id is None:
            return

        logger.warning(
            "Protective order cancel rejected for %s (order: %s, reason: %s)",
            inst_id,
            event.client_order_id,
            getattr(event, "reason", "unknown"),
        )
        self._pending_protection.pop(inst_id, None)

    def on_order_filled(self, event: OrderFilled) -> None:
        """
        Handles order filled event: if one protective order fills, immediately cancels the opposite order.
        """
        inst_id = self._order_to_instrument.get(event.client_order_id)
        if inst_id is None:
            return

        tp_id = self._tp_orders.get(inst_id)
        sl_id = self._sl_orders.get(inst_id)

        if event.client_order_id == tp_id:
            logger.info(
                "Take-profit order %s filled for %s. Cancelling corresponding stop-loss...",
                tp_id,
                inst_id,
            )
            self._pending_protection.pop(inst_id, None)
            if sl_id is not None:
                self._cancel_order_by_id(sl_id)
        elif event.client_order_id == sl_id:
            logger.info(
                "Stop-loss order %s filled for %s. Cancelling corresponding take-profit...",
                sl_id,
                inst_id,
            )
            self._pending_protection.pop(inst_id, None)
            if tp_id is not None:
                self._cancel_order_by_id(tp_id)

    # --------------------------------------------------------------------------
    # Internal Helpers
    # --------------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        if self.strategy is None:
            raise RuntimeError("PositionManager has not been initialized with a Strategy instance.")

    def _get_instrument(self, inst_id: InstrumentId) -> Any:
        inst = self.strategy.cache.instrument(inst_id)
        if inst is None:
            raise ValueError(f"Instrument '{inst_id}' not found in Nautilus cache.")
        return inst

    def _get_position(self, position_id: Any) -> Optional[Any]:
        if self.strategy is not None and self.strategy.cache is not None:
            return self.strategy.cache.position(position_id)
        return None

    def _is_position_open_for_instrument(self, inst_id: InstrumentId) -> bool:
        if self.strategy is None or self.strategy.cache is None:
            return False
        for pos in self.strategy.cache.positions_open():
            if pos.instrument_id == inst_id and pos.quantity.as_double() > 0:
                return True
        return False

    def _is_long(self, pos_or_event: Any) -> bool:
        if hasattr(pos_or_event, "is_long"):
            return bool(pos_or_event.is_long)
        if hasattr(pos_or_event, "side"):
            return pos_or_event.side == PositionSide.LONG
        return False

    def _extract_reference_price(self, pos_or_event: Any) -> float:
        if hasattr(pos_or_event, "avg_px_open") and pos_or_event.avg_px_open is not None:
            return float(pos_or_event.avg_px_open)
        if hasattr(pos_or_event, "last_px") and pos_or_event.last_px is not None:
            return float(pos_or_event.last_px)
        raise ValueError(f"Cannot extract reference price from {pos_or_event}")

    def _extract_quantity(self, pos_or_event: Any) -> Quantity:
        if hasattr(pos_or_event, "quantity"):
            return pos_or_event.quantity
        raise ValueError(f"Cannot extract quantity from {pos_or_event}")

    def _is_order_closed(self, client_order_id: Optional[ClientOrderId]) -> bool:
        if client_order_id is None:
            return True
        if self.strategy.cache.is_order_closed(client_order_id):
            return True
        order = self.strategy.cache.order(client_order_id)
        return order is not None and order.is_closed

    def _all_protective_orders_closed(self, inst_id: InstrumentId) -> bool:
        tp_id = self._tp_orders.get(inst_id)
        sl_id = self._sl_orders.get(inst_id)
        return self._is_order_closed(tp_id) and self._is_order_closed(sl_id)

    def _protection_matches(
        self,
        inst_id: InstrumentId,
        quantity: Quantity,
        tp_price: Price,
        sl_price: Price,
    ) -> bool:
        tp_id = self._tp_orders.get(inst_id)
        sl_id = self._sl_orders.get(inst_id)
        if tp_id is None or sl_id is None:
            return False

        tp_order = self.strategy.cache.order(tp_id)
        sl_order = self.strategy.cache.order(sl_id)

        return (
            tp_order is not None
            and tp_order.is_open
            and tp_order.quantity == quantity
            and tp_order.price == tp_price
            and sl_order is not None
            and sl_order.is_open
            and sl_order.quantity == quantity
            and sl_order.trigger_price == sl_price
        )

    def _submit_protective_orders(
        self,
        inst_id: InstrumentId,
        tp_price: Price,
        sl_price: Price,
        quantity: Quantity,
    ) -> None:
        if quantity.as_double() <= 0:
            return

        tp_order = self.strategy.order_factory.limit(
            instrument_id=inst_id,
            order_side=OrderSide.SELL,
            quantity=quantity,
            price=tp_price,
            reduce_only=True,
        )
        sl_order = self.strategy.order_factory.stop_market(
            instrument_id=inst_id,
            order_side=OrderSide.SELL,
            quantity=quantity,
            trigger_price=sl_price,
            reduce_only=True,
        )

        self._tp_orders[inst_id] = tp_order.client_order_id
        self._sl_orders[inst_id] = sl_order.client_order_id
        self._order_to_instrument[tp_order.client_order_id] = inst_id
        self._order_to_instrument[sl_order.client_order_id] = inst_id

        self.strategy.submit_order(tp_order)
        self.strategy.submit_order(sl_order)

    def _cancel_order_by_id(self, client_order_id: Optional[ClientOrderId]) -> None:
        if client_order_id is None or self.strategy is None:
            return
        if not self._is_order_closed(client_order_id):
            order = self.strategy.cache.order(client_order_id)
            if order is not None and not order.is_pending_cancel:
                self.strategy.cancel_order(client_order_id)

    def _cancel_active_protective_orders(self, inst_id: InstrumentId) -> None:
        tp_id = self._tp_orders.get(inst_id)
        sl_id = self._sl_orders.get(inst_id)
        if tp_id is not None:
            self._cancel_order_by_id(tp_id)
        if sl_id is not None:
            self._cancel_order_by_id(sl_id)

    def _cancel_all_for_instrument(self, inst_id: InstrumentId) -> None:
        self._pending_protection.pop(inst_id, None)
        self._cancel_active_protective_orders(inst_id)

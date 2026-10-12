import datetime
import logging
from typing import Any, Dict, List, Optional, Set
import pandas as pd

from nautilus_trader.model import ClientOrderId, OrderFilled, TradeId

logger = logging.getLogger(__name__)


class ExecutionEventRecorder:
    """
    Coordinates execution timestamps, reference prices, and fill events
    between NautilusTrader Strategy lifecycle, FeeModel, and FillModel.

    Guarantees:
    1. Supplies the actual execution timestamp (converted to exchange timezone)
       to DP charge tracking without falling back to order.ts_init.
    2. Preserves un-slipped reference prices from the FillModel during book simulation.
    3. Records slippage attribution strictly from authoritative OrderFilled events,
       correctly handling multiple partial fills per order with unique TradeIds.
    4. Ensures zero duplicate fill recording.
    """

    def __init__(self, exchange_tz: str = "Asia/Kolkata") -> None:
        self.exchange_tz = exchange_tz

        # Mapping: ClientOrderId -> nanoseconds timestamp of order release / execution
        self._execution_timestamps: Dict[str, int] = {}

        # Mapping: ClientOrderId -> reference price (un-slipped market price)
        self._reference_prices: Dict[str, float] = {}

        # Mapping: ClientOrderId -> slipped price target from fill model
        self._slipped_target_prices: Dict[str, float] = {}

        # Set of processed trade IDs to prevent double counting
        self._recorded_trade_ids: Set[str] = set()

        # Audited fill slippage records from actual OrderFilled events
        self._slippage_fill_records: List[Dict[str, Any]] = []

    def reset(self) -> None:
        """Clears all tracking state."""
        self._execution_timestamps.clear()
        self._reference_prices.clear()
        self._slipped_target_prices.clear()
        self._recorded_trade_ids.clear()
        self._slippage_fill_records.clear()

    def register_order_release(
        self,
        client_order_id: Any,
        timestamp_ns: int,
        intended_price: Optional[float] = None,
    ) -> None:
        """
        Registers an order release with its actual execution session timestamp.
        Called when the strategy releases a queued order upon opening QuoteTick arrival.

        Parameters:
            client_order_id: Order's ClientOrderId.
            timestamp_ns: Timestamp of the opening QuoteTick / order release in nanoseconds.
            intended_price: Optional reference price from the opening quote.
        """
        cid = str(client_order_id)
        if timestamp_ns <= 0:
            raise ValueError(f"Invalid execution timestamp_ns ({timestamp_ns}) for order {cid}")

        self._execution_timestamps[cid] = timestamp_ns
        if intended_price is not None and intended_price > 0.0:
            # Seed reference price if not already set by fill model
            if cid not in self._reference_prices:
                self._reference_prices[cid] = intended_price

        logger.debug(
            "Execution session registered for %s: ts_ns=%d (%s)",
            cid,
            timestamp_ns,
            self.get_trading_date_for_order(client_order_id),
        )

    def register_reference_price(
        self,
        client_order_id: Any,
        reference_price: float,
        slipped_price: Optional[float] = None,
    ) -> None:
        """
        Preserves the reference price used by the FillModel during matching simulation.

        Parameters:
            client_order_id: Order's ClientOrderId.
            reference_price: Un-slipped executable price (best ask for BUY, best bid for SELL).
            slipped_price: Slipped price target produced by adverse tick rounding.
        """
        cid = str(client_order_id)
        self._reference_prices[cid] = float(reference_price)
        if slipped_price is not None:
            self._slipped_target_prices[cid] = float(slipped_price)

    def get_reference_price(self, client_order_id: Any) -> Optional[float]:
        """Returns the preserved reference price for an order, or None if not registered."""
        return self._reference_prices.get(str(client_order_id))

    def get_execution_timestamp_ns(self, order: Any) -> int:
        """
        Retrieves the actual execution timestamp in nanoseconds for an order.
        Does NOT silently fall back to order.ts_init.

        Resolution order:
        1. Registered release timestamp from QuoteTick arrival.
        2. Order's ts_accepted / ts_submitted / ts_last (if > 0 and > ts_init).
        3. Raises RuntimeError if no valid execution timestamp exists.
        """
        cid = str(getattr(order, "client_order_id", order))

        # 1. Registered release timestamp
        if cid in self._execution_timestamps:
            return self._execution_timestamps[cid]

        # 2. Check accepted / submitted / last timestamps on order
        ts_accepted = getattr(order, "ts_accepted", None) or 0
        ts_init = getattr(order, "ts_init", 0) or 0
        if ts_accepted > 0 and ts_accepted != ts_init:
            return ts_accepted

        ts_submitted = getattr(order, "ts_submitted", None) or 0
        if ts_submitted > 0 and ts_submitted != ts_init:
            return ts_submitted

        ts_last = getattr(order, "ts_last", 0) or 0
        if ts_last > 0 and ts_last != ts_init:
            return ts_last

        # Refuse to silently use ts_init
        raise RuntimeError(
            f"Cannot determine actual execution timestamp for order '{cid}'. "
            f"Execution timestamp was not registered and order timestamp reflects creation time (ts_init={ts_init}). "
            f"FeeModel requires an actual execution timestamp for DP charge calculation."
        )

    def get_trading_date_for_order(self, order: Any) -> datetime.date:
        """
        Converts the actual execution timestamp of an order to the exchange's calendar date.
        """
        ts_ns = self.get_execution_timestamp_ns(order)
        return (
            pd.to_datetime(ts_ns, unit="ns", utc=True)
            .tz_convert(self.exchange_tz)
            .date()
        )

    def record_fill(
        self,
        event: OrderFilled,
        reference_price: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Records slippage attribution directly from an authoritative Nautilus OrderFilled event.

        Parameters:
            event: The OrderFilled event from Nautilus.
            reference_price: Optional explicit reference price override.

        Returns:
            Dict containing the itemised slippage record, or None if already recorded.
        """
        trade_id = str(event.trade_id)
        if trade_id in self._recorded_trade_ids:
            logger.warning("Duplicate OrderFilled received for trade_id %s; skipping", trade_id)
            return None

        self._recorded_trade_ids.add(trade_id)

        cid = str(event.client_order_id)
        ref_px = (
            reference_price
            if reference_price is not None and reference_price > 0.0
            else self.get_reference_price(cid)
        )

        fill_px = float(event.last_px.as_double())
        fill_qty = float(event.last_qty.as_double())
        is_buy = bool(event.is_buy)

        if ref_px is None or ref_px <= 0.0:
            # If no reference price was preserved, zero slippage is attributed
            logger.debug(
                "No reference price preserved for fill %s (client_order_id=%s); using fill_px=%.2f",
                trade_id,
                cid,
                fill_px,
            )
            ref_px = fill_px

        # Adverse slippage per share
        if is_buy:
            slippage_per_share = round(max(0.0, fill_px - ref_px), 4)
        else:
            slippage_per_share = round(max(0.0, ref_px - fill_px), 4)

        total_slippage_cost = round(fill_qty * slippage_per_share, 2)

        ts_event = int(event.ts_event)
        trading_date = (
            pd.to_datetime(ts_event, unit="ns", utc=True)
            .tz_convert(self.exchange_tz)
            .date()
        )

        record = {
            "trade_id": trade_id,
            "client_order_id": cid,
            "venue_order_id": str(getattr(event, "venue_order_id", "")),
            "instrument_id": str(event.instrument_id),
            "side": "BUY" if is_buy else "SELL",
            "fill_quantity": fill_qty,
            "fill_price": fill_px,
            "reference_price": ref_px,
            "slippage_per_share": slippage_per_share,
            "total_slippage_cost": total_slippage_cost,
            "ts_event": ts_event,
            "date": trading_date,
        }
        self._slippage_fill_records.append(record)

        logger.debug(
            "FILL RECORDED [Slippage]: %s %s trade_id=%s qty=%.2f px=%.2f ref_px=%.2f slip_per_sh=%.4f cost=₹%.2f date=%s",
            record["side"],
            record["instrument_id"],
            trade_id,
            fill_qty,
            fill_px,
            ref_px,
            slippage_per_share,
            total_slippage_cost,
            trading_date,
        )
        return record

    @property
    def slippage_records(self) -> List[Dict[str, Any]]:
        """List of all audited slippage records from actual fills."""
        return list(self._slippage_fill_records)

    @property
    def fill_count(self) -> int:
        """Total number of actual execution fills recorded."""
        return len(self._slippage_fill_records)

    @property
    def total_slippage_cost(self) -> float:
        """Total execution slippage cost incurred across all actual fills in INR."""
        return round(sum(r["total_slippage_cost"] for r in self._slippage_fill_records), 2)

    def to_dataframe(self) -> pd.DataFrame:
        """Returns audited slippage fill records as a pandas DataFrame."""
        return pd.DataFrame(self._slippage_fill_records)

from dataclasses import dataclass
import logging
import math
from typing import Any, Dict, List, Optional, Set
import pandas as pd

from nautilus_trader.execution import FillModel
from nautilus_trader.model import (
    BookAction,
    BookOrder,
    BookType,
    OrderBook,
    OrderBookDelta,
    OrderFilled,
    OrderSide,
    Price,
    Quantity,
)
from equity_strategy.execution.recorder import ExecutionEventRecorder

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdverseSlippageConfig:
    """
    Configuration parameters for execution slippage simulation.

    Parameters:
        slippage_pct: Adverse slippage percentage per side (default: 0.0005 = 0.05% = 5 bps).
                      Set to 0.0 for zero-slippage baseline comparison.
        enabled: Toggle to enable or bypass slippage simulation.
    """

    slippage_pct: float = 0.0005
    enabled: bool = True


class AdverseSlippageFillModel(FillModel):
    """
    NautilusTrader FillModel providing realistic adverse execution slippage for equity orders.

    Mechanics:
    - Responsible ONLY for supplying adverse execution prices to Nautilus's matching engine
      via `get_orderbook_for_fill_simulation`.
    - Preserves un-slipped market reference prices for each order.
    - Slipped prices are rounded strictly in adverse tick increments using the instrument's
      native tick size (ceil for BUY, floor for SELL).
    - Fills are NOT recorded during order-book construction. Slippage attribution is recorded
      strictly from actual `OrderFilled` events, supporting multiple partial fills per order ID.
    - Raw Nautilus fill records remain authoritative; no duplicate or fake executions are created.
    - Supports a 0.0 baseline mode for zero-cost comparison.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> "AdverseSlippageFillModel":
        return super().__new__(cls)

    def __init__(
        self,
        config: Optional[AdverseSlippageConfig] = None,
        slippage_pct: Optional[float] = None,
        recorder: Optional[ExecutionEventRecorder] = None,
        exchange_tz: str = "Asia/Kolkata",
    ) -> None:
        super().__init__()
        self.config = config or AdverseSlippageConfig()
        if slippage_pct is not None:
            self.slippage_pct = float(slippage_pct)
        else:
            self.slippage_pct = self.config.slippage_pct if self.config.enabled else 0.0

        self.recorder = recorder
        self.exchange_tz = exchange_tz

        # Preserved reference prices: ClientOrderId -> un-slipped price
        self._reference_prices: Dict[str, float] = {}

        # Tracking for actual fills (when used standalone without recorder)
        self._slippage_records: List[Dict[str, Any]] = []
        self._recorded_trade_ids: Set[str] = set()

    def set_recorder(self, recorder: ExecutionEventRecorder) -> None:
        """Attaches an ExecutionEventRecorder to coordinate reference prices and fills."""
        self.recorder = recorder

    def reset(self) -> None:
        """Clears preserved reference prices, fill records, and state."""
        self._reference_prices.clear()
        self._slippage_records.clear()
        self._recorded_trade_ids.clear()
        if self.recorder is not None:
            self.recorder.reset()

    def is_slipped(self) -> bool:
        """Indicates whether this model applies price slippage."""
        return self.slippage_pct > 0.0

    def register_reference_price(
        self,
        client_order_id: Any,
        reference_price: float,
        slipped_price: Optional[float] = None,
    ) -> None:
        """Preserves the un-slipped reference price for an order."""
        cid = str(client_order_id)
        self._reference_prices[cid] = float(reference_price)
        if self.recorder is not None:
            self.recorder.register_reference_price(cid, reference_price, slipped_price)

    def get_reference_price(self, client_order_id: Any) -> Optional[float]:
        """Returns the preserved un-slipped reference price for an order."""
        cid = str(client_order_id)
        if self.recorder is not None:
            ref = self.recorder.get_reference_price(cid)
            if ref is not None:
                return ref
        return self._reference_prices.get(cid)

    def get_orderbook_for_fill_simulation(
        self,
        instrument: Any,
        order: Any,
        best_bid: Price,
        best_ask: Price,
    ) -> Optional[OrderBook]:
        """
        Generates a synthetic order book with adverse slippage applied to the executable price.
        Does NOT record fills or slippage attribution here. Preserves the reference price only.

        Parameters:
            instrument: The Instrument being traded.
            order: The Order being matched.
            best_bid: Top of book best bid price.
            best_ask: Top of book best ask price.

        Returns:
            OrderBook with slipped price levels, or None if slippage is zero/disabled.
        """
        if self.slippage_pct <= 0.0:
            if order is not None and best_ask is not None and best_bid is not None:
                is_buy = (
                    order.side == OrderSide.BUY
                    if hasattr(order, "side")
                    else getattr(order, "is_buy", True)
                )
                un_slipped_px = best_ask.as_double() if is_buy else best_bid.as_double()
                self.register_reference_price(order.client_order_id, un_slipped_px)
            return None

        if (
            instrument is None
            or order is None
            or best_bid is None
            or best_ask is None
        ):
            return None

        bid_px = best_bid.as_double()
        ask_px = best_ask.as_double()
        if bid_px <= 0.0 or ask_px <= 0.0:
            return None

        # Determine order side (BUY vs SELL)
        if hasattr(order, "side"):
            is_buy = order.side == OrderSide.BUY
        elif hasattr(order, "is_buy"):
            is_buy = bool(order.is_buy)
        else:
            is_buy = True

        increment = (
            float(instrument.price_increment)
            if getattr(instrument, "price_increment", None) is not None
            else 0.05
        )
        precision = (
            instrument.price_precision
            if getattr(instrument, "price_precision", None) is not None
            else 2
        )

        order_qty = getattr(order, "quantity", None) or Quantity.from_int(1_000_000)
        book_size = max(order_qty, Quantity.from_int(1_000_000))

        client_order_id = str(getattr(order, "client_order_id", ""))

        book = OrderBook(instrument.id, BookType.L1_MBP)

        if is_buy:
            # BUY order matches against Ask. Slipped ask is HIGHER (ceil to tick).
            un_slipped_px = ask_px
            raw_slipped = un_slipped_px * (1.0 + self.slippage_pct)
            ticks = math.ceil((raw_slipped / increment) - 1e-9)
            slipped_px = round(ticks * increment, precision)

            # Preserve reference price for later OrderFilled attribution
            self.register_reference_price(client_order_id, un_slipped_px, slipped_px)

            ask_order = BookOrder(
                OrderSide.SELL,
                Price(slipped_px, precision),
                book_size,
                1,
            )
            bid_order = BookOrder(
                OrderSide.BUY,
                best_bid,
                book_size,
                2,
            )
            book.apply_delta(
                OrderBookDelta(instrument.id, BookAction.ADD, ask_order, 0, 1, 0, 0)
            )
            book.apply_delta(
                OrderBookDelta(instrument.id, BookAction.ADD, bid_order, 0, 2, 0, 0)
            )
        else:
            # SELL order matches against Bid. Slipped bid is LOWER (floor to tick).
            un_slipped_px = bid_px
            raw_slipped = un_slipped_px * (1.0 - self.slippage_pct)
            ticks = math.floor((raw_slipped / increment) + 1e-9)
            slipped_px = round(ticks * increment, precision)

            # Preserve reference price for later OrderFilled attribution
            self.register_reference_price(client_order_id, un_slipped_px, slipped_px)

            bid_order = BookOrder(
                OrderSide.BUY,
                Price(slipped_px, precision),
                book_size,
                1,
            )
            ask_order = BookOrder(
                OrderSide.SELL,
                best_ask,
                book_size,
                2,
            )
            book.apply_delta(
                OrderBookDelta(instrument.id, BookAction.ADD, bid_order, 0, 1, 0, 0)
            )
            book.apply_delta(
                OrderBookDelta(instrument.id, BookAction.ADD, ask_order, 0, 2, 0, 0)
            )

        return book

    def record_fill(
        self,
        event: OrderFilled,
        reference_price: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Records slippage attribution strictly from an authoritative OrderFilled event.
        Correctly supports multiple partial fills for the same client_order_id.

        Parameters:
            event: The OrderFilled event from Nautilus.
            reference_price: Optional explicit reference price override.

        Returns:
            Dict containing the itemised slippage record, or None if already recorded.
        """
        if self.recorder is not None:
            return self.recorder.record_fill(event, reference_price=reference_price)

        trade_id = str(event.trade_id)
        if trade_id in self._recorded_trade_ids:
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
            ref_px = fill_px

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
        self._slippage_records.append(record)

        logger.debug(
            "FILL RECORDED [Slippage]: %s %s trade_id=%s qty=%.2f px=%.2f ref_px=%.2f diff=%.4f cost=₹%.2f date=%s",
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
        if self.recorder is not None:
            return self.recorder.slippage_records
        return list(self._slippage_records)

    @property
    def fill_count(self) -> int:
        """Total number of actual execution fills recorded."""
        if self.recorder is not None:
            return self.recorder.fill_count
        return len(self._slippage_records)

    @property
    def total_slippage_cost(self) -> float:
        """Total execution slippage cost incurred across all actual fills in INR."""
        if self.recorder is not None:
            return self.recorder.total_slippage_cost
        return round(sum(r["total_slippage_cost"] for r in self._slippage_records), 2)

    def to_dataframe(self) -> pd.DataFrame:
        """Returns audited slippage fill records as a pandas DataFrame."""
        if self.recorder is not None:
            return self.recorder.to_dataframe()
        return pd.DataFrame(self._slippage_records)

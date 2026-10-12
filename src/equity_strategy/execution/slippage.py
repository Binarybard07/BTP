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
    OrderSide,
    Price,
    Quantity,
)

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
    - Overrides `get_orderbook_for_fill_simulation` to dynamically provide an order book
      whose top-of-book reflects the adversely slipped price.
    - Slipped prices are rounded strictly in adverse tick increments using the instrument's
      native tick size (ceil for BUY, floor for SELL).
    - Updates actual Nautilus execution fill prices, debiting cash balances and adjusting
      portfolio equity authoritatively through Nautilus's native accounting.
    - Supports a 0.0 baseline mode for zero-cost comparison.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> "AdverseSlippageFillModel":
        return super().__new__(cls)

    def __init__(
        self,
        config: Optional[AdverseSlippageConfig] = None,
        slippage_pct: Optional[float] = None,
    ) -> None:
        super().__init__()
        self.config = config or AdverseSlippageConfig()
        if slippage_pct is not None:
            self.slippage_pct = float(slippage_pct)
        else:
            self.slippage_pct = self.config.slippage_pct if self.config.enabled else 0.0

        self._slippage_records: List[Dict[str, Any]] = []
        self._recorded_client_order_ids: Set[str] = set()

    def reset(self) -> None:
        """Clears audit records and state."""
        self._slippage_records.clear()
        self._recorded_client_order_ids.clear()

    def is_slipped(self) -> bool:
        """Indicates whether this model applies price slippage."""
        return self.slippage_pct > 0.0

    def get_orderbook_for_fill_simulation(
        self,
        instrument: Any,
        order: Any,
        best_bid: Price,
        best_ask: Price,
    ) -> Optional[OrderBook]:
        """
        Generates a synthetic order book with adverse slippage applied to the executable price.

        Parameters:
            instrument: The Instrument being traded.
            order: The Order being matched.
            best_bid: Top of book best bid price.
            best_ask: Top of book best ask price.

        Returns:
            OrderBook with slipped price levels, or None if slippage is zero/disabled.
        """
        if self.slippage_pct <= 0.0:
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
        qty_float = (
            order_qty.as_double()
            if hasattr(order_qty, "as_double")
            else float(order_qty)
        )
        book_size = max(order_qty, Quantity.from_int(1_000_000))

        client_order_id = str(getattr(order, "client_order_id", ""))

        book = OrderBook(instrument.id, BookType.L1_MBP)

        if is_buy:
            # BUY order matches against Ask. Slipped ask is HIGHER (ceil to tick).
            un_slipped_px = ask_px
            raw_slipped = un_slipped_px * (1.0 + self.slippage_pct)
            ticks = math.ceil((raw_slipped / increment) - 1e-9)
            slipped_px = round(ticks * increment, precision)

            # Record audit once per client_order_id
            if client_order_id and client_order_id not in self._recorded_client_order_ids:
                self._record_slippage(
                    client_order_id=client_order_id,
                    instrument_id=str(instrument.id),
                    side="BUY",
                    quantity=qty_float,
                    un_slipped_price=un_slipped_px,
                    slipped_price=slipped_px,
                    ts_event=getattr(order, "ts_init", 0) or 0,
                )
                self._recorded_client_order_ids.add(client_order_id)

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

            # Record audit once per client_order_id
            if client_order_id and client_order_id not in self._recorded_client_order_ids:
                self._record_slippage(
                    client_order_id=client_order_id,
                    instrument_id=str(instrument.id),
                    side="SELL",
                    quantity=qty_float,
                    un_slipped_price=un_slipped_px,
                    slipped_price=slipped_px,
                    ts_event=getattr(order, "ts_init", 0) or 0,
                )
                self._recorded_client_order_ids.add(client_order_id)

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

    def _record_slippage(
        self,
        client_order_id: str,
        instrument_id: str,
        side: str,
        quantity: float,
        un_slipped_price: float,
        slipped_price: float,
        ts_event: int,
    ) -> None:
        """Stores itemised slippage event for audit and cost analysis."""
        slippage_per_share = round(abs(slipped_price - un_slipped_price), 4)
        total_slippage_cost = round(quantity * slippage_per_share, 2)

        self._slippage_records.append({
            "client_order_id": client_order_id,
            "instrument_id": instrument_id,
            "side": side,
            "quantity": quantity,
            "un_slipped_price": un_slipped_price,
            "slipped_price": slipped_price,
            "slippage_per_share": slippage_per_share,
            "total_slippage_cost": total_slippage_cost,
            "ts_event": ts_event,
        })

        logger.debug(
            "SLIPPAGE APPLIED: %s %s qty=%.2f | un_slipped=%.2f -> slipped=%.2f (diff=%.4f) => cost=₹%.2f",
            side,
            instrument_id,
            quantity,
            un_slipped_price,
            slipped_price,
            slippage_per_share,
            total_slippage_cost,
        )

    @property
    def slippage_records(self) -> List[Dict[str, Any]]:
        """List of all audited slippage records."""
        return list(self._slippage_records)

    @property
    def fill_count(self) -> int:
        """Total number of slipped orders tracked."""
        return len(self._slippage_records)

    @property
    def total_slippage_cost(self) -> float:
        """Total execution slippage cost incurred across all orders in INR."""
        return round(sum(r["total_slippage_cost"] for r in self._slippage_records), 2)

    def to_dataframe(self) -> pd.DataFrame:
        """Returns audited slippage records as a pandas DataFrame."""
        return pd.DataFrame(self._slippage_records)

import abc
from dataclasses import dataclass
import logging
import math
from typing import Any, Dict, Optional, Union

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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Modular Slippage Calculation Methods
# ---------------------------------------------------------------------------

class SlippageMethod(abc.ABC):
    """
    Abstract interface for execution price slippage calculation methods.

    Separates the mathematical price adjustment logic from NautilusTrader's
    order book construction, tick rounding, and execution fill lifecycle.
    """

    @abc.abstractmethod
    def calculate_adjusted_price(
        self,
        reference_price: float,
        is_buy: bool,
        instrument: Any,
        best_bid: float,
        best_ask: float,
    ) -> float:
        """
        Calculates the raw adjusted executable price before tick-precision rounding.

        Parameters:
            reference_price: Original un-slipped market price (best_ask for BUY, best_bid for SELL).
            is_buy: True for BUY orders, False for SELL orders.
            instrument: The Instrument being traded.
            best_bid: Top-of-book best bid price.
            best_ask: Top-of-book best ask price.

        Returns:
            float: Adjusted executable price.
        """
        pass


class ZeroSlippage(SlippageMethod):
    """Zero slippage method for baseline cost-free comparison."""

    def calculate_adjusted_price(
        self,
        reference_price: float,
        is_buy: bool,
        instrument: Any,
        best_bid: float,
        best_ask: float,
    ) -> float:
        return reference_price


class PercentSlippage(SlippageMethod):
    """
    Percentage-based adverse execution slippage.

    Parameters:
        slippage_pct: Adverse slippage percentage per side (e.g., 0.0005 = 0.05% = 5 bps).
    """

    def __init__(self, slippage_pct: float = 0.0005) -> None:
        self.slippage_pct = max(0.0, float(slippage_pct))

    def calculate_adjusted_price(
        self,
        reference_price: float,
        is_buy: bool,
        instrument: Any,
        best_bid: float,
        best_ask: float,
    ) -> float:
        if self.slippage_pct <= 0.0:
            return reference_price
        if is_buy:
            return reference_price * (1.0 + self.slippage_pct)
        return reference_price * (1.0 - self.slippage_pct)


class FixedTickSlippage(SlippageMethod):
    """
    Fixed price or tick-based adverse execution slippage.

    Parameters:
        ticks: Number of instrument price increments to slip adversely.
    """

    def __init__(self, ticks: int = 1) -> None:
        self.ticks = max(0, int(ticks))

    def calculate_adjusted_price(
        self,
        reference_price: float,
        is_buy: bool,
        instrument: Any,
        best_bid: float,
        best_ask: float,
    ) -> float:
        increment = float(getattr(instrument, "price_increment", None) or 0.05)
        tick_delta = self.ticks * increment
        if is_buy:
            return reference_price + tick_delta
        return reference_price - tick_delta


class SpreadSlippage(SlippageMethod):
    """
    Spread-based adverse execution slippage.

    Parameters:
        spread_pct: Fraction of the prevailing bid-ask spread to slip adversely (default: 0.5).
    """

    def __init__(self, spread_pct: float = 0.5) -> None:
        self.spread_pct = max(0.0, float(spread_pct))

    def calculate_adjusted_price(
        self,
        reference_price: float,
        is_buy: bool,
        instrument: Any,
        best_bid: float,
        best_ask: float,
    ) -> float:
        spread = max(0.0, best_ask - best_bid)
        spread_delta = spread * self.spread_pct
        if is_buy:
            return reference_price + spread_delta
        return reference_price - spread_delta


# ---------------------------------------------------------------------------
# 2. Configuration Wrapper (Backward-Compatible)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdverseSlippageConfig:
    """
    Configuration parameters for execution slippage simulation.

    Parameters:
        slippage_pct: Adverse percentage slippage per side (default: 0.0005 = 5 bps).
        enabled: Toggle to enable or bypass slippage simulation.
    """

    slippage_pct: float = 0.0005
    enabled: bool = True


# ---------------------------------------------------------------------------
# 3. Modular NautilusTrader FillModel Integration
# ---------------------------------------------------------------------------

class SlippageFillModel(FillModel):
    """
    Shared NautilusTrader FillModel coordinating modular price slippage methods,
    adverse tick-precision rounding, and reference-price attribution.

    Responsibilities:
    1. Delegates executable price calculation to a configurable `SlippageMethod`.
    2. Applies instrument-consistent adverse tick increments (ceil for BUY, floor for SELL).
    3. Preserves minimal un-slipped reference prices for each order and fill.
    4. Supports partial fills: all fills for an order map reliably to the reference price.
    5. Raises explicit errors when reference price metadata is missing instead of silent zero fallback.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> "SlippageFillModel":
        return super().__new__(cls)

    def __init__(
        self,
        method: Optional[SlippageMethod] = None,
        *,
        config: Optional[AdverseSlippageConfig] = None,
        slippage_pct: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()

        # Resolve SlippageMethod from parameters
        if method is not None:
            self.method = method
        elif config is not None and not config.enabled:
            self.method = ZeroSlippage()
        elif slippage_pct is not None:
            self.method = PercentSlippage(slippage_pct) if slippage_pct > 0.0 else ZeroSlippage()
        elif config is not None:
            self.method = PercentSlippage(config.slippage_pct) if config.slippage_pct > 0.0 else ZeroSlippage()
        else:
            self.method = PercentSlippage(0.0005)

        # Minimal reference-price metadata:
        # 1. client_order_id -> un-slipped reference price (set during book simulation)
        self._order_reference_prices: Dict[str, float] = {}
        # 2. trade_id -> un-slipped reference price (associated on OrderFilled)
        self._fill_reference_prices: Dict[str, float] = {}

    def is_slipped(self) -> bool:
        """
        Returns False to prevent NautilusTrader from applying an unintended secondary
        one-tick slippage draw on top of our synthetic OrderBook price levels.
        """
        return False

    @property
    def has_slippage(self) -> bool:
        """Indicates whether this model applies non-zero price slippage."""
        return not isinstance(self.method, ZeroSlippage)

    @property
    def slippage_pct(self) -> float:
        """Returns the configured percentage slippage, or 0.0 if not percent-based."""
        if isinstance(self.method, PercentSlippage):
            return self.method.slippage_pct
        return 0.0

    def reset(self) -> None:
        """Clears all preserved reference-price associations."""
        self._order_reference_prices.clear()
        self._fill_reference_prices.clear()

    def get_reference_price(self, identifier: Any) -> float:
        """
        Retrieves the un-slipped reference price for a given client order ID or trade ID.

        Raises:
            KeyError: If no reference price is registered for the identifier.
        """
        key = str(identifier)
        if key in self._fill_reference_prices:
            return self._fill_reference_prices[key]
        if key in self._order_reference_prices:
            return self._order_reference_prices[key]

        raise KeyError(f"No reference price registered for identifier '{key}'.")

    def record_fill(self, event: OrderFilled) -> float:
        """
        Associates an authoritative OrderFilled event with the order's reference price
        and calculates adverse slippage per share. Correctly handles multiple partial fills.

        Parameters:
            event: The OrderFilled event from Nautilus.

        Returns:
            float: Slipped price difference per share (adverse >= 0.0).

        Raises:
            KeyError: If the order's reference price was not preserved.
        """
        cid = str(event.client_order_id)
        trade_id = str(event.trade_id)

        if cid not in self._order_reference_prices:
            raise KeyError(
                f"Missing reference price metadata for client_order_id='{cid}' on trade_id='{trade_id}'. "
                f"Cannot attribute execution slippage."
            )

        ref_px = self._order_reference_prices[cid]
        self._fill_reference_prices[trade_id] = ref_px

        fill_px = float(event.last_px.as_double())
        is_buy = bool(event.is_buy)

        if is_buy:
            slippage_per_share = round(max(0.0, fill_px - ref_px), 4)
        else:
            slippage_per_share = round(max(0.0, ref_px - fill_px), 4)

        return slippage_per_share

    def get_orderbook_for_fill_simulation(
        self,
        instrument: Any,
        order: Any,
        best_bid: Price,
        best_ask: Price,
    ) -> Optional[OrderBook]:
        """
        Supplies a synthetic order book with adverse price adjustment to Nautilus's matching engine.

        Parameters:
            instrument: The Instrument being traded.
            order: The Order being matched.
            best_bid: Top of book best bid price.
            best_ask: Top of book best ask price.

        Returns:
            OrderBook with slipped price levels, or None if slippage is zero.
        """
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

        if hasattr(order, "side"):
            is_buy = order.side == OrderSide.BUY
        elif hasattr(order, "is_buy"):
            is_buy = bool(order.is_buy)
        else:
            is_buy = True

        ref_px = ask_px if is_buy else bid_px
        cid = str(getattr(order, "client_order_id", ""))
        if cid:
            self._order_reference_prices[cid] = ref_px

        # Zero slippage returns None, letting Nautilus match natively against the original quote
        if isinstance(self.method, ZeroSlippage):
            return None

        raw_slipped_px = self.method.calculate_adjusted_price(
            reference_price=ref_px,
            is_buy=is_buy,
            instrument=instrument,
            best_bid=bid_px,
            best_ask=ask_px,
        )

        increment = float(getattr(instrument, "price_increment", None) or 0.05)
        precision = int(getattr(instrument, "price_precision", None) or 2)

        # Adverse tick rounding:
        # BUY matches Ask -> higher slipped price -> ceil
        # SELL matches Bid -> lower slipped price -> floor
        if is_buy:
            ticks = math.ceil((raw_slipped_px / increment) - 1e-9)
        else:
            ticks = math.floor((raw_slipped_px / increment) + 1e-9)

        slipped_px = round(ticks * increment, precision)

        order_qty = getattr(order, "quantity", None) or Quantity.from_int(1_000_000)
        book_size = max(order_qty, Quantity.from_int(1_000_000))

        book = OrderBook(instrument.id, BookType.L1_MBP)
        if is_buy:
            ask_order = BookOrder(OrderSide.SELL, Price(slipped_px, precision), book_size, 1)
            bid_order = BookOrder(OrderSide.BUY, best_bid, book_size, 2)
            book.apply_delta(OrderBookDelta(instrument.id, BookAction.ADD, ask_order, 0, 1, 0, 0))
            book.apply_delta(OrderBookDelta(instrument.id, BookAction.ADD, bid_order, 0, 2, 0, 0))
        else:
            bid_order = BookOrder(OrderSide.BUY, Price(slipped_px, precision), book_size, 1)
            ask_order = BookOrder(OrderSide.SELL, best_ask, book_size, 2)
            book.apply_delta(OrderBookDelta(instrument.id, BookAction.ADD, bid_order, 0, 1, 0, 0))
            book.apply_delta(OrderBookDelta(instrument.id, BookAction.ADD, ask_order, 0, 2, 0, 0))

        return book
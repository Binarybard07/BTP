from dataclasses import dataclass
import datetime
import logging
from typing import Any, Dict, Optional
import pandas as pd

from nautilus_trader.execution import FeeModel
from nautilus_trader.model import Currency, Money, OrderSide, Price, Quantity

from equity_strategy.execution.charges import (
    DPChargesConfig,
    DPChargesTracker,
    OrdinaryChargesBreakdown,
    OrdinaryChargesCalculator,
    OrdinaryChargesConfig,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChargeBreakdown:
    """
    Itemised breakdown of statutory delivery transaction charges and DP charges
    for an individual executed fill, reconciling with Nautilus commission debited.
    """

    ordinary: OrdinaryChargesBreakdown
    dp_charge: float
    total_commission: float


class IndianEquityDeliveryFeeModel(FeeModel):
    """
    NautilusTrader FeeModel implementing realistic Indian equity cash-delivery transaction charges.

    Responsibilities:
    1. Ordinary Transaction Charges (via OrdinaryChargesCalculator):
       - Brokerage, STT, NSE Transaction Charge, SEBI Fee, Stamp Duty, GST.
       - Calculated strictly on the actual fill execution price (including slippage).
    2. Depository Participant (DP) Charges (via DPChargesTracker):
       - Applied strictly to delivery sells, charged at most once per security/ISIN per trading day.
       - Evaluated using the actual execution timestamp converted to the configured exchange timezone.
    3. Commission Reporting:
       - Returns authoritative total commission Money to NautilusTrader for cash balance accounting.
       - Retains itemised charge breakdowns for calculation verification without duplicating order ledgers.
    """

    def __init__(
        self,
        ordinary_config: Optional[OrdinaryChargesConfig] = None,
        dp_config: Optional[DPChargesConfig] = None,
        exchange_tz: str = "Asia/Kolkata",
    ) -> None:
        super().__init__()
        self.ordinary_config = ordinary_config if ordinary_config is not None else OrdinaryChargesConfig()
        self.dp_config = dp_config if dp_config is not None else DPChargesConfig()
        self.exchange_tz = exchange_tz
        self.clock: Optional[Any] = None

        self.ordinary_calculator = OrdinaryChargesCalculator(self.ordinary_config)
        self.dp_tracker = DPChargesTracker(self.dp_config)

        # Minimal itemised breakdown storage for verification
        self._last_breakdown: Optional[ChargeBreakdown] = None
        self._charge_breakdowns: Dict[str, ChargeBreakdown] = {}

    def set_clock(self, clock: Any) -> None:
        """Attaches the NautilusTrader simulation clock."""
        self.clock = clock

    def reset(self) -> None:
        """Clears DP tracking and stored breakdowns."""
        self.dp_tracker.reset()
        self._last_breakdown = None
        self._charge_breakdowns.clear()

    def get_trading_date(self, order: Any) -> datetime.date:
        """
        Determines the actual trading date in the exchange timezone for the fill.

        Resolution hierarchy:
        1. Nautilus simulation clock (authoritative at execution time during backtest).
        2. Order lifecycle timestamps (ts_last, ts_accepted, ts_submitted) if distinct from ts_init.
        3. Fallback to order.ts_init if no other timestamp is available.
        """
        if self.clock is not None:
            ts_ns = self.clock.timestamp_ns()
            if ts_ns > 0:
                return (
                    pd.to_datetime(ts_ns, unit="ns", utc=True)
                    .tz_convert(self.exchange_tz)
                    .date()
                )

        ts_init = getattr(order, "ts_init", 0) or 0
        for attr in ("ts_last", "ts_accepted", "ts_submitted"):
            ts = getattr(order, attr, None) or 0
            if ts > 0 and ts != ts_init:
                return (
                    pd.to_datetime(ts, unit="ns", utc=True)
                    .tz_convert(self.exchange_tz)
                    .date()
                )

        if ts_init > 0:
            return (
                pd.to_datetime(ts_init, unit="ns", utc=True)
                .tz_convert(self.exchange_tz)
                .date()
            )

        raise RuntimeError("Cannot determine execution trading date: clock not set and order has no timestamp.")

    def get_commission(
        self,
        order: Any,
        fill_quantity: Quantity,
        fill_px: Price,
        instrument: Any,
    ) -> Money:
        """
        Calculates and returns the exact execution commission for a fill.
        Directly debited by NautilusTrader from the trading account cash balance.

        Parameters:
            order: The Nautilus Order being filled.
            fill_quantity: Executed quantity.
            fill_px: Actual executed fill price (including slippage adjustment).
            instrument: The Instrument being traded.

        Returns:
            Money: Total fill commission in the instrument's quote currency.
        """
        quantity = fill_quantity.as_double()
        price = fill_px.as_double()

        if hasattr(order, "side"):
            is_buy = order.side == OrderSide.BUY
        elif hasattr(order, "is_buy"):
            is_buy = bool(order.is_buy)
        else:
            is_buy = True

        trading_date = self.get_trading_date(order)

        # Prefer explicit ISIN when defined on instrument; fall back to trading symbol
        security_id = getattr(instrument, "isin", None)
        if not security_id:
            if hasattr(instrument.id, "symbol"):
                security_id = str(instrument.id.symbol.value)
            else:
                security_id = str(instrument.id).split(".")[0]

        # Calculate ordinary statutory delivery charges on actual fill price
        ordinary = self.ordinary_calculator.calculate(
            quantity=quantity,
            price=price,
            is_buy=is_buy,
        )

        # Evaluate DP charge (only on delivery sells, at most once per security per trading day)
        dp_charge = self.dp_tracker.evaluate_dp_charge(
            security_id=security_id,
            trading_date=trading_date,
            is_sell=not is_buy,
            fill_quantity=quantity,
        )

        total_commission = round(ordinary.total_ordinary + dp_charge, 2)

        breakdown = ChargeBreakdown(
            ordinary=ordinary,
            dp_charge=dp_charge,
            total_commission=total_commission,
        )
        self._last_breakdown = breakdown

        cid = str(getattr(order, "client_order_id", ""))
        if cid:
            self._charge_breakdowns[cid] = breakdown

        logger.debug(
            "COMMISSION CHARGED: %s %s qty=%.2f px=%.2f | Ord=INR %.2f + DP=INR %.2f => Total=INR %.2f (date=%s)",
            "BUY" if is_buy else "SELL",
            security_id,
            quantity,
            price,
            ordinary.total_ordinary,
            dp_charge,
            total_commission,
            trading_date,
        )

        currency = getattr(instrument, "quote_currency", None) or Currency.from_str("INR")
        return Money(total_commission, currency)

    def get_commission_with_context(
        self,
        order: Any,
        fill_quantity: Quantity,
        fill_px: Price,
        instrument: Any,
        _underlying_px: Optional[Price] = None,
    ) -> Money:
        """Context-aware commission hook required by NautilusTrader."""
        return self.get_commission(order, fill_quantity, fill_px, instrument)

    @property
    def last_charge_breakdown(self) -> Optional[ChargeBreakdown]:
        """Most recent itemised charge breakdown calculated by this fee model."""
        return self._last_breakdown

    def get_charge_breakdown(self, client_order_id: Any) -> Optional[ChargeBreakdown]:
        """Returns the itemised charge breakdown for a given client order ID."""
        return self._charge_breakdowns.get(str(client_order_id))

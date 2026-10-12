import datetime
import logging
from typing import Any, Dict, List, Optional
import pandas as pd

from nautilus_trader.execution import FeeModel
from nautilus_trader.model import Currency, Money, OrderFilled, OrderSide, Price, Quantity

from equity_strategy.execution.charges import (
    DPChargesConfig,
    DPChargesTracker,
    OrdinaryChargesCalculator,
    OrdinaryChargesConfig,
)
from equity_strategy.execution.recorder import ExecutionEventRecorder

logger = logging.getLogger(__name__)


class IndianEquityDeliveryFeeModel(FeeModel):
    """
    NautilusTrader FeeModel implementing realistic Indian equity cash-delivery transaction charges.

    Covers:
    1. Ordinary Transaction Charges:
       - Brokerage (0% by default, configurable)
       - STT (0.10% on BUY and SELL turnover)
       - NSE Transaction Charge (0.00307% on turnover)
       - SEBI Turnover Fee (₹10 / crore = 0.0001% on turnover)
       - Stamp Duty (0.015% on BUY turnover, 0% on SELL turnover)
       - GST (18% on brokerage + NSE txn + SEBI fee)
    2. Depository Participant (DP) Charges:
       - ₹15.34 (₹9.50 broker + ₹3.50 CDSL/NSDL + 18% GST)
       - Incurred strictly on delivery sells (never on buys)
       - Charged at most once per qualifying ISIN per trading day regardless of order or fill count.
       - Handled natively via Nautilus fee accounting without duplicate cash ledgers.
       - Evaluated strictly using the actual execution timestamp (converted to exchange timezone),
         never falling back silently to order creation time (order.ts_init).
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> "IndianEquityDeliveryFeeModel":
        return super().__new__(cls)

    def __init__(
        self,
        ordinary_config: Optional[OrdinaryChargesConfig] = None,
        dp_config: Optional[DPChargesConfig] = None,
        recorder: Optional[ExecutionEventRecorder] = None,
        exchange_tz: str = "Asia/Kolkata",
    ) -> None:
        super().__init__()
        self.ordinary_config = ordinary_config or OrdinaryChargesConfig()
        self.dp_config = dp_config or DPChargesConfig()
        self.recorder = recorder
        self.exchange_tz = exchange_tz
        self.clock: Optional[Any] = None

        self.ordinary_calculator = OrdinaryChargesCalculator(self.ordinary_config)
        self.dp_tracker = DPChargesTracker(self.dp_config)

        self._fill_records: List[Dict[str, Any]] = []
        self._execution_timestamps: Dict[str, int] = {}

    def set_recorder(self, recorder: ExecutionEventRecorder) -> None:
        """Attaches an ExecutionEventRecorder to coordinate execution timestamps."""
        self.recorder = recorder

    def set_clock(self, clock: Any) -> None:
        """Attaches the NautilusTrader simulation clock."""
        self.clock = clock

    def register_execution_session(
        self,
        client_order_id: Any,
        timestamp_ns: int,
        session_date: Optional[datetime.date] = None,
    ) -> None:
        """
        Explicitly registers the actual execution session timestamp for an order.
        Called when a queued order is released upon opening QuoteTick arrival.
        """
        cid = str(client_order_id)
        self._execution_timestamps[cid] = timestamp_ns
        if self.recorder is not None:
            self.recorder.register_order_release(cid, timestamp_ns)

    def reset(self) -> None:
        """Clears all audit tracking and DP daily frequency history."""
        self._fill_records.clear()
        self._execution_timestamps.clear()
        self.dp_tracker.reset()
        if self.recorder is not None:
            self.recorder.reset()

    def get_execution_timestamp_ns(self, order: Any) -> int:
        """
        Determines the actual execution timestamp in nanoseconds for the order.
        Strictly avoids silently falling back to order.ts_init.
        """
        cid = str(getattr(order, "client_order_id", order))

        # 1. Registered via recorder
        if self.recorder is not None:
            try:
                return self.recorder.get_execution_timestamp_ns(order)
            except RuntimeError:
                pass

        # 2. Directly registered with this fee model
        if cid in self._execution_timestamps:
            return self._execution_timestamps[cid]

        # 3. Nautilus simulation clock
        if self.clock is not None:
            ts_clock = self.clock.timestamp_ns()
            if ts_clock > 0:
                return ts_clock

        # 4. Check order's lifecycle timestamps (accepted, submitted, last)
        ts_init = getattr(order, "ts_init", 0) or 0
        ts_accepted = getattr(order, "ts_accepted", None) or 0
        if ts_accepted > 0 and ts_accepted != ts_init:
            return ts_accepted

        ts_submitted = getattr(order, "ts_submitted", None) or 0
        if ts_submitted > 0 and ts_submitted != ts_init:
            return ts_submitted

        ts_last = getattr(order, "ts_last", 0) or 0
        if ts_last > 0 and ts_last != ts_init:
            return ts_last

        # Refuse to silently fall back to order.ts_init
        raise RuntimeError(
            f"Cannot determine actual execution timestamp for order '{cid}'. "
            f"Execution timestamp was not registered and order timestamp reflects creation time (ts_init={ts_init}). "
            f"FeeModel requires an actual execution timestamp to accurately attribute DP charges."
        )

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
            fill_px: Executed fill price.
            instrument: The Instrument being traded.

        Returns:
            Money: Total fill commission in the instrument's quote currency.
        """
        quantity = fill_quantity.as_double()
        price = fill_px.as_double()

        # Determine side (BUY vs SELL)
        if hasattr(order, "side"):
            is_buy = order.side == OrderSide.BUY
        elif hasattr(order, "is_buy"):
            is_buy = bool(order.is_buy)
        else:
            is_buy = True

        # Extract actual execution timestamp and convert to configured exchange timezone
        ts_ns = self.get_execution_timestamp_ns(order)
        trading_date = (
            pd.to_datetime(ts_ns, unit="ns", utc=True)
            .tz_convert(self.exchange_tz)
            .date()
        )

        # Extract instrument symbol
        if hasattr(instrument.id, "symbol"):
            symbol = str(instrument.id.symbol.value)
        else:
            symbol = str(instrument.id).split(".")[0]

        # Calculate ordinary transaction charges
        ordinary = self.ordinary_calculator.calculate(
            quantity=quantity,
            price=price,
            is_buy=is_buy,
        )

        # Calculate DP charge (only on first delivery sell of ISIN on this actual trading day)
        dp_charge = self.dp_tracker.evaluate_dp_charge(
            isin_symbol=symbol,
            trading_date=trading_date,
            is_sell=not is_buy,
            fill_quantity=quantity,
        )

        total_commission = round(ordinary.total_ordinary + dp_charge, 2)

        # Audit record for itemised reporting
        record = {
            "client_order_id": str(getattr(order, "client_order_id", "")),
            "trade_id": None,  # Populated when OrderFilled event arrives
            "instrument_id": str(instrument.id),
            "symbol": symbol,
            "date": trading_date,
            "ts_event": ts_ns,
            "side": "BUY" if is_buy else "SELL",
            "quantity": quantity,
            "price": price,
            "turnover": ordinary.turnover,
            "brokerage": ordinary.brokerage,
            "stt": ordinary.stt,
            "nse_txn": ordinary.nse_txn,
            "sebi_fee": ordinary.sebi_fee,
            "stamp_duty": ordinary.stamp_duty,
            "taxable_base": ordinary.taxable_base,
            "gst": ordinary.gst,
            "total_ordinary": ordinary.total_ordinary,
            "dp_charge": dp_charge,
            "total_commission": total_commission,
        }
        self._fill_records.append(record)

        logger.debug(
            "COMMISSION CHARGED: %s %s qty=%.2f px=%.2f | Ord=₹%.2f (STT=%.2f, NSE=%.2f, Stamp=%.2f, GST=%.2f) + DP=₹%.2f => Total=₹%.2f (date=%s)",
            "BUY" if is_buy else "SELL",
            symbol,
            quantity,
            price,
            ordinary.total_ordinary,
            ordinary.stt,
            ordinary.nse_txn,
            ordinary.stamp_duty,
            ordinary.gst,
            dp_charge,
            total_commission,
            trading_date,
        )

        currency = getattr(instrument, "quote_currency", None) or Currency.from_str("INR")
        return Money(total_commission, currency)

    def on_order_filled(self, event: OrderFilled) -> None:
        """
        Nautilus lifecycle callback when an OrderFilled event is received.
        Attaches the authoritative trade_id to the matching audit record.
        """
        cid = str(event.client_order_id)
        trade_id = str(event.trade_id)
        fill_qty = float(event.last_qty.as_double())
        fill_px = float(event.last_px.as_double())

        # Link to the most recent unlinked fill record for this client_order_id
        for rec in reversed(self._fill_records):
            if rec["client_order_id"] == cid and rec["trade_id"] is None:
                rec["trade_id"] = trade_id
                rec["ts_event"] = int(event.ts_event)
                break

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
    def fill_records(self) -> List[Dict[str, Any]]:
        """List of all audited fill commission breakdown records."""
        return list(self._fill_records)

    @property
    def fill_count(self) -> int:
        """Total number of executed fills processed."""
        return len(self._fill_records)

    @property
    def total_turnover(self) -> float:
        """Total turnover across all executed fills in INR."""
        return round(sum(r["turnover"] for r in self._fill_records), 2)

    @property
    def buy_turnover(self) -> float:
        """Total buy turnover across all executed buy fills in INR."""
        return round(sum(r["turnover"] for r in self._fill_records if r["side"] == "BUY"), 2)

    @property
    def sell_turnover(self) -> float:
        """Total sell turnover across all executed sell fills in INR."""
        return round(sum(r["turnover"] for r in self._fill_records if r["side"] == "SELL"), 2)

    @property
    def total_brokerage(self) -> float:
        """Total brokerage charges in INR."""
        return round(sum(r["brokerage"] for r in self._fill_records), 2)

    @property
    def total_stt(self) -> float:
        """Total Securities Transaction Tax (STT) in INR."""
        return round(sum(r["stt"] for r in self._fill_records), 2)

    @property
    def total_nse_txn(self) -> float:
        """Total NSE transaction charges in INR."""
        return round(sum(r["nse_txn"] for r in self._fill_records), 2)

    @property
    def total_sebi_fee(self) -> float:
        """Total SEBI turnover charges in INR."""
        return round(sum(r["sebi_fee"] for r in self._fill_records), 2)

    @property
    def total_stamp_duty(self) -> float:
        """Total stamp duty charges in INR."""
        return round(sum(r["stamp_duty"] for r in self._fill_records), 2)

    @property
    def total_gst(self) -> float:
        """Total Goods and Services Tax (GST) in INR."""
        return round(sum(r["gst"] for r in self._fill_records), 2)

    @property
    def total_ordinary_charges(self) -> float:
        """Total ordinary delivery transaction charges (excluding DP charges) in INR."""
        return round(sum(r["total_ordinary"] for r in self._fill_records), 2)

    @property
    def total_dp_charges(self) -> float:
        """Total DP charges incurred on delivery sells in INR."""
        return round(sum(r["dp_charge"] for r in self._fill_records), 2)

    @property
    def total_commission(self) -> float:
        """Total combined commission (ordinary charges + DP charges) in INR."""
        return round(sum(r["total_commission"] for r in self._fill_records), 2)

    def to_dataframe(self) -> pd.DataFrame:
        """Returns audited fill commission records as a pandas DataFrame."""
        return pd.DataFrame(self._fill_records)

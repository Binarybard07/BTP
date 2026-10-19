"""
FIFO buy lot tracking and matching for Indian equity tax accounting.
"""
from dataclasses import dataclass
from typing import Dict, List
import pandas as pd

from nautilus_trader.model import InstrumentId


@dataclass
class BuyLot:
    """Represents an open buy lot for an instrument."""
    instrument_id: InstrumentId
    quantity: float
    price: float
    ts_event: int


@dataclass
class RealisedTrade:
    """Represents a matched trade chunk from a sell order."""
    instrument_id: InstrumentId
    quantity: float
    buy_price: float
    sell_price: float
    buy_ts: int
    sell_ts: int
    is_long_term: bool
    pnl: float


class FIFOMatcher:
    """
    Maintains buy lots per instrument and matches sell orders in FIFO order.
    """

    def __init__(self, exchange_tz: str = "Asia/Kolkata") -> None:
        self.exchange_tz: str = exchange_tz
        self.buy_lots: Dict[InstrumentId, List[BuyLot]] = {}

    def add_buy(
        self,
        instrument_id: InstrumentId,
        quantity: float,
        price: float,
        ts_event: int,
    ) -> None:
        """Records an executed buy fill as a new lot."""
        if quantity <= 0:
            raise ValueError(f"Buy lot quantity must be positive, got {quantity}")
        if price <= 0:
            raise ValueError(f"Buy lot price must be positive, got {price}")

        if instrument_id not in self.buy_lots:
            self.buy_lots[instrument_id] = []
        self.buy_lots[instrument_id].append(
            BuyLot(
                instrument_id=instrument_id,
                quantity=quantity,
                price=price,
                ts_event=ts_event,
            )
        )

    def match_sell(
        self,
        instrument_id: InstrumentId,
        quantity: float,
        price: float,
        ts_event: int,
    ) -> List[RealisedTrade]:
        """
        Matches an executed sell fill against existing buy lots using FIFO.
        Fails fast if sell quantity exceeds available buy lots for the instrument.
        """
        if quantity <= 0:
            raise ValueError(f"Sell quantity must be positive, got {quantity}")
        if price <= 0:
            raise ValueError(f"Sell price must be positive, got {price}")

        lots = self.buy_lots.get(instrument_id, [])
        total_available = sum(lot.quantity for lot in lots)

        if round(quantity - total_available, 8) > 0:
            raise RuntimeError(
                f"Sell quantity {quantity} exceeds available buy lots ({total_available}) "
                f"for instrument {instrument_id}."
            )

        matched_trades: List[RealisedTrade] = []
        remaining_sell = quantity

        while remaining_sell > 1e-8:
            if not lots:
                raise RuntimeError(
                    f"Unexpected exhaustion of buy lots for {instrument_id} "
                    f"with {remaining_sell} remaining to match."
                )

            oldest_lot = lots[0]
            if oldest_lot.quantity <= remaining_sell:
                matched_qty = oldest_lot.quantity
                lots.pop(0)
            else:
                matched_qty = remaining_sell
                oldest_lot.quantity = round(oldest_lot.quantity - remaining_sell, 8)

            remaining_sell = round(remaining_sell - matched_qty, 8)

            buy_date = (
                pd.Timestamp(oldest_lot.ts_event, unit="ns", tz="UTC")
                .tz_convert(self.exchange_tz)
                .date()
            )
            sell_date = (
                pd.Timestamp(ts_event, unit="ns", tz="UTC")
                .tz_convert(self.exchange_tz)
                .date()
            )
            cutoff_date = (pd.Timestamp(buy_date) + pd.DateOffset(months=12)).date()
            is_long_term = sell_date > cutoff_date
            pnl = (price - oldest_lot.price) * matched_qty

            matched_trades.append(
                RealisedTrade(
                    instrument_id=instrument_id,
                    quantity=matched_qty,
                    buy_price=oldest_lot.price,
                    sell_price=price,
                    buy_ts=oldest_lot.ts_event,
                    sell_ts=ts_event,
                    is_long_term=is_long_term,
                    pnl=pnl,
                )
            )

        return matched_trades

    def clear(self) -> None:
        """Clears all tracked buy lots."""
        self.buy_lots.clear()


__all__ = [
    "BuyLot",
    "RealisedTrade",
    "FIFOMatcher",
]

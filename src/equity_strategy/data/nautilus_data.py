import datetime
import pathlib
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd

from nautilus_trader.model import (
    Bar,
    BarType,
    Currency,
    Equity,
    InstrumentId,
    Price,
    Quantity,
    Symbol,
    Venue,
)


class NautilusDataConverter:
    """
    Reusable, strategy-agnostic converter that transforms project data
    into native NautilusTrader 2.0 objects (Equity instruments, BarType, and daily OHLCV Bar objects).
    """

    DEFAULT_VENUE = "NSE"
    DEFAULT_CURRENCY = "INR"
    DEFAULT_PRICE_PRECISION = 2
    DEFAULT_PRICE_INCREMENT = 0.05
    DEFAULT_LOT_SIZE = 1

    @staticmethod
    def timestamp_to_ns(ts_input: Any) -> int:
        """
        Converts a datetime, date, pd.Timestamp, or date string to Unix epoch nanoseconds (UTC).
        If the timestamp is naive and represents a daily date (00:00:00),
        it is localized to Indian equity market close (15:30:00 IST / Asia/Kolkata)
        before conversion to UTC nanoseconds.
        """
        if isinstance(ts_input, int):
            return ts_input

        ts = pd.to_datetime(ts_input)

        if ts.tzinfo is None:
            # If time is unset (e.g. 00:00:00 date), set to EOD close time (15:30 IST)
            if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
                ts = ts.replace(hour=15, minute=30, second=0)
            ts_utc = ts.tz_localize("Asia/Kolkata").tz_convert("UTC")
        else:
            ts_utc = ts.tz_convert("UTC")

        return int(ts_utc.value)

    def create_equity(
        self,
        symbol: str,
        venue: str = DEFAULT_VENUE,
        currency: str = DEFAULT_CURRENCY,
        price_precision: int = DEFAULT_PRICE_PRECISION,
        price_increment: float = DEFAULT_PRICE_INCREMENT,
        lot_size: int = DEFAULT_LOT_SIZE,
        max_quantity: Optional[int] = 1_000_000,
        min_quantity: Optional[int] = 1,
    ) -> Equity:
        """
        Creates a Nautilus Equity instrument object.
        """
        sym = Symbol(symbol.strip().upper())
        ven = Venue(venue.strip().upper())
        inst_id = InstrumentId(sym, ven)

        return Equity(
            instrument_id=inst_id,
            raw_symbol=sym,
            currency=Currency.from_str(currency),
            price_precision=price_precision,
            price_increment=Price.from_str(f"{price_increment:.4f}".rstrip("0").rstrip(".")),
            lot_size=Quantity.from_int(lot_size),
            max_quantity=Quantity.from_int(max_quantity) if max_quantity is not None else None,
            min_quantity=Quantity.from_int(min_quantity) if min_quantity is not None else None,
            ts_event=0,
            ts_init=0,
        )

    def create_bar_type(
        self,
        instrument_id: Union[InstrumentId, str],
        spec: str = "1-DAY-LAST-EXTERNAL",
    ) -> BarType:
        """
        Creates a standard BarType for the given instrument.
        """
        inst_str = str(instrument_id)
        if "-" in spec:
            bar_type_str = f"{inst_str}-{spec}"
        else:
            bar_type_str = f"{inst_str}-{spec}-LAST-EXTERNAL"
        return BarType.from_str(bar_type_str)

    def df_to_bars(
        self,
        df: pd.DataFrame,
        bar_type: BarType,
        price_precision: int = DEFAULT_PRICE_PRECISION,
        size_precision: int = 0,
    ) -> List[Bar]:
        """
        Converts an OHLCV pandas DataFrame into a list of Nautilus Bar objects.
        Requires columns: 'Date', 'Open', 'High', 'Low', 'Close', 'Volume'.
        """
        required_cols = {"Date", "Open", "High", "Low", "Close", "Volume"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Input DataFrame missing required OHLCV columns: {missing}")

        bars: List[Bar] = []
        df_sorted = df.sort_values(by="Date").reset_index(drop=True)

        for row in df_sorted.itertuples(index=False):
            ts_ns = self.timestamp_to_ns(row.Date)
            open_val = getattr(row, "Open")
            high_val = getattr(row, "High")
            low_val = getattr(row, "Low")
            close_val = getattr(row, "Close")
            vol_val = getattr(row, "Volume")

            # Validate numeric values
            if (
                pd.isna(open_val)
                or pd.isna(high_val)
                or pd.isna(low_val)
                or pd.isna(close_val)
                or pd.isna(vol_val)
            ):
                continue

            bar = Bar(
                bar_type=bar_type,
                open=Price(float(open_val), price_precision),
                high=Price(float(high_val), price_precision),
                low=Price(float(low_val), price_precision),
                close=Price(float(close_val), price_precision),
                volume=Quantity(int(vol_val), size_precision),
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
            bars.append(bar)

        return bars

    def convert_dataframe(
        self,
        df: pd.DataFrame,
        venue: str = DEFAULT_VENUE,
        currency: str = DEFAULT_CURRENCY,
        price_precision: int = DEFAULT_PRICE_PRECISION,
    ) -> Dict[str, Tuple[Equity, BarType, List[Bar]]]:
        """
        Converts a multi-symbol or single-symbol DataFrame into a mapping of:
        symbol -> (Equity, BarType, List[Bar])
        """
        if "Symbol" not in df.columns:
            raise ValueError("DataFrame must contain 'Symbol' column for multi-symbol conversion.")

        result: Dict[str, Tuple[Equity, BarType, List[Bar]]] = {}
        for symbol, group in df.groupby("Symbol", group_keys=False):
            sym_str = str(symbol).strip().upper()
            equity = self.create_equity(
                symbol=sym_str,
                venue=venue,
                currency=currency,
                price_precision=price_precision,
            )
            bar_type = self.create_bar_type(equity.id)
            bars = self.df_to_bars(group, bar_type=bar_type, price_precision=price_precision)
            result[sym_str] = (equity, bar_type, bars)

        return result

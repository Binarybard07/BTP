import pathlib
import logging
from typing import List, Dict, Tuple
import pandas as pd

from nautilus_trader.model import (
    Bar, BarType, BarSpecification, Symbol, Venue, InstrumentId, Equity, Price, Quantity, Currency,
    BarAggregation, PriceType
)
from nautilus_trader.persistence import ParquetDataCatalog

logger = logging.getLogger(__name__)

class NautilusDataConverter:
    """
    Converts historical pandas market data into NautilusTrader Equity instruments and Bar objects,
    and writes them to a ParquetDataCatalog.
    """

    def __init__(self, venue_name: str = "NSE", currency_code: str = "INR"):
        self.venue = Venue(venue_name)
        self.currency = Currency.from_str(currency_code)

    def create_equity_instrument(self, symbol_str: str) -> Equity:
        """Creates a Nautilus Equity instrument definition for an Indian stock symbol."""
        symbol = Symbol(symbol_str)
        instrument_id = InstrumentId(symbol, self.venue)
        return Equity(
            instrument_id=instrument_id,
            raw_symbol=symbol,
            currency=self.currency,
            price_precision=2,
            price_increment=Price.from_str("0.05"),
            lot_size=Quantity.from_int(1),
            ts_event=0,
            ts_init=0,
        )

    def dataframe_to_bars_and_instruments(
        self,
        df: pd.DataFrame
    ) -> Tuple[List[Equity], List[Bar], Dict[str, BarType]]:
        """
        Converts OHLCV DataFrame to lists of Nautilus Equity instruments and Bar objects.
        """
        instruments = []
        bars = []
        bar_types = {}

        unique_symbols = df["Symbol"].unique()

        for symbol_str in unique_symbols:
            equity = self.create_equity_instrument(symbol_str)
            instruments.append(equity)

            bar_spec = BarSpecification(1, BarAggregation.DAY, PriceType.LAST)
            bar_type = BarType(equity.id, bar_spec)
            bar_types[symbol_str] = bar_type

            df_symbol = df[df["Symbol"] == symbol_str].sort_values(by="Date")

            for _, row in df_symbol.iterrows():
                dt = pd.Timestamp(row["Date"])

                # Handle timezone offset cleanly
                if dt.tzinfo is None:
                    dt = dt.tz_localize("UTC")
                else:
                    dt = dt.tz_convert("UTC")

                ts_ns = int(dt.timestamp() * 1e9)

                open_p = Price.from_str(f"{float(row['Open']):.2f}")
                high_p = Price.from_str(f"{float(row['High']):.2f}")
                low_p = Price.from_str(f"{float(row['Low']):.2f}")
                close_p = Price.from_str(f"{float(row['Close']):.2f}")
                volume_q = Quantity.from_int(int(row["Volume"]))

                bar = Bar(
                    bar_type=bar_type,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    volume=volume_q,
                    ts_event=ts_ns,
                    ts_init=ts_ns,
                )
                bars.append(bar)

        return instruments, bars, bar_types

    def save_to_catalog(
        self,
        df: pd.DataFrame,
        catalog_path: str = "data/processed/catalog"
    ) -> Tuple[ParquetDataCatalog, List[Equity], Dict[str, BarType]]:
        """
        Writes Equity instruments and daily Bar objects to Nautilus ParquetDataCatalog.
        """
        cat_dir = pathlib.Path(catalog_path)
        cat_dir.mkdir(parents=True, exist_ok=True)

        catalog = ParquetDataCatalog(str(cat_dir))

        instruments, bars, bar_types = self.dataframe_to_bars_and_instruments(df)

        catalog.write_instruments(instruments)
        catalog.write_bars(bars)

        logger.info(f"Successfully wrote {len(instruments)} instruments and {len(bars)} bars to catalog at '{catalog_path}'.")
        return catalog, instruments, bar_types

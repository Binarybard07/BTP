import pathlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union
import pandas as pd

from nautilus_trader.model import Bar, BarType, Equity, QuoteTick

from equity_strategy.data.loader import CSVDataLoader
from equity_strategy.data.nautilus_data import NautilusDataConverter
from equity_strategy.features.engine import FeatureEngine


@dataclass
class PreparedStrategyData:
    """
    Container for prepared strategy data produced by StrategyDataPipeline.
    Supports attribute access as well as tuple unpacking.
    """

    featured_df: pd.DataFrame
    equity: Equity
    bar_type: BarType
    bars: List[Bar]
    opening_quotes: Optional[List[QuoteTick]] = None

    def __iter__(self):
        return iter((self.featured_df, self.equity, self.bar_type, self.bars))


class StrategyDataPipeline:
    """
    Reusable, strategy-agnostic data orchestration pipeline.

    Coordinates existing project components:
        Raw Data (directory or CSV file)
            ↓
        CSVDataLoader (handles CSV discovery, column standardization, sorting, and DataValidator)
            ↓
        FeatureEngine (computes modular features per symbol without data leakage)
            ↓
        NautilusDataConverter (converts data into native Equity, BarType, and daily OHLCV Bar objects)
            ↓
        PreparedStrategyData (packaged for strategy consumption)
    """

    def __init__(
        self,
        feature_config: Optional[Dict[str, Any]] = None,
        converter: Optional[NautilusDataConverter] = None,
    ):
        self.feature_config = feature_config
        self.feature_engine = FeatureEngine(self.feature_config)
        self.converter = converter or NautilusDataConverter()

    def prepare(
        self,
        data_source: Union[str, pathlib.Path, pd.DataFrame],
        symbol: Optional[str] = None,
        venue: str = "NSE",
        currency: str = "INR",
        price_precision: int = 2,
        validate: bool = True,
    ) -> PreparedStrategyData:
        """
        Orchestrates data preparation from raw data source for a single symbol.

        Parameters:
            data_source: Path to CSV file, directory of CSVs, or existing DataFrame.
            symbol: Target symbol (optional if data_source is a single-symbol file or DataFrame).
            venue: Exchange venue code (default 'NSE').
            currency: Quote currency (default 'INR').
            price_precision: Price precision decimal places (default 2).
            validate: Whether to run DataValidator checks during loading.

        Returns:
            PreparedStrategyData containing (featured_df, equity, bar_type, bars).
        """
        # Step 1: Ingest raw data via CSVDataLoader (or copy if DataFrame)
        if isinstance(data_source, pd.DataFrame):
            df_raw = data_source.copy()
        else:
            p = pathlib.Path(data_source)
            if symbol and p.is_dir() and (p / f"{symbol.strip().upper()}.csv").exists():
                loader = CSVDataLoader(p / f"{symbol.strip().upper()}.csv")
            else:
                loader = CSVDataLoader(p)
            df_raw = loader.load_data(validate=validate)

        # Filter by symbol if specified
        if symbol:
            sym_clean = symbol.strip().upper()
            df_sym = df_raw[df_raw["Symbol"] == sym_clean].reset_index(drop=True)
            if df_sym.empty:
                raise ValueError(f"Symbol '{symbol}' not found in data source '{data_source}'.")
        else:
            sym_clean = str(df_raw["Symbol"].iloc[0]).strip().upper()
            df_sym = df_raw[df_raw["Symbol"] == sym_clean].reset_index(drop=True)

        # Step 2: Extract features via FeatureEngine
        featured_df = self.feature_engine.transform(df_sym)

        # Step 3: Convert to Nautilus-native objects via NautilusDataConverter
        equity = self.converter.create_equity(
            symbol=sym_clean,
            venue=venue,
            currency=currency,
            price_precision=price_precision,
        )
        bar_type = self.converter.create_bar_type(equity.id)
        bars = self.converter.df_to_bars(
            featured_df,
            bar_type=bar_type,
            price_precision=price_precision,
        )
        opening_quotes = self.converter.df_to_opening_quote_ticks(
            featured_df,
            instrument_id=equity.id,
            price_precision=price_precision,
        )

        return PreparedStrategyData(
            featured_df=featured_df,
            equity=equity,
            bar_type=bar_type,
            bars=bars,
            opening_quotes=opening_quotes,
        )

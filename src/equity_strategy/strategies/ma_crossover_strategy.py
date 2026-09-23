import pathlib
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd

from nautilus_trader.model import (
    Bar,
    BarType,
    Equity,
    InstrumentId,
    OrderSide,
    Quantity,
)
from nautilus_trader.trading import Strategy, StrategyConfig

from equity_strategy.data.loader import CSVDataLoader
from equity_strategy.data.nautilus_data import NautilusDataConverter
from equity_strategy.features.engine import FeatureEngine


class MACrossoverConfig(StrategyConfig):
    """
    Configuration for the 50/200 Moving Average Crossover Strategy.
    """

    def __init__(
        self,
        instrument_id: InstrumentId,
        bar_type: BarType,
        trade_size: Quantity = Quantity.from_int(1),
        featured_df: Optional[pd.DataFrame] = None,
        data_dir: Optional[Union[str, pathlib.Path]] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.trade_size = trade_size
        self.featured_df = featured_df
        self.data_dir = data_dir


class MACrossoverStrategy(Strategy):
    """
    Concrete 50/200 Moving Average Crossover Strategy built as a Nautilus Strategy.

    Orchestrates:
    - Loading market data via CSVDataLoader
    - Computing trend features via FeatureEngine (using close_to_ma50 and close_to_ma200)
    - Converting data into Nautilus objects via NautilusDataConverter
    - Executing on_start() / on_bar() lifecycle for 50/200 long-only crossover logic:
        * Entry (Bullish Crossover):
          previous 50 relationship <= 200 relationship AND current 50 relationship > 200 relationship
        * Exit (Bearish Crossover):
          previous 50 relationship >= 200 relationship AND current 50 relationship < 200 relationship
    """

    def __init__(self, config: MACrossoverConfig):
        super().__init__(config)
        self.instrument_id: InstrumentId = config.instrument_id
        self.bar_type: BarType = config.bar_type
        self.trade_size: Quantity = config.trade_size
        self.data_dir = config.data_dir
        self.featured_df = config.featured_df

        self._converter = NautilusDataConverter()
        self._prev_ma50: Optional[float] = None
        self._prev_ma200: Optional[float] = None
        self._local_position_state: str = "FLAT"
        self._decisions_log: List[Dict[str, Any]] = []
        self._features_by_ts: Dict[int, Tuple[float, float]] = {}

        if self.featured_df is not None:
            self._index_features(self.featured_df)
        elif self.data_dir is not None:
            df_featured, _, _, _ = self.prepare_data(
                self.data_dir,
                symbol=str(self.instrument_id.symbol),
                venue=str(self.instrument_id.venue),
            )
            self.featured_df = df_featured
            self._index_features(df_featured)

    @classmethod
    def prepare_data(
        cls,
        data_source: Union[str, pathlib.Path, pd.DataFrame],
        symbol: Optional[str] = None,
        venue: str = "NSE",
        config: Optional[Dict[str, Any]] = None,
    ) -> Tuple[pd.DataFrame, Equity, BarType, List[Bar]]:
        """
        Orchestration helper connecting:
        CSVDataLoader -> DataValidator -> FeatureEngine -> NautilusDataConverter.

        Can accept:
        - A directory path containing CSVs
        - A specific symbol's CSV file path
        - An already loaded/validated DataFrame

        Returns:
            (featured_df, equity, bar_type, bars)
        """
        if isinstance(data_source, pd.DataFrame):
            df_raw = data_source.copy()
            sym_clean = symbol.strip().upper() if symbol else str(df_raw["Symbol"].iloc[0]).strip().upper()
            df_sym = df_raw[df_raw["Symbol"] == sym_clean].reset_index(drop=True)
        else:
            p = pathlib.Path(data_source)
            if symbol and p.is_dir() and (p / f"{symbol.strip().upper()}.csv").exists():
                return cls.prepare_data(p / f"{symbol.strip().upper()}.csv", symbol=symbol, venue=venue, config=config)
            elif p.is_file():
                df = pd.read_csv(p)
                col_map = {col: str(col).strip() for col in df.columns}
                df = df.rename(columns=col_map)
                lower_map = {c.lower(): c for c in df.columns}
                rename_dict = {}
                for std_col in ["Date", "Open", "High", "Low", "Close", "Volume", "Symbol"]:
                    if std_col.lower() in lower_map:
                        rename_dict[lower_map[std_col.lower()]] = std_col
                df = df.rename(columns=rename_dict)
                if "Symbol" not in df.columns:
                    df["Symbol"] = symbol.upper() if symbol else p.stem.upper()
                df["Date"] = pd.to_datetime(df["Date"], format="mixed", dayfirst=True)
                df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
                for c in ["Open", "High", "Low", "Close", "Volume"]:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                df_sym = df.sort_values(by=["Symbol", "Date"]).reset_index(drop=True)
                sym_clean = str(df_sym["Symbol"].iloc[0]).strip().upper()
            else:
                loader = CSVDataLoader(p)
                df_raw = loader.load_data(validate=True)
                if symbol:
                    sym_clean = symbol.strip().upper()
                    df_sym = df_raw[df_raw["Symbol"] == sym_clean].reset_index(drop=True)
                    if df_sym.empty:
                        raise ValueError(f"Symbol '{symbol}' not found in data directory '{data_source}'.")
                else:
                    sym_clean = str(df_raw["Symbol"].iloc[0]).strip().upper()
                    df_sym = df_raw[df_raw["Symbol"] == sym_clean].reset_index(drop=True)

        # Feature extraction
        engine = FeatureEngine(config)
        df_featured = engine.transform(df_sym)

        # Nautilus data conversion
        converter = NautilusDataConverter()
        equity = converter.create_equity(symbol=sym_clean, venue=venue)
        bar_type = converter.create_bar_type(equity.id)
        bars = converter.df_to_bars(df_featured, bar_type=bar_type)

        return df_featured, equity, bar_type, bars

    def set_features(self, df: pd.DataFrame):
        """Sets and indexes featured DataFrame for bar lookups."""
        self.featured_df = df
        self._index_features(df)

    def _index_features(self, df: pd.DataFrame):
        """Indexes close_to_ma50 and close_to_ma200 by bar event timestamp (Unix nanoseconds)."""
        self._features_by_ts.clear()
        if "close_to_ma50" not in df.columns or "close_to_ma200" not in df.columns:
            return

        for row in df.itertuples(index=False):
            ts_ns = self._converter.timestamp_to_ns(row.Date)
            ma50_ratio = getattr(row, "close_to_ma50", None)
            ma200_ratio = getattr(row, "close_to_ma200", None)
            self._features_by_ts[ts_ns] = (ma50_ratio, ma200_ratio)

    @property
    def decisions_log(self) -> List[Dict[str, Any]]:
        return self._decisions_log

    @property
    def position_state(self) -> str:
        return self._local_position_state

    def _is_flat(self) -> bool:
        try:
            return self.portfolio.is_net_flat(self.instrument_id)
        except RuntimeError:
            return self._local_position_state == "FLAT"

    def _is_long(self) -> bool:
        try:
            return self.portfolio.is_net_long(self.instrument_id)
        except RuntimeError:
            return self._local_position_state == "LONG"

    def on_start(self):
        """
        Nautilus lifecycle on_start: subscribes to instrument bars.
        """
        try:
            self.subscribe_bars(self.bar_type)
        except RuntimeError:
            # When testing strategy standalone without registered trader engine
            pass

    def on_bar(self, bar: Bar):
        """
        Nautilus lifecycle on_bar: receives market bars and evaluates 50/200 crossover rules.
        """
        if bar.bar_type != self.bar_type:
            return

        close_price = float(bar.close)
        ts_ns = bar.ts_event

        features = self._features_by_ts.get(ts_ns)
        if features is None:
            return

        close_to_ma50, close_to_ma200 = features

        # Warmup guard: wait until both ratios are valid non-zero numbers
        if (
            close_to_ma50 is None
            or close_to_ma200 is None
            or pd.isna(close_to_ma50)
            or pd.isna(close_to_ma200)
            or close_to_ma50 == 0
            or close_to_ma200 == 0
        ):
            return

        # Derive 50 and 200 moving averages: MA = Close / (Close / MA)
        curr_ma50 = close_price / float(close_to_ma50)
        curr_ma200 = close_price / float(close_to_ma200)

        # Crossover evaluation
        if self._prev_ma50 is not None and self._prev_ma200 is not None:
            # Bullish Crossover (Golden Cross): prev 50 <= prev 200 AND curr 50 > curr 200
            is_bullish = (self._prev_ma50 <= self._prev_ma200) and (curr_ma50 > curr_ma200)

            # Bearish Crossover (Death Cross): prev 50 >= prev 200 AND curr 50 < curr 200
            is_bearish = (self._prev_ma50 >= self._prev_ma200) and (curr_ma50 < curr_ma200)

            if is_bullish and self._is_flat():
                self._enter_long(bar, curr_ma50, curr_ma200)
            elif is_bearish and self._is_long():
                self._exit_long(bar, curr_ma50, curr_ma200)

        # Update previous bar values
        self._prev_ma50 = curr_ma50
        self._prev_ma200 = curr_ma200

    def _enter_long(self, bar: Bar, ma50: float, ma200: float):
        """Executes long entry on bullish crossover."""
        self._local_position_state = "LONG"
        self._decisions_log.append({
            "action": "BUY",
            "ts_event": bar.ts_event,
            "close": float(bar.close),
            "ma50": ma50,
            "ma200": ma200,
        })
        try:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self.trade_size,
            )
            self.submit_order(order)
        except RuntimeError:
            pass

    def _exit_long(self, bar: Bar, ma50: float, ma200: float):
        """Executes exit on bearish crossover."""
        self._local_position_state = "FLAT"
        self._decisions_log.append({
            "action": "SELL",
            "ts_event": bar.ts_event,
            "close": float(bar.close),
            "ma50": ma50,
            "ma200": ma200,
        })
        try:
            self.close_all_positions(self.instrument_id)
        except RuntimeError:
            pass

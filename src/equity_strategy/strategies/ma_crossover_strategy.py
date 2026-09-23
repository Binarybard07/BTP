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

from equity_strategy.data.nautilus_data import NautilusDataConverter
from equity_strategy.data.strategy_data import PreparedStrategyData, StrategyDataPipeline


class MACrossoverConfig(StrategyConfig):
    """
    Simplified configuration for the 50/200 Moving Average Crossover Strategy.

    Intended user flow:
        raw data source (CSV file, directory, or DataFrame)
            -> MACrossoverStrategy
            -> StrategyDataPipeline
            -> Nautilus
            -> Strategy execution

    The user does not need to manually construct featured_df, Equity, BarType, or Bars.
    Direct passing of instrument_id / bar_type / featured_df remains supported for mock testing.
    """

    def __init__(
        self,
        data_source: Optional[Union[str, pathlib.Path, pd.DataFrame]] = None,
        symbol: Optional[str] = None,
        venue: str = "NSE",
        trade_size: Quantity = Quantity.from_int(1),
        instrument_id: Optional[InstrumentId] = None,
        bar_type: Optional[BarType] = None,
        featured_df: Optional[pd.DataFrame] = None,
        feature_config: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.data_source = data_source
        self.symbol = symbol
        self.venue = venue
        self.trade_size = trade_size
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.featured_df = featured_df
        self.feature_config = feature_config


class MACrossoverStrategy(Strategy):
    """
    Concrete 50/200 Moving Average Crossover Strategy built as a Nautilus Strategy.

    Separation of Concerns:
    - StrategyDataPipeline: handles common data plumbing (loader -> feature engine -> converter).
    - MACrossoverStrategy: handles MA crossover strategy rules and orchestrates data preparation.
    - NautilusDataConverter: handles conversion into Nautilus-native objects.

    Strategy Rules (Long-Only):
    - Entry (Golden Cross):
      previous 50 relationship <= 200 relationship AND current 50 relationship > 200 relationship
    - Exit (Death Cross):
      previous 50 relationship >= 200 relationship AND current 50 relationship < 200 relationship
    """

    def __init__(self, config: MACrossoverConfig):
        super().__init__(config)
        self.trade_size: Quantity = config.trade_size
        self.pipeline = StrategyDataPipeline(feature_config=config.feature_config)
        self._converter = self.pipeline.converter

        self.featured_df: Optional[pd.DataFrame] = config.featured_df
        self.bars: List[Bar] = []
        self._prev_ma50: Optional[float] = None
        self._prev_ma200: Optional[float] = None
        self._local_position_state: str = "FLAT"
        self._decisions_log: List[Dict[str, Any]] = []
        self._features_by_ts: Dict[int, Tuple[float, float]] = {}

        if config.data_source is not None:
            prepared = self.pipeline.prepare(
                data_source=config.data_source,
                symbol=config.symbol,
                venue=config.venue,
            )
            self.instrument_id: InstrumentId = prepared.equity.id
            self.bar_type: BarType = prepared.bar_type
            self.bars = prepared.bars
            self.featured_df = prepared.featured_df
            self._index_features(self.featured_df)
        elif config.instrument_id is not None and config.bar_type is not None:
            self.instrument_id = config.instrument_id
            self.bar_type = config.bar_type
            if config.featured_df is not None:
                self._index_features(config.featured_df)
        else:
            raise ValueError(
                "MACrossoverConfig requires either 'data_source' or ('instrument_id' and 'bar_type')."
            )

    @classmethod
    def prepare_data(
        cls,
        data_source: Union[str, pathlib.Path, pd.DataFrame],
        symbol: Optional[str] = None,
        venue: str = "NSE",
        config: Optional[Dict[str, Any]] = None,
    ) -> PreparedStrategyData:
        """
        Convenience orchestration helper delegating to StrategyDataPipeline.
        Returns PreparedStrategyData containing (featured_df, equity, bar_type, bars).
        """
        pipeline = StrategyDataPipeline(feature_config=config)
        return pipeline.prepare(data_source, symbol=symbol, venue=venue)

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
            # Standalone mode / unregistered trader
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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run MA Crossover Strategy on Indian equity data.")
    parser.add_argument(
        "--data-source",
        type=str,
        default="data/raw/Final_NIFTY100_EoD_Data",
        help="Path to CSV file or directory containing market data.",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="ABB",
        help="Stock symbol to run (e.g. ABB).",
    )
    args = parser.parse_args()

    print(f"=== Initializing MA Crossover Strategy for {args.symbol} ===")
    config = MACrossoverConfig(
        data_source=args.data_source,
        symbol=args.symbol,
    )
    strategy = MACrossoverStrategy(config)
    strategy.on_start()

    print(f"Instrument: {strategy.instrument_id}, BarType: {strategy.bar_type}")
    print(f"Processing {len(strategy.bars)} bars...")

    for bar in strategy.bars:
        strategy.on_bar(bar)

    print(f"Completed! Total strategy decisions: {len(strategy.decisions_log)}")
    print(f"Final position state: {strategy.position_state}")
    if strategy.decisions_log:
        print("Sample decisions:")
        for dec in strategy.decisions_log[:5]:
            print(f"  {dec['action']} @ Close {dec['close']:.2f} (MA50={dec['ma50']:.2f}, MA200={dec['ma200']:.2f})")

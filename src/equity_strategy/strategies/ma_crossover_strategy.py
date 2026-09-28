import logging
import pathlib
from typing import Dict, List, Optional, Tuple, Union
import pandas as pd

from nautilus_trader.analysis import create_tearsheet
from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model import (
    AccountType,
    Bar,
    BarType,
    Currency,
    InstrumentId,
    Money,
    OmsType,
    OrderSide,
    Quantity,
    Venue,
)
from nautilus_trader.trading import Strategy, StrategyConfig
from equity_strategy.data.nautilus_data import NautilusDataConverter
from equity_strategy.data.strategy_data import StrategyDataPipeline

logger = logging.getLogger(__name__)



class MACrossoverConfig(StrategyConfig):
    """
    Configuration for the multi-instrument MACrossoverStrategy.
    """

    def __init__(
        self,
        bar_types: List[BarType],
        trade_size: Union[int, Quantity] = 1,
        features: Optional[
            Dict[
                InstrumentId,
                Dict[int, Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]],
            ]
        ] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.bar_types = bar_types
        self.trade_size = (
            trade_size
            if isinstance(trade_size, Quantity)
            else Quantity.from_int(trade_size)
        )
        self.features: Dict[
            InstrumentId,
            Dict[int, Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]],
        ] = features or {}

    @staticmethod
    def _index_dataframe(
        df: pd.DataFrame,
    ) -> Dict[int, Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]]:
        """
        Indexes precomputed feature ratios for current and immediately preceding chronological bars
        by timestamp nanoseconds.

        For each bar i in chronological order of this instrument:
        - current_c50: close_to_ma50 at bar i
        - current_c200: close_to_ma200 at bar i
        - previous_c50: close_to_ma50 at bar i-1 (None for the first observation)
        - previous_c200: close_to_ma200 at bar i-1 (None for the first observation)

        Operates strictly on the instrument's own chronological order.
        """
        lookup: Dict[
            int, Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]
        ] = {}
        if df.empty:
            return lookup

        df_sorted = df.sort_values(by="Date").reset_index(drop=True)
        n = len(df_sorted)

        dates = df_sorted["Date"].values
        c50_series = df_sorted["close_to_ma50"].values if "close_to_ma50" in df_sorted.columns else [None] * n
        c200_series = df_sorted["close_to_ma200"].values if "close_to_ma200" in df_sorted.columns else [None] * n

        prev_c50: Optional[float] = None
        prev_c200: Optional[float] = None

        for i in range(n):
            ts_ns = NautilusDataConverter.timestamp_to_ns(dates[i])
            c50 = c50_series[i]
            c200 = c200_series[i]

            curr_c50: Optional[float] = (
                float(c50) if c50 is not None and not pd.isna(c50) else None
            )
            curr_c200: Optional[float] = (
                float(c200) if c200 is not None and not pd.isna(c200) else None
            )

            lookup[ts_ns] = (curr_c50, curr_c200, prev_c50, prev_c200)

            prev_c50 = curr_c50
            prev_c200 = curr_c200

        return lookup


class MACrossoverStrategy(Strategy):
    """
    Concrete Multi-Instrument 50/200 Moving Average Crossover Strategy running on NautilusTrader.

    Strategy Rules (Long-Only per Instrument):
    - Moving average ratios (close_to_ma50, close_to_ma200) are precomputed by FeatureEngine
      where close_to_ma = Close / MA.
    - Mathematical Equivalence (for positive equity prices):
        MA50 > MA200  <=>  close_to_ma50 < close_to_ma200
        MA50 < MA200  <=>  close_to_ma50 > close_to_ma200
        MA50 <= MA200 <=>  close_to_ma50 >= close_to_ma200
        MA50 >= MA200 <=>  close_to_ma50 <= close_to_ma200
    - Entry (Golden Cross):
        previous_c50 >= previous_c200 AND current_c50 < current_c200
        Condition: portfolio is net flat for this instrument -> submit Market Buy order.
    - Exit (Death Cross):
        previous_c50 <= previous_c200 AND current_c50 > current_c200
        Condition: portfolio is net long for this instrument -> close all positions for this instrument.
    - If any ratio is unavailable, NaN, or non-positive -> wait (no action).
    - If there is no crossover -> no action.
    """

    def __init__(self, config: MACrossoverConfig):
        super().__init__(config=config)

        self.bar_types: List[BarType] = config.bar_types
        self.trade_size: Quantity = config.trade_size
        self._features_by_instrument: Dict[
            InstrumentId,
            Dict[int, Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]],
        ] = config.features

    def on_start(self) -> None:
        """Nautilus lifecycle callback: subscribes to all instrument bar streams."""
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def on_bar(self, bar: Bar) -> None:
        """
        Nautilus lifecycle callback: evaluates ratio-based 50/200 MA crossover
        per instrument on each bar and submits real Nautilus orders / close position requests.
        """
        inst_id = bar.bar_type.instrument_id
        inst_features = self._features_by_instrument.get(inst_id)
        if not inst_features:
            return

        feat = inst_features.get(bar.ts_init)
        if feat is None:
            return

        curr_c50, curr_c200, prev_c50, prev_c200 = feat
        if (
            curr_c50 is None
            or curr_c200 is None
            or prev_c50 is None
            or prev_c200 is None
            or pd.isna(curr_c50)
            or pd.isna(curr_c200)
            or pd.isna(prev_c50)
            or pd.isna(prev_c200)
            or curr_c50 <= 0
            or curr_c200 <= 0
            or prev_c50 <= 0
            or prev_c200 <= 0
        ):
            return

        # Golden Cross: MA50 crosses above MA200 (close_to_ma50 crosses below close_to_ma200)
        if prev_c50 >= prev_c200 and curr_c50 < curr_c200:
            if self.portfolio.is_net_flat(inst_id):
                order = self.order_factory.market(
                    instrument_id=inst_id,
                    order_side=OrderSide.BUY,
                    quantity=self.trade_size,
                )
                self.submit_order(order)

        # Death Cross: MA50 crosses below MA200 (close_to_ma50 crosses above close_to_ma200)
        elif prev_c50 <= prev_c200 and curr_c50 > curr_c200:
            if self.portfolio.is_net_long(inst_id):
                self.close_all_positions(inst_id)

    @classmethod
    def run(
        cls,
        data_source: Union[str, pathlib.Path, pd.DataFrame] = "data/raw/Final_NIFTY50_EoD_Data",
        symbol: Optional[str] = None,
        venue: str = "NSE",
        currency: str = "INR",
        trade_size: Union[int, Quantity] = 1,
        starting_balance: float = 10_000_000.0,
        pipeline: Optional[StrategyDataPipeline] = None,
        engine_config: Optional[BacktestEngineConfig] = None,
    ) -> BacktestEngine:
        """
        Executes a real multi-instrument NautilusTrader backtest for MACrossoverStrategy.

        Flow:
            Directory of CSVs (or single CSV / DataFrame)
              -> For each source: StrategyDataPipeline.prepare() with strict validation
              -> Register each valid Equity with BacktestEngine
              -> Add all Bar streams to BacktestEngine
              -> Sort all bars chronologically
              -> Create ONE MACrossoverStrategy for all instruments
              -> Run ONE shared cash account / portfolio
              -> Return completed BacktestEngine
        """
        items_to_process: List[Tuple[Union[str, pathlib.Path, pd.DataFrame], Optional[str]]] = []

        if isinstance(data_source, pd.DataFrame):
            items_to_process.append((data_source, symbol))
        else:
            p = pathlib.Path(data_source)
            if p.is_file():
                sym = symbol or p.stem.strip().upper()
                items_to_process.append((p, sym))
            elif p.is_dir():
                if symbol:
                    target_csv = p / f"{symbol.strip().upper()}.csv"
                    if target_csv.exists():
                        items_to_process.append((target_csv, symbol.strip().upper()))
                    else:
                        raise FileNotFoundError(f"Symbol file '{target_csv}' not found in directory.")
                else:
                    csv_files = sorted(p.glob("*.csv"))
                    if not csv_files:
                        raise FileNotFoundError(f"No CSV files found in directory: {p}")
                    for f in csv_files:
                        items_to_process.append((f, f.stem.strip().upper()))
            else:
                raise FileNotFoundError(f"Data source not found: {data_source}")

        pipe = pipeline or StrategyDataPipeline()

        cfg = engine_config or BacktestEngineConfig()
        engine = BacktestEngine(config=cfg)

        engine.add_venue(
            venue=Venue(venue),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(starting_balance, Currency.from_str(currency))],
        )

        bar_types: List[BarType] = []
        features: Dict[
            InstrumentId,
            Dict[int, Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]],
        ] = {}
        skipped_files: List[Tuple[str, Optional[str], str]] = []

        for src, sym in items_to_process:
            filename = (
                pathlib.Path(src).name
                if not isinstance(src, pd.DataFrame)
                else "<DataFrame>"
            )
            try:
                prepared = pipe.prepare(
                    data_source=src,
                    symbol=sym,
                    venue=venue,
                    currency=currency,
                    validate=True,
                )
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "[DATA VALIDATION SKIP] File: '%s', Symbol: '%s' -> Error: %s",
                    filename,
                    sym,
                    err_msg,
                )
                skipped_files.append((filename, sym, err_msg))
                continue

            engine.add_instrument(prepared.equity)
            engine.add_data(prepared.bars)
            bar_types.append(prepared.bar_type)
            features[prepared.equity.id] = MACrossoverConfig._index_dataframe(prepared.featured_df)

        if not bar_types:
            raise RuntimeError(
                f"No valid instruments were loaded for backtest. All {len(items_to_process)} sources failed validation."
            )

        if skipped_files:
            logger.info(
                "Data validation summary: %d loaded, %d skipped.",
                len(bar_types),
                len(skipped_files),
            )
            for fname, s, err in skipped_files:
                logger.warning("  - File: '%s', Symbol: '%s' -> %s", fname, s, err)

        engine.sort_data()

        config = MACrossoverConfig(
            bar_types=bar_types,
            trade_size=trade_size,
            features=features,
        )
        strategy = cls(config=config)
        engine.add_strategy(strategy)

        engine.run()
        return engine


if __name__ == "__main__":
    engine = MACrossoverStrategy.run(
        data_source="data/raw/Final_NIFTY100_EoD_Data",
        trade_size=1,
        starting_balance=1000000.00,
    )
    try:
        create_tearsheet(
            engine=engine,
            output_path="backtest_results.html",
            title="MACrossover Strategy Backtest",
        )
    except Exception as e:
        logger.error("Could not generate tearsheet: %s", e)

    result = engine.get_result()
    positions_report = engine.generate_positions_report()
    logger.info("=== Multi-Instrument Backtest Completed ===")
    logger.info("Total orders: %s", result.total_orders)
    logger.info("Total positions: %s", result.total_positions)
    logger.info("Positions report:\n%s", positions_report)
    logger.info("PnL: %s", result.stats_pnls)


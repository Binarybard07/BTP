import pathlib
from typing import Dict, Optional, Tuple, Union
import pandas as pd

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
from nautilus_trader.analysis import create_tearsheet

from equity_strategy.data.nautilus_data import NautilusDataConverter
from equity_strategy.data.strategy_data import PreparedStrategyData, StrategyDataPipeline


class MACrossoverConfig(StrategyConfig):
    def __init__(
        self,
        bar_type: BarType,
        featured_df: Optional[pd.DataFrame] = None,
        trade_size: Union[int, Quantity] = 1,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.bar_type = bar_type
        self.featured_df = featured_df
        self.trade_size = (
            trade_size
            if isinstance(trade_size, Quantity)
            else Quantity.from_int(trade_size)
        )

class MACrossoverStrategy(Strategy):
    """
    Concrete 50/200 Moving Average Crossover Strategy running on NautilusTrader.

    Strategy Rules (Long-Only):
    - Moving average ratios (close_to_ma50, close_to_ma200) are precomputed by FeatureEngine
      and indexed by bar timestamp (nanoseconds).
    - Moving averages are derived dynamically without lookahead:
        MA50 = Close / close_to_ma50
        MA200 = Close / close_to_ma200
    - Entry (Golden Cross):
        prev_ma50 <= prev_ma200 AND curr_ma50 > curr_ma200
        Condition: portfolio is net flat for instrument -> submit Market Buy order.
    - Exit (Death Cross):
        prev_ma50 >= prev_ma200 AND curr_ma50 < curr_ma200
        Condition: portfolio is net long for instrument -> close all positions.
    - If either MA is unavailable or NaN, wait (no action).
    - If there is no crossover, no action.
    """

    def __init__(self, config: MACrossoverConfig):
        super().__init__(config=config)

        self.bar_type = config.bar_type
        self.trade_size = config.trade_size
        self.instrument_id = self.bar_type.instrument_id

        self._prev_ma50 = None
        self._prev_ma200 = None
        self._features_by_ts = {}

        if config.featured_df is not None and not config.featured_df.empty:
            self._index_features(config.featured_df)

    def _index_features(self, df: pd.DataFrame) -> None:
        """Indexes precomputed feature ratios and close prices by timestamp nanoseconds."""
        for _, row in df.iterrows():
            ts_ns = NautilusDataConverter.timestamp_to_ns(row["Date"])
            c50 = row.get("close_to_ma50")
            c200 = row.get("close_to_ma200")
            close_px = row.get("Close")
            self._features_by_ts[ts_ns] = (c50, c200, close_px)

    def on_start(self) -> None:
        """Nautilus lifecycle callback: subscribes to market bars."""
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        """
        Nautilus lifecycle callback: evaluates 50/200 MA crossover on each bar
        and submits real Nautilus orders / close position requests.
        """
        if bar.bar_type != self.bar_type:
            return

        feat = self._features_by_ts.get(bar.ts_init)
        if feat is None:
            return

        c50, c200, close_val = feat
        if pd.isna(c50) or pd.isna(c200) or c50 == 0 or c200 == 0:
            return

        close = float(close_val if close_val is not None else bar.close.as_double())
        curr_ma50 = close / float(c50)
        curr_ma200 = close / float(c200)

        if self._prev_ma50 is not None and self._prev_ma200 is not None:
            # Golden Cross: 50 MA crosses above 200 MA
            if self._prev_ma50 <= self._prev_ma200 and curr_ma50 > curr_ma200:
                if self.portfolio.is_net_flat(self.instrument_id):
                    order = self.order_factory.market(
                        instrument_id=self.instrument_id,
                        order_side=OrderSide.BUY,
                        quantity=self.trade_size,
                    )
                    self.submit_order(order)

            # Death Cross: 50 MA crosses below 200 MA
            elif self._prev_ma50 >= self._prev_ma200 and curr_ma50 < curr_ma200:
                if self.portfolio.is_net_long(self.instrument_id):
                    self.close_all_positions(self.instrument_id)

        self._prev_ma50 = curr_ma50
        self._prev_ma200 = curr_ma200

    @classmethod
    def run(
        cls,
        data_source: Union[str, pathlib.Path, pd.DataFrame],
        symbol: Optional[str] = None,
        venue: str = "NSE",
        currency: str = "INR",
        trade_size: Union[int, Quantity] = 1,
        starting_balance: float = 1_000_000.0,
        pipeline: Optional[StrategyDataPipeline] = None,
        engine_config: Optional[BacktestEngineConfig] = None,
    ) -> BacktestEngine:
        """
        Executes a real NautilusTrader backtest for MACrossoverStrategy.

        Flow:
            raw CSV / data source
              -> StrategyDataPipeline.prepare()
              -> Nautilus Equity + BarType + Bars + Features
              -> Nautilus BacktestEngine
              -> MACrossoverStrategy.on_bar()
              -> simulated orders / fills
              -> actual portfolio / backtest results

        Returns:
            The executed BacktestEngine instance with reports and result populated.
        """
        pipe = pipeline or StrategyDataPipeline()
        prepared_data = pipe.prepare(
            data_source=data_source,
            symbol=symbol,
            venue=venue,
            currency=currency,
        )

        cfg = engine_config or BacktestEngineConfig()
        engine = BacktestEngine(config=cfg)

        engine.add_venue(
            venue=Venue(venue),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(starting_balance, Currency.from_str(currency))],
        )

        engine.add_instrument(prepared_data.equity)
        engine.add_data(prepared_data.bars)

        strategy = MACrossoverStrategy(
            config=MACrossoverConfig(
                bar_type=prepared_data.bar_type,
                featured_df=prepared_data.featured_df,
                trade_size=trade_size,
            )
)
        engine.add_strategy(strategy)

        engine.run()
        return engine


if __name__ == "__main__":
    engine = MACrossoverStrategy.run(
        data_source="data/raw/Final_NIFTY100_EoD_Data/ABB.csv",
        symbol="ABB",
        trade_size=100,
    )
    # Generate tearsheet
    create_tearsheet(
        engine=engine,
        output_path="backtest_results.html",
        title="MACrossover Strategy Backtest"

    )
    result = engine.get_result()
    print("=== Backtest Completed ===")
    print(f"Total orders: {result.total_orders}")
    print(f"Total positions: {result.total_positions}")
    print(f"PnL: {result.stats_pnls}")

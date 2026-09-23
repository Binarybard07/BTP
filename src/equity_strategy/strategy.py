import pathlib
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Union
import pandas as pd

from equity_strategy.data.loader import CSVDataLoader
from equity_strategy.features.engine import FeatureEngine

class BaseStrategy(ABC):
    """
    Abstract Base Strategy Interface.

    Orchestrates the pipeline through FeatureEngine:
    CSVDataLoader -> DataValidator -> FeatureEngine -> Strategy Decisions.

    Provides a generic framework for chronological (bar-by-bar) decision generation.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.feature_engine = FeatureEngine(self.config)

    def load_and_prepare_data(self, data_dir: Union[str, pathlib.Path]) -> pd.DataFrame:
        """
        Loads raw CSV market data, validates schema/integrity, and computes features.

        Pipeline:
        CSVDataLoader -> DataValidator -> FeatureEngine
        """
        loader = CSVDataLoader(data_dir)
        df_raw = loader.load_data(validate=True)
        df_featured = self.feature_engine.transform(df_raw)
        return df_featured

    def run(self, data_dir: Union[str, pathlib.Path]) -> pd.DataFrame:
        """
        End-to-end execution:
        Loads raw data, validates, extracts features, and generates bar-by-bar strategy decisions.
        """
        df_featured = self.load_and_prepare_data(data_dir)
        return self.generate_decisions(df_featured)

    @abstractmethod
    def generate_decisions(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Processes featured DataFrame chronologically bar-by-bar per symbol and produces decisions.

        Must return a DataFrame containing at minimum:
        Date, Symbol, Decision ('BUY', 'SELL', 'HOLD', 'WAIT'), and PositionState ('LONG', 'FLAT').
        """
        pass


class EMACrossoverStrategy(BaseStrategy):
    """
    Moving Average Crossover Strategy.

    Rules:
    - Entry (Bullish Crossover):
      previous MA50 <= previous MA200 AND current MA50 > current MA200
      Action: 'BUY', PositionState -> 'LONG'

    - Exit (Bearish Crossover):
      previous MA50 >= previous MA200 AND current MA50 < current MA200
      Action: 'SELL', PositionState -> 'FLAT'

    - Unavailable MAs (either MA50 or MA200 is NaN/None):
      Action: 'WAIT', PositionState -> 'FLAT'

    - No Crossover / In Position:
      Action: 'HOLD', PositionState -> unchanged ('LONG' or 'FLAT')

    Chronological bar-by-bar processing ensures decision at bar T strictly uses information through T.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        short_ma_period: int = 50,
        long_ma_period: int = 200,
    ):
        super().__init__(config=config)
        self.short_ma_period = short_ma_period
        self.long_ma_period = long_ma_period

    def generate_decisions(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Processes input featured DataFrame chronologically per symbol and generates crossover decisions.
        """
        if df.empty:
            return pd.DataFrame(columns=["Date", "Symbol", "Close", "MA50", "MA200", "Decision", "PositionState"])

        # Sort chronologically by Symbol and Date
        df_sorted = df.sort_values(by=["Symbol", "Date"]).reset_index(drop=True)

        results = []

        # Group by Symbol for independent chronological processing
        for symbol, group in df_sorted.groupby("Symbol", group_keys=False):
            symbol_decisions = self._process_symbol_chronologically(group)
            results.append(symbol_decisions)

        if not results:
            return pd.DataFrame(columns=["Date", "Symbol", "Close", "MA50", "MA200", "Decision", "PositionState"])

        output_df = pd.concat(results, ignore_index=True)
        return output_df.sort_values(by=["Date", "Symbol"]).reset_index(drop=True)

    def _process_symbol_chronologically(self, group_df: pd.DataFrame) -> pd.DataFrame:
        """
        Processes single-symbol DataFrame bar-by-bar chronologically.
        Guarantees decision at bar T only uses information available through T.
        """
        df_sym = group_df.sort_values(by="Date").reset_index(drop=True)
        close_series = df_sym["Close"]

        # Derive MA50
        if "close_to_ma50" in df_sym.columns:
            ma50_series = close_series / df_sym["close_to_ma50"]
        elif "close_ma50" in df_sym.columns:
            ma50_series = close_series / df_sym["close_ma50"]
        else:
            ma50_series = close_series.rolling(window=self.short_ma_period, min_periods=self.short_ma_period).mean()

        # Derive MA200
        if "close_to_ma200" in df_sym.columns:
            ma200_series = close_series / df_sym["close_to_ma200"]
        elif "close_ma200" in df_sym.columns:
            ma200_series = close_series / df_sym["close_ma200"]
        else:
            ma200_series = close_series.rolling(window=self.long_ma_period, min_periods=self.long_ma_period).mean()

        df_sym["MA50"] = ma50_series
        df_sym["MA200"] = ma200_series

        decisions = []
        position_states = []

        current_state = "FLAT"  # Initial state: FLAT
        prev_ma50 = None
        prev_ma200 = None

        # Bar-by-bar chronological iteration
        for idx in range(len(df_sym)):
            curr_ma50 = df_sym.at[idx, "MA50"]
            curr_ma200 = df_sym.at[idx, "MA200"]

            # Check if MAs are available
            ma_available = (
                prev_ma50 is not None
                and prev_ma200 is not None
                and not pd.isna(prev_ma50)
                and not pd.isna(prev_ma200)
                and curr_ma50 is not None
                and curr_ma200 is not None
                and not pd.isna(curr_ma50)
                and not pd.isna(curr_ma200)
            )

            if not ma_available:
                decision = "WAIT"
            else:
                # Bullish Crossover (Entry)
                is_bullish_crossover = (prev_ma50 <= prev_ma200) and (curr_ma50 > curr_ma200)

                # Bearish Crossover (Exit)
                is_bearish_crossover = (prev_ma50 >= prev_ma200) and (curr_ma50 < curr_ma200)

                if is_bullish_crossover and current_state == "FLAT":
                    decision = "BUY"
                    current_state = "LONG"
                elif is_bearish_crossover and current_state == "LONG":
                    decision = "SELL"
                    current_state = "FLAT"
                else:
                    decision = "HOLD"

            decisions.append(decision)
            position_states.append(current_state)

            prev_ma50 = curr_ma50
            prev_ma200 = curr_ma200

        df_sym["Decision"] = decisions
        df_sym["PositionState"] = position_states

        return df_sym

import os
import pathlib
import logging
from typing import Dict, Any, Optional
import pandas as pd

from equity_strategy.universe.provider import UniverseProvider
from equity_strategy.features.engine import FeatureEngine
from equity_strategy.signals.baseline import BaselineSignal
from equity_strategy.portfolio.equal_weight import EqualWeightPortfolio
from equity_strategy.research.rebalancer import MonthlyRebalancer

logger = logging.getLogger(__name__)

class TargetWeightGenerator:
    """
    Executes the research pipeline and generates target weight schedules.
    Pipeline: Market Data -> Features -> Baseline Signals -> Monthly Rebalancing -> Equal Weight Portfolio -> Target Weights
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        universe_provider: Optional[UniverseProvider] = None
    ):
        self.config = config or {}
        self.universe_provider = universe_provider or UniverseProvider()
        self.feature_engine = FeatureEngine(self.config)
        self.signal_model = BaselineSignal(self.config)
        self.portfolio_constructor = EqualWeightPortfolio(self.config)

    def generate(self, df_raw: pd.DataFrame) -> pd.DataFrame:
        """
        Processes raw OHLCV DataFrame through feature engine, baseline signal, rebalancer,
        and portfolio constructor to produce target weight table.
        """
        logger.info("Starting target weight generation pipeline...")

        # 1. Feature Engineering
        df_featured = self.feature_engine.transform(df_raw)

        # 2. Signal Generation
        df_signals = self.signal_model.generate_signals(df_featured)

        # 3. Monthly Rebalance Dates
        rebalance_pairs = MonthlyRebalancer.get_rebalance_dates(df_signals["Date"])
        logger.info(f"Identified {len(rebalance_pairs)} monthly rebalance sessions.")

        all_target_weights = []

        for signal_date, effective_date in rebalance_pairs:
            # Active symbols on signal_date
            available_symbols = set(df_signals[df_signals["Date"] == signal_date]["Symbol"])
            eligible_symbols = self.universe_provider.get_eligible_symbols(signal_date, available_symbols)

            # Filter signals at signal_date for eligible symbols
            day_signals = df_signals[
                (df_signals["Date"] == signal_date) & (df_signals["Symbol"].isin(eligible_symbols))
            ].copy()

            if day_signals.empty:
                continue

            # Portfolio construction
            target_df = self.portfolio_constructor.construct_portfolio(day_signals)
            if target_df.empty:
                continue

            target_df["SignalDate"] = signal_date
            target_df["EffectiveDate"] = effective_date

            # Order columns canonically
            target_df = target_df[
                ["SignalDate", "EffectiveDate", "Symbol", "Rank", "FinalScore", "TargetWeight"]
            ]
            all_target_weights.append(target_df)

        if not all_target_weights:
            result = pd.DataFrame(
                columns=["SignalDate", "EffectiveDate", "Symbol", "Rank", "FinalScore", "TargetWeight"]
            )
        else:
            result = pd.concat(all_target_weights, ignore_index=True)

        return result

    def save_targets(self, df_targets: pd.DataFrame, output_dir: str = "data/outputs"):
        """Saves generated target weights to Parquet and CSV format."""
        out_path = pathlib.Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        parquet_file = out_path / "target_weights.parquet"
        csv_file = out_path / "target_weights.csv"

        df_targets.to_parquet(parquet_file, index=False)
        df_targets.to_csv(csv_file, index=False)

        logger.info(f"Target weights saved to '{parquet_file}' and '{csv_file}'.")

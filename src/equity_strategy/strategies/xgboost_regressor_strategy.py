import copy
from dataclasses import dataclass, field
import logging
import pathlib
import pickle
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd

logger = logging.getLogger(__name__)


from equity_strategy.data.loader import CSVDataLoader
from equity_strategy.features.engine import FeatureEngine
from equity_strategy.targets.engine import TargetEngine
from equity_strategy.data.training_dataset import (
    TrainingDatasetBuilder,
    FEATURE_COLUMNS,
    TARGET_COLUMN,
)
from equity_strategy.models.base import BaseModel
from equity_strategy.models.xgboost_regressor import XGBoostRegressor
from equity_strategy.selection.base import BaseSelector
from equity_strategy.selection.top_n import TopNSelector
from equity_strategy.portfolio.base import BasePortfolio
from equity_strategy.portfolio.equal_weight import EqualWeightPortfolio
from equity_strategy.rebalancing.base import BaseRebalanceStrategy
from equity_strategy.rebalancing.monthly import MonthlyRebalanceStrategy


@dataclass
class StrategyResult:
    """
    Encapsulates the complete outputs from an ML strategy orchestration run.
    Provides methods for human-readable reporting and lossless serialization.
    """

    target_weights: pd.DataFrame
    weights_by_date: Dict[pd.Timestamp, Dict[str, float]]
    selected_stocks_by_date: Dict[pd.Timestamp, List[str]]
    predictions_by_date: Dict[pd.Timestamp, pd.DataFrame]
    rebalance_dates: List[pd.Timestamp]
    training_df: pd.DataFrame
    featured_df: pd.DataFrame

    def to_text(self) -> str:
        """
        Returns a structured, human-readable summary of the complete strategy result.
        Shows informative overviews and representative samples without dumping huge DataFrames.
        """
        lines: List[str] = []
        sep = "=" * 80
        sub_sep = "-" * 80

        lines.append(sep)
        lines.append("ML STRATEGY EXECUTION RESULT SUMMARY")
        lines.append(sep)

        # 1. Rebalance Overview
        lines.append("\n1. REBALANCE OVERVIEW")
        lines.append(sub_sep)
        total_rebalances = len(self.rebalance_dates)
        lines.append(f"Total Rebalance Dates:  {total_rebalances}")
        if total_rebalances > 0:
            first_reb = self.rebalance_dates[0].strftime("%Y-%m-%d")
            last_reb = self.rebalance_dates[-1].strftime("%Y-%m-%d")
            lines.append(f"First Rebalance Date:   {first_reb}")
            lines.append(f"Last Rebalance Date:    {last_reb}")
            lines.append("\nRebalance Schedule:")
            if total_rebalances <= 24:
                for d in self.rebalance_dates:
                    lines.append(f"  - {d.strftime('%Y-%m-%d')}")
            else:
                for d in self.rebalance_dates[:10]:
                    lines.append(f"  - {d.strftime('%Y-%m-%d')}")
                lines.append(f"  ... [{total_rebalances - 20} intermediate dates omitted] ...")
                for d in self.rebalance_dates[-10:]:
                    lines.append(f"  - {d.strftime('%Y-%m-%d')}")
        else:
            lines.append("No rebalance dates executed.")

        # 2. Featured Dataset Summary
        lines.append("\n2. FEATURED DATASET SUMMARY")
        lines.append(sub_sep)
        if self.featured_df is not None and not self.featured_df.empty:
            f_rows, f_cols = self.featured_df.shape
            lines.append(f"Shape:            {f_rows:,} rows x {f_cols} columns")
            lines.append(f"Columns ({len(self.featured_df.columns)}):   {', '.join(self.featured_df.columns.astype(str))}")
            if "Date" in self.featured_df.columns:
                f_min_date = pd.to_datetime(self.featured_df["Date"]).min().strftime("%Y-%m-%d")
                f_max_date = pd.to_datetime(self.featured_df["Date"]).max().strftime("%Y-%m-%d")
                lines.append(f"Date Range:       {f_min_date} to {f_max_date}")
            if "Symbol" in self.featured_df.columns:
                lines.append(f"Unique Symbols:   {self.featured_df['Symbol'].nunique()}")
        else:
            lines.append("Featured dataset is empty or None.")

        # 3. Training Dataset Summary
        lines.append("\n3. TRAINING DATASET SUMMARY")
        lines.append(sub_sep)
        if self.training_df is not None and not self.training_df.empty:
            t_rows, t_cols = self.training_df.shape
            lines.append(f"Shape:            {t_rows:,} rows x {t_cols} columns")
            lines.append(f"Columns ({len(self.training_df.columns)}):   {', '.join(self.training_df.columns.astype(str))}")
            if "Date" in self.training_df.columns:
                t_min_date = pd.to_datetime(self.training_df["Date"]).min().strftime("%Y-%m-%d")
                t_max_date = pd.to_datetime(self.training_df["Date"]).max().strftime("%Y-%m-%d")
                lines.append(f"Date Range:       {t_min_date} to {t_max_date}")
            if "Symbol" in self.training_df.columns:
                lines.append(f"Unique Symbols:   {self.training_df['Symbol'].nunique()}")
        else:
            lines.append("Training dataset is empty or None.")

        # 4. Selected Stocks by Date
        lines.append("\n4. SELECTED STOCKS BY REBALANCE DATE")
        lines.append(sub_sep)
        total_selected_dates = len(self.selected_stocks_by_date)
        lines.append(f"Total Selection Periods: {total_selected_dates}")
        if total_selected_dates > 0:
            sample_dates = list(self.selected_stocks_by_date.keys())
            if total_selected_dates <= 12:
                display_dates = sample_dates
                omitted = 0
            else:
                display_dates = sample_dates[:5] + sample_dates[-5:]
                omitted = total_selected_dates - 10

            for i, dt in enumerate(display_dates):
                if omitted > 0 and i == 5:
                    lines.append(f"  ... [{omitted} intermediate selection periods omitted] ...")
                syms = self.selected_stocks_by_date[dt]
                dt_str = dt.strftime("%Y-%m-%d")
                lines.append(f"  {dt_str} ({len(syms)} stocks): {', '.join(syms)}")
        else:
            lines.append("No selected stocks recorded.")

        # 5. Predictions by Date
        lines.append("\n5. PREDICTIONS SUMMARY BY REBALANCE DATE")
        lines.append(sub_sep)
        total_pred_dates = len(self.predictions_by_date)
        lines.append(f"Total Prediction Snapshots: {total_pred_dates}")
        if total_pred_dates > 0:
            pred_sample_dates = list(self.predictions_by_date.keys())
            focus_dates = [pred_sample_dates[0]]
            if len(pred_sample_dates) > 1:
                focus_dates.append(pred_sample_dates[-1])

            for dt in focus_dates:
                df_pred = self.predictions_by_date[dt]
                dt_str = dt.strftime("%Y-%m-%d")
                lines.append(f"\nSnapshot for {dt_str} (Evaluated Universe: {len(df_pred)} stocks):")
                if not df_pred.empty and "score" in df_pred.columns:
                    top5 = df_pred.sort_values(by="score", ascending=False).head(5)
                    bot5 = df_pred.sort_values(by="score", ascending=False).tail(5)
                    lines.append("  Top 5 Predicted Scores:")
                    for _, row in top5.iterrows():
                        lines.append(f"    - {row['Symbol']}: {row['score']:.6f}")
                    lines.append("  Bottom 5 Predicted Scores:")
                    for _, row in bot5.iterrows():
                        lines.append(f"    - {row['Symbol']}: {row['score']:.6f}")
                    s_min = df_pred["score"].min()
                    s_mean = df_pred["score"].mean()
                    s_max = df_pred["score"].max()
                    s_std = df_pred["score"].std()
                    lines.append(f"  Score Distribution: min={s_min:.6f}, mean={s_mean:.6f}, max={s_max:.6f}, std={s_std:.6f}")
        else:
            lines.append("No prediction snapshots recorded.")

        # 6. Target Weights
        lines.append("\n6. TARGET WEIGHTS SUMMARY")
        lines.append(sub_sep)
        if self.target_weights is not None and not self.target_weights.empty:
            w_rows = len(self.target_weights)
            lines.append(f"Total Weight Allocations: {w_rows:,}")
            if "Symbol" in self.target_weights.columns:
                lines.append(f"Unique Symbols Held:      {self.target_weights['Symbol'].nunique()}")
            if "Weight" in self.target_weights.columns:
                w_min = self.target_weights["Weight"].min()
                w_mean = self.target_weights["Weight"].mean()
                w_max = self.target_weights["Weight"].max()
                lines.append(f"Weight Stats:             min={w_min:.4f}, mean={w_mean:.4f}, max={w_max:.4f}")

            lines.append("\nSample Target Weights Head:")
            head_str = self.target_weights.head(10).to_string(index=False)
            for line in head_str.splitlines():
                lines.append(f"  {line}")

            if w_rows > 10:
                lines.append("\nSample Target Weights Tail:")
                tail_str = self.target_weights.tail(10).to_string(index=False)
                for line in tail_str.splitlines():
                    lines.append(f"  {line}")
        else:
            lines.append("Target weights table is empty.")

        lines.append("\n" + sep)
        return "\n".join(lines)

    def save(
        self,
        output_dir: Union[str, pathlib.Path],
    ) -> Tuple[pathlib.Path, pathlib.Path]:
        """
        Saves the complete StrategyResult object and its human-readable text summary.

        Outputs:
            - strategy_result.pkl: Lossless pickled StrategyResult instance.
            - strategy_result.txt: Human-readable text summary.

        Parameters:
            output_dir: Directory where result files will be saved. Created if it does not exist.

        Returns:
            Tuple[pathlib.Path, pathlib.Path]: Paths to (pkl_file, txt_file).
        """
        out_path = pathlib.Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        pkl_file = out_path / "strategy_result.pkl"
        txt_file = out_path / "strategy_result.txt"

        # 1. Lossless object serialization via standard pickle
        with open(pkl_file, "wb") as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)

        # 2. Human-readable text representation
        txt_content = self.to_text()
        txt_file.write_text(txt_content, encoding="utf-8")

        logger.info("Saved complete StrategyResult to: %s", pkl_file)
        logger.info("Saved human-readable summary to:   %s", txt_file)

        return pkl_file, txt_file

    @classmethod
    def load(cls, pkl_path: Union[str, pathlib.Path]) -> "StrategyResult":
        """
        Loads a StrategyResult instance from a pickle file.
        """
        with open(pkl_path, "rb") as f:
            obj = pickle.load(f)
        if not isinstance(obj, cls):
            raise TypeError(f"Expected {cls.__name__}, got {type(obj).__name__}")
        return obj


@dataclass
class XGBoostRegressorConfig:
    """
    Configuration for the XGBoost Regressor ML Strategy Orchestrator.
    Allows swappable components via dependency injection.
    """

    data_source: Union[str, pathlib.Path, pd.DataFrame] = "data/raw/NIFTY50"
    feature_columns: List[str] = field(default_factory=lambda: list(FEATURE_COLUMNS))
    target_horizon: int = 10
    target_column: str = TARGET_COLUMN
    model: Optional[BaseModel] = None
    selector: Optional[BaseSelector] = None
    portfolio: Optional[BasePortfolio] = None
    feature_engine: Optional[FeatureEngine] = None
    target_engine: Optional[TargetEngine] = None
    dataset_builder: Optional[TrainingDatasetBuilder] = None
    rebalance_strategy: Optional[BaseRebalanceStrategy] = None
    rebalance_frequency: str = "monthly"
    min_train_days: int = 252  # Minimum historical trading days required before first rebalance


class XGBoostRegressorStrategy:
    """
    Concrete ML Strategy Orchestrator.

    Orchestrates the complete ML equity strategy flow:
        data
          ↓
        features (FeatureEngine)
          ↓
        target (TargetEngine)
          ↓
        training dataset (TrainingDatasetBuilder)
          ↓
        train model (expanding training window)
          ↓
        predict scores
          ↓
        selection DataFrame (Symbol, score)
          ↓
        selector (TopNSelector / BaseSelector)
          ↓
        selected symbols
          ↓
        portfolio weights (EqualWeightPortfolio / BasePortfolio)
    """

    def __init__(self, config: Optional[XGBoostRegressorConfig] = None, **kwargs: Any):
        if config is not None:
            self.config = config
        else:
            self.config = XGBoostRegressorConfig(**kwargs)

        self.feature_columns: List[str] = list(
            self.config.feature_columns or FEATURE_COLUMNS
        )
        self.target_horizon: int = self.config.target_horizon
        self.target_column: str = self.config.target_column or TARGET_COLUMN
        self.min_train_days: int = self.config.min_train_days

        # Swappable components (with default concrete implementations)
        self.feature_engine: FeatureEngine = (
            self.config.feature_engine or FeatureEngine()
        )
        self.target_engine: TargetEngine = self.config.target_engine or TargetEngine(
            horizon=self.target_horizon, target_col=self.target_column
        )
        self.dataset_builder: TrainingDatasetBuilder = (
            self.config.dataset_builder
            or TrainingDatasetBuilder(
                feature_columns=self.feature_columns,
                target_column=self.target_column,
                feature_engine=self.feature_engine,
                target_engine=self.target_engine,
            )
        )
        self.model_prototype: BaseModel = self.config.model or XGBoostRegressor()
        self.selector: BaseSelector = self.config.selector or TopNSelector(n=20)
        self.portfolio: BasePortfolio = (
            self.config.portfolio or EqualWeightPortfolio()
        )
        self.rebalance_strategy: BaseRebalanceStrategy = (
            self.config.rebalance_strategy or MonthlyRebalanceStrategy()
        )

    def _get_fresh_model(self) -> BaseModel:
        """Instantiates a fresh model instance to avoid carryover state between folds."""
        if hasattr(self.model_prototype, "params") and hasattr(
            type(self.model_prototype), "__init__"
        ):
            return type(self.model_prototype)(**self.model_prototype.params)
        return copy.deepcopy(self.model_prototype)

    def execute(
        self,
        data_source: Optional[Union[str, pathlib.Path, pd.DataFrame]] = None,
    ) -> StrategyResult:
        """
        Executes the ML strategy orchestration loop across the dataset.

        Parameters:
            data_source: Path to CSV or directory, or existing DataFrame.

        Returns:
            StrategyResult: Target weights, selected stocks, predictions, and datasets.
        """
        src = data_source if data_source is not None else self.config.data_source

        # Step 1: Ingest raw market data
        if isinstance(src, (str, pathlib.Path)):
            loader = CSVDataLoader(src)
            df_raw = loader.load_data(validate=True)
        elif isinstance(src, pd.DataFrame):
            df_raw = src.copy()
        else:
            raise TypeError(f"Unsupported data source type: {type(src)}")

        if df_raw.empty:
            raise ValueError("Input data source is empty.")

        # Ensure Date column is datetime
        df_raw["Date"] = pd.to_datetime(df_raw["Date"])
        df_raw = df_raw.sort_values(by=["Symbol", "Date"]).reset_index(drop=True)

        # Step 2: Compute features
        featured_df = self.feature_engine.transform(df_raw)
        featured_df["Date"] = pd.to_datetime(featured_df["Date"])

        # Step 3: Compute targets
        targeted_df = self.target_engine.transform(featured_df)
        targeted_df["Date"] = pd.to_datetime(targeted_df["Date"])

        # Step 4: Build clean training dataset
        training_df = self.dataset_builder.build(targeted_df)
        training_df["Date"] = pd.to_datetime(training_df["Date"])

        # Step 5: Causally generate monthly rebalance dates
        all_trading_dates = (
            pd.Series(featured_df["Date"].unique())
            .sort_values()
            .reset_index(drop=True)
        )
        rebalance_dates = self.rebalance_strategy.generate_rebalance_dates(all_trading_dates)
        date_to_index = {d: i for i, d in enumerate(all_trading_dates)}

        # Output containers
        weights_records: List[Dict[str, Any]] = []
        weights_by_date: Dict[pd.Timestamp, Dict[str, float]] = {}
        selected_stocks_by_date: Dict[pd.Timestamp, List[str]] = {}
        predictions_by_date: Dict[pd.Timestamp, pd.DataFrame] = {}
        executed_rebalance_dates: List[pd.Timestamp] = []

        # Step 6: Orchestrate expanding-window training and rebalancing
        for reb_date in rebalance_dates:
            date_idx = date_to_index.get(reb_date)
            if date_idx is None or date_idx < self.min_train_days:
                # Not enough historical warmup periods before this rebalance date
                continue

            # Target realization cutoff: prevent target leakage / lookahead
            # Forward returns over target_horizon days from date tau realize on tau + target_horizon
            cutoff_idx = max(0, date_idx - self.target_horizon)
            train_cutoff_date = all_trading_dates.iloc[cutoff_idx]

            # Expanding training window using data strictly through train_cutoff_date
            train_subset = training_df[training_df["Date"] <= train_cutoff_date]
            if train_subset.empty:
                continue

            X_train = train_subset[self.feature_columns]
            y_train = train_subset[self.target_column]

            # Train fresh model on expanding window
            model = self._get_fresh_model()
            model.train(X_train, y_train)

            # Extract features for all active stocks on the rebalance decision date
            current_bar_data = featured_df[featured_df["Date"] == reb_date].copy()
            valid_current = current_bar_data.dropna(subset=self.feature_columns)
            if valid_current.empty:
                continue

            X_pred = valid_current[self.feature_columns]
            scores = model.predict(X_pred)

            # Build standardized selection DataFrame: Symbol, score
            selection_df = pd.DataFrame({
                "Symbol": valid_current["Symbol"].astype(str).values,
                "score": scores,
            })
            predictions_by_date[reb_date] = selection_df

            # Select stocks via selector
            selected_symbols = self.selector.select(selection_df)
            if not selected_symbols:
                continue

            # Construct portfolio weights
            weights = self.portfolio.construct(selected_symbols)
            if not weights:
                continue

            # Record rebalance results
            executed_rebalance_dates.append(reb_date)
            weights_by_date[reb_date] = weights
            selected_stocks_by_date[reb_date] = selected_symbols

            for sym, w in weights.items():
                weights_records.append({
                    "Date": reb_date,
                    "Symbol": sym,
                    "Weight": w,
                })

        target_weights_df = (
            pd.DataFrame(weights_records)
            if weights_records
            else pd.DataFrame(columns=["Date", "Symbol", "Weight"])
        )

        return StrategyResult(
            target_weights=target_weights_df,
            weights_by_date=weights_by_date,
            selected_stocks_by_date=selected_stocks_by_date,
            predictions_by_date=predictions_by_date,
            rebalance_dates=executed_rebalance_dates,
            training_df=training_df,
            featured_df=featured_df,
        )

    @classmethod
    def run(
        cls,
        data_source: Optional[Union[str, pathlib.Path, pd.DataFrame]] = None,
        config: Optional[XGBoostRegressorConfig] = None,
        **kwargs: Any,
    ) -> StrategyResult:
        """Convenience entry point to execute the strategy."""
        cfg = config or XGBoostRegressorConfig(**kwargs)
        strategy = cls(config=cfg)
        return strategy.execute(data_source=data_source)



# Aliases for convenience
XGBoostStrategyConfig = XGBoostRegressorConfig


if __name__ == "__main__":
    result = XGBoostRegressorStrategy.run()
    logger.info("=== XGBoost Regressor Strategy Run Completed ===")
    logger.info("Total rebalance dates: %d", len(result.rebalance_dates))
    logger.info("Total weight records: %d", len(result.target_weights))
    result.save("output/strategy_result")
    if not result.target_weights.empty:
        logger.info("Sample Target Weights:\n%s", result.target_weights.head(10))
import copy
from dataclasses import dataclass, field
import logging
import pathlib
from typing import Any, Dict, List, Optional, Union
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


@dataclass
class StrategyResult:
    """
    Encapsulates the complete outputs from an ML strategy orchestration run.
    """

    target_weights: pd.DataFrame
    weights_by_date: Dict[pd.Timestamp, Dict[str, float]]
    selected_stocks_by_date: Dict[pd.Timestamp, List[str]]
    predictions_by_date: Dict[pd.Timestamp, pd.DataFrame]
    rebalance_dates: List[pd.Timestamp]
    training_df: pd.DataFrame
    featured_df: pd.DataFrame


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

    def _get_fresh_model(self) -> BaseModel:
        """Instantiates a fresh model instance to avoid carryover state between folds."""
        if hasattr(self.model_prototype, "params") and hasattr(
            type(self.model_prototype), "__init__"
        ):
            return type(self.model_prototype)(**self.model_prototype.params)
        return copy.deepcopy(self.model_prototype)

    @staticmethod
    def get_monthly_rebalance_dates(
        dates: pd.Series,
        mode: str = "last_day",
    ) -> List[pd.Timestamp]:
        """
        Identifies monthly rebalance dates from trading calendar dates.

        Parameters:
            dates: Series of trading dates.
            mode: 'last_day' (last available trading session of each month) or 'first_day'.

        Returns:
            List[pd.Timestamp]: Chronologically sorted list of rebalance dates.
        """
        unique_dates = (
            pd.Series(pd.to_datetime(dates).unique())
            .sort_values()
            .reset_index(drop=True)
        )
        if unique_dates.empty:
            return []

        df_dates = pd.DataFrame({"Date": unique_dates})
        df_dates["YearMonth"] = df_dates["Date"].dt.to_period("M")

        if mode == "first_day":
            indices = df_dates.groupby("YearMonth")["Date"].idxmin().tolist()
        else:
            indices = df_dates.groupby("YearMonth")["Date"].idxmax().tolist()

        return unique_dates.iloc[sorted(indices)].tolist()

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

        # Step 5: Identify calendar trading days and monthly rebalance dates
        all_trading_dates = (
            pd.Series(featured_df["Date"].unique())
            .sort_values()
            .reset_index(drop=True)
        )
        rebalance_dates = self.get_monthly_rebalance_dates(all_trading_dates, mode="last_day")
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

            # Expanding training window
            train_subset = training_df[training_df["Date"] <= train_cutoff_date]
            if train_subset.empty:
                continue

            X_train = train_subset[self.feature_columns]
            y_train = train_subset[self.target_column]

            # Train fresh model on expanding window
            model = self._get_fresh_model()
            model.train(X_train, y_train)

            # Extract features for all active stocks on the rebalance date
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
    if not result.target_weights.empty:
        logger.info("Sample Target Weights:\n%s", result.target_weights.head(10))


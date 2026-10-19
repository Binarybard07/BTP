from typing import List, Optional
import numpy as np
import pandas as pd
from equity_strategy.features.engine import FeatureEngine
from equity_strategy.targets.engine import TargetEngine

# Retained market price/volume columns (not treated as model-input features)
OHLCV_COLUMNS: List[str] = ["Open", "High", "Low", "Close", "Volume"]

# Canonical column definitions
FEATURE_COLUMNS: List[str] = [
    "return_21",
    "return_63",
    "return_252",
    "close_to_ma50",
    "close_to_ma200",
    "volatility_21",
]

# Forward return target column
TARGET_COLUMN: str = "target_10d"

# Full dataset columns: Date, Symbol, OHLCV, 6 features, target_10d
DATASET_COLUMNS: List[str] = (
    ["Date", "Symbol"] + OHLCV_COLUMNS + FEATURE_COLUMNS + [TARGET_COLUMN]
)


class TrainingDatasetBuilder:
    """
    Combines computed features and forward return targets into a clean training dataset.

    Dataset Structure:
        - Metadata: Date, Symbol
        - OHLCV: Open, High, Low, Close, Volume (retained in dataset, not model features)
        - Model Features: return_21, return_63, return_252, close_to_ma50, close_to_ma200, volatility_21
        - Target: target_10d (from TargetEngine)

    Rows with NaN, missing, or inf values in any required column are dropped.
    """

    def __init__(
        self,
        feature_columns: List[str],
        ohlcv_columns: List[str],
        target_column: str,
        feature_engine: FeatureEngine,
        target_engine: TargetEngine
    ):
        # Keep feature_columns explicitly separate as the model-input feature list
        self.feature_columns = list(feature_columns)
        self.ohlcv_columns = list(ohlcv_columns)
        self.target_column = target_column 
        self.feature_engine = feature_engine
        self.target_engine = target_engine

    def build(
        self,
        data: pd.DataFrame,
        targets_df: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """
        Builds the clean training dataset.

        Parameters:
            data: DataFrame (raw, featured, or featured+targeted).
            targets_df: Optional precomputed targets DataFrame to merge with data.

        Returns:
            pd.DataFrame: Clean dataset containing Date, Symbol, OHLCV, the six features, and target_10d.
        """
        # Step 1: Ingest data
        df = data.copy()

        if df.empty:
            return pd.DataFrame(columns=self.dataset_columns)

        # Step 2: Compute features if missing
        missing_features = [col for col in self.feature_columns if col not in df.columns]
        if missing_features:
            df = self.feature_engine.transform(df)

        # Step 3: Combine with targets
        if targets_df is not None:
            if self.target_column not in targets_df.columns:
                raise ValueError(
                    f"Target column '{self.target_column}' not found in provided targets_df."
                )
            
            if self.target_column in df.columns:
                raise ValueError(
                    f"Input data already contains '{self.target_column}'. "
                    "Pass either targeted data or targets_df, not both."
                )

            merge_keys = ["Symbol", "Date"]

            if not all(
                col in df.columns and col in targets_df.columns
                for col in merge_keys
            ):
                raise ValueError(
                    "Both data and targets_df must contain 'Symbol' and 'Date' "
                    "to align targets correctly."
                )
            
            cols_to_merge = merge_keys + [self.target_column]
            df = pd.merge(df, targets_df[cols_to_merge], on=merge_keys, how="inner")

        else:
            if self.target_column not in df.columns:
                df = self.target_engine.transform(df)

        # Step 4: Validate required columns exist
        required_cols = self.dataset_columns
        missing_cols = [c for c in required_cols if c not in df.columns]
        if missing_cols:
            raise ValueError(f"Required columns missing after processing: {missing_cols}")

        # Step 5: Clean and filter dataset
        clean_df = df[required_cols].copy()
        clean_df = clean_df.replace([np.inf, -np.inf], np.nan)
        clean_df = clean_df.dropna(subset=required_cols)
        clean_df = clean_df.sort_values(by=["Symbol", "Date"]).reset_index(drop=True)

        return clean_df

    @property
    def dataset_columns(self) -> List[str]:
        """Returns the complete list of columns in the final training dataset."""
        return ["Date", "Symbol"] + self.ohlcv_columns + self.feature_columns + [self.target_column]


def build_training_dataset(
    data: pd.DataFrame,
    feature_columns: List[str],
    ohlcv_columns: List[str],
    target_column: str,
    feature_engine: FeatureEngine,
    target_engine: TargetEngine,
    targets_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Convenience function to build a clean training dataset combining features and target.

    Parameters:
        data: DataFrame
        feature_columns: Required List of model-input feature column names.
        ohlcv_columns: Required List of OHLCV column names.
        target_column: Required Target column name.
        feature_engine: Required FeatureEngine instance.
        target_engine: Required TargetEngine instance.
        targets_df: Precomputed targets DataFrame.

    Returns:
        pd.DataFrame: Clean dataset containing Date, Symbol, OHLCV, features, and target.
    """
    builder = TrainingDatasetBuilder(
        feature_columns=feature_columns,
        ohlcv_columns=ohlcv_columns,
        target_column=target_column,
        feature_engine=feature_engine,
        target_engine=target_engine,
    )
    return builder.build(data, targets_df=targets_df)

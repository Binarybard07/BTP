from typing import List, Optional
import numpy as np
import pandas as pd
from equity_strategy.features.engine import FeatureEngine
from equity_strategy.targets.engine import TargetEngine


class TrainingDatasetBuilder:
    """
    Combines configured features and targets into a clean training dataset.
    Rows with NaN, missing, or inf values in any required column are dropped.
    """

    def __init__(
        self,
        feature_engine: FeatureEngine,
        target_engine: TargetEngine,
        ohlcv_columns: List[str],
    ):
        self.feature_engine = feature_engine
        self.target_engine = target_engine
        self.ohlcv_columns = list(ohlcv_columns)
        self.feature_columns = list(feature_engine.feature_columns)
        self.target_column = str(target_engine.target_col)

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
            pd.DataFrame: Clean dataset containing Date, Symbol, OHLCV, features, and target.
        """
        # Step 1: Ingest data
        df = data.copy()

        if df.empty:
            return pd.DataFrame(columns=self.dataset_columns)

        # Step 2: Validate required feature columns
        missing_features = [col for col in self.feature_columns if col not in df.columns]
        if missing_features:
            raise ValueError(
                f"Missing required feature columns: {missing_features}. "
                "Please compute feature computation using FeatureEngine before building the training dataset."
            )

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
    feature_engine: FeatureEngine,
    target_engine: TargetEngine,
    ohlcv_columns: List[str],
    targets_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Convenience function to build a clean training dataset combining features and target.
    """
    builder = TrainingDatasetBuilder(
        feature_engine=feature_engine,
        target_engine=target_engine,
        ohlcv_columns=ohlcv_columns,
    )
    return builder.build(data, targets_df=targets_df)

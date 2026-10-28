from typing import Any, Callable, Dict, List
import pandas as pd

from equity_strategy.features.momentum import MomentumFeatures
from equity_strategy.features.trend import TrendFeatures
from equity_strategy.features.volatility import VolatilityFeatures


class FeatureEngine:
    """
    Applies modular feature computations per symbol without mixing data across symbols.
    Computes only explicitly configured features.
    """

    INDICATORS: Dict[str, Callable[..., pd.Series]] = {
        # Trend features
        "sma": TrendFeatures.sma,
        "ema": TrendFeatures.ema,
        "close_to_sma": TrendFeatures.close_to_sma,
        "close_to_ema": TrendFeatures.close_to_ema,
        # Momentum features
        "returns": MomentumFeatures.returns,
        "roc": MomentumFeatures.roc,
        # Volatility features
        "rolling_std": VolatilityFeatures.rolling_std,
    }

    def __init__(self, features: Dict[str, Dict[str, Any]]) -> None:
        if not features:
            raise ValueError("FeatureEngine requires a non-empty features dictionary.")
        if not isinstance(features, dict):
            raise TypeError("features must be a dictionary mapping column names to indicator configurations.")

        for col_name, spec in features.items():
            if not isinstance(spec, dict) or "indicator" not in spec:
                raise ValueError(f"Feature '{col_name}' must be a dict containing an 'indicator' key.")
            if spec["indicator"] not in self.INDICATORS:
                raise ValueError(
                    f"Unknown indicator '{spec['indicator']}' for column '{col_name}'. "
                    f"Available indicators: {list(self.INDICATORS.keys())}"
                )

        self.features = dict(features)
        self.feature_columns: List[str] = list(self.features.keys())

    def _compute_symbol_features(self, df_symbol: pd.DataFrame) -> pd.DataFrame:
        """Computes configured features for a single symbol DataFrame sorted by Date."""
        df_symbol = df_symbol.sort_values(by="Date").reset_index(drop=True)
        df_result = df_symbol.copy()

        for col_name, spec in self.features.items():
            func = self.INDICATORS[spec["indicator"]]
            params = spec.get("params", {k: v for k, v in spec.items() if k != "indicator"})
            df_result[col_name] = func(df_result, **params)

        return df_result

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Groups the dataset by Symbol and applies feature transformations per symbol.
        """
        if df.empty:
            return df

        if "Symbol" not in df.columns:
            return self._compute_symbol_features(df)

        sort_cols = ["Symbol", "Date"] if "Date" in df.columns else ["Symbol"]
        df_sorted = df.sort_values(by=sort_cols).reset_index(drop=True)

        featured_dfs = []
        for symbol, group in df_sorted.groupby("Symbol", group_keys=False):
            featured_group = self._compute_symbol_features(group)
            featured_dfs.append(featured_group)

        result = pd.concat(featured_dfs, ignore_index=True)
        result = result.sort_values(by=sort_cols).reset_index(drop=True)
        return result

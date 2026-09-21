from typing import Dict, Any, Optional
import pandas as pd

from equity_strategy.features.momentum import compute_momentum_features
from equity_strategy.features.trend import compute_trend_features
from equity_strategy.features.volatility import compute_volatility_features

class FeatureEngine:
    """
    Applies modular feature computations per symbol without mixing data across symbols.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config.get("features", {}) if config else {}
        self.momentum_short = cfg.get("momentum_short", 21)
        self.momentum_medium = cfg.get("momentum_medium", 63)
        self.momentum_long = cfg.get("momentum_long", 252)
        self.moving_average_short = cfg.get("moving_average_short", 50)
        self.moving_average_long = cfg.get("moving_average_long", 200)
        self.volatility_window = cfg.get("volatility_window", 21)

    def _compute_symbol_features(self, df_symbol: pd.DataFrame) -> pd.DataFrame:
        """Computes all features for a single symbol DataFrame sorted by Date."""
        df_symbol = df_symbol.sort_values(by="Date").reset_index(drop=True)

        df_symbol = compute_momentum_features(
            df_symbol,
            short_window=self.momentum_short,
            medium_window=self.momentum_medium,
            long_window=self.momentum_long,
        )

        df_symbol = compute_trend_features(
            df_symbol,
            short_ma=self.moving_average_short,
            long_ma=self.moving_average_long,
        )

        df_symbol = compute_volatility_features(
            df_symbol,
            window=self.volatility_window,
        )

        return df_symbol

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Groups the dataset by Symbol and applies feature transformations per symbol.
        """
        if df.empty:
            return df

        # Ensure sorted
        df_sorted = df.sort_values(by=["Symbol", "Date"]).reset_index(drop=True)

        # Apply per symbol group
        featured_dfs = []
        for symbol, group in df_sorted.groupby("Symbol", group_keys=False):
            featured_group = self._compute_symbol_features(group)
            featured_dfs.append(featured_group)

        result = pd.concat(featured_dfs, ignore_index=True)
        result = result.sort_values(by=["Symbol", "Date"]).reset_index(drop=True)
        return result

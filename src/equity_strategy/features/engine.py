import pandas as pd

from equity_strategy.features.momentum import compute_momentum_features
from equity_strategy.features.trend import compute_trend_features
from equity_strategy.features.volatility import compute_volatility_features

class FeatureEngine:
    """
    Applies modular feature computations per symbol without mixing data across symbols.
    """

    def __init__(
        self,
        momentum_short: int = 21,
        momentum_medium: int = 63,
        momentum_long: int = 252,
        moving_average_short: int = 50,
        moving_average_long: int = 200,
        volatility_window: int = 21,
    ) -> None:
        if momentum_short <= 0 or momentum_medium <= 0 or momentum_long <= 0:
            raise ValueError("Momentum windows must be positive integers.")
        if moving_average_short <= 0 or moving_average_long <= 0:
            raise ValueError("Moving average windows must be positive integers.")
        if volatility_window <= 0:
            raise ValueError("Volatility window must be a positive integer.")
        self.momentum_short = int(momentum_short)
        self.momentum_medium = int(momentum_medium)
        self.momentum_long = int(momentum_long)
        self.moving_average_short = int(moving_average_short)
        self.moving_average_long = int(moving_average_long)
        self.volatility_window = int(volatility_window)

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

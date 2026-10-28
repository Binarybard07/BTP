import pandas as pd


class MomentumFeatures:
    """Momentum feature calculations per symbol."""

    @staticmethod
    def returns(df: pd.DataFrame, period: int = 21, price_col: str = "Close") -> pd.Series:
        if price_col not in df.columns:
            raise ValueError(f"Input DataFrame missing required '{price_col}' column.")
        if period <= 0:
            raise ValueError("period must be a positive integer.")
        return df[price_col] / df[price_col].shift(period) - 1.0

    @staticmethod
    def roc(df: pd.DataFrame, period: int = 21, price_col: str = "Close") -> pd.Series:
        if price_col not in df.columns:
            raise ValueError(f"Input DataFrame missing required '{price_col}' column.")
        if period <= 0:
            raise ValueError("period must be a positive integer.")
        return (df[price_col] - df[price_col].shift(period)) / df[price_col].shift(period) * 100.0

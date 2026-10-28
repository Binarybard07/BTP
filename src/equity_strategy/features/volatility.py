import pandas as pd


class VolatilityFeatures:
    """Volatility feature calculations per symbol."""

    @staticmethod
    def rolling_std(df: pd.DataFrame, period: int = 21, price_col: str = "Close") -> pd.Series:
        if price_col not in df.columns:
            raise ValueError(f"Input DataFrame missing required '{price_col}' column.")
        if period <= 0:
            raise ValueError("period must be a positive integer.")
        daily_ret = df[price_col].pct_change()
        return daily_ret.rolling(window=period, min_periods=period).std()

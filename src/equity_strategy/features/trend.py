import pandas as pd


class TrendFeatures:
    """Trend feature calculations per symbol."""

    @staticmethod
    def sma(df: pd.DataFrame, period: int = 50, price_col: str = "Close") -> pd.Series:
        if price_col not in df.columns:
            raise ValueError(f"Input DataFrame missing required '{price_col}' column.")
        if period <= 0:
            raise ValueError("period must be a positive integer.")
        return df[price_col].rolling(window=period, min_periods=period).mean()

    @staticmethod
    def ema(df: pd.DataFrame, period: int = 50, price_col: str = "Close") -> pd.Series:
        if price_col not in df.columns:
            raise ValueError(f"Input DataFrame missing required '{price_col}' column.")
        if period <= 0:
            raise ValueError("period must be a positive integer.")
        return df[price_col].ewm(span=period, adjust=False).mean()

    @staticmethod
    def close_to_sma(df: pd.DataFrame, period: int = 50, price_col: str = "Close") -> pd.Series:
        if price_col not in df.columns:
            raise ValueError(f"Input DataFrame missing required '{price_col}' column.")
        if period <= 0:
            raise ValueError("period must be a positive integer.")
        return df[price_col] / TrendFeatures.sma(df, period=period, price_col=price_col)

    @staticmethod
    def close_to_ema(df: pd.DataFrame, period: int = 50, price_col: str = "Close") -> pd.Series:
        if price_col not in df.columns:
            raise ValueError(f"Input DataFrame missing required '{price_col}' column.")
        if period <= 0:
            raise ValueError("period must be a positive integer.")
        return df[price_col] / TrendFeatures.ema(df, period=period, price_col=price_col)

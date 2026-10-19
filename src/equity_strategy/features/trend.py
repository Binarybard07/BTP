import pandas as pd

def compute_trend_features(
    df: pd.DataFrame,
    short_ma: int = 50,
    long_ma: int = 200
) -> pd.DataFrame:
    """
    Computes trend features per symbol:
    - close_to_ma50: Close / MA50
    - close_to_ma200: Close / MA200
    """
    if "Close" not in df.columns:
        raise ValueError("Input DataFrame missing required 'Close' column.")
    if short_ma <= 0 or long_ma <= 0:
        raise ValueError("Moving average windows must be positive integers.")

    df = df.copy()
    close = df["Close"]

    ma_short = close.rolling(window=short_ma, min_periods=short_ma).mean()
    ma_long = close.rolling(window=long_ma, min_periods=long_ma).mean()

    df[f"close_to_ma{short_ma}"] = close / ma_short
    df[f"close_to_ma{long_ma}"] = close / ma_long

    return df

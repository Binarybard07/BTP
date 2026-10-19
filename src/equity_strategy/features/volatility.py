import pandas as pd

def compute_volatility_features(
    df: pd.DataFrame,
    window: int = 21
) -> pd.DataFrame:
    """
    Computes volatility feature per symbol:
    - volatility_21: 21-day rolling standard deviation of daily returns
    """
    if "Close" not in df.columns:
        raise ValueError("Input DataFrame missing required 'Close' column.")
    if window <= 0:
        raise ValueError("Volatility window must be a positive integer.")

    df = df.copy()
    daily_return = df["Close"].pct_change()
    df[f"volatility_{window}"] = daily_return.rolling(window=window, min_periods=window).std()

    return df

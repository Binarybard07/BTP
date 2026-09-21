import pandas as pd

def compute_volatility_features(
    df: pd.DataFrame,
    window: int = 21
) -> pd.DataFrame:
    """
    Computes volatility feature per symbol:
    - volatility_21: 21-day rolling standard deviation of daily returns
    """
    df = df.copy()
    daily_return = df["Close"].pct_change()
    df[f"volatility_{window}"] = daily_return.rolling(window=window, min_periods=window).std()

    return df

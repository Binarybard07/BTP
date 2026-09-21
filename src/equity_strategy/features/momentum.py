import pandas as pd

def compute_momentum_features(
    df: pd.DataFrame,
    short_window: int = 21,
    medium_window: int = 63,
    long_window: int = 252
) -> pd.DataFrame:
    """
    Computes momentum features per symbol:
    - return_21: Close[t] / Close[t-21] - 1
    - return_63: Close[t] / Close[t-63] - 1
    - return_252: Close[t] / Close[t-252] - 1
    """
    df = df.copy()
    close = df["Close"]

    df[f"return_{short_window}"] = close / close.shift(short_window) - 1.0
    df[f"return_{medium_window}"] = close / close.shift(medium_window) - 1.0
    df[f"return_{long_window}"] = close / close.shift(long_window) - 1.0

    return df

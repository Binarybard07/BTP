from typing import Optional
import pandas as pd


def compute_future_returns(
    df: pd.DataFrame,
    horizon: int = 10,
    target_col: Optional[str] = None,
) -> pd.DataFrame:
    """
    Calculates future return for a configurable trading-day horizon.

    Formula:
        Close.shift(-horizon) / Close - 1.0

    Parameters:
        df: Input DataFrame containing a 'Close' column.
        horizon: Configurable trading-day forward horizon (default: 10).
        target_col: Optional column name for the computed target.
                    Defaults to f"target_{horizon}d".

    Returns:
        pd.DataFrame: A copy of the DataFrame with the future return target column added.
    """
    if horizon <= 0:
        raise ValueError(f"horizon must be a positive integer, got {horizon}")
    if "Close" not in df.columns:
        raise ValueError("Input DataFrame missing required 'Close' column.")

    df = df.copy()
    col_name = target_col if target_col is not None else f"target_{horizon}d"
    close = df["Close"]
    df[col_name] = close.shift(-horizon) / close - 1.0

    return df
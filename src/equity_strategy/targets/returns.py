import pandas as pd


class ReturnTargets:
    """Return target calculations per symbol."""

    @staticmethod
    def forward_return(df: pd.DataFrame, horizon: int = 10, price_col: str = "Close") -> pd.Series:
        if price_col not in df.columns:
            raise ValueError(f"Input DataFrame missing required '{price_col}' column.")
        if horizon <= 0:
            raise ValueError(f"horizon must be a positive integer, got {horizon}")
        return df[price_col].shift(-horizon) / df[price_col] - 1.0

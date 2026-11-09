from typing import List, Optional
import numpy as np
import pandas as pd

from equity_strategy.selection.base import BaseSelector


class PercentileSelector(BaseSelector):
    """
    Selects stocks with prediction scores at or above a configurable cross-sectional percentile,
    with an optional minimum predicted return hurdle rate (min_return).
    """

    def __init__(self, percentile: float = 80.0, min_return: Optional[float] = None):
        if not (0.0 <= percentile <= 100.0):
            raise ValueError(
                f"Parameter 'percentile' must be between 0 and 100, got {percentile}"
            )
        # Convert fractional percentile (e.g. 0.8) to 0-100 scale if 0 < percentile < 1
        if 0.0 < percentile < 1.0:
            self.percentile: float = float(percentile * 100.0)
        else:
            self.percentile: float = float(percentile)
        self.min_return: Optional[float] = None if min_return is None else float(min_return)

    def select(self, df: pd.DataFrame, **kwargs) -> List[str]:
        """
        Selects stocks with scores at or above the percentile cutoff and exceeding min_return (if set),
        sorted descending by score.

        Parameters:
            df: Standardized DataFrame with 'Symbol' and 'score' columns.

        Returns:
            List[str]: Selected stock symbols.
        """
        if df.empty:
            return []

        if "Symbol" not in df.columns or "score" not in df.columns:
            raise ValueError("Input DataFrame must contain 'Symbol' and 'score' columns.")

        clean_df = df[["Symbol", "score"]].dropna().copy()
        clean_df = clean_df.replace([np.inf, -np.inf], np.nan).dropna(subset=["score"])
        if clean_df.empty:
            return []

        cutoff = float(np.percentile(clean_df["score"].values, self.percentile))
        if self.min_return is not None:
            filtered = clean_df[(clean_df["score"] >= cutoff) & (clean_df["score"] > self.min_return)]
        else:
            filtered = clean_df[clean_df["score"] >= cutoff]

        sorted_df = filtered.sort_values(by="score", ascending=False)
        return sorted_df["Symbol"].astype(str).tolist()

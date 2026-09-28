from typing import List
import numpy as np
import pandas as pd

from equity_strategy.selection.base import BaseSelector


class ThresholdSelector(BaseSelector):
    """
    Selects stocks with prediction scores above (or at/above) a configurable threshold.
    """

    def __init__(self, threshold: float = 0.0, inclusive: bool = False):
        self.threshold: float = float(threshold)
        self.inclusive: bool = bool(inclusive)

    def select(self, df: pd.DataFrame, **kwargs) -> List[str]:
        """
        Selects stocks with scores exceeding the threshold, sorted descending by score.

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

        if self.inclusive:
            filtered = clean_df[clean_df["score"] >= self.threshold]
        else:
            filtered = clean_df[clean_df["score"] > self.threshold]

        sorted_df = filtered.sort_values(by="score", ascending=False)
        return sorted_df["Symbol"].astype(str).tolist()

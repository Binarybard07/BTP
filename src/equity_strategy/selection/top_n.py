from typing import List
import numpy as np
import pandas as pd

from equity_strategy.selection.base import BaseSelector


class TopNSelector(BaseSelector):
    """
    Selects the top N stocks with the highest prediction scores.
    """

    def __init__(self, n: int = 20):
        if n <= 0:
            raise ValueError(f"Parameter 'n' must be a positive integer, got {n}")
        self.n: int = int(n)

    def select(self, df: pd.DataFrame, **kwargs) -> List[str]:
        """
        Selects the highest N scoring stocks, sorted descending by score.

        Parameters:
            df: Standardized DataFrame with 'Symbol' and 'score' columns.

        Returns:
            List[str]: Top N selected stock symbols.
        """
        if df.empty:
            return []

        if "Symbol" not in df.columns or "score" not in df.columns:
            raise ValueError("Input DataFrame must contain 'Symbol' and 'score' columns.")

        clean_df = df[["Symbol", "score"]].dropna().copy()
        clean_df = clean_df.replace([np.inf, -np.inf], np.nan).dropna(subset=["score"])
        if clean_df.empty:
            return []

        sorted_df = clean_df.sort_values(by="score", ascending=False)
        return sorted_df["Symbol"].astype(str).tolist()[: self.n]

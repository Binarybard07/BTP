from typing import Optional
import pandas as pd

from equity_strategy.targets.returns import compute_future_returns


class TargetEngine:
    """
    Applies modular target computations per symbol without mixing data across symbols.
    Preserves chronological ordering.
    """

    def __init__(
        self,
        horizon: int = 10,
        target_col: Optional[str] = None,
    ) -> None:
        if horizon <= 0:
            raise ValueError(f"horizon must be a positive integer, got {horizon}")
        self.horizon: int = int(horizon)
        self.target_col: str = (
            target_col
            if target_col is not None
            else f"target_{self.horizon}d"
        )

    def _compute_symbol_targets(self, df_symbol: pd.DataFrame) -> pd.DataFrame:
        """
        Computes future return target for a single symbol DataFrame
        sorted chronologically by Date.
        """
        if "Date" in df_symbol.columns:
            df_symbol = df_symbol.sort_values(by="Date").reset_index(drop=True)

        df_symbol = compute_future_returns(
            df_symbol,
            horizon=self.horizon,
            target_col=self.target_col,
        )

        return df_symbol

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Groups the dataset by Symbol and applies target calculations per symbol.
        Preserves chronological ordering.
        """
        if df.empty:
            return df

        if "Symbol" not in df.columns:
            return self._compute_symbol_targets(df)

        sort_cols = ["Symbol", "Date"] if "Date" in df.columns else ["Symbol"]
        df_sorted = df.sort_values(by=sort_cols).reset_index(drop=True)

        target_dfs = []
        for symbol, group in df_sorted.groupby("Symbol", group_keys=False):
            target_group = self._compute_symbol_targets(group)
            target_dfs.append(target_group)

        result = pd.concat(target_dfs, ignore_index=True)
        result = result.sort_values(by=sort_cols).reset_index(drop=True)
        return result

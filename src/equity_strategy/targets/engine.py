from typing import Any, Callable, Dict
import pandas as pd

from equity_strategy.targets.returns import ReturnTargets


class TargetEngine:
    """
    Applies modular target computations per symbol without mixing data across symbols.
    Preserves chronological ordering.
    """

    INDICATORS: Dict[str, Callable[..., pd.Series]] = {
        "forward_return": ReturnTargets.forward_return,
    }

    def __init__(
        self,
        target_col: str,
        indicator: str,
        params: Dict[str, Any],
    ) -> None:
        if not target_col:
            raise ValueError("TargetEngine requires target_col.")
        if indicator not in self.INDICATORS:
            raise ValueError(
                f"Unknown target indicator '{indicator}'. "
                f"Available target indicators: {list(self.INDICATORS.keys())}"
            )
        if not params or not isinstance(params, dict):
            raise ValueError("TargetEngine requires a non-empty params dictionary.")

        col = str(target_col).strip()
        if not col.startswith("target_"):
            col = f"target_{col}"
        self.target_col = col
        self.indicator = str(indicator)
        self.params = dict(params)
        self.horizon: int = int(self.params["horizon"])

    def _compute_symbol_targets(self, df_symbol: pd.DataFrame) -> pd.DataFrame:
        """
        Computes configured target for a single symbol DataFrame sorted chronologically by Date.
        """
        if "Date" in df_symbol.columns:
            df_symbol = df_symbol.sort_values(by="Date").reset_index(drop=True)

        df_result = df_symbol.copy()
        func = self.INDICATORS[self.indicator]
        df_result[self.target_col] = func(df_result, **self.params)
        return df_result

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

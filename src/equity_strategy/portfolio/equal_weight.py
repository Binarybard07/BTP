import logging
from typing import Dict, Any, Optional
import pandas as pd

from equity_strategy.portfolio.base import PortfolioConstructor

logger = logging.getLogger(__name__)

class EqualWeightPortfolio(PortfolioConstructor):
    """
    Selects top candidates based on FinalScore and constructs an equal-weighted target portfolio.
    - Candidates selected: Top candidate_count (default 50)
    - Holdings selected: Top holding_count (default 30) from candidates
    - Weight: 1 / N for each selected stock
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config.get("portfolio", {}) if config else {}
        self.candidate_count = cfg.get("candidates", 50)
        self.holding_count = cfg.get("holdings", 30)

    def construct_portfolio(self, df_signals: pd.DataFrame) -> pd.DataFrame:
        """
        Input DataFrame must contain Date, Symbol, FinalScore.
        Returns DataFrame containing Rank, Symbol, FinalScore, TargetWeight for selected stocks.
        """
        if df_signals.empty:
            return pd.DataFrame(columns=["Rank", "Symbol", "FinalScore", "TargetWeight"])

        # Filter out NaN FinalScores
        df_valid = df_signals.dropna(subset=["FinalScore"]).copy()
        if df_valid.empty:
            return pd.DataFrame(columns=["Rank", "Symbol", "FinalScore", "TargetWeight"])

        # Sort descending by FinalScore
        df_sorted = df_valid.sort_values(by="FinalScore", ascending=False).reset_index(drop=True)
        df_sorted["Rank"] = df_sorted.index + 1

        # Select top candidates (e.g. 50)
        candidates = df_sorted.head(self.candidate_count).copy()

        # Select top holdings (e.g. 30)
        holdings = candidates.head(self.holding_count).copy()
        n = len(holdings)

        if n == 0:
            return pd.DataFrame(columns=["Rank", "Symbol", "FinalScore", "TargetWeight"])

        weight = 1.0 / n
        holdings["TargetWeight"] = weight

        return holdings[["Rank", "Symbol", "FinalScore", "TargetWeight"]]

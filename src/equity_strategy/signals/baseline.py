import logging
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np

from equity_strategy.signals.base import SignalModel

logger = logging.getLogger(__name__)

class BaselineSignal(SignalModel):
    """
    V1 Baseline Quantitative Signal Model.
    Computes cross-sectional percentile ranks on each Date for:
    - MomentumScore = 0.4 * rank(return_21) + 0.6 * rank(return_63)
    - TrendScore    = 0.5 * rank(close_ma50) + 0.5 * rank(close_ma200)
    - FinalScore    = 0.6 * MomentumScore    + 0.4 * TrendScore
    """

    REQUIRED_FEATURES = ["return_21", "return_63", "close_ma50", "close_ma200"]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config.get("signal", {}) if config else {}
        self.w_mom_short = cfg.get("momentum_weight_short", 0.4)
        self.w_mom_med = cfg.get("momentum_weight_medium", 0.6)
        self.w_trend_short = cfg.get("trend_weight_short", 0.5)
        self.w_trend_long = cfg.get("trend_weight_long", 0.5)
        self.w_mom_final = cfg.get("momentum_final_weight", 0.6)
        self.w_trend_final = cfg.get("trend_final_weight", 0.4)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculates cross-sectional rank scores per Date for eligible stocks.
        Excludes stocks with missing required feature values on that date.
        """
        if df.empty:
            return df

        df_out = df.copy()

        # Identify missing values in required features
        valid_mask = pd.Series(True, index=df_out.index)
        for feat in self.REQUIRED_FEATURES:
            if feat not in df_out.columns:
                raise KeyError(f"Required feature '{feat}' missing from input DataFrame.")
            valid_mask = valid_mask & (~df_out[feat].isna())

        excluded_count = len(df_out) - valid_mask.sum()
        logger.info(f"BaselineSignal: Excluded {excluded_count} row(s) due to invalid/missing feature values.")

        # Filter valid rows for cross-sectional ranking
        df_valid = df_out[valid_mask].copy()

        if df_valid.empty:
            df_out["MomentumScore"] = np.nan
            df_out["TrendScore"] = np.nan
            df_out["FinalScore"] = np.nan
            return df_out

        # Perform cross-sectional percentile ranking per Date
        rank_21 = df_valid.groupby("Date")["return_21"].rank(pct=True)
        rank_63 = df_valid.groupby("Date")["return_63"].rank(pct=True)
        rank_ma50 = df_valid.groupby("Date")["close_ma50"].rank(pct=True)
        rank_ma200 = df_valid.groupby("Date")["close_ma200"].rank(pct=True)

        df_valid["MomentumScore"] = (self.w_mom_short * rank_21) + (self.w_mom_med * rank_63)
        df_valid["TrendScore"] = (self.w_trend_short * rank_ma50) + (self.w_trend_long * rank_ma200)
        df_valid["FinalScore"] = (self.w_mom_final * df_valid["MomentumScore"]) + (self.w_trend_final * df_valid["TrendScore"])

        # Merge back scores into full dataframe
        df_out["MomentumScore"] = np.nan
        df_out["TrendScore"] = np.nan
        df_out["FinalScore"] = np.nan

        df_out.loc[df_valid.index, "MomentumScore"] = df_valid["MomentumScore"]
        df_out.loc[df_valid.index, "TrendScore"] = df_valid["TrendScore"]
        df_out.loc[df_valid.index, "FinalScore"] = df_valid["FinalScore"]

        return df_out

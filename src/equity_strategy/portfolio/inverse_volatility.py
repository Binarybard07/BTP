"""
Inverse Volatility & Alpha-Tilted Portfolio Construction Module
---------------------------------------------------------------
Allocates portfolio sleeve weights inversely proportional to realized volatility (1 / sigma_i),
optionally tilted by model alpha conviction (score_i / sigma_i) to maximize portfolio Sharpe ratio.
Enforces weight capping via water-filling and handles short-history assets gracefully.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from equity_strategy.portfolio.base import BasePortfolio

logger = logging.getLogger(__name__)


class InverseVolatilityPortfolio(BasePortfolio):
    """
    Constructs an Inverse-Volatility or Alpha-Tilted (Sharpe-proportional) portfolio.

    Formula:
        Raw weight:
            w_i = alpha_i / sigma_i
        where:
            sigma_i: Sample standard deviation of daily simple returns over `lookback` sessions up to `date`.
            alpha_i: Model conviction signal (expected return from `scores`).
                     If use_alpha_tilt is True and scores are provided, alpha_i = max(score_i, 1e-4).
                     If use_alpha_tilt is False or scores are omitted, alpha_i = 1.0 (pure inverse vol).

        Weight Capping:
            If cap_mult is set (e.g. 2.5), no individual stock can exceed cap_mult / N of the sleeve.
            Excess weight is redistributed across unconstrained assets via iterative water-filling.

        Short History Handling:
            Assets with fewer than `min_obs` return observations receive an equal-weight share (1 / N),
            and the remaining budget is split among seasoned assets according to their risk/alpha weights.
    """

    def __init__(
        self,
        lookback: int = 63,
        min_obs: int = 40,
        cap_mult: Optional[float] = 2.5,
        use_alpha_tilt: bool = True,
    ):
        """
        Parameters:
            lookback: Lookback window in trading sessions to estimate return volatility (default: 63).
            min_obs: Minimum required trading sessions of return history (default: 40).
            cap_mult: Maximum weight cap expressed as a multiple of equal weight (e.g. 2.5 / N).
                      Set to None or 0 to disable capping (default: 2.5).
            use_alpha_tilt: If True, scale weights by model prediction score (Sharpe weighting) (default: True).
        """
        self.lookback = int(lookback)
        self.min_obs = int(min_obs)
        self.cap_mult = float(cap_mult) if cap_mult is not None and float(cap_mult) > 0 else None
        self.use_alpha_tilt = bool(use_alpha_tilt)

    def _returns(self, closes: pd.DataFrame, symbols: List[str], date: pd.Timestamp) -> pd.DataFrame:
        """
        Calculates daily simple returns for symbols up to `date` inclusive.
        Strictly slices data <= date to guarantee zero future data leakage.
        """
        cols = [s for s in symbols if s in closes.columns]
        if not cols:
            return pd.DataFrame()
        px = closes.loc[:pd.Timestamp(date), cols].tail(int(self.lookback) + 1)
        return px.pct_change().iloc[1:]

    def _project_bounds(self, w: np.ndarray, lo: float, hi: float) -> np.ndarray:
        """
        Projects weights into [lo, hi] while strictly maintaining sum(w) == 1.0 via water-filling.
        """
        w = np.clip(np.asarray(w, dtype=float), lo, hi)
        for _ in range(100):
            gap = 1.0 - w.sum()
            if abs(gap) < 1e-12:
                break
            room = (hi - w) if gap > 0 else (w - lo)
            tot = room.sum()
            if tot <= 1e-15:
                break
            w = np.clip(w + gap * room / tot, lo, hi)
        return w

    def construct(
        self,
        symbols: List[str],
        closes: Optional[pd.DataFrame] = None,
        date: Optional[pd.Timestamp] = None,
        scores: Optional[Dict[str, float]] = None,
        **kwargs,
    ) -> Dict[str, float]:
        """
        Calculates target weights for selected symbols.

        Parameters:
            symbols: List of selected stock symbols.
            closes: Historical daily close price panel (index = dates, columns = symbols).
            date: Decision timestamp (weights computed using data strictly <= date).
            scores: Optional mapping of symbol -> predicted forward return from model.
            **kwargs: Extra keyword arguments.

        Returns:
            Dict[str, float]: Mapping of symbol to portfolio target weight (summing to 1.0).
        """
        if not symbols:
            return {}

        unique_symbols = list(dict.fromkeys(str(s) for s in symbols if s))
        n = len(unique_symbols)
        if n == 0:
            return {}
        if n == 1:
            return {unique_symbols[0]: 1.0}

        # Fallback to equal weight if historical closes or decision date are unavailable
        if closes is None or date is None:
            return {s: 1.0 / n for s in unique_symbols}

        rets = self._returns(closes, unique_symbols, date)
        if rets.empty:
            return {s: 1.0 / n for s in unique_symbols}

        # Partition symbols by history length
        have, short = [], []
        for s in unique_symbols:
            count = int(rets[s].notna().sum()) if s in rets.columns else 0
            (have if count >= self.min_obs else short).append(s)

        if not have:
            return {s: 1.0 / n for s in unique_symbols}

        # Calculate standard deviation of returns
        sd = rets[have].std()
        sd = sd.replace(0.0, np.nan)
        inv = 1.0 / sd
        inv = inv.replace([np.inf, -np.inf], np.nan).dropna()

        # If standard deviations cannot be computed, fallback to 1 / N
        if inv.empty:
            return {s: 1.0 / n for s in unique_symbols}

        valid_have = [s for s in have if s in inv.index]
        if not valid_have:
            return {s: 1.0 / n for s in unique_symbols}

        # Alpha signal: model predicted score if enabled
        # Clamped so alpha is strictly positive: max(score, 1e-4)
        if self.use_alpha_tilt and scores is not None:
            alpha = {}
            for s in valid_have:
                val = scores.get(s, None)
                if val is not None and not np.isnan(val):
                    alpha[s] = max(float(val), 1e-4)
                else:
                    alpha[s] = 1.0
        else:
            alpha = {s: 1.0 for s in valid_have}

        # Raw weights = alpha / sigma
        raw = np.array([alpha[s] * inv[s] for s in valid_have], dtype=float)
        tot_raw = raw.sum()
        if tot_raw <= 0 or not np.isfinite(tot_raw):
            w_have = np.full(len(valid_have), 1.0 / len(valid_have))
        else:
            w_have = raw / tot_raw

        # Weight capping via water-filling projection
        if self.cap_mult is not None and self.cap_mult > 0:
            max_cap = min(1.0, float(self.cap_mult) / n)
            min_feasible_cap = 1.0 / len(valid_have)
            cap = max(max_cap, min_feasible_cap)
            w_have = self._project_bounds(w_have, 0.0, cap)

        # Budget division: short-history names take equal weight share (1 / N)
        eq_share = 1.0 / n
        budget = max(0.0, 1.0 - eq_share * len(short))
        total_w_have = w_have.sum()
        if total_w_have > 0:
            w_have = (w_have / total_w_have) * budget

        weights = {s: float(w_have[i]) for i, s in enumerate(valid_have)}
        for s in short:
            weights[s] = eq_share

        # Normalize to ensure exact sum to 1.0
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        return weights

r"""
stage2_strategy/xgboost_regressor_strategy.py
=============================================
XGBoost Regressor equity strategy integration for Backtest_Module.

Orchestrates the investment logic from equity_strategy through Backtest_Module's
MarketData and StrategyOutput contracts:
  1. Consumes wide-format price history from MarketData.
  2. Computes modular technical features (momentum, trend, volatility) via FeatureEngine.
  3. Builds leak-free forward return targets (target_10d) via TargetEngine.
  4. Curates sanitized training datasets via TrainingDatasetBuilder.
  5. Performs expanding-window walk-forward training with strict target realization
     cutoff (T - target_horizon) to prevent lookahead bias.
  6. Generates cross-sectional return forecasts for eligible symbols on decision dates.
  7. Selects top-ranked symbols using PercentileSelector.
  8. Computes equal-weighted allocations via EqualWeightPortfolio.
  9. Applies the 75% max capital deployment constraint.
  10. Produces a validated StrategyOutput for execution on NautilusTrader.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

# Ensure equity_strategy and Backtest_Module/src are on sys.path regardless of execution CWD
_CURRENT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = Path(__file__).resolve().parents[4]  # c:\Users\Jenish\Desktop\SEM VII\BTP
_SRC_DIR = _REPO_ROOT / "src"
_BM_SRC_DIR = Path(__file__).resolve().parents[1]  # .../Backtest_Module/src

if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
if str(_BM_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_BM_SRC_DIR))

from qbeast_backtest import config as C
from qbeast_backtest.contracts import MarketData, StrategyOutput
from qbeast_backtest.stage2_strategy.base import StrategyBase

from equity_strategy.data.training_dataset import FEATURE_COLUMNS, TARGET_COLUMN, TrainingDatasetBuilder
from equity_strategy.features.engine import FeatureEngine
from equity_strategy.models.xgboost_regressor import XGBoostRegressor
from equity_strategy.portfolio.equal_weight import EqualWeightPortfolio
from equity_strategy.selection.percentile import PercentileSelector
from equity_strategy.targets.engine import TargetEngine


class XGBoostRegressorStrategy(StrategyBase):
    """
    Cross-Sectional XGBoost Regressor Strategy integrated with Backtest_Module.

    Strictly satisfies contracts.Strategy and inherits StrategyBase.
    """

    name: str = "xgboost_regressor"

    def __init__(
        self,
        rebalance_frequency: str = "monthly",
        selector: str = "percentile",
        percentile: float = 85.0,
        top_n: Optional[int] = None,
        weighting: str = "equal",
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None,
        max_deployment_pct: float = 0.75,
        target_horizon: int = 10,
        min_train_days: int = 252,
        n_estimators: int = 100,
        max_depth: int = 3,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        random_state: int = 42,
        n_jobs: int = -1,
        **kwargs: Any,
    ) -> None:
        self.rebalance_frequency = rebalance_frequency
        self.selector_name = selector
        self.percentile = float(percentile)
        self.top_n = top_n
        self.weighting = weighting
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_deployment_pct = float(max_deployment_pct)
        self.target_horizon = int(target_horizon)
        self.min_train_days = int(min_train_days)

        if not (0.0 < self.max_deployment_pct <= 1.0):
            raise ValueError(
                f"max_deployment_pct must be in (0, 1], got {self.max_deployment_pct}"
            )

        if self.selector_name == "percentile":
            self.selector = PercentileSelector(percentile=self.percentile)
        else:
            raise ValueError(
                f"Unsupported selector {self.selector_name!r}. Expected 'percentile'."
            )

        if self.weighting == "equal":
            self.portfolio = EqualWeightPortfolio()
        else:
            raise ValueError(
                f"Unsupported weighting {self.weighting!r}. Expected 'equal'."
            )

        self.model_params: Dict[str, Any] = {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "learning_rate": learning_rate,
            "subsample": subsample,
            "colsample_bytree": colsample_bytree,
            "random_state": random_state,
            "n_jobs": n_jobs,
        }

        self.feature_columns: List[str] = list(FEATURE_COLUMNS)
        self.target_column: str = (
            f"target_{self.target_horizon}d" if self.target_horizon != 10 else TARGET_COLUMN
        )

        self.feature_engine = FeatureEngine()
        self.target_engine = TargetEngine(horizon=self.target_horizon, target_col=self.target_column)

    def decide(self, md: MarketData) -> StrategyOutput:
        """
        Executes cross-sectional predictions and allocations across decision sessions.
        """
        closes = md.history_closes
        if closes is None or closes.empty:
            raise ValueError("md.history_closes is empty; cannot compute features or targets.")

        # 1. Transform wide-format historical closes into a canonical long DataFrame
        long_df = closes.unstack().reset_index()
        long_df.columns = ["Symbol", "Date", "Close"]
        long_df = (
            long_df.dropna(subset=["Close"])
            .sort_values(by=["Symbol", "Date"])
            .reset_index(drop=True)
        )

        # 2. Compute modular features across entire available price history
        featured_df = self.feature_engine.transform(long_df)

        # 3. Build training dataset (features, forward target, and clean rows)
        builder = TrainingDatasetBuilder(
            feature_columns=self.feature_columns,
            ohlcv_columns=["Close"],
            target_column=self.target_column,
            feature_engine=self.feature_engine,
            target_engine=self.target_engine,
        )
        training_df = builder.build(featured_df)

        # 4. Map historical trading calendar to establish chronological indexes
        all_trading_dates: List[pd.Timestamp] = list(closes.index.sort_values().unique())
        date_to_idx: Dict[pd.Timestamp, int] = {d: i for i, d in enumerate(all_trading_dates)}

        targets: Dict[pd.Timestamp, Dict[str, float]] = {}
        exposure: Dict[pd.Timestamp, float] = {}
        posture: Dict[pd.Timestamp, str] = {}

        # 5. Evaluate decisions strictly on decision sessions (excluding final session in backtest)
        decision_sessions = self.decision_sessions(md)

        for t in decision_sessions:
            t_ts = pd.Timestamp(t)
            if t_ts not in date_to_idx:
                targets[t_ts] = {}
                exposure[t_ts] = 0.0
                posture[t_ts] = "cash"
                continue

            date_idx = date_to_idx[t_ts]

            # Require minimum historical trading days before first deployment
            if date_idx < self.min_train_days:
                targets[t_ts] = {}
                exposure[t_ts] = 0.0
                posture[t_ts] = "cash"
                continue

            # Prevent target leakage: cutoff at T - target_horizon
            cutoff_idx = date_idx - self.target_horizon
            if cutoff_idx < 0:
                targets[t_ts] = {}
                exposure[t_ts] = 0.0
                posture[t_ts] = "cash"
                continue

            train_cutoff_date = all_trading_dates[cutoff_idx]
            train_subset = training_df[training_df["Date"] <= train_cutoff_date]
            if train_subset.empty:
                targets[t_ts] = {}
                exposure[t_ts] = 0.0
                posture[t_ts] = "cash"
                continue

            X_train = train_subset[self.feature_columns]
            y_train = train_subset[self.target_column]

            # Fit fresh XGBoost model on expanding historical window
            model = XGBoostRegressor(**self.model_params)
            model.train(X_train, y_train)

            # Query point-in-time tradability / eligibility set
            elig = self.eligible_on(md, t_ts)
            if not elig:
                targets[t_ts] = {}
                exposure[t_ts] = 0.0
                posture[t_ts] = "cash"
                continue

            # Cross-sectional features on decision session T filtered strictly to eligible set
            current_bar_data = featured_df[
                (featured_df["Date"] == t_ts) & (featured_df["Symbol"].isin(elig))
            ].copy()

            valid_current = current_bar_data.dropna(subset=self.feature_columns)
            if valid_current.empty:
                targets[t_ts] = {}
                exposure[t_ts] = 0.0
                posture[t_ts] = "cash"
                continue

            X_pred = valid_current[self.feature_columns]
            scores = model.predict(X_pred)

            selection_df = pd.DataFrame({
                "Symbol": valid_current["Symbol"].astype(str).values,
                "score": scores,
            })

            # Stock selection via PercentileSelector
            selected_symbols = self.selector.select(selection_df)
            if not selected_symbols:
                targets[t_ts] = {}
                exposure[t_ts] = 0.0
                posture[t_ts] = "cash"
                continue

            # Construct equal-weighted portfolio allocations
            raw_weights = self.portfolio.construct(selected_symbols)
            if not raw_weights:
                targets[t_ts] = {}
                exposure[t_ts] = 0.0
                posture[t_ts] = "cash"
                continue

            # Apply maximum capital deployment fraction (e.g. 0.75)
            target_weights = {
                sym: float(w * self.max_deployment_pct)
                for sym, w in raw_weights.items()
            }

            targets[t_ts] = target_weights
            tot_w = float(sum(target_weights.values()))
            exposure[t_ts] = tot_w
            posture[t_ts] = "sized" if tot_w > 0 else "cash"

        output = StrategyOutput(
            targets=targets,
            exposure=pd.Series(exposure).sort_index() if exposure else None,
            posture=pd.Series(posture).sort_index() if posture else None,
            name=self.name,
            params=dict(
                rebalance_frequency=self.rebalance_frequency,
                selector=self.selector_name,
                percentile=self.percentile,
                top_n=self.top_n,
                weighting=self.weighting,
                stop_loss_pct=self.stop_loss_pct,
                take_profit_pct=self.take_profit_pct,
                max_deployment_pct=self.max_deployment_pct,
                target_horizon=self.target_horizon,
                min_train_days=self.min_train_days,
            ),
        )

        cash_buffer = float(getattr(C, "CASH_BUFFER", 0.97))
        self.self_check(md, output, cash_buffer=cash_buffer, strict=True)
        return output


def main() -> int:
    """
    Runnable entrypoint executing XGBoostRegressorStrategy through Backtest_Module on NautilusTrader.
    """
    from qbeast_backtest import config as C
    from qbeast_backtest import pipeline, report
    from qbeast_backtest.stage2_strategy.registry import REGISTRY

    # 1. Strategy & Backtest Parameters
    strategy_params = {
        "rebalance_frequency": "monthly",
        "selector": "percentile",
        "percentile": 85.0,
        "top_n": None,
        "weighting": "equal",
        "stop_loss_pct": None,
        "take_profit_pct": None,
        "max_deployment_pct": 0.75,
        "min_train_days": 252,
        "target_horizon": 10,
    }

    # 2. Universe configuration
    universe = os.environ.get("QBEAST_BACKTEST_UNIVERSE", "nifty100")
    os.environ["QBEAST_BACKTEST_UNIVERSE"] = universe

    # 3. Instantiate strategy explicitly
    strat = XGBoostRegressorStrategy(
        rebalance_frequency=strategy_params["rebalance_frequency"],
        selector=strategy_params["selector"],
        percentile=strategy_params["percentile"],
        top_n=strategy_params["top_n"],
        weighting=strategy_params["weighting"],
        stop_loss_pct=strategy_params["stop_loss_pct"],
        take_profit_pct=strategy_params["take_profit_pct"],
        max_deployment_pct=strategy_params["max_deployment_pct"],
        min_train_days=strategy_params["min_train_days"],
        target_horizon=strategy_params["target_horizon"],
    )

    # 4. Explicit registration in Stage 2 registry
    REGISTRY[strat.name] = lambda **kwargs: strat

    # 5. Invoke Backtest_Module pipeline exclusively on NautilusTrader
    # Fails clearly if Nautilus is unavailable; never falls back to internal ledger
    result = pipeline.run(strategy=strat.name, engine="nautilus", strict=True)

    # 6. Generate standard Backtest_Module reporting suite
    report.write(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())

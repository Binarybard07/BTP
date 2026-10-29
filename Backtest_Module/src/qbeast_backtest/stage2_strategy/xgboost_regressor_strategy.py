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

from dotenv import load_dotenv
load_dotenv()

# Centralized strategy execution environment defaults (overridden by .env if set)
os.environ["QBEAST_BACKTEST_UNIVERSE"] = os.environ.get("QBEAST_BACKTEST_UNIVERSE", "nifty500_ddq")
os.environ["QBEAST_BACKTEST_START"] = os.environ.get("QBEAST_BACKTEST_START", "2019-01-01")
os.environ["QBEAST_BACKTEST_END"] = os.environ.get("QBEAST_BACKTEST_END", "2027-01-01")
os.environ["REBALANCE_FREQ"] = os.environ.get("REBALANCE_FREQ", "monthly")

import sys
import copy
import importlib
import pandas as pd
from typing import Any, Dict, List

from qbeast_backtest.contracts import MarketData, StrategyOutput
from qbeast_backtest.stage2_strategy.base import StrategyBase
from qbeast_backtest import pipeline, report
from qbeast_backtest.stage2_strategy import registry as pkg_registry

from equity_strategy.data.training_dataset import TrainingDatasetBuilder
from equity_strategy.features.engine import FeatureEngine
from equity_strategy.targets.engine import TargetEngine
from equity_strategy.models.base import BaseModel
from equity_strategy.models.xgboost_regressor import XGBoostRegressor
from equity_strategy.portfolio.base import BasePortfolio
from equity_strategy.portfolio.equal_weight import EqualWeightPortfolio
from equity_strategy.selection.base import BaseSelector
from equity_strategy.selection.percentile import PercentileSelector
from equity_strategy.risk_management.base import BaseRiskManager
from equity_strategy.risk_management.fixed_percentage import FixedPercentageRiskManager


def _get_params(obj: Any) -> Dict[str, Any]:
    """Extract public constructor/instance attributes from a component instance."""
    if obj is None:
        return {}
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {}


class XGBoostRegressorStrategy(StrategyBase):
    """
    Cross-Sectional XGBoost Regressor Strategy integrated with Backtest_Module.

    Strictly satisfies contracts.Strategy and inherits StrategyBase.
    """

    name: str = "XGBoost_Regressor"

    def __init__(
        self,
        feature_engine: FeatureEngine,
        target_engine: TargetEngine,
        model: BaseModel,
        min_train_days: int = 252,
        selector: BaseSelector = None,
        portfolio: BasePortfolio = None,
        risk_manager: BaseRiskManager = None,
        rebalance_frequency: str = "monthly",
        **kwargs: Any,
    ) -> None:
        self.feature_engine = feature_engine
        self.target_engine = target_engine
        self.model = model
        self.min_train_days = int(min_train_days)
        self.selector = selector
        self.portfolio = portfolio
        self.risk_manager = risk_manager
        self.rebalance_frequency = rebalance_frequency

    @property
    def feature_columns(self) -> List[str]:
        return self.feature_engine.feature_columns

    @property
    def target_column(self) -> str:
        return self.target_engine.target_col

    @property
    def target_horizon(self) -> int:
        return self.target_engine.horizon

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
            feature_engine=self.feature_engine,
            target_engine=self.target_engine,
            ohlcv_columns=["Close"],
        )
        full_dataset = builder.build(featured_df)

        # 4. Resolve decision dates matching the strategy's rebalance frequency
        decision_dates = sorted(self.decision_sessions(md))

        targets: Dict[pd.Timestamp, Dict[str, float]] = {}
        exposure: Dict[pd.Timestamp, float] = {}
        posture: Dict[pd.Timestamp, str] = {}

        # 5. Expanding-window walk-forward training & prediction loop
        # Use full historical calendar so pre-window history is used for model training and warm-up
        full_calendar = md.history_closes.index

        for t_idx, t in enumerate(decision_dates):
            t_ts = pd.Timestamp(t)

            # Target realization cutoff: target realized over `target_horizon` sessions.
            # Observations beyond cutoff have unobserved forward returns -> exclude to avoid leak.
            cal_until_t = full_calendar[full_calendar <= t_ts]
            if len(cal_until_t) <= self.target_horizon:
                targets[t_ts] = {}
                exposure[t_ts] = 0.0
                posture[t_ts] = "cash"
                continue

            cutoff_date = cal_until_t[-self.target_horizon]  # T - target_horizon
            eligible_train = full_dataset[full_dataset["Date"] < cutoff_date]

            unique_train_days = eligible_train["Date"].nunique()
            if unique_train_days < self.min_train_days:
                targets[t_ts] = {}
                exposure[t_ts] = 0.0
                posture[t_ts] = "cash"
                continue

            X_train = eligible_train[self.feature_columns]
            y_train = eligible_train[self.target_column]

            # Fit fresh model on expanding historical window
            model = copy.deepcopy(self.model)
            model.train(X_train, y_train)

            # Retrieve eligible tradable symbols on decision date (Stage 1 point-in-time filter)
            elig = self.eligible_on(md, t_ts)
            if not elig:
                targets[t_ts] = {}
                exposure[t_ts] = 0.0
                posture[t_ts] = "cash"
                continue

            # Feature vectors for eligible symbols on current decision date
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

            # Construct weighted portfolio allocations
            raw_weights = self.portfolio.construct(selected_symbols)
            if not raw_weights:
                targets[t_ts] = {}
                exposure[t_ts] = 0.0
                posture[t_ts] = "cash"
                continue

            # Apply maximum capital deployment fraction (e.g. 0.75)
            # Normalized budget fraction: 1.0 represents 100% of current equity
            deployment_fraction = self.risk_manager.calculate_deployment_budget(1.0)

            target_weights = {
                sym: float(w * deployment_fraction)
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
                feature_engine=type(self.feature_engine).__name__,
                feature_engine_params=_get_params(self.feature_engine),
                target_engine=type(self.target_engine).__name__,
                target_engine_params=_get_params(self.target_engine),
                model=type(self.model).__name__,
                min_train_days=self.min_train_days,
                selector=type(self.selector).__name__,
                selector_params=_get_params(self.selector),
                portfolio=type(self.portfolio).__name__,
                portfolio_params=_get_params(self.portfolio),
                risk_manager=type(self.risk_manager).__name__,
                risk_manager_params=_get_params(self.risk_manager),
                rebalance_frequency=self.rebalance_frequency,
            ),
        )

        self.self_check(md, output, strict=True)
        return output


def main() -> int:
    """
    Runnable entrypoint executing XGBoostRegressorStrategy through Backtest_Module on NautilusTrader.
    """
    # ---------------------------------------------------------------------------------------------------
    # Strategy & Model Configuration: all experiment parameters centralized here
    # ---------------------------------------------------------------------------------------------------
    model_params = {
        "n_estimators": 100,
        "max_depth": 3,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "random_state": 42,
        "n_jobs": -1,
    }

    feature_config = {
        "return_21": {"indicator": "returns", "params": {"period": 21}},
        "return_63": {"indicator": "returns", "params": {"period": 63}},
        "return_252": {"indicator": "returns", "params": {"period": 252}},
        "close_to_ma50": {"indicator": "close_to_sma", "params": {"period": 50}},
        "close_to_ma200": {"indicator": "close_to_sma", "params": {"period": 200}},
        "volatility_21": {"indicator": "rolling_std", "params": {"period": 21}},
    }
    feature_engine = FeatureEngine(features=feature_config)
    target_engine = TargetEngine(
        target_col="fwd_return_30",
        indicator="forward_return",
        params={"horizon": 30},
    )

    strategy_params = {
        "feature_engine": feature_engine,
        "target_engine": target_engine,
        "model": XGBoostRegressor(**model_params),
        "min_train_days": 252,
        "selector": PercentileSelector(percentile=0.85),
        "portfolio": EqualWeightPortfolio(),
        "risk_manager": FixedPercentageRiskManager(max_deployment_pct=0.9),
        "rebalance_frequency": "monthly",
    }

    # 1. Instantiate strategy explicitly with the centralized configuration block
    strat = XGBoostRegressorStrategy(**strategy_params)

    # 2. Register configured instance in both package and flat registries so pipeline sees it
    factory = lambda **kwargs: strat
    pkg_registry.REGISTRY[strat.name.lower()] = factory
    flat_registry = importlib.import_module("stage2_strategy.registry")
    flat_registry.REGISTRY[strat.name.lower()] = factory

    # 3. Explicitly invoke Backtest_Module pipeline exclusively on the Nautilus execution engine
    # Fails clearly if Nautilus is unavailable; never falls back to internal ledger
    result = pipeline.run(strategy=strat.name, engine="nautilus", strict=True)

    # 4. Generate standard Backtest_Module reporting suite
    report.write(result)

    return 0

if __name__ == "__main__":
    sys.exit(main())
